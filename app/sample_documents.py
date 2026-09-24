"""
Generates the prototype demo dataset described in SRS section 10.

These are *real rendered images*, not text files: PIL draws ruled register pages
(printed and handwritten styles, Latin and Devanagari scripts), so Tesseract has
to genuinely OCR them. One document is deliberately degraded (skew + noise +
JPEG artefacts) so the CV preprocessing stage has measurable work to do.

Scenarios encoded in the set (see SRS 10):
  DOC 1  clean printed RoR                -> clean pass, GIS match
  DOC 2  degraded print, bad reg. ref     -> BR-4 chronological, BR-10 format
  DOC 3  handwritten Hindi register       -> HTR path + BR-8 owner fuzzy match
  DOC 4  conflicting owner, same khasra   -> BR-3 duplicate (critical)
  DOC 5  area disagreeing with polygon    -> BR-9 spatial mismatch
  DOC 6  multi-plot page, village typo    -> BR-1 mandatory, BR-2 sub-plot sum,
                                             BR-4 unparseable date, BR-7 master
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import DATA_DIR

SAMPLE_DIR = DATA_DIR / "samples"
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\ARIALUNI.TTF",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\ARIALUNI.TTF",
]
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_DEVANAGARI_CANDIDATES = [
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\ARIALUNI.TTF",
    r"C:\Windows\Fonts\mangal.ttf",
]
FONT_ORIYA_CANDIDATES = [
    "/usr/share/fonts/truetype/lohit-oriya/Lohit-Odia.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\ARIALUNI.TTF",
    r"C:\Windows\Fonts\Kalinga.ttf",
]
# Backwards-compatible single-path aliases (first existing entry, may be empty).
FONT_DEVANAGARI = next((p for p in FONT_DEVANAGARI_CANDIDATES if Path(p).exists()), "")
FONT_ORIYA = next((p for p in FONT_ORIYA_CANDIDATES if Path(p).exists()), "")


def _first_existing(paths: list[str]) -> str | None:
    for path in paths:
        if Path(path).exists():
            return path
    return None


REGULAR = _first_existing(FONT_CANDIDATES)
BOLD = _first_existing(FONT_BOLD_CANDIDATES) or REGULAR


def font(size: int, *, bold: bool = False, script: str = "latin") -> ImageFont.FreeTypeFont:
    if script == "devanagari":
        dev = _first_existing(FONT_DEVANAGARI_CANDIDATES)
        if dev:
            return ImageFont.truetype(dev, size)
    if script == "oriya":
        oriya = _first_existing(FONT_ORIYA_CANDIDATES)
        if oriya:
            return ImageFont.truetype(oriya, size)
    base = (BOLD if bold else REGULAR) if (BOLD if bold else REGULAR) else None
    if base:
        return ImageFont.truetype(base, size)
    # Last resort: PIL bitmap default (always available, limited glyphs).
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Rendering primitives
# ---------------------------------------------------------------------------
@dataclass
class Line:
    label: str
    value: str
    style: str = "printed"       # printed | handwritten
    size: int = 26


def _draw_printed(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fnt) -> None:
    draw.text((x, y), text, font=fnt, fill=(25, 25, 30))


def _draw_handwritten(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    fnt,
    rng: random.Random,
    ink=(35, 45, 130),
) -> None:
    """Draw a value in a handwriting style that stays legible to OCR.

    Jitter is applied per *word* (baseline shift, slant, slight size variance)
    rather than per glyph: per-glyph jitter destroys Devanagari conjuncts and
    breaks Latin words into unreadable fragments, which would make the demo
    document unreadable for reasons that have nothing to do with handwriting.
    """
    cursor = x
    for word in text.split(" "):
        if not word:
            cursor += fnt.getlength(" ")
            continue
        word_font = fnt.font_variant(size=fnt.size + rng.randint(-1, 1))
        layer = Image.new("RGBA", (int(word_font.getlength(word)) + 16, fnt.size + 24), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.text((4, 8), word, font=word_font, fill=ink + (255,))
        layer = layer.rotate(rng.uniform(-2.2, 2.2), resample=Image.BICUBIC, expand=True)
        shear = rng.uniform(-0.10, 0.10)
        layer = layer.transform(
            (layer.width + 8, layer.height), Image.AFFINE, (1, shear, 0, 0, 1, 0),
            resample=Image.BILINEAR,
        )
        draw._image.paste(layer, (int(cursor), int(y + rng.uniform(-3.0, 3.0)) - 8), layer)
        cursor += word_font.getlength(word) + fnt.getlength(" ") + rng.uniform(-1.0, 1.6)


def _draw_stamp(image: Image.Image, centre: tuple[int, int], text: str) -> None:
    """Red circular revenue stamp - exercises the CV stamp detector."""
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = centre
    red = (190, 30, 40, 205)
    draw.ellipse([cx - 78, cy - 78, cx + 78, cy + 78], outline=red, width=6)
    draw.ellipse([cx - 64, cy - 64, cx + 64, cy + 64], outline=red, width=2)
    stamp_font = font(17, bold=True)
    for offset, line in enumerate(text.split("\n")):
        width = draw.textlength(line, font=stamp_font)
        draw.text((cx - width / 2, cy - 26 + offset * 22), line, font=stamp_font, fill=red)
    image.alpha_composite(overlay)


def render_document(
    *,
    path: Path,
    header: list[str],
    lines: list[Line],
    script: str = "latin",
    width: int = 1400,
    line_height: int = 58,
    top_margin: int = 150,
    stamp_text: str | None = None,
    table: bool = True,
    footer: list[str] | None = None,
    skew_degrees: float = 0.0,
    noise: int = 0,
    jpeg_quality: int | None = None,
    seed: int = 7,
) -> Path:
    rng = random.Random(seed)
    height = top_margin + line_height * (len(lines) + 3) + 220
    # RGBA canvas so the stamp can be alpha-composited; converted to RGB on save
    image = Image.new("RGBA", (width, height), (247, 244, 235, 255))
    draw = ImageDraw.Draw(image)

    # paper texture + border
    for _ in range(int(width * height / 900)):
        px, py = rng.randrange(width), rng.randrange(height)
        shade = rng.randint(228, 245)
        draw.point((px, py), fill=(shade, shade - 3, shade - 8))
    draw.rectangle([26, 26, width - 27, height - 27], outline=(90, 80, 70), width=3)
    draw.rectangle([34, 34, width - 35, height - 35], outline=(150, 140, 128), width=1)

    # header
    title_font = font(34, bold=True, script=script)
    sub_font = font(22, bold=True, script=script)
    for index, line in enumerate(header):
        used_font = title_font if index == 0 else sub_font
        text_width = draw.textlength(line, font=used_font)
        draw.text(((width - text_width) / 2, 52 + index * (44 if index == 0 else 32)),
                  line, font=used_font, fill=(30, 30, 35))

    y = top_margin
    label_font = font(23, bold=True, script=script)
    value_font = font(26, script=script)
    hand_font = font(29, script=script)
    left_label, left_value = 70, 430
    right_edge = width - 70

    for line in lines:
        if table:
            draw.line([(left_label - 12, y + 44), (right_edge, y + 44)], fill=(120, 112, 100), width=2)
            draw.line([(left_label - 12, y - 6), (left_label - 12, y + 44)], fill=(120, 112, 100), width=2)
            draw.line([(left_value - 14, y - 6), (left_value - 14, y + 44)], fill=(120, 112, 100), width=2)
            draw.line([(right_edge, y - 6), (right_edge, y + 44)], fill=(120, 112, 100), width=2)

        _draw_printed(draw, left_label, y + 6, line.label, label_font)
        if line.style == "handwritten":
            _draw_handwritten(draw, left_value, y + 4, line.value, hand_font, rng)
        else:
            _draw_printed(draw, left_value, y + 4, line.value, value_font)
        y += line_height

    if table and lines:
        draw.line([(left_label - 12, top_margin - 6), (right_edge, top_margin - 6)],
                  fill=(120, 112, 100), width=2)

    if footer:
        foot_font = font(20, script=script)
        foot_y = y + 30
        for line in footer:
            draw.text((left_label, foot_y), line, font=foot_font, fill=(60, 58, 55))
            foot_y += 30
        draw.line([(width - 430, y + 120), (width - 120, y + 120)], fill=(40, 40, 40), width=2)
        draw.text((width - 420, y + 130), "Signature of Revenue Inspector",
                  font=font(18, script=script), fill=(60, 58, 55))

    if stamp_text:
        _draw_stamp(image, (width - 250, y - 40), stamp_text)

    if skew_degrees:
        image = image.rotate(skew_degrees, resample=Image.BICUBIC, expand=False,
                             fillcolor=(247, 244, 235))
    if noise:
        from PIL import ImageOps

        noise_layer = Image.effect_noise(image.size, noise).convert("L")
        image = Image.blend(image.convert("L"), noise_layer, 0.10).convert("RGB")
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.GaussianBlur(0.4))

    rgb = image.convert("RGB")
    if jpeg_quality:
        # Save, re-open and re-save so the compression artefacts are baked into
        # the file the pipeline reads - the point of the degraded sample.
        rgb.save(path, "JPEG", quality=jpeg_quality)
        rgb = Image.open(path).convert("RGB")
        rgb.save(path, "JPEG", quality=jpeg_quality)
    else:
        rgb.save(path, "PNG")
    return path


# ---------------------------------------------------------------------------
# The six demo documents
# ---------------------------------------------------------------------------
def document_specs() -> list[dict]:
    return [
        {
            "key": "ror_clean",
            "filename": "sample_01_ror_clean.png",
            "language": "eng",
            "doc_type": "ror",
            "notes": "Clean printed Record of Rights - happy path",
            "header": ["GOVERNMENT OF ODISHA", "RECORD OF RIGHTS (RoR)",
                       "Revenue Department - Khordha District"],
            "lines": [
                Line("District", "Khordha"),
                Line("Tehsil / Block", "Khordha Sadar"),
                Line("Village / Mouza", "Balarampur"),
                Line("Khata Number", "204"),
                Line("Khasra Number", "118/2"),
                Line("Survey Number", "118"),
                Line("Plot Number", "2"),
                Line("Owner Name", "Prafulla Kumar Sahoo"),
                Line("Father Name", "Basanta Kumar Sahoo"),
                Line("Address", "At Balarampur, PO Sundarpada"),
                Line("Total Area", "1.14 acre"),
                Line("Land Classification", "Irrigated land"),
                Line("Mutation Number", "MUT/204/2019"),
                Line("Mutation Date", "12/03/2019"),
                Line("Previous Owner", "Basanta Kumar Sahoo"),
                Line("New Owner", "Prafulla Kumar Sahoo"),
                Line("Registration Number", "1234 of 2019"),
            ],
            "footer": ["Certified true copy - prepared under the Odisha Survey and Settlement Act."],
            "stamp_text": "REVENUE\nDEPARTMENT\nODISHA",
        },
        {
            "key": "ror_degraded",
            "filename": "sample_02_ror_degraded.jpg",
            "language": "eng",
            "doc_type": "ror",
            "notes": "Skewed, noisy scan with an invalid registration reference and a "
                     "mutation date that precedes registration (BR-4, BR-10)",
            "header": ["GOVERNMENT OF ODISHA", "RECORD OF RIGHTS (RoR)",
                       "Revenue Department - Khordha District"],
            "lines": [
                Line("District", "Khordha"),
                Line("Tehsil / Block", "Khordha Sadar"),
                Line("Village / Mouza", "Balarampur"),
                Line("Khata Number", "204"),
                Line("Khasra Number", "118/4"),
                Line("Survey Number", "118"),
                Line("Plot Number", "4"),
                Line("Owner Name", "Prafulla Kumar Sahoo"),
                Line("Father Name", "Basanta Kumar Sahoo"),
                Line("Total Area", "0.75 acre"),
                Line("Land Classification", "Irrigated land"),
                Line("Mutation Number", "MUT/204/2020"),
                Line("Mutation Date", "04/01/2020"),
                Line("Previous Owner", "Basanta Kumar Sahoo"),
                Line("New Owner", "Prafulla Kumar Sahoo"),
                Line("Registration Number", "REG-X-99-13"),
            ],
            "footer": ["Photocopy of register page - legibility reduced."],
            "skew_degrees": 2.4,
            "noise": 55,
            "jpeg_quality": 42,
        },
        {
            "key": "register_handwritten_hi",
            "filename": "sample_03_register_handwritten_hindi.png",
            "language": "hin",
            "doc_type": "register_page",
            "notes": "Handwritten Devanagari register page - exercises the HTR path; the "
                     "owner name is a spelling variant of DOC 1 (BR-8)",
            "header": ["ओडिशा सरकार", "जमाबंदी रजिस्टर (हस्तलिखित)",
                       "राजस्व विभाग - खुर्दा जिला"],
            "lines": [
                Line("जिला", "खुर्दा"),
                Line("तहसील", "खुर्दा सदर"),
                Line("ग्राम", "बलरामपुर"),
                Line("खाता संख्या", "204"),
                Line("खसरा संख्या", "88/1"),
                Line("दाग", "1"),
                Line("खातेदार का नाम", "प्रफुल्ल कुमार साहू"),
                Line("पिता का नाम", "बसंत कुमार साहू"),
                Line("क्षेत्रफल", "0.60 acre"),
                Line("जमीन का प्रकार", "Plantation land"),
                Line("नामांतरण संख्या", "MUT/204/2018"),
            ],
            "footer": ["हस्तलिखित पृष्ठ - राजस्व निरीक्षक द्वारा लिखित।"],
            "table": False,
            "stamp_text": "तहसीलदार\nखुर्दा",
        },
        {
            "key": "conflicting_owner",
            "filename": "sample_04_conflicting_owner.png",
            "language": "eng",
            "doc_type": "mutation_record",
            "notes": "Same khasra 227/1 held by a different owner (BR-3 critical duplicate) "
                     "and the area disagrees with the cadastral polygon (BR-9)",
            "header": ["GOVERNMENT OF ODISHA", "MUTATION REGISTER ENTRY",
                       "Revenue Department - Khordha District"],
            "lines": [
                Line("District", "Khordha"),
                Line("Tehsil / Block", "Nirakarpur"),
                Line("Village / Mouza", "Golabai"),
                Line("Khata Number", "118"),
                Line("Khasra Number", "227/1"),
                Line("Survey Number", "227"),
                Line("Plot Number", "1"),
                Line("Owner Name", "Sunita Pradhan"),
                Line("Father Name", "Bhagirathi Pradhan"),
                Line("Total Area", "1.00 acre"),
                Line("Land Classification", "Unirrigated land"),
                Line("Mutation Number", "MUT/118/2021"),
                Line("Mutation Date", "21/07/2021"),
                Line("Previous Owner", "Gopinath Pradhan"),
                Line("New Owner", "Sunita Pradhan"),
                Line("Registration Number", "876 of 2021"),
            ],
            "footer": ["Entered from the mutation case file."],
            "stamp_text": "MUTATION\nAPPROVED\nTAHSILDAR",
        },
        {
            "key": "area_mismatch",
            "filename": "sample_05_area_mismatch.png",
            "language": "eng",
            "doc_type": "ror",
            "notes": "Duplicate khasra 118/2 held by a different owner (BR-3 critical) with an "
                     "area that also disagrees with Bhulekh",
            "header": ["GOVERNMENT OF ODISHA", "RECORD OF RIGHTS (RoR)",
                       "Revenue Department - Khordha District"],
            "lines": [
                Line("District", "Khordha"),
                Line("Tehsil / Block", "Khordha Sadar"),
                Line("Village / Mouza", "Balarampur"),
                Line("Khata Number", "209"),
                Line("Khasra Number", "118/2"),
                Line("Survey Number", "118"),
                Line("Plot Number", "2"),
                Line("Owner Name", "Bhagaban Nayak"),
                Line("Father Name", "Hari Nayak"),
                Line("Total Area", "1.42 acre"),
                Line("Land Classification", "Irrigated land"),
                Line("Mutation Number", "MUT/209/2022"),
                Line("Mutation Date", "09/09/2022"),
                Line("Previous Owner", "Hari Nayak"),
                Line("New Owner", "Bhagaban Nayak"),
                Line("Registration Number", "3311 of 2022"),
            ],
            "footer": ["Second claim on the same plot - digitised for conflict review."],
        },
        {
            "key": "multi_plot_page",
            "filename": "sample_06_multi_plot_page.png",
            "language": "eng",
            "doc_type": "register_page",
            "notes": "Multi-plot register page: sub-plot areas do not sum to the parent "
                     "(BR-2), village name is not in the master (BR-7), classification is "
                     "missing (BR-1) and the mutation date is unparseable (BR-4)",
            "header": ["GOVERNMENT OF ODISHA", "PLOT-WISE REGISTER EXTRACT",
                       "Revenue Department - Khordha District"],
            "lines": [
                Line("District", "Khordha"),
                Line("Tehsil / Block", "Jankia"),
                Line("Village / Mouza", "Janakpur"),
                Line("Khata Number", "77"),
                Line("Khasra Number", "340"),
                Line("Survey Number", "340"),
                Line("Owner Name", "Ramesh Chandra Behera"),
                Line("Father Name", "Dibakar Behera"),
                Line("Total Area", "3.00 acre"),
                Line("Sub Plot Khasra", "340/1  Area 1.48 acre"),
                Line("Sub Plot Khasra", "340/2  Area 1.24 acre"),
                Line("Mutation Number", "MUT/77/2021"),
                Line("Mutation Date", "32/13/2021"),
                Line("Registration Number", "4455 of 2020"),
            ],
            "footer": ["The land-use column was illegible on the source page."],
        },
    ]


def generate_samples() -> list[dict]:
    """Render every demo document and return its metadata."""
    generated = []
    for spec in document_specs():
        path = SAMPLE_DIR / spec["filename"]
        render_document(
            path=path,
            header=spec["header"],
            lines=spec["lines"],
            script="devanagari" if spec["language"] == "hin" else "latin",
            stamp_text=spec.get("stamp_text"),
            table=spec.get("table", True),
            footer=spec.get("footer"),
            skew_degrees=spec.get("skew_degrees", 0.0),
            noise=spec.get("noise", 0),
            jpeg_quality=spec.get("jpeg_quality"),
            seed=11 if spec["key"] == "register_handwritten_hi" else 7,
        )
        generated.append({
            "key": spec["key"],
            "filename": spec["filename"],
            "path": str(path),
            "language": spec["language"],
            "doc_type": spec["doc_type"],
            "notes": spec["notes"],
            "size_bytes": path.stat().st_size,
        })
    return generated


if __name__ == "__main__":  # pragma: no cover
    for item in generate_samples():
        print(f"{item['filename']:48s} {item['size_bytes']:>9,} bytes  {item['notes'][:60]}")
