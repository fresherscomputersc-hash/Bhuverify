"""
BhuVerify - application configuration.

Central place for every tunable the prototype exposes. Values here map 1:1 to
the targets published in the SRS (sections 7.1 Performance, 5.2 Technology
Stack) so that the running system can be audited against the specification.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"          # raw scanned files (SRS 6.2 object store)
PROCESSED_DIR = DATA_DIR / "processed"     # CV-enhanced images / region crops
GEOJSON_DIR = DATA_DIR / "geojson"         # sample cadastral layer (FR-9)
REPORTS_DIR = DATA_DIR / "reports"         # exported MIS reports
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "bhuverify.db"

for _d in (DATA_DIR, UPLOAD_DIR, PROCESSED_DIR, GEOJSON_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


# ---------------------------------------------------------------------------
# Confidence bands (SRS FR-5)
# ---------------------------------------------------------------------------
CONFIDENCE_HIGH = 90.0     # 90-100%  high confidence
CONFIDENCE_MEDIUM = 70.0   # 70-89%   medium confidence
                         # < 70%   low confidence -> human review required

# ---------------------------------------------------------------------------
# Performance budgets (SRS 7.1) - asserted by the test-suite, not just claimed
# ---------------------------------------------------------------------------
PERF_OCR_EXTRACTION_BUDGET_S = 10.0   # prototype: < 10 s / document
PERF_VALIDATION_BUDGET_S = 1.0        # < 1 s / record
PERF_SEARCH_BUDGET_S = 2.0            # < 2 s


@dataclass(frozen=True)
class Settings:
    # Confidence bands and performance budgets live on the settings object so
    # every module reads them from one place (the module-level constants above
    # remain as aliases for imports).
    CONFIDENCE_HIGH: float = CONFIDENCE_HIGH
    CONFIDENCE_MEDIUM: float = CONFIDENCE_MEDIUM
    PERF_OCR_EXTRACTION_BUDGET_S: float = PERF_OCR_EXTRACTION_BUDGET_S
    PERF_VALIDATION_BUDGET_S: float = PERF_VALIDATION_BUDGET_S
    PERF_SEARCH_BUDGET_S: float = PERF_SEARCH_BUDGET_S

    app_name: str = "BhuVerify"
    app_subtitle: str = "Intelligent Land Record Digitization and Validation System"
    problem_statement: str = "SIH26018"
    theme: str = "Smart Automation"
    team: str = "Merge Conflict"
    version: str = "0.1.0-prototype"

    database_url: str = field(
        default_factory=lambda: os.getenv("BHUVERIFY_DB", f"sqlite:///{DB_PATH}")
    )

    # OCR languages enabled in this deployment (SRS FR-3). Land-record pages
    # routinely mix scripts on one page (Odia body text, English headers and
    # numerals, occasional Hindi), so the default is the combined Tesseract
    # language string, not a single guessed pack - one OCR pass reads all
    # three scripts and each word is tagged with its real script afterwards.
    ocr_languages: tuple[str, ...] = ("eng", "hin", "ori")
    default_ocr_language: str = "eng+hin+ori"

    # Administrative hierarchy used by BR-7 (Village-Tehsil Master Check).
    # Seeded from the LGD-style master dataset in app/seed_data.py.
    district: str = "Khordha"
    state: str = "Odisha"

    # GIS
    geojson_file: str = "sample_cadastral.geojson"
    gis_area_tolerance_pct: float = 15.0   # BR-9 spatial mismatch threshold

    # Notification thresholds
    review_pending_hours_alert: int = 48

    # Security
    token_ttl_minutes: int = 480
    session_secret: str = field(
        default_factory=lambda: os.getenv("BHUVERIFY_SECRET", "prototype-only-secret")
    )

    # Continuous learning
    enable_correction_capture: bool = True


settings = Settings()
