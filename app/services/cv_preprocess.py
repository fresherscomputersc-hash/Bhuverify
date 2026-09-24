"""
Computer-vision preprocessing and layout detection (SRS FR-2).

Prototype tier: OpenCV heuristics, exactly as the SRS specifies. Every stage is
instrumented and returns measurable stats (deskew angle, contrast delta, region
counts) so the demo can prove the pipeline actually ran instead of asserting it.

Pilot/production tier: swap `detect_layout` for a YOLO / Detectron2 model - the
returned shape (`regions`: list of labelled boxes) is already the contract.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from app.config import PROCESSED_DIR


# ---------------------------------------------------------------------------
# Stage 1 - geometric + photometric correction
# ---------------------------------------------------------------------------
def estimate_skew_angle(gray: np.ndarray) -> float:
    """Estimate page skew in degrees from the text-line angles.

    Returns the rotation that must be applied to straighten the page (so it can
    be passed straight to `cv2.getRotationMatrix2D`).

    The naive "min-area rect of every ink pixel" approach is unusable here: the
    page border and table rules are long straight lines that drag the rectangle
    to 45/90 degrees, and a contour-median estimate collapses to 0 for small
    skews. Text rows are merged into line blobs and a length-weighted Hough
    estimate is taken instead - verified against synthetic skews of
    0/1/2/5/-2.4/-3 degrees.
    """
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = binary.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 25), 2))
    merged = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
    edges = cv2.Canny(merged, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 360, threshold=120,
        minLineLength=int(w * 0.25), maxLineGap=20,
    )
    if lines is None:
        return 0.0

    angles: list[float] = []
    weights: list[float] = []
    # OpenCV 4 returns (N,1,4), OpenCV 5 returns (N,4); normalise both.
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        length = abs(x2 - x1)
        if length < 20:
            continue
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if abs(angle) <= 20.0:  # ignore the vertical rules of the table
            angles.append(angle)
            weights.append(float(length))
    if not angles:
        return 0.0
    return float(np.average(angles, weights=weights))


def _apply_same_rotation(image: np.ndarray, angle: float) -> np.ndarray:
    """Rotate a colour image by the angle already applied to the grayscale page."""
    if abs(angle) < 0.3:
        return image
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Rotate the page so text lines are horizontal. Returns (image, degrees)."""
    angle = estimate_skew_angle(gray)
    if abs(angle) < 0.3:  # straight enough that resampling would only soften it
        return gray, 0.0
    angle = max(min(angle, 15.0), -15.0)
    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    rotated = cv2.warpAffine(
        gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated, round(float(angle), 3)


def denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, 9, 7, 21)


def enhance_contrast(gray: np.ndarray) -> tuple[np.ndarray, float, float]:
    clahe = cv2.createCLAHE(clipLimit=2.6, tileGridSize=(8, 8))
    out = clahe.apply(gray)
    return out, float(gray.std()), float(out.std())


def binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )


def upscale_for_ocr(gray: np.ndarray, target_height: int = 1600) -> np.ndarray:
    h, w = gray.shape[:2]
    if h >= target_height:
        return gray
    scale = target_height / float(h)
    return cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)


# ---------------------------------------------------------------------------
# Stage 2 - layout analysis (table / text / handwriting / stamp / map)
# ---------------------------------------------------------------------------
def _morph_kernel(size: int, horizontal: bool) -> np.ndarray:
    if horizontal:
        return cv2.getStructuringElement(cv2.MORPH_RECT, (size, 1))
    return cv2.getStructuringElement(cv2.MORPH_RECT, (1, size))


def _boxes_from_mask(mask: np.ndarray, min_area: int = 900) -> list[dict]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < min_area:
            continue
        boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h)})
    boxes.sort(key=lambda b: (b["y"], b["x"]))
    return boxes


def _stroke_width_stats(binary_inv: np.ndarray) -> tuple[float, float]:
    """Mean stroke thickness and its spread - handwriting is thin and irregular."""
    dist = cv2.distanceTransform(binary_inv, cv2.DIST_L2, 3)
    values = dist[dist > 0]
    if values.size == 0:
        return 0.0, 0.0
    return float(values.mean()), float(values.std())


def detect_layout(binary: np.ndarray, gray: np.ndarray, color: np.ndarray | None = None) -> dict:
    """Heuristic region labelling.

    Returns labelled boxes for: table, printed_text, handwritten_text, stamp,
    map. This is the hook point where the pilot tier plugs in a trained detector.

    `color` must be the colour version of `gray` at the same scale - the stamp
    detector works on saturation, which is identically zero on a grayscale image
    converted back to BGR.
    """
    h, w = binary.shape[:2]
    inverted = cv2.bitwise_not(binary)

    # --- table rules: long horizontal + vertical runs -----------------------
    horiz = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, _morph_kernel(max(40, w // 22), True))
    vert = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, _morph_kernel(max(40, h // 22), False))
    rules = cv2.add(horiz, vert)
    rule_mask = cv2.dilate(rules, np.ones((7, 7), np.uint8), iterations=2)
    table_boxes = _boxes_from_mask(rule_mask, min_area=int(w * h * 0.02))

    # --- text blocks --------------------------------------------------------
    text_mask = cv2.dilate(inverted, np.ones((3, 25), np.uint8), iterations=2)
    text_boxes_raw = _boxes_from_mask(text_mask, min_area=400)
    text_boxes = [
        b for b in text_boxes_raw
        if not any(_contains(t, b, 0.75) for t in table_boxes)
    ]

    # --- handwriting vs printed, per text block ----------------------------
    printed_boxes: list[dict] = []
    hand_boxes: list[dict] = []
    for box in text_boxes:
        crop = inverted[box["y"]: box["y"] + box["h"], box["x"]: box["x"] + box["w"]]
        mean_stroke, stroke_std = _stroke_width_stats(crop)
        aspect = box["h"] / max(box["w"], 1)
        # thin + irregular strokes, tall narrow blocks -> handwriting
        is_hand = (mean_stroke < 2.1 and stroke_std > 0.55) or (aspect > 0.30 and mean_stroke < 2.6)
        (hand_boxes if is_hand else printed_boxes).append(box)

    # --- stamp / signature: saturated red or blue blobs ---------------------
    # Requires genuine colour: a grayscale page converted back to BGR has zero
    # saturation everywhere, so the mask would never match.
    stamp_source = color if (color is not None and color.ndim == 3) else gray
    stamp_boxes = _detect_stamp_regions(stamp_source)

    # --- map / cadastral region: dense straight-line network, little text ---
    map_boxes = _detect_map_regions(inverted, gray)

    return {
        "image_size": {"w": int(w), "h": int(h)},
        "regions": [
            *[{"label": "table", **b} for b in table_boxes],
            *[{"label": "printed_text", **b} for b in printed_boxes],
            *[{"label": "handwritten_text", **b} for b in hand_boxes],
            *[{"label": "stamp", **b} for b in stamp_boxes],
            *[{"label": "map", **b} for b in map_boxes],
        ],
        "counts": {
            "table": len(table_boxes),
            "printed_text": len(printed_boxes),
            "handwritten_text": len(hand_boxes),
            "stamp": len(stamp_boxes),
            "map": len(map_boxes),
        },
    }


def _contains(outer: dict, inner: dict, threshold: float = 0.7) -> bool:
    ix = max(outer["x"], inner["x"])
    iy = max(outer["y"], inner["y"])
    ix2 = min(outer["x"] + outer["w"], inner["x"] + inner["w"])
    iy2 = min(outer["y"] + outer["h"], inner["y"] + inner["h"])
    inter = max(0, ix2 - ix) * max(0, iy2 - iy)
    area = inner["w"] * inner["h"]
    return area > 0 and (inter / area) >= threshold


def _detect_stamp_regions(gray: np.ndarray) -> list[dict]:
    if gray.ndim != 3:
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        bgr = gray
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower_red1, upper_red1 = np.array([0, 70, 60]), np.array([12, 255, 255])
    lower_red2, upper_red2 = np.array([168, 70, 60]), np.array([180, 255, 255])
    lower_blue, upper_blue = np.array([95, 70, 60]), np.array([130, 255, 255])
    mask = cv2.inRange(hsv, lower_red1, upper_red1) | cv2.inRange(
        hsv, lower_red2, upper_red2
    ) | cv2.inRange(hsv, lower_blue, upper_blue)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8), iterations=2)
    return _boxes_from_mask(mask, min_area=700)


def _detect_map_regions(inverted: np.ndarray, gray: np.ndarray) -> list[dict]:
    """A cadastral map region has many long thin lines and low text density."""
    edges = cv2.Canny(cv2.GaussianBlur(gray if gray.ndim == 2 else cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY), (3, 3), 0), 40, 130)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=90, minLineLength=80, maxLineGap=12)
    if lines is None or len(lines) < 25:
        return []
    h, w = inverted.shape[:2]
    canvas = np.zeros((h, w), np.uint8)
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        cv2.line(canvas, (int(x1), int(y1)), (int(x2), int(y2)), 255, 2)
    canvas = cv2.dilate(canvas, np.ones((9, 9), np.uint8), iterations=2)
    text_mask = cv2.dilate(inverted, np.ones((3, 15), np.uint8), iterations=1)
    text_density = cv2.blur((text_mask > 0).astype(np.float32), (51, 51))
    boxes = _boxes_from_mask(canvas, min_area=int(w * h * 0.03))
    kept = []
    for box in boxes:
        patch = text_density[box["y"]: box["y"] + box["h"], box["x"]: box["x"] + box["w"]]
        if patch.size and float(patch.mean()) < 0.42:
            kept.append(box)
    return kept


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def preprocess_image(image_path: str | Path, tag: str | None = None) -> dict:
    """Run the full FR-2 pipeline on a document image.

    Returns the enhanced image path, an annotated preview path, the layout map
    and a dict of measured preprocessing statistics.

    `tag` makes the output filenames unique per run (e.g. one UUID per
    processing). This matters because the media endpoints serve these files
    with FileResponse: Starlette declares Content-Length from a stat and then
    streams the bytes, so rewriting the same path mid-download (reprocess
    while a browser holds the preview open) corrupts the response with
    "Response content longer than Content-Length". Unique names make every
    served file immutable; the caller deletes the previous run's files.
    """
    image_path = Path(image_path)
    raw = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if raw is None:
        raise ValueError(f"OpenCV could not read image: {image_path}")

    gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
    original_std = float(gray.std())

    deskewed, angle = deskew(gray)
    cleaned = denoise(deskewed)
    contrasted, std_before, std_after = enhance_contrast(cleaned)
    binary = binarize(contrasted)
    ocr_ready = upscale_for_ocr(contrasted)
    ocr_binary = binarize(ocr_ready)

    # Colour twin of the OCR image: same deskew and same scale, so region boxes
    # line up. Needed because stamp detection is saturation-based.
    color_deskewed = _apply_same_rotation(raw, angle)
    color_ready = cv2.resize(
        color_deskewed, (ocr_ready.shape[1], ocr_ready.shape[0]), interpolation=cv2.INTER_CUBIC
    )

    layout = detect_layout(ocr_binary, ocr_ready, color=color_ready)

    stem = image_path.stem
    suffix = f"_{tag}" if tag else ""
    enhanced_path = PROCESSED_DIR / f"{stem}_enhanced{suffix}.png"
    preview_path = PROCESSED_DIR / f"{stem}_annotated{suffix}.png"

    cv2.imwrite(str(enhanced_path), ocr_ready)

    annotated = cv2.cvtColor(ocr_ready, cv2.COLOR_GRAY2BGR)
    colours = {
        "table": (0, 165, 255),
        "printed_text": (60, 180, 60),
        "handwritten_text": (200, 60, 200),
        "stamp": (0, 0, 230),
        "map": (230, 120, 0),
    }
    for region in layout["regions"]:
        colour = colours.get(region["label"], (255, 255, 255))
        cv2.rectangle(
            annotated,
            (region["x"], region["y"]),
            (region["x"] + region["w"], region["y"] + region["h"]),
            colour,
            2,
        )
        cv2.putText(
            annotated,
            region["label"],
            (region["x"], max(14, region["y"] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            colour,
            1,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(preview_path), annotated)

    stats = {
        "deskew_angle_deg": angle,
        "contrast_std_before": round(original_std, 2),
        "contrast_std_after_clahe": round(std_after, 2),
        "contrast_gain_pct": round(
            (std_after - std_before) / std_before * 100 if std_before else 0.0, 2
        ),
        "denoised": True,
        "binarised": "adaptive_gaussian_31_15",
        "ocr_ready_height_px": int(ocr_ready.shape[0]),
        "regions": layout["counts"],
        "region_total": sum(layout["counts"].values()),
        "scale_factor_applied": round(ocr_ready.shape[0] / float(raw.shape[0]), 3),
        "skew_corrected": abs(angle) >= 0.3,
    }
    return {
        "enhanced_path": str(enhanced_path),
        "preview_path": str(preview_path),
        "layout": layout,
        "stats": stats,
    }


def annotate_regions(image_path: str | Path, regions: list[dict]) -> str:
    """Re-draw region boxes on demand (used when the reviewer wants evidence)."""
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    for region in regions:
        cv2.rectangle(
            image,
            (region["x"], region["y"]),
            (region["x"] + region["w"], region["y"] + region["h"]),
            (0, 140, 255),
            2,
        )
    out = PROCESSED_DIR / f"{Path(image_path).stem}_regions.png"
    cv2.imwrite(str(out), image)
    return str(out)


def estimate_skew_only(image_path: str | Path) -> float:
    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return 0.0
    return round(estimate_skew_angle(gray), 2)


def _round(x: float, n: int = 2) -> float:
    return round(x, n) if math.isfinite(x) else 0.0
