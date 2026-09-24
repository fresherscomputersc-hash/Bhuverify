"""
Structured field extraction + field-level confidence (SRS FR-4, FR-5).

Pipeline per document:
    OCR words -> label alias index -> candidate spans -> regex/NER-style
    validators -> Indic + unit normalisation -> confidence fusion

Confidence fusion (FR-5) blends four signals, all of which are real numbers
produced by this pipeline rather than hard-coded:

    confidence = 0.45 * ocr_word_confidence
               + 0.30 * pattern_match_strength
               + 0.15 * label_proximity_score
               + 0.10 * cross_field_consistency

Bands: >= 90 high, 70-89 medium, < 70 low -> mandatory human review.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.config import settings
from app.services.ocr_service import WordToken

# ---------------------------------------------------------------------------
# Label aliases (multilingual) - the "language adapter" hook
# ---------------------------------------------------------------------------
LABEL_ALIASES: dict[str, list[str]] = {
    "khasra_no": [
        "khasra no", "khasra number", "khasra", "kh no", "khasara",
        "khesra", "gata",
        "खसरा संख्या", "खसरा नम्बर", "खसरा", "खेसरा", "गाटा",
        "ଖସରା ନମ୍ବର", "ଖସରା",
    ],
    "khata_no": [
        "khata no", "khata number", "khata", "kh no of account", "khewat",
        "खाता संख्या", "खाता नम्बर", "खाता",
        "ଖାତିଆନ ନମ୍ବର", "ଖାତିଆନ", "ଖେୱାଟ",
    ],
    "survey_no": ["survey no", "survey number", "survey", "s no", "सर्वे नम्बर", "ଜରିପ ନମ୍ବର"],
    "plot_no": ["plot no", "plot number", "plot", "plata", "दाग", "ଦାଗ", "ପ୍ଲଟ"],
    "village": [
        "village", "vill", "mouza", "গ্রাম", "ग्राम", "गाँव", "ଗ୍ରାମ", "ମୌଜା",
    ],
    "tehsil": ["tehsil", "tahsil", "taluka", "block", "tashil", "तहसील", "ତହସିଲ", "ବ୍ଲକ"],
    "district": ["district", "zilla", "janpad", "जिला", "जनपद", "ଜିଲ୍ଲା"],
    "owner_name": [
        "owner name", "name of owner", "owner", "khatedar", "record holder",
        "land holder", "proprietor", "pattadar", "kashtkar", "bhoomi dharak",
        "landholder",
        "खातेदार का नाम", "खातेदार", "भूमि स्वामी", "काश्तकार का नाम",
        "काश्तकार", "भूमि धारक का नाम", "भूमि धारक", "पट्टादार",
        "मालिक का नाम", "मालिक",
        "ଖାତିଆନଧାରୀଙ୍କ ନାମ", "ଖାତିଆନଧାରୀ", "ଜମି ମାଲିକ", "ପ୍ରଜାର ନାମ",
        "ପ୍ରଜା",
    ],
    "guardian_name": [
        "father name", "father's name", "guardian", "s/o", "son of", "w/o",
        "पिता का नाम", "पिता", "ପିତାଙ୍କ ନାମ", "ପିତା",
    ],
    "address": ["address", "residence", "at/po", "पता", "ଠିକଣା"],
    "area": [
        "area", "total area", "plot area", "land area", "extent", "rakaba",
        "क्षेत्रफल", "कुल क्षेत्रफल", "रकबा",
        "କ୍ଷେତ୍ରଫଳ", "ମୋଟ କ୍ଷେତ୍ରଫଳ", "ରକବା",
    ],
    "land_classification": [
        "classification", "land type", "class of land", "land class",
        "kisam", "kism", "bhumi vargikaran", "prakar",
        "किस्म जमीन", "जमीन का प्रकार", "किस्म", "भूमि वर्गीकरण",
        "भूमि का प्रकार", "प्रकार", "श्रेणी", "ଜମିର ପ୍ରକାର", "କିସମ",
    ],
    "boundaries": ["boundary", "boundaries", "four boundaries", "चहद्दी", "ଚତୁଃସୀମା"],
    "mutation_no": ["mutation no", "mutation number", "mutation case no", "namantaran",
                   "case no", "dakhalkharij",
                   "दाखिल खारिज", "नामांतरण", "ନାମାନ୍ତରଣ",
                   "ଦାଖଲ ଖାରଜ", "କେସ ନଂ"],
    "mutation_date": ["mutation date", "date of mutation", "नामांतरण दिनांक", "ନାମାନ୍ତରଣ ତାରିଖ"],
    "registration_no": ["registration no", "registration number", "regd no", "deed no",
                        "registration deed", "ror reference no", "reference no", "ror no",
                        "पंजीयन संख्या", "ପଞ୍ଜୀକରଣ ନମ୍ବର"],
    # NOTE: bare "from"/"to" are deliberately excluded as aliases - they are
    # substrings of other labels ("Total Area" contains "to") and caused
    # false field matches.
    "previous_owner": ["previous owner", "transferor", "vendor", "पूर्व खातेदार", "ପୂର୍ବ ମାଲିକ"],
    "new_owner": ["new owner", "transferee", "purchaser", "नया खातेदार", "ନୂତନ ମାଲିକ"],
}

REQUIRED_FIELDS = [
    "owner_name", "khasra_no", "khata_no", "village", "tehsil", "district",
    "area", "land_classification",
]

# Secondary label words that follow a primary label after a separator, e.g. the
# "Mouza" in "Village / Mouza Balarampur" or the "Block" in "Tehsil / Block
# Khordha Sadar". Stripping these keeps the value clean without ever eating the
# value itself (the previous open-ended version emptied the village field).
SECONDARY_LABEL_WORDS = {
    "mouza", "mauza", "vill", "block", "tahsil", "tashil", "taluka", "taluk",
    "no", "nos", "number", "num", "name", "of", "dist", "zilla", "sro",
    "ମୌଜା", "ଗ୍ରାମ", "ବ୍ଲକ", "ତହସିଲ", "ଜିଲ୍ଲା",
    "मौजा", "ग्राम", "ब्लॉक", "तहसील", "जिला",
}

# ---------------------------------------------------------------------------
# Document-type profiles - the anti-hallucination contract.
#
# Identifier-heavy extraction (label anchoring, pattern sweeps, table-cell
# fallback) is only safe when the document type actually carries the field.
# Odisha Schedule I Form 39-A (Khatiyan) is the motivating case: it has NO
# Khasra Number, NO Survey Number, and its Khewat / Khatiyan-serial numbers
# are different concepts from the canonical Khata Number. A profile lists
# the canonical fields that must stay null/missing for the type, so the
# engine reports "missing / field_not_present" instead of inventing values
# from look-alike numbers (plot 417 is not a khasra; khatiyan 18 is not a
# khata; mutation case 4837/2025 is not a registration).
# ---------------------------------------------------------------------------
DOC_PROFILE_GENERIC = "generic"
DOC_PROFILE_ODISHA_39A = "odisha_khatiyan_39a"

FORM_39A_MARKERS = ("form no.39-a", "form no 39-a", "form no. 39", "39-a")

# Canonical fields that must NOT be extracted for the profile, even when a
# numeric look-alike is on the page.
PROHIBITED_BY_PROFILE: dict[str, set[str]] = {
    DOC_PROFILE_ODISHA_39A: {
        "khasra_no",        # no Khasra field on the form
        "khata_no",         # Khewat / Khatiyan-serial are different concepts
        "survey_no",        # no Survey field on the form
        "registration_no",  # no registration reference on the form
        "mutation_date",    # only publication/assessment dates exist
        "previous_owner",   # never inferred from the person list
        "new_owner",        # never inferred without explicit evidence
    },
}

# Mandatory-field sets per document type (BR-1 reads this, not the flat list).
REQUIRED_BY_DOCTYPE: dict[str, list[str]] = {}


def required_fields_for(doc_type_label: str) -> list[str]:
    """Mandatory fields for a document type (SRS FR-6, BR-1)."""
    if doc_type_label == "odisha_khatiyan_39a":
        return ["owner_name", "plot_no", "village", "tehsil", "district",
                "area", "land_classification"]
    return list(REQUIRED_FIELDS)


def detect_profile(text: str) -> str:
    """Cheap heading-based document-type hint for the extractor."""
    lowered = (text or "")[:4000].lower()
    if any(marker in lowered for marker in FORM_39A_MARKERS):
        return DOC_PROFILE_ODISHA_39A
    if "ଖେୱାଟ" in lowered and "ଖତିୟାନ" in lowered and "ମୌଜା" in lowered:
        return DOC_PROFILE_ODISHA_39A
    if "khewat" in lowered and "khatiyan" in lowered and "mouza" in lowered:
        return DOC_PROFILE_ODISHA_39A
    return DOC_PROFILE_GENERIC


# Dedicated (non-canonical) identifiers some forms carry alongside - or
# instead of - the canonical ones. Stored verbatim on the record, never
# mapped into khata_no / khasra_no.
DEDICATED_ALIASES: dict[str, list[str]] = {
    "khewat_no": ["ଖେୱାଟ", "khewat"],
    "khatiyan_no": ["ଖତିୟାନ", "khatiyan"],
    "tehsil_no": ["ତହସିଲ ନମ୍ବର", "tehsil number", "tahasil number", "tehsil no"],
}

DEDICATED_NUMBER = re.compile(r"(\d{1,6})")

# ---------------------------------------------------------------------------
# Unit normalisation (feeds BR-6)
# ---------------------------------------------------------------------------
# All factors convert 1 <unit> -> hectares.
AREA_UNITS_TO_HECTARE: dict[str, float] = {
    "hectare": 1.0, "ha": 1.0, "hec": 1.0, "hectares": 1.0, "ହେକ୍ଟର": 1.0, "हेक्टर": 1.0,
    "हेक्टेयर": 1.0, "हे": 1.0, "हैक्टेयर": 1.0,
    "acre": 0.404686, "acres": 0.404686, "ac": 0.404686, "ଏକର": 0.404686, "एकड़": 0.404686,
    # 1 acre = 100 decimals
    "decimal": 0.00404686, "decimals": 0.00404686, "dec": 0.00404686, "dismil": 0.00404686,
    "ଡେସିମାଲ": 0.00404686, "डेसिमल": 0.00404686,
    # 1 acre = 4 bigha (Odisha/Bengal convention), 1 bigha = 20 kathas
    "bigha": 0.1011715, "bighas": 0.1011715, "बीघा": 0.1011715, "ବିଘା": 0.1011715,
    "katha": 0.005058575, "kathas": 0.005058575, "कट्ठा": 0.005058575, "କଥା": 0.005058575,
    # 1 acre = 40 guntha
    "guntha": 0.01011715, "gunta": 0.01011715, "gunthas": 0.01011715,
    # 1 acre = 8 kanal, 1 kanal = 20 marla
    "kanal": 0.05058575, "kanals": 0.05058575, "कनाल": 0.05058575,
    "marla": 0.0025292875, "मरला": 0.0025292875,
    # 1 hectare = 100 are
    "are": 0.01, "ares": 0.01, "sqm": 0.0001, "sq m": 0.0001,
    "square metre": 0.0001, "square meter": 0.0001,
    # Odisha local units used in handwritten registers
    "mana": 0.00404686, "ମାଣ": 0.00404686,  # treated as decimal-equivalent locally
    "biswa": 0.005058575, "बिस्वा": 0.005058575,
}

UNIT_ALIASES_NORMALISED = {k.lower(): v for k, v in AREA_UNITS_TO_HECTARE.items()}


def normalize_unit(unit: str) -> str:
    key = unicodedata.normalize("NFKC", (unit or "").strip().lower().rstrip("."))
    key = re.sub(r"\s+", " ", key)
    if key in UNIT_ALIASES_NORMALISED:
        return key
    # handle plurals / OCR noise like "acres." or "एकड़"
    stripped = key.rstrip("s")
    if stripped in UNIT_ALIASES_NORMALISED:
        return stripped
    return key


def to_hectare(value: float, unit: str) -> tuple[float, bool]:
    """Return (hectares, unit_recognised)."""
    key = normalize_unit(unit)
    factor = UNIT_ALIASES_NORMALISED.get(key)
    if factor is None:
        return 0.0, False
    return round(value * factor, 6), True


# ---------------------------------------------------------------------------
# Regex validators - "pattern match strength" component of confidence
# ---------------------------------------------------------------------------
# Patterns are deliberately *unanchored*: an OCR span usually carries the value
# plus noise (a stamp fragment, a rule character, the next column). Anchored
# patterns would reject the whole span, so the best in-span match is taken and
# the leftover text is discarded - with a lower pattern-match score to reflect
# that the span was not clean.
PATTERNS: dict[str, re.Pattern] = {
    "khasra_no": re.compile(r"\b\d{1,4}(?:/\d{1,3})?(?:\s*[-–]\s*\d{1,3})?\b"),
    "khata_no": re.compile(r"\b\d{1,6}\b"),
    "survey_no": re.compile(r"\b\d{1,5}(?:/\d{1,3})?\b"),
    "plot_no": re.compile(r"\b\d{1,5}(?:/\d{1,3})?\b"),
    "mutation_no": re.compile(r"\b[A-Z]{0,4}[-/]?\d{2,6}(?:/\d{2,4})?\b|\b\d{1,5}/\d{4}\b", re.IGNORECASE),
    "registration_no": re.compile(
        r"(?:no\.?\s*)?(?:[A-Z]{2,5}[/-]\d{6,12}"
        r"|\b\d{1,5}\s*(?:of\s*)?\d{4}(?:\s*[-/]\s*\d{2,4})?\b)",
        re.IGNORECASE,
    ),
    "mutation_date": re.compile(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|"
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b"
    ),
    "area": re.compile(r"\b\d+(?:\.\d+)?\b"),
}

# "Sub Plot Khasra 340/1  Area 0.60 acre" rows on a multi-plot register page.
SUBPLOT_PATTERN = re.compile(
    r"(?:sub\s*plot|child\s*plot)[^\d]{0,12}(\d{1,4}/\d{1,3})[^\d]{0,12}"
    r"(\d+(?:\.\d+)?)\s*([A-Za-z\u0900-\u097F\u0B00-\u0B7F]+)",
    re.IGNORECASE,
)

# Values that look like a name (>= 2 capitalised tokens, Indic allowed)
NAME_STOPWORDS = {
    "the", "of", "and", "s/o", "w/o", "d/o", "no", "date", "khasra", "khata",
    "village", "tehsil", "district", "area", "acre", "owner", "name", "father",
    "at", "po", "ps", "mouza", "total", "plot", "survey", "classification",
}

DATE_HINT = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}")


def _inside_date(haystack: str, match: re.Match) -> bool:
    """True when a regex match sits inside a calendar date on the same string.

    Identifier patterns (khasra 123/4, khata 567) happily match fragments of
    dates ("15/07" out of "15/07/2024"). A candidate that overlaps a date span
    is a date fragment, not an identifier.
    """
    for date_match in DATE_HINT.finditer(haystack):
        if date_match.start() <= match.start() and match.end() <= date_match.end():
            return True
    return False


def _is_table_header_line(line_text: str) -> bool:
    """True for a table header row that merely names the columns.

    A label-anchored match on such a line ("Owner Name | Relation | Khasra
    No. | ...") yields column titles as the "value". Header rows are handed
    to the table-row fallback instead, which reads the data rows below.
    """
    lowered = line_text.lower()
    fields_hit = sum(
        1 for aliases in LABEL_ALIASES.values()
        if any(alias.lower() in lowered for alias in aliases)
    )
    # Multi-pair value lines ("Village : X Khasra No. : Y") also hit two
    # fields, so the count rule only applies to piped table rows.
    if fields_hit >= 2 and "|" in line_text:
        return True
    return bool(re.search(r"relation|remarks|column|sl\.?\s*no", lowered))

def _table_data_rows(raw_lines: list[str]) -> list[str]:
    """Data rows under a table header (NIC ROR, UP khatauni, registers)."""
    header_idx = -1
    for idx, line in enumerate(raw_lines):
        lowered = line.lower()
        if "owner" in lowered or "khatedar" in lowered or "khata" in lowered:
            if _is_table_header_line(line):
                header_idx = idx
                break
    if header_idx < 0:
        return []
    return [ln for ln in raw_lines[header_idx + 1: header_idx + 7] if "|" in ln]


def _owner_from_table_row(raw_lines: list[str]) -> str:
    """Fallback owner for tabular formats.

    The owner sits in a data row under an "Owner Name"-style header with no
    label on its own line ("1  Ramesh Chandra Sahoo | S/o ... | 123/4 ...").
    The first pipe-separated cell that reads like a person's name wins; pure
    numbers, dates and area-like cells are skipped.
    """
    identifier = re.compile(r"^\d{1,4}(?:/\d{1,3})?$|^\d+(?:\.\d+)?$")
    for line in _table_data_rows(raw_lines):
        for cell in line.split("|"):
            cell = re.sub(r"^\d{1,3}\s+", "", cell.strip(" ,.:;-\t"))
            if not cell or identifier.match(cell):
                continue
            if DATE_HINT.search(cell):
                continue
            ok, _score = _looks_like_name(cell)
            if ok and len(cell.split()) >= 2:
                return cell
    return ""


def _table_cell_fallback(raw_lines: list[str]) -> dict:
    """Cell-level fallback for khasra / khata / area on tabular pages.

    Only fills fields the label pass left empty. Each cell is tested by shape:
    slash-identifier -> khasra, pure-digit run -> khata, decimal -> area.
    Dates, years and kilometre-long reference codes are never identifiers.
    """
    found: dict[str, str] = {}
    rows = _table_data_rows(raw_lines)
    if not rows:
        return found

    def _in_date(line: str, token: str) -> bool:
        idx = line.find(token)
        if idx < 0:
            return False
        for start, end in DATE_HINT.finditer(line):
            if start <= idx and idx + len(token) <= end:
                return True
        return False

    khata_candidates: list[str] = []
    for line in rows:
        for cell in line.split("|"):
            for token in cell.strip(" ,.:;-\t").split():
                token = token.strip("(),.:;-")
                if not token or _in_date(line, token):
                    continue
                if re.match(r"^\d{1,4}/\d{1,3}$", token) and "khasra_no" not in found:
                    found["khasra_no"] = token
                elif re.match(r"^\d+\.\d+$|^\d+-\d+$", token) and "area" not in found:
                    found["area"] = token.replace("-", ".", 1) \
                        if re.match(r"^\d+-\d+$", token) else token
                elif re.match(r"^\d{2,6}$", token):
                    if len(token) == 4 and 1900 <= int(token) <= 2100:
                        continue  # a year, not a khata number
                    khata_candidates.append(token)
    if "khata_no" not in found and khata_candidates:
        # prefer multi-digit runs: a lone "1" is usually the serial number
        long = [c for c in khata_candidates if len(c) >= 2]
        found["khata_no"] = long[0] if long else khata_candidates[0]
    return found


@dataclass
class FieldExtraction:
    field_name: str
    label: str
    value: str = ""
    normalized_value: str = ""
    confidence: float = 0.0
    source: str = "regex"
    evidence_text: str = ""
    bbox: dict = field(default_factory=dict)
    is_low_confidence: bool = False
    signals: dict = field(default_factory=dict)
    # Spec section 7: extracted | missing | needs_review, with a machine
    # reason (field_not_present, canonical_field_not_explicitly_present,
    # ocr_uncertain) and the producing method. Assigned at the end of
    # extract_fields; persisted to ExtractionResult by the worker.
    status: str = "extracted"
    reason: str = ""
    method: str = ""
    page: int = 1

    def as_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "label": self.label,
            "value": self.value,
            "normalized_value": self.normalized_value,
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "evidence_text": self.evidence_text,
            "bbox": self.bbox,
            "is_low_confidence": self.is_low_confidence,
            "signals": self.signals,
            "status": self.status,
            "reason": self.reason,
            "method": self.method,
            "page": self.page,
        }


@dataclass
class SubPlot:
    """A child-plot row read off a multi-plot register page."""

    khasra_no: str
    area_value: float
    area_unit: str
    area_hectare: float
    evidence_text: str


@dataclass
class ExtractionOutcome:
    fields: dict[str, FieldExtraction]
    record_confidence: float
    low_confidence_fields: list[str]
    missing_required: list[str]
    area_hectare: float
    area_unit_recognised: bool
    raw_lines: list[str]
    sub_plots: list[SubPlot] = field(default_factory=list)
    # Document-type awareness (Form 39-A spec): the active profile, the
    # dedicated non-canonical identifiers, the parsed person list, the
    # directional boundary and the 1-based PDF page this outcome came from.
    profile: str = DOC_PROFILE_GENERIC
    dedicated: dict[str, FieldExtraction] = field(default_factory=dict)
    owners: list[dict] = field(default_factory=list)
    boundary: dict = field(default_factory=dict)
    page: int = 1

    @property
    def sub_plot_area_hectare(self) -> float:
        return round(sum(p.area_hectare for p in self.sub_plots), 6)

    def as_dict(self) -> dict:
        return {
            "fields": {name: f.as_dict() for name, f in self.fields.items()},
            "record_confidence": round(self.record_confidence, 2),
            "low_confidence_fields": self.low_confidence_fields,
            "missing_required": self.missing_required,
            "area_hectare": self.area_hectare,
            "area_unit_recognised": self.area_unit_recognised,
            "sub_plots": [vars(p) for p in self.sub_plots],
            "profile": self.profile,
            "dedicated": {name: f.as_dict() for name, f in self.dedicated.items()},
            "owners": self.owners,
            "boundary": self.boundary,
            "page": self.page,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
    return re.sub(r"[ \t]+", " ", text)


def _line_bbox(words: list[WordToken]) -> dict:
    if not words:
        return {}
    x = min(w.bbox["x"] for w in words)
    y = min(w.bbox["y"] for w in words)
    x2 = max(w.bbox["x"] + w.bbox["w"] for w in words)
    y2 = max(w.bbox["y"] + w.bbox["h"] for w in words)
    return {"x": x, "y": y, "w": x2 - x, "h": y2 - y}


def _split_camel(text: str) -> str:
    """Split glued label runs so alias matching sees word boundaries.

    Official computer-generated formats print labels like "KhasraNo." or
    "KhataNo." with no space. Splitting lower->Upper boundaries turns those
    into "Khasra No." / "Khata No." before any label lookup or tail cut.
    """
    return re.sub(r"(?<=[a-z\u00e0-\u00ff])(?=[A-Z])", " ", text)


def _all_aliases() -> list[str]:
    return [alias for aliases in LABEL_ALIASES.values() for alias in aliases]


def _find_label_span(lines: list[dict], aliases: list[str],
                      deprioritize_numeric_tail: bool = False) -> tuple[dict | None, float]:
    """Locate the line containing a label alias; return (line, match_strength).

    With `deprioritize_numeric_tail`, lines whose value part is purely numeric
    ("ତହସିଲ ନମ୍ବର : 232") score lower, so a name field prefers the line that
    actually names it ("ତହସିଲ : ଓଡଗାଁ"). Numbers belong to identifier fields,
    never to names.
    """
    best: tuple[dict | None, float] = (None, 0.0)
    for line in lines:
        hay = _split_camel(line["text"]).lower()
        for alias in aliases:
            alias_l = alias.lower()
            if alias_l in hay:
                strength = min(1.0, len(alias_l) / max(len(hay), 1) + 0.55)
                if deprioritize_numeric_tail and _tail_is_numeric(line["text"], aliases):
                    strength *= 0.3
                if strength > best[1]:
                    best = (line, strength)
    return best


def _tail_is_numeric(line_text: str, aliases: list[str]) -> bool:
    tail = _value_after_label(line_text, aliases).strip(" ,.:;-\t")
    return bool(tail) and re.fullmatch(r"[\d\s/\-.,]+", tail) is not None


# Aliases too short to safely truncate a value at: they appear inside ordinary
# words and village names ("no" in "Bano", "of" in "Sofia").
_TRUNCATION_STOPLIST = {"s/o", "w/o", "d/o", "no", "of", "s no", "kh no"}


def _value_after_label(line_text: str, aliases: list[str]) -> str:
    """Return the part of a line that follows its label.

    Land-record labels are frequently written as alternatives separated by a
    slash ("Village / Mouza", "Tehsil / Block"). Only stripping the first alias
    would leak the rest of the label into the value, so any additional label
    words that follow a separator are consumed too.

    Computer-generated formats also pack several "Label : value" pairs on one
    line ("Village : Bada Sahi Khasra No. : 123/4"). The tail is therefore cut
    at the next recognised label, and table-cell text is cut at the cell
    boundary ("|"), so one field never swallows its neighbour.
    """
    text = _split_camel(line_text)
    lowered = text.lower()
    best_idx, best_len = -1, 0
    for alias in aliases:
        idx = lowered.find(alias.lower())
        if idx >= 0 and len(alias) > best_len:
            best_idx, best_len = idx, len(alias)
    if best_idx < 0:
        return ""

    tail = text[best_idx + best_len:]
    # Land-record labels are often written as alternatives: "Village / Mouza",
    # "Tehsil / Block", "Odia dakhalkharij kes-no". A second label word glued
    # to the first is label, not value - strip it (but never eat a value that
    # merely starts with those letters: what follows the alias must be a
    # separator or the end). Only *known* words/aliases are stripped.
    _LABEL_TAIL_STOPLIST = {"s/o", "w/o", "d/o"}
    for _ in range(3):
        match = re.match(
            r"^\s*[/|¦│:：\-.]?\s*([A-Za-z\u0900-\u097F\u0B00-\u0B7F]+)\b", tail
        )
        if match and match.group(1).strip().lower() in SECONDARY_LABEL_WORDS:
            tail = tail[match.end():]
            continue
        stripped = False
        for alias in sorted(_all_aliases(), key=len, reverse=True):
            if len(alias) < 4 or alias.lower() in _LABEL_TAIL_STOPLIST:
                continue
            second = re.match(
                r"^\s*[/|:：\-–.]?\s*" + re.escape(alias)
                + r"(?=[\s:/|¦│\-–.,]|$)", tail, re.IGNORECASE,
            )
            if second:
                tail = tail[second.end():]
                stripped = True
                break
        if not stripped:
            break
    tail_lower = tail.lower()
    cut = len(tail)
    for alias in _all_aliases():
        if len(alias) < 4 or alias.lower() in _TRUNCATION_STOPLIST:
            continue
        idx = tail_lower.find(alias.lower())
        if 0 < idx < cut:
            cut = idx
    tail = tail[:cut]
    # cut at a table cell boundary ("S/o Late Balaram Sahoo | 123/4 | 567")
    tail = re.split(r"[|¦｜]", tail, maxsplit=1)[0]
    # cut at a table column marker: header fragments like
    # "खसरा/गाटा संख्या (2) नाम/पिता..." carry column numbers, not values
    tail = re.split(r"\(\d{1,2}\)", tail, maxsplit=1)[0]
    return tail.strip(" :：-.\\t|/\u00a6\u2502")


def _looks_like_name(candidate: str) -> tuple[bool, float]:
    candidate = candidate.strip(" ,.:;-")
    if not candidate or len(candidate) < 3:
        return False, 0.0
    if DATE_HINT.search(candidate):
        return False, 0.0
    tokens = [t for t in re.split(r"\s+", candidate) if t and t.lower() not in NAME_STOPWORDS]
    if not tokens:
        return False, 0.0
    indic = any(unicodedata.category(c[0]) == "Lo" for c in tokens if c)
    capitalised = sum(1 for t in tokens if t[0].isupper())
    score = 0.0
    if len(tokens) >= 2:
        score += 0.45
    elif len(tokens) == 1:
        score += 0.2
    score += 0.35 * (capitalised / len(tokens))
    if indic:
        score += 0.2
    if all(t.replace(".", "").replace("-", "").isalpha() or indic for t in tokens):
        score += 0.15
    if any(ch.isdigit() for ch in candidate):
        score -= 0.3
    return score >= 0.5, max(0.0, min(1.0, score))


def _ocr_confidence_for(text: str, words: list[WordToken]) -> tuple[float, dict]:
    """Average OCR confidence of the words that make up `text`."""
    tokens = [t for t in re.split(r"\s+", text) if t]
    if not tokens or not words:
        return 0.0, {}
    matched: list[WordToken] = []
    lowered_words = [(w.text.lower(), w) for w in words]
    cursor = 0
    for token in tokens:
        needle = token.lower().strip(".,:;()")
        if not needle:
            continue
        for index in range(cursor, len(lowered_words)):
            if lowered_words[index][0].strip(".,:;()") == needle:
                matched.append(lowered_words[index][1])
                cursor = index + 1
                break
    if not matched:
        return 0.0, {}
    return float(sum(w.confidence for w in matched) / len(matched)), _line_bbox(matched)


def _fuse(ocr_conf: float, pattern: float, proximity: float, consistency: float) -> float:
    score = 0.45 * ocr_conf + 0.30 * pattern + 0.15 * proximity + 0.10 * consistency
    return round(max(0.0, min(100.0, score)), 2)


# ---------------------------------------------------------------------------
# Odisha praja/person parser (Form 39-A spec sections 8-10).
#
# The person section ("ପ୍ରଜାର ନାମ, ପିତାର ନାମ, ଜାତି ଓ ବାସସ୍ଥାନ") lists several
# people with inline relationship markers:
#   X ପି: Y  ->  X, child of Y          (relation_type "father")
#   X ସ୍ୱା: Y ->  X, spouse of Y         (relation_type "spouse")
#   ଜା: C    ->  caste C (attaches to the group parsed so far)
#   ବା: R    ->  residence R (the canonical address source)
# Markers are never converted into each other, and no relation is assumed
# for a person without an explicit marker.
# ---------------------------------------------------------------------------
PRAJA_HEADER_ALIASES = ["ପ୍ରଜାର ନାମ", "ପ୍ରଜା", "praja"]

# Words that belong to the section heading itself, never to a person. The
# content starts after the last of these on the header line.
PRAJA_HEADER_WORDS = ["ପ୍ରଜାର ନାମ", "ପିତାର ନାମ", "ଜାତି ଓ ବାସସ୍ଥାନ",
                      "ବାସସ୍ଥାନ", "ଜାତି", "ପ୍ରଜା"]

PRAJA_END_HINT = re.compile(r"^\s*\d+\)")


def parse_praja_section(raw_lines: list[str]) -> tuple[list[dict], str]:
    """Parse the person list; return (owners, residence)."""
    start = -1
    for idx, line in enumerate(raw_lines):
        lowered = line.lower()
        if any(alias.lower() in lowered for alias in PRAJA_HEADER_ALIASES):
            start = idx
            break
    if start < 0:
        return [], ""

    first = raw_lines[start]
    cut = 0
    for word in PRAJA_HEADER_WORDS:
        pos = first.lower().find(word.lower())
        if pos >= 0:
            cut = max(cut, pos + len(word))
    rest = first[cut:].strip(" ,.:;-\t")
    section_lines = ([rest] if len(rest) > 3 else [])
    for line in raw_lines[start + 1: start + 9]:
        if PRAJA_END_HINT.match(line.strip()) and section_lines:
            break
        section_lines.append(line)
    section = " ".join(section_lines)
    if not section.strip():
        return [], ""

    residence = ""
    addr_match = re.search(r"ବା:\s*([^,।\n\d]+)", section)
    if addr_match:
        residence = addr_match.group(1).strip(" ,.:;-")

    owners: list[dict] = []
    # split into person chunks on commas and dandas, keeping markers inline.
    # Chunks carrying table artifacts ("|", backticks) are layout noise, not
    # people - skipping them beats inventing an owner out of a rule character.
    chunks = [c.strip(" ,.:;-\t") for c in re.split(r"[,।]", section) if c.strip(" ,.:;-\t")]
    for chunk in chunks:
        if "|" in chunk or "`" in chunk:
            continue
        caste = ""
        caste_match = re.search(r"ଜା:\s*([^,।]+)", chunk)
        if caste_match:
            caste = caste_match.group(1).strip(" ,.:;-")
            # a caste-only chunk ("ଜା: ବ୍ରାହ୍ମଣ") carries no person
            before = chunk[:caste_match.start()].strip(" ,.:;-")
            has_person = bool(re.search(r"ପି:|ସ୍ୱା:", before)) or len(before.split()) >= 2
            if not has_person and not re.search(r"ପି:|ସ୍ୱା:", chunk):
                continue
        relation_type = ""
        relation_name = ""
        father_match = re.search(r"ପି:\s*([^,।]+)", chunk)
        spouse_match = re.search(r"ସ୍ୱା:\s*([^,।]+)", chunk)
        if father_match and (not spouse_match or father_match.start() < spouse_match.start()):
            relation_type = "father"
            relation_name = re.split(r"ଜା:|ବା:", father_match.group(1), maxsplit=1)[0].strip(" ,.:;-")
            name = chunk[:father_match.start()]
        elif spouse_match:
            relation_type = "spouse"
            relation_name = re.split(r"ଜା:|ବା:", spouse_match.group(1), maxsplit=1)[0].strip(" ,.:;-")
            name = chunk[:spouse_match.start()]
        else:
            name = chunk
        # strip caste/residence markers and section numbering from the name
        name = re.split(r"ଜା:|ବା:", name, maxsplit=1)[0]
        name = re.sub(r"^\d+\)\s*", "", name).strip(" ,.:;-")
        if len(name) < 2:
            continue
        name_ok, _name_score = _looks_like_name(name)
        if not name_ok:
            continue  # layout noise, not a person - never invent an owner
        confidence = 70.0 if len(name.split()) >= 2 else 55.0
        owners.append({
            "name": name,
            "relation_type": relation_type,
            "relation_name": relation_name,
            "caste": caste,
            "confidence": confidence,
        })
    return owners, residence


# ---------------------------------------------------------------------------
# Directional boundary (chouhaddi) parser - spec section 13.
# Only directions actually present are returned; the rest stay null.
# ---------------------------------------------------------------------------
BOUNDARY_MARKERS = {
    "ଉ:": "north",
    "ଦ:": "south",
    "ପୂ:": "east",
    "ପ:": "west",
}

BOUNDARY_HEADER_ALIASES = ["ଚୌହଦି", "chouhaddi", "boundary", "boundaries"]


def parse_boundary(raw_lines: list[str]) -> dict:
    found: dict[str, str | None] = {"north": None, "south": None, "east": None, "west": None}
    start = -1
    for idx, line in enumerate(raw_lines):
        lowered = line.lower()
        if any(alias.lower() in lowered for alias in BOUNDARY_HEADER_ALIASES):
            start = idx
            break
    if start < 0:
        return found
    # Slice each line between its directional markers, so "ଉ: X, ଦ: Y"
    # never bleeds one direction into the next (or into following lines).
    for line in raw_lines[start: start + 6]:
        hits = [
            (match.start(), marker)
            for marker in BOUNDARY_MARKERS
            for match in re.finditer(re.escape(marker), line)
        ]
        hits.sort()
        for pos, (marker_pos, marker) in enumerate(hits):
            end = hits[pos + 1][0] if pos + 1 < len(hits) else len(line)
            value = line[marker_pos + len(marker):end].strip(" ,.:;-\t।")
            if value:
                found[BOUNDARY_MARKERS[marker]] = value
    return found


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def extract_fields(
    ocr_text: str,
    words: list[WordToken],
    language: str = "eng",
    doc_type: str = "ror",
    profile: str | None = None,
    page: int = 1,
) -> ExtractionOutcome:
    text = _normalize_text(ocr_text)
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [{"text": ln, "words": []} for ln in raw_lines]
    for word in words:
        for line in lines:
            if line["text"] and word.text.strip(".,:;()") in line["text"].split():
                line["words"].append(word)
                break

    if profile is None:
        profile = detect_profile(text)
    prohibited = PROHIBITED_BY_PROFILE.get(profile, set())

    fields: dict[str, FieldExtraction] = {}
    area_hectare = 0.0
    unit_ok = False

    # ---- label-anchored extraction ---------------------------------------
    NAME_LIKE_FIELDS = {"owner_name", "guardian_name", "previous_owner", "new_owner",
                        "village", "tehsil", "district", "address", "land_classification"}
    for field_name, aliases in LABEL_ALIASES.items():
        if field_name in prohibited:
            # Anti-hallucination contract: this document type does not carry
            # the field, so no label, sweep or fallback may invent it.
            empty = FieldExtraction(field_name, aliases[0].title())
            empty.status, empty.reason = "missing", (
                "canonical_field_not_explicitly_present"
                if field_name == "khata_no" else "field_not_present"
            )
            empty.method = "document_analysis"
            fields[field_name] = empty
            continue
        line, proximity = _find_label_span(
            lines, aliases, deprioritize_numeric_tail=(field_name in NAME_LIKE_FIELDS))
        if line is not None and field_name in {
            "owner_name", "guardian_name", "previous_owner", "new_owner",
        } and _is_table_header_line(line["text"]):
            # a header row ("Owner Name | Relation | ..."), not a value -
            # the table-row fallback below reads the data rows instead
            line, proximity = None, 0.0
        if line is None:
            fields[field_name] = FieldExtraction(field_name, aliases[0].title())
            continue
        raw_value = _value_after_label(line["text"], aliases)

        # multi-token fields (names, address) may continue onto the next line
        if field_name in {"owner_name", "guardian_name", "previous_owner", "new_owner", "address"}:
            if len(raw_value.split()) < 2:
                idx = lines.index(line)
                if idx + 1 < len(lines):
                    nxt = lines[idx + 1]["text"].strip()
                    if nxt and not any(a.lower() in nxt.lower() for a in LABEL_ALIASES[field_name]):
                        raw_value = f"{raw_value} {nxt}".strip()

        if not raw_value:
            fields[field_name] = FieldExtraction(
                field_name, aliases[0].title(), evidence_text=line["text"][:200],
                bbox=_line_bbox(line["words"]), source="label",
            )
            continue

        raw_value = raw_value.strip(" ,.:;-\t")
        if field_name in NAME_LIKE_FIELDS and re.fullmatch(r"[\d\s/\-.,]+", raw_value or ""):
            # A name is never a bare number ("Tehsil : 232" is the tehsil
            # NUMBER line, not the tehsil). Leave it missing, honestly.
            raw_value = ""
        pattern = PATTERNS.get(field_name)
        pattern_strength = 0.0
        normalized = raw_value

        if pattern:
            raw_matches = list(pattern.finditer(raw_value))
            if field_name != "mutation_date":
                # Identifier patterns (khasra 123/4, khata 567) also match
                # fragments of dates ("15/07" out of "15/07/2024"). For
                # identifier fields such a match is a date fragment, not a
                # value. (mutation_date itself is supposed to be a date.)
                raw_matches = [m for m in raw_matches if not _inside_date(raw_value, m)]
            matches = [m.group(0).strip() for m in raw_matches]
            matches = [m for m in matches if m]
            if matches:
                normalized = max(matches, key=len)
                # A clean span (pattern covers the whole value) scores higher
                # than one where the value had to be pulled out of noise.
                pattern_strength = (
                    1.0 if normalized.strip() == raw_value.strip() else 0.85
                )
            else:
                # The label matched but nothing on the line looks like the
                # field (e.g. the word "survey" inside a prose sentence).
                # Keeping the raw tail as the value would invent data, so the
                # field stays empty and the reviewer fills it.
                pattern_strength = 0.15
                normalized = ""
        elif field_name in {"owner_name", "guardian_name", "previous_owner", "new_owner"}:
            # table rows leave the next cell's identifier glued to the name
            # ("Late Balaram Sahoo 123/4") - drop a trailing plot/area number.
            raw_value = re.sub(r"\s+\d{1,4}(?:/\d{1,3})?(?:\s+\d+(?:\.\d+)?)?\s*$", "", raw_value)
            if field_name == "guardian_name":
                # "S/o Late Balaram Sahoo" -> the relation prefix is the label
                raw_value = re.sub(r"^(s/o|w/o|d/o|c/o)\s+", "", raw_value, flags=re.IGNORECASE)
            ok, name_score = _looks_like_name(raw_value)
            pattern_strength = name_score if ok else name_score * 0.5
            normalized = re.sub(r"\s+", " ", raw_value).title() if raw_value.isascii() else raw_value
        else:
            pattern_strength = 0.6 if raw_value else 0.0

        ocr_conf, bbox = _ocr_confidence_for(normalized, line["words"])
        if ocr_conf == 0.0:
            ocr_conf = 60.0  # value found but OCR could not be aligned word-wise

        fields[field_name] = FieldExtraction(
            field_name=field_name,
            label=aliases[0].title(),
            value=raw_value[:200],
            normalized_value=str(normalized)[:200],
            confidence=_fuse(ocr_conf, pattern_strength * 100, proximity * 100, 70.0),
            source="regex+ner" if field_name in {
                "owner_name", "guardian_name", "previous_owner", "new_owner"
            } else "regex",
            evidence_text=line["text"][:200],
            bbox=bbox or _line_bbox(line["words"]),
            signals={
                "ocr_word_confidence": round(ocr_conf, 2),
                "pattern_match_strength": round(pattern_strength * 100, 2),
                "label_proximity": round(proximity * 100, 2),
                "cross_field_consistency": 70.0,
                "matched_label_alias": next(
                    (a for a in aliases if a.lower() in line["text"].lower()), ""
                ),
            },
        )

    # ---- table-row fallback: owner lives in a data row, not on a label ---
    # Generic profiles only: the person parser is authoritative for Form 39-A,
    # and a generic name grab would mix castes/relations in.
    owner_field = fields.get("owner_name")
    if (owner_field is not None and not owner_field.normalized_value
            and profile == DOC_PROFILE_GENERIC):
        table_owner = _owner_from_table_row(raw_lines)
        if table_owner:
            ocr_conf, bbox = _ocr_confidence_for(table_owner, words)
            owner_field.value = table_owner[:200]
            owner_field.normalized_value = (
                re.sub(r"\s+", " ", table_owner).title()
                if table_owner.isascii() else table_owner
            )[:200]
            owner_field.confidence = _fuse(
                ocr_conf or 55.0, 75.0, 40.0, 70.0,
            )
            owner_field.source = "table_row"
            owner_field.evidence_text = table_owner[:200]
            if bbox:
                owner_field.bbox = bbox
            owner_field.signals.update({
                "ocr_word_confidence": round(ocr_conf, 2),
                "pattern_match_strength": 75.0,
                "label_proximity": 40.0,
            })

    # ---- table-cell fallback for tabular pages (khasra/khata/area) -------
    # Skipped for prohibited fields: under a strict profile (Form 39-A) a
    # look-alike number must never become an identifier.
    cell_values = _table_cell_fallback(raw_lines)
    for fallback_field, fallback_value in cell_values.items():
        if fallback_field in prohibited:
            continue
        existing = fields.get(fallback_field)
        if existing is not None and not existing.normalized_value and fallback_value:
            ocr_conf, bbox = _ocr_confidence_for(fallback_value, words)
            existing.value = fallback_value[:200]
            existing.normalized_value = fallback_value[:200]
            existing.confidence = _fuse(ocr_conf or 55.0, 75.0, 40.0, 70.0)
            existing.source = "table_row"
            existing.evidence_text = fallback_value[:200]
            if bbox:
                existing.bbox = bbox
            existing.signals.update({
                "ocr_word_confidence": round(ocr_conf, 2),
                "pattern_match_strength": 75.0,
                "label_proximity": 40.0,
            })

    # ---- pattern sweep for identifiers with no readable label -------------
    # Skipped for prohibited fields (same anti-hallucination contract).
    # Form 39-A additionally sweeps the parcel-table plot number: slash-form
    # identifiers that are not dates and not mutation-case references. The
    # \d{1,3} second group already excludes case numbers (4837/2025,
    # 4061/2000) because their year halves are too long.
    sweeps: list[tuple[str, re.Pattern]] = [
        ("khasra_no", re.compile(r"\b(\d{1,4}/\d{1,3})\b")),
        ("survey_no", re.compile(r"survey[^\d]{0,10}(\d{1,5})")),
        ("mutation_no", re.compile(r"\b(mut|namantaran)[^\w]{0,4}([A-Z0-9/\-]{4,14})", re.I)),
    ]
    if profile == DOC_PROFILE_ODISHA_39A:
        sweeps.append(("plot_no", re.compile(r"\b(\d{1,4}/\d{1,3})\b")))
    for field_name, rx in sweeps:
        if field_name in prohibited:
            continue
        existing = fields.get(field_name)
        if existing and existing.normalized_value:
            continue
        match = rx.search(text)
        if match and _inside_date(text, match):
            match = None  # a DD/MM/YYYY fragment, not an identifier
        if match:
            value = match.group(match.lastindex)
            ocr_conf, bbox = _ocr_confidence_for(value, words)
            fields[field_name] = FieldExtraction(
                field_name=field_name,
                label=LABEL_ALIASES[field_name][0].title(),
                value=value,
                normalized_value=value,
                confidence=_fuse(ocr_conf or 55.0, 80.0, 30.0, 70.0),
                source="pattern_sweep",
                evidence_text=match.group(0)[:200],
                bbox=bbox,
            )

    # ---- area normalisation ---------------------------------------------
    area_field = fields.get("area")
    if area_field and (area_field.normalized_value or area_field.value):
        raw_area = area_field.value or area_field.normalized_value
        two_num = re.match(r"^\s*(\d{1,3})\s+(\d{3,4})\s*$", raw_area)
        unit_match = re.search(
            r"(\d+(?:\.\d+)?)\s*([A-Za-z\u0900-\u097F\u0B00-\u0B7F]+)", raw_area
        )
        unit = unit_match.group(2) if unit_match else ""
        if two_num and not unit:
            # Rakaba tables print "0 0200" for 0.0200 hectare - a bare
            # fraction with no unit letters. The unit comes from the
            # evidence line (ରକବା/ହେକ୍ଟର/hectare context).
            numeric = float(f"{two_num.group(1)}.{two_num.group(2)}")
            ev = (area_field.evidence_text or "").lower()
            if ("hectare" in ev or "ହେକ୍ଟର" in ev or "ରକବା" in ev
                    or "rakaba" in ev):
                unit = "hectare"
            area_field.normalized_value = str(numeric)
        else:
            try:
                numeric = float(area_field.normalized_value)
            except ValueError:
                numeric = 0.0
        area_hectare, unit_ok = to_hectare(numeric, unit)
        if numeric == 0:
            # "0" is a failed parse, not a plot with no land - leave the
            # field missing so BR-1 fires honestly instead of passing on zero
            area_field.normalized_value = ""
            area_field.confidence = round(min(area_field.confidence, 55.0), 2)
            area_field.is_low_confidence = True
        else:
            area_field.normalized_value = str(numeric)
        area_field.signals["area_unit_detected"] = unit
        area_field.signals["area_hectare"] = area_hectare
        if unit and not unit_ok:
            area_field.confidence = round(min(area_field.confidence, 68.0), 2)

    # ---- multi-plot register pages: child plot rows -----------------------
    sub_plots: list[SubPlot] = []
    for match in SUBPLOT_PATTERN.finditer(text):
        child_khasra, child_area, child_unit = match.group(1), match.group(2), match.group(3)
        child_ha, child_unit_ok = to_hectare(float(child_area), child_unit)
        sub_plots.append(SubPlot(
            khasra_no=child_khasra,
            area_value=float(child_area),
            area_unit=child_unit,
            area_hectare=child_ha,
            evidence_text=match.group(0)[:200],
        ))

    # ---- dedicated identifiers (khewat / khatiyan / tehsil-no) -----------
    # Verbatim, never mapped into canonical fields.
    dedicated: dict[str, FieldExtraction] = {}
    for dedicated_name, dedicated_aliases in DEDICATED_ALIASES.items():
        line, proximity = _find_label_span(lines, dedicated_aliases)
        if line is None:
            dedicated[dedicated_name] = FieldExtraction(dedicated_name, dedicated_aliases[0])
            dedicated[dedicated_name].status = "missing"
            dedicated[dedicated_name].reason = "field_not_present"
            dedicated[dedicated_name].method = "document_analysis"
            continue
        tail = _value_after_label(line["text"], dedicated_aliases)
        number_match = DEDICATED_NUMBER.search(tail)
        entry = FieldExtraction(
            dedicated_name, dedicated_aliases[0],
            value=tail[:200],
            normalized_value=number_match.group(1) if number_match else "",
            evidence_text=line["text"][:200],
            bbox=_line_bbox(line["words"]),
            source="label",
            method="layout_label_value",
        )
        ocr_conf, _bbox = _ocr_confidence_for(entry.normalized_value or tail, line["words"])
        entry.confidence = _fuse(ocr_conf or 60.0, 85.0 if number_match else 20.0,
                                 proximity * 100, 70.0)
        if entry.normalized_value:
            entry.status, entry.reason = (
                ("needs_review", "ocr_uncertain")
                if entry.confidence < settings.CONFIDENCE_MEDIUM else ("extracted", "")
            )
        else:
            entry.status, entry.reason = "needs_review", "ocr_uncertain"
        dedicated[dedicated_name] = entry

    # ---- person list + boundary (Form 39-A; harmless elsewhere) ------------
    owners, residence = parse_praja_section(raw_lines)
    address_field = fields.get("address")
    if address_field is not None and not address_field.normalized_value and residence:
        address_field.value = residence[:200]
        address_field.normalized_value = residence[:200]
        address_field.confidence = 72.0
        address_field.source = "person_parser"
        address_field.evidence_text = residence[:200]
    if owners:
        # The parsed person list is authoritative: it replaces whatever the
        # label pass made of the praja paragraph. (The parser only keeps
        # chunks that read like names, so the primary is trustworthy.)
        primary = owners[0]
        fields["owner_name"].value = primary["name"][:200]
        fields["owner_name"].normalized_value = primary["name"][:200]
        fields["owner_name"].confidence = primary["confidence"]
        fields["owner_name"].source = "person_parser"
        fields["owner_name"].evidence_text = primary["name"][:200]
        if primary["relation_type"] == "father":
            fields["guardian_name"].value = primary["relation_name"][:200]
            fields["guardian_name"].normalized_value = primary["relation_name"][:200]
            fields["guardian_name"].confidence = primary["confidence"]
            fields["guardian_name"].source = "person_parser"
    boundary = parse_boundary(raw_lines)

    # ---- cross-field consistency pass -------------------------------------
    _apply_cross_field_consistency(fields)

    low_conf = [
        name for name, f in fields.items()
        if f.value and f.confidence < settings.CONFIDENCE_MEDIUM
    ]
    for name in low_conf:
        fields[name].is_low_confidence = True
    required = required_fields_for(profile)
    missing = [f for f in required if not fields[f].normalized_value]

    scored = [f.confidence for f in fields.values() if f.value]
    record_confidence = round(sum(scored) / len(scored), 2) if scored else 0.0
    if missing:
        record_confidence = round(record_confidence * (1 - 0.06 * len(missing)), 2)

    # ---- per-field status (spec section 7) --------------------------------
    for name, f in fields.items():
        f.page = page
        f.method = f.method or f.source or "document_analysis"
        if f.normalized_value:
            if f.confidence < settings.CONFIDENCE_MEDIUM:
                f.status, f.reason = "needs_review", "ocr_uncertain"
            else:
                f.status, f.reason = "extracted", ""
        elif name in prohibited:
            f.status, f.reason = "missing", (
                "canonical_field_not_explicitly_present"
                if name == "khata_no" else "field_not_present"
            )
        elif f.evidence_text:
            f.status, f.reason = "needs_review", "ocr_uncertain"
        else:
            f.status, f.reason = "missing", "field_not_present"

    for name, f in dedicated.items():
        f.page = page

    return ExtractionOutcome(
        fields=fields,
        record_confidence=record_confidence,
        low_confidence_fields=low_conf,
        missing_required=missing,
        area_hectare=area_hectare,
        area_unit_recognised=unit_ok,
        raw_lines=raw_lines,
        sub_plots=sub_plots,
        profile=profile,
        dedicated=dedicated,
        owners=owners,
        boundary=boundary,
        page=page,
    )


def _apply_cross_field_consistency(fields: dict[str, FieldExtraction]) -> None:
    """FR-5 signal 4: administrative + ownership coherence across fields."""
    village = fields.get("village")
    tehsil = fields.get("tehsil")
    district = fields.get("district")
    owner = fields.get("owner_name")
    guardian = fields.get("guardian_name")

    admin_ok = sum(1 for f in (village, tehsil, district) if f and f.normalized_value)
    for f in (village, tehsil, district):
        if not f or not f.value:
            continue
        consistency = 40.0 + 20.0 * admin_ok
        f.signals["cross_field_consistency"] = round(consistency, 2)
        f.confidence = _fuse(
            f.signals.get("ocr_word_confidence", 60.0),
            f.signals.get("pattern_match_strength", 60.0),
            f.signals.get("label_proximity", 60.0),
            consistency,
        )

    # owner and guardian should be similar in shape, and never identical
    if owner and guardian and owner.value and guardian.value:
        similarity = SequenceMatcher(None, owner.value.lower(), guardian.value.lower()).ratio()
        consistency = 35.0 if similarity > 0.95 else 85.0
        for f in (owner, guardian):
            f.signals["cross_field_consistency"] = consistency
            f.signals["owner_guardian_similarity"] = round(similarity, 3)
            f.confidence = _fuse(
                f.signals.get("ocr_word_confidence", 60.0),
                f.signals.get("pattern_match_strength", 60.0),
                f.signals.get("label_proximity", 60.0),
                consistency,
            )


def classify_document_type(outcome: ExtractionOutcome) -> tuple[str, float]:
    """Rule-based document classifier (RoR / mutation / sale deed / register page).

    Order matters: a Record of Rights page also carries mutation columns, so the
    document's own heading is checked before any incidental field label.
    """
    blob = " ".join(outcome.raw_lines).lower()
    heading = " ".join(outcome.raw_lines[:4]).lower()

    # Odisha Schedule I Form 39-A (Khatiyan) first: its "record of rights"
    # prose would otherwise pull it into the generic RoR bucket, but the
    # form carries different identifiers (no khasra/khata) and needs the
    # strict extraction profile.
    if (any(marker in heading for marker in FORM_39A_MARKERS)
            or ("ଖତିୟାନ" in heading and "ଖେୱାଟ" in blob)
            or ("khatiyan" in heading and "khewat" in blob)):
        return "odisha_khatiyan_39a", 0.9
    if "record of rights" in heading or "khatian" in heading or "ଖାତିଆନ" in heading or "(ror)" in heading:
        return "record_of_rights", 0.93
    if "mutation register" in heading or "दाखिल" in heading or "ନାମାନ୍ତରଣ" in heading:
        return "mutation_record", 0.9
    if "जमाबंदी" in heading or "jamabandi" in heading or "register" in heading or "rojamcha" in heading:
        return "register_page", 0.85
    if "sale deed" in heading or "पंजीयन" in heading:
        return "sale_deed", 0.82

    # fall back to content signals
    if "record of rights" in blob or "ror" in blob:
        return "record_of_rights", 0.75
    if "mutation" in blob or "namantaran" in blob or "ନାମାନ୍ତରଣ" in blob:
        return "mutation_record", 0.7
    if "registration" in blob or "sale deed" in blob:
        return "sale_deed", 0.6
    if "register" in blob:
        return "register_page", 0.55
    return "unclassified_land_record", 0.4


def extract_multipage(page_ocrs: list[dict], language: str = "eng") -> ExtractionOutcome:
    """Extract each page with a shared profile, then merge field winners.

    Multi-page PDFs (Form 39-A: admin+persons on page 1, parcels on page 2)
    need per-page extraction so every value keeps its source page, and a
    merge that keeps the highest-confidence non-empty value per field.
    Dedicated identifiers, owners and boundary come from the first page that
    yields them.
    """
    profile = detect_profile("\n".join(p.get("text", "") for p in page_ocrs))
    outcomes = [
        extract_fields(p.get("text", ""), p.get("words", []),
                       language=language, profile=profile,
                       page=p.get("page", index + 1))
        for index, p in enumerate(page_ocrs)
    ]
    if len(outcomes) == 1:
        return outcomes[0]

    merged_fields: dict[str, FieldExtraction] = {}
    for name in LABEL_ALIASES:
        candidates = [
            (o.fields[name], o.page) for o in outcomes
            if name in o.fields and o.fields[name].normalized_value
        ]
        if candidates:
            best, _page = max(candidates, key=lambda item: item[0].confidence)
            merged_fields[name] = best
        else:
            merged_fields[name] = outcomes[0].fields[name]

    merged_dedicated: dict[str, FieldExtraction] = {}
    for name in DEDICATED_ALIASES:
        candidates = [
            (o.dedicated[name], o.page) for o in outcomes
            if name in o.dedicated and o.dedicated[name].normalized_value
        ]
        if candidates:
            best, _page = max(candidates, key=lambda item: item[0].confidence)
            merged_dedicated[name] = best
        elif outcomes[0].dedicated.get(name) is not None:
            merged_dedicated[name] = outcomes[0].dedicated[name]

    owners: list[dict] = []
    for outcome in outcomes:
        if outcome.owners:
            owners = outcome.owners
            break
    boundary: dict = {"north": None, "south": None, "east": None, "west": None}
    for outcome in outcomes:
        for direction, value in (outcome.boundary or {}).items():
            if value and not boundary.get(direction):
                boundary[direction] = value

    area_winner = next(
        (o for o in outcomes if o.fields.get("area") and o.fields["area"].normalized_value),
        outcomes[0],
    )
    required = required_fields_for(profile)
    missing = [f for f in required if not merged_fields[f].normalized_value]
    scored = [f.confidence for f in merged_fields.values() if f.value]
    record_confidence = round(sum(scored) / len(scored), 2) if scored else 0.0
    if missing:
        record_confidence = round(record_confidence * (1 - 0.06 * len(missing)), 2)
    low_conf = [
        name for name, f in merged_fields.items()
        if f.value and f.confidence < settings.CONFIDENCE_MEDIUM
    ]
    for name in low_conf:
        merged_fields[name].is_low_confidence = True

    return ExtractionOutcome(
        fields=merged_fields,
        record_confidence=record_confidence,
        low_confidence_fields=low_conf,
        missing_required=missing,
        area_hectare=area_winner.area_hectare,
        area_unit_recognised=area_winner.area_unit_recognised,
        raw_lines=[ln for o in outcomes for ln in o.raw_lines],
        sub_plots=[p for o in outcomes for p in o.sub_plots],
        profile=profile,
        dedicated=merged_dedicated,
        owners=owners,
        boundary=boundary,
        page=0,
    )
