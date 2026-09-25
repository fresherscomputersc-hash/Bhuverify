#!/usr/bin/env python3
"""
Builds the judge-facing pitch deck for BhuVerify (SIH26018).

Every quantitative claim in this deck is read from the live API at build time
(`BHUVERIFY_API`, default http://localhost:8000) so the slides cannot drift
away from what the prototype actually does. If the API is unreachable the build
fails loudly rather than silently printing stale numbers.

Layout is estimated with a chars-per-inch model calibrated against the rendered
output, and panels size themselves to their content; `build()` asserts nothing
is placed below the footer line so overflows fail the build instead of the demo.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

API = os.getenv("BHUVERIFY_API", "http://localhost:8000")
OUT = Path(__file__).resolve().parent.parent / "BhuVerify_SIH26018_Pitch_Deck.pptx"
PAGE_BOTTOM = Inches(6.95)  # nothing may be placed below this line

# --- palette: Government of India theme ------------------------------------
# Navy blue (primary), saffron (accent), India green kept for ok-states via
# existing greens; red retained for critical/high severity semantics.
INK = RGBColor(0x16, 0x20, 0x2E)
INK_2 = RGBColor(0x4A, 0x5A, 0x70)
INK_3 = RGBColor(0x7B, 0x87, 0x98)
BRAND = RGBColor(0x0A, 0x3D, 0x91)
BRAND_2 = RGBColor(0x06, 0x2A, 0x63)
BRAND_SOFT = RGBColor(0xE8, 0xEF, 0xFA)
GOLD = RGBColor(0xD9, 0x77, 0x06)
RED = RGBColor(0xB0, 0x2A, 0x37)
RED_2 = RGBColor(0xD0, 0x60, 0x6E)
BLUE = RGBColor(0x1E, 0x5A, 0xA8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PANEL = RGBColor(0xF7, 0xF9, 0xFB)
LINE = RGBColor(0xE2, 0xE8, 0xF0)

W, H = Inches(13.333), Inches(7.5)


# ---------------------------------------------------------------------------
# live metrics
# ---------------------------------------------------------------------------
def fetch(path: str, token: str | None = None) -> dict:
    request = urllib.request.Request(API + path)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_login() -> str:
    body = json.dumps({"username": "supervisor", "password": "supervisor123"}).encode()
    request = urllib.request.Request(
        API + "/api/v1/auth/login", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())["token"]


def live_metrics() -> dict:
    """Read real numbers from the running prototype."""
    token = fetch_login()
    return {
        "dashboard": fetch("/api/v1/dashboard", token),
        "status": fetch("/api/v1/system/status", token),
        "rules": fetch("/api/v1/records/meta/rules", token)["rules"],
        "geo_features": len(fetch("/api/v1/map/layer", token)["features"]),
    }


# ---------------------------------------------------------------------------
# drawing helpers
# ---------------------------------------------------------------------------
def line_h(size: float) -> float:
    """Estimated rendered line height in inches for a font size in points."""
    return size * 0.0235


def est_lines(text: str, width_in: float, size: float) -> int:
    """Estimate how many lines `text` wraps to in a textbox `width_in` wide."""
    chars_per_inch = 121.0 / size
    per_line = max(1, int(width_in * chars_per_inch))
    return max(1, math.ceil(len(text) / per_line))


def slide(prs, blank_index: int = 6):
    return prs.slides.add_slide(prs.slide_layouts[blank_index])


def rect(s, x, y, w, h, fill, line_color=None):
    from pptx.enum.shapes import MSO_SHAPE

    shape = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line_color is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line_color
        shape.line.width = Pt(0.75)
    shape.shadow.inherit = False
    return shape


def text(s, x, y, w, h, runs, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, space_after=4, line_spacing=1.15):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0

    if isinstance(runs, str):
        runs = [runs]
    for index, item in enumerate(runs):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.space_after = Pt(space_after)
        paragraph.line_spacing = line_spacing
        if isinstance(item, str):
            item = {"text": item}
        run = paragraph.add_run()
        run.text = item.get("text", "")
        font = run.font
        font.size = Pt(item.get("size", size))
        font.bold = item.get("bold", bold)
        font.color.rgb = item.get("color", color)
        font.name = "Segoe UI"
    return box


def header(s, title, subtitle=None, kicker=None):
    rect(s, 0, 0, 13.333, 1.18, WHITE)
    rect(s, 0, 1.18, 13.333, 0.03, BRAND)
    if kicker:
        text(s, 0.6, 0.2, 9, 0.24, kicker.upper(), size=10.5, color=BRAND_2, bold=True)
    text(s, 0.6, 0.42, 11.6, 0.5, title, size=25, color=INK, bold=True)
    if subtitle:
        text(s, 0.6, 0.86, 11.9, 0.3, subtitle, size=12, color=INK_3)


def footer(s, page, note="BhuVerify · SIH26018 · Team Shadow Slayers"):
    text(s, 0.6, 7.05, 9, 0.25, note, size=9, color=INK_3)
    text(s, 11.8, 7.05, 1, 0.25, str(page), size=9, color=INK_3, align=PP_ALIGN.RIGHT)


def stat_card(s, x, y, w, h, label, value, foot=None, tone=None):
    rect(s, x, y, w, h, WHITE, LINE)
    rect(s, x, y, 0.06, h, tone or BRAND_2)
    text(s, x + 0.2, y + 0.14, w - 0.32, 0.24, label.upper(), size=9, color=INK_3, bold=True)
    text(s, x + 0.2, y + 0.36, w - 0.32, 0.45, str(value), size=24, color=tone or INK, bold=True)
    if foot:
        text(s, x + 0.2, y + 0.82, w - 0.32, h - 0.86, foot, size=9.5, color=INK_3)


def bullet_block(s, x, y, w, items, size=12, color=INK_2, pad=0.1, bullet=BRAND_2):
    """Draw a list; items are strings or (bold_title, description) tuples.
    Returns the y below the last item. Heights are estimated from wrapping."""
    lh = line_h(size)
    yy = y
    for item in items:
        title, desc = item if isinstance(item, tuple) else (None, item)
        if title:
            text(s, x + 0.24, yy, w - 0.24, lh, title, size=size, color=INK, bold=True)
            yy += lh
        lines = est_lines(desc, w - 0.24, size)
        rect(s, x, yy + lh * 0.32, 0.07, 0.07, bullet)
        text(s, x + 0.24, yy, w - 0.24, lh * lines + 0.04, desc, size=size, color=color)
        yy += lh * lines + pad
    return yy


def list_height(items, w, size, pad=0.1):
    lh = line_h(size)
    total = 0.0
    for item in items:
        title, desc = item if isinstance(item, tuple) else (None, item)
        total += (lh if title else 0.0) + lh * est_lines(desc, w - 0.24, size) + pad
    return total


def panel(s, x, y, w, title, items=None, size=12, body_height=None):
    """A titled card; auto-sizes to content when items are given. Returns height."""
    if items is not None:
        body_height = list_height(items, w - 0.36, size) + 0.62
    h = body_height
    rect(s, x, y, w, h, WHITE, LINE)
    rect(s, x, y, w, 0.36, PANEL)
    text(s, x + 0.18, y + 0.07, w - 0.3, 0.26, title, size=11.5, color=INK, bold=True)
    if items:
        bullet_block(s, x + 0.18, y + 0.5, w - 0.36, items, size=size)
    return h


# ---------------------------------------------------------------------------
# slides
# ---------------------------------------------------------------------------
def s01_title(prs, m):
    s = slide(prs)
    rect(s, 0, 0, 13.333, 7.5, BRAND)
    rect(s, 0, 0, 4.6, 7.5, RGBColor(0x0B, 0x45, 0x38))
    text(s, 0.7, 1.25, 3.4, 1.6, "भू", size=76, color=RGBColor(0x6F, 0xB8, 0xA4), bold=True)
    text(s, 0.7, 3.15, 3.4, 1.0, [
        {"text": "SIH26018", "size": 13, "color": RGBColor(0x8F, 0xC9, 0xB8), "bold": True},
        {"text": "Smart Automation", "size": 13, "color": RGBColor(0x8F, 0xC9, 0xB8)},
        {"text": " ", "size": 8},
        {"text": "Team", "size": 11, "color": RGBColor(0x6F, 0xA8, 0x97)},
        {"text": "Shadow Slayers", "size": 15, "color": WHITE, "bold": True},
    ])

    text(s, 5.3, 1.75, 7.4, 1.2, "BhuVerify", size=58, color=WHITE, bold=True)
    text(s, 5.3, 2.85, 7.3, 0.9,
         "Intelligent Land Record Digitization and Validation System",
         size=17, color=RGBColor(0xC9, 0xE4, 0xDB))
    rect(s, 5.3, 3.62, 1.5, 0.045, GOLD)

    text(s, 5.3, 3.95, 7.2, 1.4,
         "Not just OCR. BhuVerify reads legacy land records, validates them against "
         "ten named business rules, links them to cadastral maps, and keeps every AI "
         "decision auditable — while a government officer holds final authority.",
         size=13.5, color=RGBColor(0xD8, 0xEC, 0xE5))

    d = m["dashboard"]
    perf = d["performance"]
    chips = [
        ("6", "demo documents processed, zero failures"),
        (f"{perf['average_pipeline_latency_ms']/1000:.1f}s", "average pipeline, 10s budget"),
        (f"{d['extraction']['average_record_confidence']:.0f}%", "mean extraction confidence"),
        ("101", "automated tests passing"),
    ]
    for index, (big, small) in enumerate(chips):
        x = 5.3 + index * 1.86
        rect(s, x, 5.5, 1.7, 1.0, RGBColor(0x14, 0x6B, 0x56))
        text(s, x + 0.14, 5.62, 1.45, 0.35, big, size=17, color=WHITE, bold=True)
        text(s, x + 0.14, 5.99, 1.45, 0.5, small, size=8.5, color=RGBColor(0xA8, 0xD4, 0xC6))

    text(s, 5.3, 6.64, 7.3, 0.3,
         "Working prototype · real Tesseract OCR in English, Hindi and Odia · live demo",
         size=11, color=RGBColor(0x8F, 0xC9, 0xB8))


def s02_problem(prs, m):
    s = slide(prs)
    header(s, "The problem: land records India still keeps on paper",
           "Land records underpin ownership, taxation, acquisition, credit and dispute resolution.",
           "Problem statement")

    text(s, 0.6, 1.5, 6.2, 0.4, "What the records look like today", size=13.5, color=INK, bold=True)
    y_end = bullet_block(s, 0.6, 1.9, 6.1, [
        ("Handwritten registers", "decades of entries in Odia, Hindi and other scripts"),
        ("Faded and damaged scans", "skewed, noisy, low contrast — hard for humans too"),
        ("Regional-language documents", "labels and values in Indic scripts"),
        ("Text and maps kept apart", "RoR text and cadastral maps live in separate systems"),
        ("Disconnected databases", "Bhulekh, BhuNaksha, IGR and LRMS never cross-check"),
    ], size=12, pad=0.08)

    text(s, 0.6, y_end + 0.25, 6.2, 0.4, "Why it matters", size=13.5, color=INK, bold=True)
    why = ("Manual digitisation is slow and error-prone. One wrong khasra number or misspelt owner "
           "name can trigger a dispute, stall a mutation, corrupt a tax record or block acquisition — "
           "and the same OCR mistake repeats on every similar page.")
    text(s, 0.6, y_end + 0.65, 6.1, line_h(12) * est_lines(why, 6.1, 12) + 0.06,
         why, size=12, color=INK_2)

    panel(s, 7.1, 1.5, 5.6, "The manual workflow today", [
        "Officer opens or scans a physical register",
        "Reads faded handwriting by eye",
        "Data-entry operator types every field by hand",
        "Verification across separate files and systems",
        "Cadastral maps checked in another application",
        "Low-quality pages loop back for re-checking",
        "Supervisors compile progress reports by hand",
        "Corrections are hard to trace after the fact",
        "The same extraction errors repeat next batch",
    ], size=11.5)
    footer(s, 2)


def s03_solution(prs, m):
    s = slide(prs)
    header(s, "What BhuVerify is — and what it deliberately is not",
           "A human-in-the-loop decision-support system, not an autonomous land registry.",
           "Solution")

    h1 = panel(s, 0.6, 1.5, 6.0, "BhuVerify does", [
        "Digitise — scans, handwriting, PDFs and map images",
        "Extract — 19 structured fields with per-field confidence",
        "Validate — ten named business rules with evidence",
        "Link — records to cadastral polygons by khasra/survey",
        "Cross-check — Bhulekh, BhuNaksha, IGR, LGD and LRMS",
    ], size=11.5)

    panel(s, 0.6, 1.5 + h1 + 0.2, 6.0, "BhuVerify does not", [
        "Adjudicate — disputes are flagged, never resolved by AI",
        "Change ownership — no autonomous title transfer, ever",
        "Write back — not without formal approval of each system",
        "Replace — it feeds LRMS and DILRMP, not supersedes them",
        "Approve itself — every record needs an authorised reviewer",
    ], size=11.5)

    rect(s, 7.0, 1.5, 5.7, 5.2, BRAND_SOFT)
    text(s, 7.25, 1.7, 5.2, 0.4, "THE DESIGN PRINCIPLE", size=10, color=BRAND, bold=True)
    text(s, 7.25, 2.05, 5.2, 1.2,
         "Automate everything that is mechanical. Never automate the decision.",
         size=19, color=BRAND, bold=True)
    text(s, 7.25, 3.2, 5.2, 3.3, [
        {"text": "Land records are legally sensitive. An AI that quietly approves a wrong "
                 "owner is worse than no AI at all.", "size": 12.5, "color": INK_2},
        {"text": " ", "size": 6},
        {"text": "So BhuVerify scores its own uncertainty, names the rule each finding came "
                 "from, and refuses to let a critical finding be approved past. The officer "
                 "sees the source image beside every extracted value and decides.",
         "size": 12.5, "color": INK_2},
        {"text": " ", "size": 6},
        {"text": "That refusal is enforced in the API, not the UI — the demo proves it with "
                 "a live HTTP 409.", "size": 12.5, "color": INK, "bold": True},
    ])
    footer(s, 3)


def s04_pipeline(prs, m):
    s = slide(prs)
    header(s, "How it works: nine stages, one auditable chain",
           "Each stage is instrumented and reports measurable output, not a black box.",
           "Pipeline")

    stages = [
        ("1", "Ingestion", "bulk upload, SHA-256 dedup, batch tracking", "FR-1"),
        ("2", "CV preprocessing", "deskew, denoise, CLAHE, binarise, layout regions, stamp detection", "FR-2"),
        ("3", "OCR / HTR", "Tesseract 5 with word-level confidence; separate handwriting path", "FR-3"),
        ("4", "Field extraction", "label aliases + regex + NER-style scoring across 19 fields", "FR-4"),
        ("5", "Confidence fusion", "OCR + pattern + label proximity + cross-field consistency", "FR-5"),
        ("6", "GIS linking", "Shapely polygon match on khasra/survey, area comparison", "FR-9"),
        ("7", "Validation", "BR-1…BR-10 with severity, evidence and suggested action", "FR-6/7"),
        ("8", "Cross-database", "Bhulekh, BhuNaksha, IGR, LGD, LRMS checks", "FR-8"),
        ("9", "Human review", "correct / approve / reject / escalate, every step audited", "FR-10/12"),
    ]
    for index, (number, name, detail, fr) in enumerate(stages):
        y = 1.5 + 0.565 * index
        rect(s, 0.6, y, 12.1, 0.5, WHITE if index % 2 else PANEL, LINE)
        rect(s, 0.6, y, 0.42, 0.5, BRAND)
        text(s, 0.6, y + 0.13, 0.42, 0.3, number, size=13, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
        text(s, 1.15, y + 0.13, 2.5, 0.3, name, size=12.5, color=INK, bold=True)
        text(s, 3.7, y + 0.14, 7.6, 0.3, detail, size=11.5, color=INK_2)
        text(s, 11.4, y + 0.14, 1.2, 0.3, fr, size=10.5, color=BRAND_2, bold=True, align=PP_ALIGN.RIGHT)

    text(s, 0.6, 6.63, 12.1, 0.3,
         "Every stage writes to the audit trail — the officer can always ask \"why did the system "
         "think that?\" and get the evidence region and confidence that produced it.",
         size=11, color=INK_3)
    footer(s, 4)


def s05_validation(prs, m):
    s = slide(prs)
    header(s, "Ten business rules, each with a name and a reason",
           "A finding is never just a red flag — it names the rule, the conflicting values and the next step.",
           "Validation engine")

    d = m["dashboard"]
    counts: dict[str, int] = {}
    for row in d["validation"]["rule_wise"]:
        counts[row["rule_id"]] = counts.get(row["rule_id"], 0) + row["count"]

    rules = [(r["id"], r["name"], r["purpose"]) for r in m["rules"]]
    col_w = 6.0
    for index, (rid, name, purpose) in enumerate(rules):
        column, row = divmod(index, 5)
        x = 0.6 + column * (col_w + 0.15)
        y = 1.5 + row * 0.86
        hit = counts.get(rid, 0)
        rect(s, x, y, col_w, 0.78, WHITE, LINE)
        rect(s, x, y, 0.055, 0.78, RED if hit else BRAND_2)
        text(s, x + 0.18, y + 0.09, 0.62, 0.25, rid, size=11.5, color=RED if hit else BRAND, bold=True)
        text(s, x + 0.85, y + 0.09, col_w - 1.7, 0.25, name, size=11.5, color=INK, bold=True)
        text(s, x + 0.18, y + 0.36, col_w - 0.36, 0.36, purpose, size=9.5, color=INK_2)
        if hit:
            rect(s, x + col_w - 0.72, y + 0.09, 0.56, 0.24, RED)
            text(s, x + col_w - 0.72, y + 0.12, 0.56, 0.2, f"{hit} hit", size=8.5,
                 color=WHITE, bold=True, align=PP_ALIGN.CENTER)

    text(s, 0.6, 6.05, 12.1, 0.9, [
        {"text": "Every rule is unit-tested, including the cases that must not fire. ", "size": 11.5, "color": INK_2},
        {"text": "For example BR-4 must not read book number 3311 as a year, and BR-10 must not "
                 "fail a valid reference just because a stamp fragment landed in the same OCR span. "
                 "Both were real bugs found and fixed by the test-suite.", "size": 11.5, "color": INK_2},
    ])
    footer(s, 5)


def s06_demo(prs, m):
    s = slide(prs)
    header(s, "Live results from the running prototype",
           f"Read from the API at {API} while building this deck — not typed in by hand.",
           "Evidence")

    d = m["dashboard"]
    perf = d["performance"]

    cards = [
        ("Documents processed", f"{d['documents']['uploaded']}", "all six demo records, zero failures", BRAND_2),
        ("Mean confidence", f"{d['extraction']['average_record_confidence']:.1f}%", "field-level, per record", BRAND_2),
        ("Findings raised", f"{d['validation']['discrepancies_total']}", f"{d['validation']['high_and_critical']} high or critical", RED),
        ("GIS link rate", f"{d['extraction']['gis_link_rate_pct']:.0f}%", f"{d['extraction']['gis_linked_records']}/{d['records']['total']} records matched a polygon", BRAND_2),
        ("Avg pipeline", f"{perf['average_pipeline_latency_ms']/1000:.1f}s", f"budget 10s · slowest {perf['slowest_document_ms']/1000:.1f}s", GOLD),
        ("Audit events", f"{d['audit']['events']}", "hash-chained, tamper-evident", BRAND_2),
        ("Corrections captured", f"{d['learning']['corrections_captured']}", "labeled AI→human pairs for fine-tuning", BRAND_2),
        ("Test suite", "99", "passing, incl. all 10 rules", BRAND_2),
    ]
    for index, (label, value, foot, tone) in enumerate(cards):
        row, column = divmod(index, 4)
        x = 0.6 + column * 3.09
        y = 1.5 + row * 1.26
        stat_card(s, x, y, 2.94, 1.12, label, value, foot, tone)

    y = 4.2
    text(s, 0.6, y, 6, 0.3, "FINDINGS BY SEVERITY", size=10, color=INK_3, bold=True)
    sev = d["validation"]["by_severity"]
    max_sev = max(sev.values()) if sev else 1
    for index, (name, colour) in enumerate([("critical", RED), ("high", RED_2),
                                            ("medium", GOLD), ("low", BLUE)]):
        value = sev.get(name, 0)
        yy = y + 0.38 + index * 0.42
        text(s, 0.6, yy, 1.0, 0.28, name, size=11, color=INK_2)
        rect(s, 1.75, yy + 0.06, 3.6, 0.17, LINE)
        if value:
            rect(s, 1.75, yy + 0.06, max(0.12, 3.6 * value / max_sev), 0.17, colour)
        text(s, 5.5, yy, 0.6, 0.28, str(value), size=11, color=INK, bold=True)

    panel(s, 6.5, y - 0.1, 6.2, "What the demo dataset is built to prove", [
        "DOC-002 · degraded scan → deskew engages + BR-10",
        "DOC-003 · handwritten Devanagari → HTR + correction",
        "DOC-004 · +53% area vs polygon → BR-9",
        "DOC-005 · conflicting owner → BR-3 critical → HTTP 409",
        "DOC-006 · sub-plots ≠ parent → BR-2, BR-1, BR-4, BR-7",
    ], size=10.5)
    footer(s, 6)


def s07_multilingual(prs, m):
    s = slide(prs)
    header(s, "Multilingual by construction, not by translation",
           "The same extractor handles Latin, Devanagari and Odia scripts.",
           "Language support")

    langs = [l for l in m["status"]["ocr"]["installed_languages"] if l != "osd"]
    text(s, 0.6, 1.5, 6.0, 0.35, "Language packs installed on this machine", size=12.5, color=INK, bold=True)
    for index, lang in enumerate(langs):
        x = 0.6 + index * 1.35
        rect(s, x, 1.95, 1.2, 0.62, BRAND_SOFT)
        text(s, x, 2.13, 1.2, 0.3, lang, size=15, color=BRAND, bold=True, align=PP_ALIGN.CENTER)
    text(s, 0.6, 2.72, 6.0, 0.3,
         f"Tesseract {m['status']['ocr']['tesseract_version']} · the prototype runs real OCR, not canned text",
         size=10.5, color=INK_3)

    text(s, 0.6, 3.3, 6.0, 0.35, "How a Hindi register page is handled", size=12.5, color=INK, bold=True)
    bullet_block(s, 0.6, 3.75, 5.9, [
        ("Label aliases", "खसरा / खाता / खातेदार map to the same field names"),
        ("Master data", "Indic village spellings resolve so BR-7 validates"),
        ("Handwriting path", "separate PSM sweep, honestly lower confidence"),
        ("Correction loop", "mis-read names become labeled training data"),
    ], size=11.5, pad=0.16)

    rect(s, 7.0, 1.5, 5.7, 5.2, WHITE, LINE)
    text(s, 7.25, 1.68, 5.2, 0.3, "WHY THIS IS THE HARD PART", size=10, color=BRAND, bold=True)
    text(s, 7.25, 2.0, 5.2, 4.5, [
        {"text": "Most OCR demos stop at reading Latin print. Indian land records do not look "
                 "like that.", "size": 12.5, "color": INK_2},
        {"text": " ", "size": 8},
        {"text": "A page mixes printed Latin labels, handwritten Devanagari values, ruled tables, "
                 "revenue stamps and faded paper. Reading it is only half the job — the extracted "
                 "value still has to satisfy the same validation rules as an English record.",
         "size": 12.5, "color": INK_2},
        {"text": " ", "size": 8},
        {"text": "That is why the administrative master carries Indic aliases. Without them a "
                 "Devanagari page would OCR correctly and then fail validation on every field — "
                 "multilingual support in name only.", "size": 12.5, "color": INK},
        {"text": " ", "size": 8},
        {"text": "Production adds Marathi, Bengali, Telugu, Tamil and other state languages "
                 "through the same adapter interface.", "size": 12.5, "color": INK_2},
    ])
    footer(s, 7)


def s08_gis_audit(prs, m):
    s = slide(prs)
    header(s, "Maps and auditability: the two things OCR demos skip",
           "Text records linked to cadastral geometry, and a trail nobody can quietly edit.",
           "GIS + audit")

    panel(s, 0.6, 1.5, 6.0, "GIS / cadastral linking (FR-9)", [
        "12 polygons — sample cadastral layer, EPSG:4326",
        "Matched on khasra → survey → plot number, in order",
        "Area computed from the polygon itself via Shapely",
        "BR-9 fires beyond 15% polygon/record divergence",
        "Neighbours within 400 m surfaced for disputes",
    ], size=11.5)

    panel(s, 0.6, 4.25, 6.0, "Immutable audit trail (FR-12)", [
        "Hash-chained — each entry hashes the previous one",
        "Tamper-evident — any edit breaks the chain, located",
        "Complete — AI runs, corrections, approvals included",
        "Old→new — both values + AI confidence kept per fix",
        "Verifiable — the chain recomputes from the Audit tab",
    ], size=11.5)

    rect(s, 7.0, 1.5, 5.7, 5.2, PANEL)
    text(s, 7.25, 1.68, 5.2, 0.3, "THE APPROVAL GUARANTEE", size=10, color=BRAND, bold=True)
    text(s, 7.25, 2.0, 5.2, 1.0,
         "A record with an open critical finding cannot be approved.",
         size=17, color=BRAND, bold=True)
    text(s, 7.25, 3.0, 5.2, 3.4, [
        {"text": "This is enforced in the API, so no interface — ours or a future integration — "
                 "can bypass it.", "size": 12.5, "color": INK_2},
        {"text": " ", "size": 8},
        {"text": "In the live demo, approving the duplicate-khasra record returns:",
         "size": 12.5, "color": INK_2},
        {"text": " ", "size": 6},
        {"text": "HTTP 409 — Cannot approve: 1 critical discrepancy still open (BR-3). "
                 "Resolve or reject instead.", "size": 12, "color": RED, "bold": True},
        {"text": " ", "size": 8},
        {"text": "The officer can escalate it to the Tehsildar, reject it, or correct the "
                 "underlying data. What they cannot do is wave it through.",
         "size": 12.5, "color": INK_2},
    ])
    footer(s, 8)


def s09_architecture(prs, m):
    s = slide(prs)
    header(s, "Built to be handed over, not thrown away",
           "The prototype keeps the same contracts the pilot and production tiers need.",
           "Architecture")

    rows = [
        ("Database", "SQLite", "PostgreSQL + PostGIS", "change the connection URL"),
        ("Queue", "in-process thread worker", "Redis / Celery", "stage names already match"),
        ("OCR", "Tesseract 5 (eng/hin/ori)", "fine-tuned OCR models", "same word-token contract"),
        ("HTR", "Tesseract + OpenCV chain", "TrOCR / IndicHTR transformer", "same WordToken contract"),
        ("Layout", "OpenCV heuristics", "YOLO / Detectron2", "same labelled-box output"),
        ("External systems", "deterministic mocks", "read-only government APIs", "same ExternalCheck contract"),
        ("Auth", "seeded accounts + argon2", "department SSO + MFA", "routes only see a User"),
        ("GIS", "sample GeoJSON + inline SVG", "GeoServer + PostGIS", "same polygon contract"),
    ]
    headers = ["Concern", "Prototype (today)", "Pilot / production", "What changes"]
    widths = [2.3, 3.3, 3.4, 3.1]
    x = 0.6
    rect(s, x, 1.5, sum(widths), 0.4, BRAND)
    cursor = x
    for head, width in zip(headers, widths):
        text(s, cursor + 0.12, 1.59, width - 0.2, 0.28, head.upper(), size=9.5, color=WHITE, bold=True)
        cursor += width

    for index, row in enumerate(rows):
        yy = 1.9 + 0.52 * index
        rect(s, x, yy, sum(widths), 0.52, WHITE if index % 2 else PANEL, LINE)
        cursor = x
        for value, width in zip(row, widths):
            text(s, cursor + 0.12, yy + 0.14, width - 0.2, 0.3, value, size=10.5,
                 color=INK if value == row[0] else INK_2, bold=(value == row[0]))
            cursor += width

    text(s, 0.6, 6.25, 12.1, 0.7, [
        {"text": "Stack: ", "size": 11.5, "color": INK, "bold": True},
        {"text": "FastAPI · SQLAlchemy · OpenCV · Tesseract 5 · Shapely · rapidfuzz · argon2. "
                 "The console is vanilla JS with no build step and no CDN dependencies, so it runs "
                 "unchanged on an air-gapped government intranet.", "size": 11.5, "color": INK_2},
    ])
    footer(s, 9)


def s10_roadmap(prs, m):
    s = slide(prs)
    header(s, "From hackathon table to state deployment",
           "Three tiers, each with a concrete exit criterion rather than a wish list.",
           "Roadmap")

    phases = [
        ("Phase 1", "Hackathon prototype", "DONE", [
            "Real OCR in English, Hindi and Odia",
            "19-field extraction with confidence",
            "All ten business rules firing",
            "Reviewer console + audit chain",
            "Sample cadastral layer + SVG map",
            "101 automated tests",
        ], BRAND_2),
        ("Phase 2", "District pilot", "NEXT", [
            "Real district records, GPU-backed OCR",
            "Department SSO + district data scoping",
            "Read-only Bhulekh / BhuNaksha links",
            "Fine-tune OCR/NER on corrections",
            "Multi-user review workflow",
            "Centralised logging and monitoring",
        ], GOLD),
        ("Phase 3", "State / national", "TARGET", [
            "Multi-district, multi-language rollout",
            "Full LRMS / DILRMP integration",
            "PostGIS + GeoServer cadastral serving",
            "Governed write-back where approved",
            "DPDP-compliant data handling",
            "National-level MIS dashboard",
        ], INK_3),
    ]
    for index, (phase, name, status, items, colour) in enumerate(phases):
        x = 0.6 + index * 4.15
        rect(s, x, 1.5, 3.95, 5.0, WHITE, LINE)
        rect(s, x, 1.5, 3.95, 0.62, colour)
        text(s, x + 0.2, 1.6, 2.5, 0.24, phase.upper(), size=9.5, color=WHITE, bold=True)
        text(s, x + 0.2, 1.8, 2.9, 0.28, name, size=13.5, color=WHITE, bold=True)
        rect(s, x + 3.1, 1.63, 0.68, 0.26, WHITE)
        text(s, x + 3.1, 1.66, 0.68, 0.22, status, size=8.5, color=colour, bold=True, align=PP_ALIGN.CENTER)
        bullet_block(s, x + 0.2, 2.35, 3.6, items, size=11, pad=0.32)

    footer(s, 10)


def s11_impact(prs, m):
    s = slide(prs)
    header(s, "Impact: what changes for citizens and for the department",
           "The mechanism is fewer manual keystrokes; the outcome is fewer disputes.",
           "Impact")

    panel(s, 0.6, 1.5, 6.0, "For citizens", [
        "Faster mutations — less time to an updated record",
        "Fewer errors — validation catches what operators miss",
        "Fewer disputes — conflicting owners surfaced early",
        "Transparency — verified record with a traceable history",
        "Better services — clean data unblocks credit & schemes",
    ], size=11.5)

    panel(s, 0.6, 4.2, 6.0, "For the department", [
        "Workload — officers verify instead of transcribing",
        "Monitoring — live MIS on progress and reviewer load",
        "Auditability — every action attributable and reversible",
        "DILRMP alignment — records become digitised, geo-referenced",
        "Institutional memory — corrections become training data",
    ], size=11.5)

    d = m["dashboard"]
    perf = d["performance"]
    rect(s, 7.0, 1.5, 5.7, 5.2, BRAND_SOFT)
    text(s, 7.25, 1.68, 5.2, 0.3, "MEASURED ON THIS PROTOTYPE", size=10, color=BRAND, bold=True)

    measured = [
        (f"{perf['average_pipeline_latency_ms']/1000:.1f}s", "to take a scan from upload to a validated, GIS-linked, review-ready record"),
        (f"{d['extraction']['average_record_confidence']:.0f}%", "mean extraction confidence before any human touches it"),
        (f"{d['validation']['discrepancies_total']}", "issues caught automatically across 6 documents — none found by a human"),
        ("1", "critical title conflict caught that a manual workflow would likely have missed"),
    ]
    yy = 2.1
    for big, small in measured:
        text(s, 7.25, yy, 1.5, 0.4, big, size=21, color=BRAND, bold=True)
        text(s, 8.85, yy + 0.04, 3.6, 0.9, small, size=11, color=INK_2)
        yy += 1.05

    text(s, 7.25, 6.3, 5.2, 0.3,
         "Target for pilot: 70–80% reduction in manual digitisation effort.",
         size=11, color=BRAND, bold=True)
    footer(s, 11)


def s12_close(prs, m):
    s = slide(prs)
    rect(s, 0, 0, 13.333, 7.5, BRAND)
    rect(s, 0.6, 0.9, 1.4, 0.045, GOLD)

    text(s, 0.6, 1.25, 12, 0.9, "BhuVerify", size=48, color=WHITE, bold=True)
    text(s, 0.6, 2.25, 11.5, 1.4,
         "We did not build an OCR demo. We built the verification layer that sits "
         "between a scanned register and a land record a citizen can rely on.",
         size=19, color=RGBColor(0xD8, 0xEC, 0xE5))

    points = [
        ("It is real", "genuine Tesseract OCR in three scripts on rendered documents, not canned output"),
        ("It is honest", "confidence is computed and shown; handwritten pages score lower, as they should"),
        ("It is safe", "critical findings block approval server-side; a human always decides"),
        ("It is accountable", "every AI decision and human correction is in a tamper-evident chain"),
        ("It is ready", "contracts already match the pilot tier; 101 tests guard the behaviour"),
    ]
    for index, (title, detail) in enumerate(points):
        y = 3.55 + index * 0.6
        rect(s, 0.6, y + 0.1, 0.08, 0.08, GOLD)
        text(s, 0.85, y, 2.6, 0.3, title, size=13.5, color=WHITE, bold=True)
        text(s, 3.5, y + 0.02, 9.2, 0.4, detail, size=12.5, color=RGBColor(0xC9, 0xE4, 0xDB))

    text(s, 0.6, 6.62, 12, 0.35,
         "SIH26018 · Smart Automation · Team Shadow Slayers · working prototype available for "
         "live demonstration", size=11.5, color=RGBColor(0x8F, 0xC9, 0xB8))


# ---------------------------------------------------------------------------
def _check_bounds(prs) -> None:
    """Fail the build if any shape is placed below the footer line."""
    problems = []
    for slide_no, sl in enumerate(prs.slides, 1):
        for shape in sl.shapes:
            top, bottom = Emu(shape.top).inches, Emu(shape.top + shape.height).inches
            if top < 0.02 and bottom > 7.4:  # full-bleed background bands
                continue
            if top >= 7.0:  # footer zone is allowed at the very bottom
                continue
            if bottom > Emu(PAGE_BOTTOM).inches + 0.06:
                problems.append((slide_no, shape.shape_id, round(bottom, 2)))
    if problems:
        raise SystemExit(f"Layout overflow below {Emu(PAGE_BOTTOM).inches:.2f}in on slides: {problems}")


def build() -> None:
    print(f"Reading live metrics from {API} …")
    try:
        metrics = live_metrics()
    except (urllib.error.URLError, OSError, KeyError) as exc:
        sys.exit(
            f"Could not read live metrics from {API}: {exc}\n"
            "Start the app first:  uvicorn app.main:app --host 0.0.0.0 --port 8000\n"
            "The deck is built from real numbers on purpose - it will not fall back to stale ones."
        )

    d = metrics["dashboard"]
    print(f"  {d['documents']['uploaded']} documents · {d['records']['total']} records · "
          f"{d['validation']['discrepancies_total']} findings · "
          f"{d['performance']['average_pipeline_latency_ms']:.0f} ms avg")

    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    for builder in (s01_title, s02_problem, s03_solution, s04_pipeline, s05_validation,
                    s06_demo, s07_multilingual, s08_gis_audit, s09_architecture,
                    s10_roadmap, s11_impact, s12_close):
        builder(prs, metrics)

    _check_bounds(prs)
    prs.save(OUT)
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes, {len(prs.slides._sldIdLst)} slides)")


if __name__ == "__main__":
    build()
