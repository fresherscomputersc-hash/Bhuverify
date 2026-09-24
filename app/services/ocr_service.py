"""
OCR / Handwritten Text Recognition service (SRS FR-3).

Prototype tier uses Tesseract 5 with the eng / hin / ori language packs
actually installed on this machine. Two recognition paths are exposed:

  * `run_ocr`  - printed text, PSM 6 (uniform block) / PSM 4 (column layout)
  * `run_htr`  - handwritten blocks, PSM 7/11 on a cleaned, upscaled crop

Both return word-level confidence and bounding boxes, which is what FR-5
(confidence scoring) and FR-4 (evidence references) consume.

Pilot tier: replace `run_htr` with a transformer HTR model (e.g. TrOCR /
IndicHTR). The returned `WordToken` contract already supports that swap.
"""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pytesseract

from app.config import settings


def _ensure_tesseract_on_path() -> str | None:
    """Locate the tesseract binary on Linux, Docker and Windows.

    `shutil.which` misses the default Windows installer location
    (``C:\\Program Files\\Tesseract-OCR``) when PATH was not updated, and
    the User-level TESSDATA_PREFIX set by the installer script is invisible
    to already-running processes. Point pytesseract at the binary explicitly
    and export a user-writable tessdata dir if the system one is missing
    packs. Returns the binary path or None.
    """
    import os

    found = shutil.which("tesseract")
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Tesseract-OCR\tesseract.exe"),
    ]
    if not found:
        for cand in candidates:
            if cand and Path(cand).exists():
                pytesseract.pytesseract.tesseract_cmd = cand
                # make shutil.which work for child processes in this session
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(Path(cand).parent)
                found = cand
                break
    # Prefer a user-writable tessdata dir that has hin/ori when the system
    # tessdata only ships eng/osd (default Windows installer).
    # NOTE: Tesseract 5.4 Windows expects TESSDATA_PREFIX to point at the
    # tessdata directory itself (parent gives "tessdata/eng" entries that
    # pytesseract cannot parse -> empty language list).
    local_prefix = Path(os.environ.get("LOCALAPPDATA", "") or Path.home()) / "Tesseract"
    local_tessdata = local_prefix / "tessdata"
    if local_tessdata.is_dir():
        have = {p.stem for p in local_tessdata.glob("*.traineddata")}
        if {"hin", "ori"} <= have and "TESSDATA_PREFIX" not in os.environ:
            os.environ["TESSDATA_PREFIX"] = str(local_tessdata)
    return found

_TESSERACT_CMD = _ensure_tesseract_on_path()
TESSERACT_AVAILABLE = _TESSERACT_CMD is not None
try:
    TESSERACT_VERSION = (
        str(pytesseract.get_tesseract_version()) if TESSERACT_AVAILABLE else "unavailable"
    )
except Exception:
    TESSERACT_VERSION = "unavailable"
    TESSERACT_AVAILABLE = False
try:
    INSTALLED_LANGS: tuple[str, ...] = (
        tuple(pytesseract.get_languages(config="")) if TESSERACT_AVAILABLE else ()
    )
except Exception:
    INSTALLED_LANGS = ()

SCRIPT_BY_LANG = {"eng": "Latin", "hin": "Devanagari", "ori": "Odia", "ben": "Bengali"}

# Land records in Odisha routinely mix scripts on the same page (Odia body
# text, English headers/numerals, occasional Hindi) so a single-language
# pass silently drops whatever script wasn't selected. Unicode block ranges
# let us tag each *word* with its actual script after a combined-language
# OCR pass, regardless of which language "won" the page.
_SCRIPT_RANGES = (
    ("Devanagari", re.compile(r"[\u0900-\u097F]")),
    ("Odia", re.compile(r"[\u0B00-\u0B7F]")),
    ("Bengali", re.compile(r"[\u0980-\u09FF]")),
    ("Latin", re.compile(r"[A-Za-z]")),
)


def detect_script(text: str) -> str:
    """Best-effort script tag for a single OCR'd word, by Unicode block."""
    for name, pattern in _SCRIPT_RANGES:
        if pattern.search(text):
            return name
    return "Latin"


@dataclass
class WordToken:
    text: str
    confidence: float
    bbox: dict
    line_no: int = 0
    word_no: int = 0
    script: str = "Latin"
    page: int = 1  # 1-based PDF/image page the token was read from


@dataclass
class OcrResult:
    text: str
    words: list[WordToken] = field(default_factory=list)
    language: str = "eng"
    engine: str = "tesseract"
    mode: str = "printed"          # printed | handwritten
    psm: int = 6
    mean_confidence: float = 0.0
    min_confidence: float = 0.0
    word_count: int = 0
    low_confidence_word_count: int = 0
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "engine": self.engine,
            "mode": self.mode,
            "psm": self.psm,
            "mean_confidence": round(self.mean_confidence, 2),
            "min_confidence": round(self.min_confidence, 2),
            "word_count": self.word_count,
            "low_confidence_word_count": self.low_confidence_word_count,
            "latency_ms": self.latency_ms,
            "warnings": self.warnings,
            "words": [
                {
                    "text": w.text,
                    "confidence": round(w.confidence, 2),
                    "bbox": w.bbox,
                    "line_no": w.line_no,
                    "script": w.script,
                }
                for w in self.words
            ],
        }


def resolve_language(hint: str | None = None) -> str:
    """Build a Tesseract language string from genuinely installed packs.

    `hint` may be a single pack ("hin") or a Tesseract-style combo
    ("eng+hin+ori"). Land-record pages routinely mix scripts on one page
    (Odia body text with English headers/numerals, occasional Hindi), so
    the prototype's default is the full eng+hin+ori combo rather than a
    single guessed language - Tesseract reads all three scripts in one
    pass and each word is tagged by its actual script afterwards (see
    `detect_script`). An explicit single-language hint is still honoured
    for callers that know the page is monolingual.
    """
    candidates: list[str] = []
    if hint:
        candidates.append(hint)
    candidates.append(settings.default_ocr_language)
    candidates.append("+".join(settings.ocr_languages))
    candidates.extend(settings.ocr_languages)

    for candidate in candidates:
        if not candidate:
            continue
        parts = [p.strip().lower() for p in candidate.split("+") if p.strip()]
        if not parts:
            continue
        if not INSTALLED_LANGS:
            return "+".join(parts)
        available = [p for p in parts if p in INSTALLED_LANGS]
        if available:
            # Keep only the packs that are actually installed rather than
            # rejecting the whole combo for one missing/misspelled pack.
            return "+".join(dict.fromkeys(available))
    return settings.default_ocr_language


def _tesseract_config(psm: int, lang: str) -> str:
    config = f"--psm {psm} -c preserve_interword_spaces=1"
    if any(part in ("hin", "ori", "ben") for part in lang.split("+")):
        # Indic scripts: disable dictionary-driven correction, keep ligatures intact
        config += " -c textord_force_make_prop_words=0"
    return config


def _words_from_data(data: dict, default_script: str) -> list[WordToken]:
    words: list[WordToken] = []
    for index, text in enumerate(data.get("text", [])):
        text = (text or "").strip()
        if not text:
            continue
        confidence = float(data["conf"][index])
        if confidence < 0:
            continue
        # With a combined language pass (e.g. eng+hin+ori) a single page
        # mixes scripts, so tag each word by what it actually contains
        # rather than the one script implied by the whole-page language.
        script = detect_script(text) if any(ch.isalpha() for ch in text) else default_script
        words.append(
            WordToken(
                text=text,
                confidence=confidence,
                bbox={
                    "x": int(data["left"][index]),
                    "y": int(data["top"][index]),
                    "w": int(data["width"][index]),
                    "h": int(data["height"][index]),
                },
                line_no=int(data["line_num"][index]),
                word_no=int(data["word_num"][index]),
                script=script,
            )
        )
    return words


def _summarise(result: OcrResult) -> OcrResult:
    result.word_count = len(result.words)
    if result.words:
        confidences = [w.confidence for w in result.words]
        result.mean_confidence = float(np.mean(confidences))
        result.min_confidence = float(np.min(confidences))
        result.low_confidence_word_count = sum(
            1 for c in confidences if c < settings.CONFIDENCE_MEDIUM
        )
    return result


def run_ocr(image_path: str | Path, language: str | None = None, psm: int = 4) -> OcrResult:
    """Full-page printed OCR with word-level confidence.

    PSM 4 ("single column of text of variable sizes") is the default because
    land-record pages are label/value registers, not uniform prose blocks: on
    the demo set PSM 6 drops whole rows (khasra, area) that PSM 4 and 11 read
    correctly. Callers can still override.
    """
    started = time.perf_counter()
    language = resolve_language(language)
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"OCR could not read image: {image_path}")

    result = OcrResult(text="", language=language, mode="printed", psm=psm)
    if not TESSERACT_AVAILABLE:  # pragma: no cover - guarded at startup
        result.warnings.append("tesseract binary not found on PATH")
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    data = pytesseract.image_to_data(
        image, lang=language, config=_tesseract_config(psm, language),
        output_type=pytesseract.Output.DICT,
    )
    result.words = _words_from_data(data, SCRIPT_BY_LANG.get(language, "Latin"))
    result.text = pytesseract.image_to_string(
        image, lang=language, config=_tesseract_config(psm, language)
    ).strip()
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return _summarise(result)


def run_htr(image_path: str | Path, language: str | None = None) -> OcrResult:
    """Handwritten-block recognition on a cleaned, upscaled crop.

    Handwriting is harder than print, so the prototype applies a
    handwriting-tuned preprocessing chain (thicker strokes preserved, no
    aggressive binarisation) and reports the lower confidence band honestly
    rather than pretending the score is comparable to printed text.
    """
    started = time.perf_counter()
    language = resolve_language(language)
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"HTR could not read image: {image_path}")

    h, w = image.shape[:2]
    scale = max(1.0, 1200.0 / float(h))
    if scale > 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    image = cv2.bilateralFilter(image, 7, 45, 45)
    image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    # Keep stroke mass: Otsu on inverted image, then invert back
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    image = cv2.dilate(binary, np.ones((2, 2), np.uint8), iterations=1)

    result = OcrResult(text="", language=language, mode="handwritten", psm=7, engine="tesseract+htr-pipeline")
    if not TESSERACT_AVAILABLE:  # pragma: no cover
        result.warnings.append("tesseract binary not found on PATH")
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    best: OcrResult | None = None
    for psm in (7, 11, 6):
        data = pytesseract.image_to_data(
            image, lang=language, config=_tesseract_config(psm, language),
            output_type=pytesseract.Output.DICT,
        )
        words = _words_from_data(data, SCRIPT_BY_LANG.get(language, "Latin"))
        if not words:
            continue
        candidate = OcrResult(
            text=" ".join(word.text for word in words),
            words=words,
            language=language,
            engine="tesseract+htr-pipeline",
            mode="handwritten",
            psm=psm,
        )
        _summarise(candidate)
        if best is None or candidate.mean_confidence > best.mean_confidence:
            best = candidate

    result = best or result
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    if not result.words:
        result.warnings.append("HTR returned no readable tokens; routed to human review")
    return _summarise(result)


def crop_region(image_path: str | Path, bbox: dict, padding: int = 8) -> np.ndarray:
    """Crop a detected layout region for per-region OCR/HTR."""
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    h, w = image.shape[:2]
    x = max(0, bbox["x"] - padding)
    y = max(0, bbox["y"] - padding)
    x2 = min(w, bbox["x"] + bbox["w"] + padding)
    y2 = min(h, bbox["y"] + bbox["h"] + padding)
    return image[y:y2, x:x2]


def engine_status() -> dict:
    return {
        "tesseract_available": TESSERACT_AVAILABLE,
        "tesseract_version": TESSERACT_VERSION,
        "installed_languages": list(INSTALLED_LANGS),
        "enabled_languages": list(settings.ocr_languages),
        "htr_pipeline": "tesseract + OpenCV handwriting chain (prototype tier)",
        "pilot_upgrade_path": "TrOCR / IndicHTR transformer model",
    }
