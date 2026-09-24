#!/usr/bin/env python3
"""Batch-run CV preprocess + OCR + extraction over sample/ and dump a report.

No DB writes: runs the pipeline stages directly so extractor gaps can be
compared file by file. Usage: python tools/analyze_extraction.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.config import PROCESSED_DIR  # noqa: E402
from app.services.cv_preprocess import preprocess_image  # noqa: E402
from app.services.extraction import classify_document_type, extract_fields  # noqa: E402
from app.services.ocr_service import WordToken, run_ocr  # noqa: E402
from app.services.worker import pdf_to_png  # noqa: E402

CACHE_DIR = BASE / "data" / "reports" / "ocr_cache"

SKIP = {"download.pdf"}  # placeholder names, not real samples


def _cached_ocr(key: str, enhanced_path: str):
    """Reuse a cached OCR pass so extractor-only edits iterate in seconds."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        print("  ocr cache hit", flush=True)
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        words = [WordToken(**w) for w in payload["words"]]
        from app.services.ocr_service import OcrResult

        return OcrResult(
            text=payload["text"], words=words, language=payload["language"],
            engine=payload["engine"], mode=payload.get("mode", "printed"),
            psm=payload.get("psm", 4),
            mean_confidence=payload.get("mean_confidence", 0.0),
            word_count=len(words),
        )
    ocr = run_ocr(enhanced_path, language="eng+hin+ori", psm=4)
    if ocr.word_count < 15:
        alt = run_ocr(enhanced_path, language="eng+hin+ori", psm=11)
        if alt.word_count > ocr.word_count:
            ocr = alt
    cache_file.write_text(json.dumps({
        "text": ocr.text, "language": ocr.language, "engine": ocr.engine,
        "mode": ocr.mode, "psm": ocr.psm,
        "mean_confidence": ocr.mean_confidence,
        "words": [vars(w) for w in ocr.words],
    }, ensure_ascii=False), encoding="utf-8")
    return ocr


def analyze(path: Path) -> dict:
    print(f"=== {path.name} ===", flush=True)
    src = path
    if path.suffix.lower() == ".pdf":
        src = pdf_to_png(path)
        # pdf_to_png writes into PROCESSED_DIR; point a stable copy at samples
        print(f"  pdf rendered -> {src}", flush=True)
    pre = preprocess_image(src)
    print(f"  deskew={pre['stats']['deskew_angle_deg']} regions={pre['stats']['region_total']}", flush=True)
    ocr = _cached_ocr(path.stem, pre["enhanced_path"])
    print(f"  ocr words={ocr.word_count} mean_conf={ocr.mean_confidence:.1f}", flush=True)
    outcome = extract_fields(ocr.text, ocr.words, language=ocr.language)
    doc_type, conf = classify_document_type(outcome)
    fields = {
        name: {
            "value": f.value[:120],
            "normalized": f.normalized_value[:120],
            "conf": f.confidence,
        }
        for name, f in outcome.fields.items()
    }
    return {
        "file": path.name,
        "doc_type": f"{doc_type} ({conf:.0%})",
        "record_confidence": outcome.record_confidence,
        "missing_required": outcome.missing_required,
        "low_confidence": outcome.low_confidence_fields,
        "area_hectare": outcome.area_hectare,
        "ocr_text": ocr.text[:3000],
        "fields": fields,
    }


def main() -> None:
    sample_dir = BASE / "sample"
    files = sorted(p for p in sample_dir.iterdir() if p.is_file())
    report = []
    for path in files:
        try:
            report.append(analyze(path))
        except Exception as exc:  # noqa: BLE001 - report must complete per file
            print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)
            report.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
    out = BASE / "data" / "reports" / "extraction_analysis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nreport -> {out}", flush=True)


if __name__ == "__main__":
    main()
