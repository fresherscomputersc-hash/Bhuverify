"""CV preprocessing + OCR smoke tests (SRS FR-2, FR-3)."""
import time

import cv2
import numpy as np
import pytest

from app.config import settings
from app.sample_documents import generate_samples
from app.services.cv_preprocess import deskew, estimate_skew_angle, preprocess_image
from app.services.ocr_service import TESSERACT_AVAILABLE, run_ocr


def _synthetic_text_page(skew_deg=0.0):
    img = np.full((900, 1200, 3), 255, np.uint8)
    for i in range(12):
        cv2.putText(img, f"Khasra Number 118/{i} Owner Prafulla Kumar Sahoo",
                    (60, 80 + i * 65), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    if skew_deg:
        h, w = img.shape[:2]
        m = cv2.getRotationMatrix2D((w // 2, h // 2), skew_deg, 1.0)
        img = cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))
    return img


@pytest.mark.parametrize("skew", [1.0, 2.0, -3.0, 5.0, -2.4])
def test_deskew_recovers_a_known_skew(tmp_path, skew):
    img = _synthetic_text_page(skew)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    estimated = estimate_skew_angle(gray)
    # estimator returns the correction angle; sign is opposite the applied skew
    assert abs(abs(estimated) - abs(skew)) < 1.0
    straight, applied = deskew(gray)
    assert straight.shape == gray.shape
    assert abs(applied) > 0.0


def test_preprocess_returns_stats(tmp_path):
    p = tmp_path / "page.png"
    cv2.imwrite(str(p), _synthetic_text_page(2.0))
    out = preprocess_image(p)
    assert out["enhanced_path"] and out["preview_path"]
    assert out["stats"]["region_total"] >= 0
    assert "deskew_angle_deg" in out["stats"]


@pytest.mark.skipif(not TESSERACT_AVAILABLE, reason="tesseract not installed")
def test_ocr_reads_the_printed_demo_page():
    samples = generate_samples()
    clean = next(s for s in samples if s["filename"] == "sample_01_ror_clean.png")
    started = time.perf_counter()
    # eng-only is fast and sufficient for the printed Latin demo page; the
    # combined eng+hin+ori pass is ~2x slower on Windows and covered by worker tests.
    result = run_ocr(clean["path"], language="eng", psm=4)
    elapsed = time.perf_counter() - started
    assert result.word_count > 15
    assert elapsed < settings.PERF_OCR_EXTRACTION_BUDGET_S + 5.0  # warm-up tolerance
    blob = result.text.lower()
    assert "khasra" in blob or "118" in result.text
