"""
Document processing worker (SRS 5.3 async processing queue).

Runs the full pipeline for one document:

    preprocess -> OCR/HTR -> structured extraction -> confidence scoring
      -> GIS linking -> business rule validation -> cross-database checks
      -> reviewer queue + audit trail + notifications

The prototype uses an in-process thread queue so `docker-compose up` (or a bare
`uvicorn`) is the whole runtime. The stage names and status transitions are the
same ones the pilot tier publishes to Redis/Celery.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

import cv2
from sqlalchemy import select

from app.config import PROCESSED_DIR, settings
from app.database import session_scope
from app.models import (
    ActionType,
    CrossCheckResult,
    DocumentStatus,
    ExtractionResult,
    LandRecord,
    Notification as _NotifUnused,  # noqa: F401  (keeps import graph explicit)
    ProcessingJob,
    RecordStatus,
    Severity,
    SourceDocument,
    SpatialGeometry,
)
from app.services import audit
from app.services.cv_preprocess import preprocess_image
from app.services.extraction import classify_document_type, extract_fields, to_hectare
from app.services.gis_service import link_record
from app.services.ocr_service import crop_region, run_htr, run_ocr
from app.services.validation import RecordContext, highest_severity, run_all_rules
from app.services import external_adapters, notifications
from app.models import Role

_STAGE_PROGRESS = {
    DocumentStatus.PREPROCESSING: 15,
    DocumentStatus.OCR_RUNNING: 45,
    DocumentStatus.EXTRACTING: 65,
    DocumentStatus.VALIDATING: 85,
    DocumentStatus.COMPLETED: 100,
}

_QUEUE: "list[int]" = []
_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_STOP = threading.Event()


# ---------------------------------------------------------------------------
# PDF handling
# ---------------------------------------------------------------------------
def pdf_to_png(pdf_path: Path) -> Path:
    """Render the first PDF page to PNG (300 DPI) for the CV/OCR pipeline."""
    out_path = PROCESSED_DIR / f"{pdf_path.stem}_page1.png"
    try:  # pure-python wheel, works on Windows with no system packages
        import pypdfium2 as pdfium  # type: ignore

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            page = pdf[0]
            bitmap = page.render(scale=300 / 72)
            bitmap.to_pil().save(str(out_path), "PNG")
            return out_path
        finally:
            pdf.close()
    except Exception:
        pass
    poppler = shutil.which("pdftoppm")
    if poppler:
        subprocess.run(
            [poppler, "-r", "300", "-png", "-f", "1", "-l", "1", str(pdf_path),
             str(PROCESSED_DIR / pdf_path.stem)],
            check=True, capture_output=True, timeout=120,
        )
        produced = sorted(PROCESSED_DIR.glob(f"{pdf_path.stem}-*.png"))
        if produced:
            produced[0].rename(out_path)
            return out_path
    try:  # fallback: pdf2image if present (still needs poppler binaries)
        from pdf2image import convert_from_path  # type: ignore

        pages = convert_from_path(str(pdf_path), dpi=300, first_page=1, last_page=1)
        if pages:
            pages[0].save(str(out_path), "PNG")
            return out_path
    except Exception:
        pass
    raise RuntimeError(
        "PDF rendering needs pypdfium2 (`pip install pypdfium2`) or "
        "poppler-utils (`pdftoppm`). "
        "Upload a PNG/JPEG/TIFF scan instead."
    )


# ---------------------------------------------------------------------------
# OCR orchestration: printed OCR for the page, HTR for handwritten regions
# ---------------------------------------------------------------------------
def _ocr_document(enhanced_path: str, layout: dict, language: str) -> dict:
    printed = run_ocr(enhanced_path, language=language, psm=4)
    if printed.word_count < 15:
        # Sparse layout (torn page, register with wide gaps): retry as sparse text.
        alt = run_ocr(enhanced_path, language=language, psm=11)
        if alt.word_count > printed.word_count:
            printed = alt

    handwritten_parts: list[dict] = []
    htr_words = []
    for region in layout.get("regions", []):
        if region["label"] != "handwritten_text":
            continue
        try:
            crop = crop_region(enhanced_path, region)
        except ValueError:
            continue
        if crop is None or crop.size == 0:
            continue
        crop_path = PROCESSED_DIR / f"{Path(enhanced_path).stem}_hw_{region['y']}.png"
        cv2.imwrite(str(crop_path), crop)
        htr = run_htr(crop_path, language=language)
        htr_words.extend(htr.words)
        handwritten_parts.append({
            "bbox": region,
            "text": htr.text,
            "confidence": round(htr.mean_confidence, 2),
            "word_count": htr.word_count,
            "engine": htr.engine,
            "warnings": htr.warnings,
        })

    all_words = list(printed.words) + htr_words
    combined_text = printed.text
    if handwritten_parts:
        combined_text = printed.text + "\n\n" + "\n".join(
            part["text"] for part in handwritten_parts if part["text"]
        )
    confidences = [w.confidence for w in all_words]
    return {
        "text": combined_text.strip(),
        "words": all_words,
        "language": printed.language,
        "engine": "tesseract+opencv" if not handwritten_parts else "tesseract+opencv+htr",
        "mode": "printed+handwritten" if handwritten_parts else "printed",
        "mean_confidence": round(sum(confidences) / len(confidences), 2) if confidences else 0.0,
        "word_count": len(all_words),
        "low_confidence_words": sum(1 for c in confidences if c < settings.CONFIDENCE_MEDIUM),
        "latency_ms": printed.latency_ms + sum(p.get("latency_ms", 0) for p in handwritten_parts),
        "handwritten_regions": handwritten_parts,
    }


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def process_document(document_id: int) -> dict:
    started = time.perf_counter()
    summary: dict = {"document_id": document_id}

    with session_scope() as db:
        document = db.get(SourceDocument, document_id)
        if document is None:
            return {"document_id": document_id, "error": "document not found"}

        job = db.execute(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id)
            .order_by(ProcessingJob.id.desc()).limit(1)
        ).scalar_one_or_none()
        if job:
            job.stage = "running"
            job.started_at = audit.utcnow()
            job.attempts += 1

        try:
            # ---------------- Stage 1: CV preprocessing (FR-2) -------------
            document.status = DocumentStatus.PREPROCESSING
            document.progress_pct = _STAGE_PROGRESS[DocumentStatus.PREPROCESSING]
            db.commit()

            source_path = Path(document.stored_path)
            image_path = source_path
            if source_path.suffix.lower() == ".pdf":
                image_path = pdf_to_png(source_path)

            import uuid as _uuid_pre

            run_tag = _uuid_pre.uuid4().hex[:8]
            pre = preprocess_image(image_path, tag=run_tag)
            # Drop the previous run's files: with unique names per run the
            # served files are immutable, so stale ones are just disk waste.
            for old in (document.enhanced_path, document.preview_path):
                if old and old not in (pre["enhanced_path"], pre["preview_path"]):
                    try:
                        Path(old).unlink(missing_ok=True)
                    except OSError:
                        pass
            document.enhanced_path = pre["enhanced_path"]
            document.preview_path = pre["preview_path"]
            document.layout_json = pre["layout"]
            document.preprocessing_stats = pre["stats"]
            audit.log_action(
                db, ActionType.PREPROCESSING_DONE, actor_label="cv-service",
                entity_type="document", entity_id=document.doc_id, document_id=document.id,
                detail=(
                    f"deskew {pre['stats']['deskew_angle_deg']}deg, "
                    f"contrast gain {pre['stats']['contrast_gain_pct']}%, "
                    f"{pre['stats']['region_total']} regions "
                    f"({pre['stats']['regions']})"
                ),
            )

            # ---------------- Stage 2: OCR / HTR (FR-3) --------------------
            document.status = DocumentStatus.OCR_RUNNING
            document.progress_pct = _STAGE_PROGRESS[DocumentStatus.OCR_RUNNING]
            db.commit()

            ocr_started = time.perf_counter()
            ocr = _ocr_document(pre["enhanced_path"], pre["layout"], document.language)
            document.ocr_text = ocr["text"]
            document.ocr_word_count = ocr["word_count"]
            document.ocr_mean_confidence = ocr["mean_confidence"]
            document.ocr_engine = ocr["engine"]
            document.ocr_language = ocr["language"]
            document.ocr_latency_ms = int((time.perf_counter() - ocr_started) * 1000)
            document.preprocessing_stats["handwritten_regions"] = ocr["handwritten_regions"]
            audit.log_action(
                db, ActionType.OCR_EXECUTED, actor_label=ocr["engine"],
                entity_type="document", entity_id=document.doc_id, document_id=document.id,
                ai_confidence=ocr["mean_confidence"],
                detail=(
                    f"{ocr['word_count']} words, mean confidence {ocr['mean_confidence']:.1f}%, "
                    f"mode {ocr['mode']}, lang {ocr['language']}, "
                    f"{document.ocr_latency_ms} ms"
                ),
            )

            # ---------------- Stage 3: structured extraction (FR-4/5) ------
            document.status = DocumentStatus.EXTRACTING
            document.progress_pct = _STAGE_PROGRESS[DocumentStatus.EXTRACTING]
            db.commit()

            outcome = extract_fields(ocr["text"], ocr["words"], language=ocr["language"])
            doc_type, doc_type_confidence = classify_document_type(outcome)

            record = document.record
            if record is None:
                import uuid

                record = LandRecord(
                    record_id=f"LR-{uuid.uuid4().hex[:10].upper()}",
                    document_id=document.id,
                )
                db.add(record)
                db.flush()

            def set_field(name: str, value: str) -> None:
                setattr(record, name, value)

            value = outcome.fields
            set_field("khasra_no", value.get("khasra_no").normalized_value if value.get("khasra_no") else "")
            set_field("khata_no", value.get("khata_no").normalized_value if value.get("khata_no") else "")
            set_field("survey_no", value.get("survey_no").normalized_value if value.get("survey_no") else "")
            set_field("plot_no", value.get("plot_no").normalized_value if value.get("plot_no") else "")
            set_field("village", value.get("village").normalized_value if value.get("village") else "")
            set_field("tehsil", value.get("tehsil").normalized_value if value.get("tehsil") else "")
            set_field("district", value.get("district").normalized_value if value.get("district") else settings.district)
            set_field("state", settings.state)
            set_field("owner_name", value.get("owner_name").normalized_value if value.get("owner_name") else "")
            set_field("guardian_name", value.get("guardian_name").normalized_value if value.get("guardian_name") else "")
            set_field("address", value.get("address").normalized_value if value.get("address") else "")
            set_field("land_classification", value.get("land_classification").normalized_value
                      if value.get("land_classification") else "")
            set_field("boundaries", value.get("boundaries").value if value.get("boundaries") else "")
            set_field("mutation_no", value.get("mutation_no").normalized_value if value.get("mutation_no") else "")
            set_field("mutation_date", value.get("mutation_date").normalized_value
                      if value.get("mutation_date") else "")
            set_field("registration_no", value.get("registration_no").normalized_value
                      if value.get("registration_no") else "")
            set_field("previous_owner", value.get("previous_owner").normalized_value
                      if value.get("previous_owner") else "")
            set_field("new_owner", value.get("new_owner").normalized_value if value.get("new_owner") else "")
            set_field("document_type_label", doc_type)

            area_field = value.get("area")
            if area_field and area_field.normalized_value:
                try:
                    record.area_value = float(area_field.normalized_value)
                except ValueError:
                    record.area_value = 0.0
                unit = area_field.signals.get("area_unit_detected", "")
                record.area_unit = unit
                record.area_hectare = outcome.area_hectare
            else:
                record.area_value, record.area_unit, record.area_hectare = 0.0, "", 0.0

            # parent khasra for BR-2 ("khasra 118/4" -> parent "118")
            if "/" in record.khasra_no:
                record.parent_khasra_no = record.khasra_no.split("/")[0]

            record.record_confidence = outcome.record_confidence

            # persist field-level artifacts
            for existing in list(record.extractions):
                db.delete(existing)
            db.flush()
            from app.master_data import canonical_classification

            for field_name, extraction in value.items():
                normalised = extraction.normalized_value
                if field_name == "land_classification" and normalised:
                    normalised = canonical_classification(normalised)
                    extraction.normalized_value = normalised
                row = ExtractionResult(
                    record_id=record.id,
                    field_name=field_name,
                    field_label=extraction.label,
                    value=extraction.value,
                    normalized_value=normalised,
                    confidence=extraction.confidence,
                    source=extraction.source,
                    evidence_text=extraction.evidence_text,
                    bbox=extraction.bbox,
                    is_low_confidence=(
                        bool(extraction.value) and extraction.confidence < settings.CONFIDENCE_MEDIUM
                    ),
                )
                db.add(row)
            db.flush()

            if record.land_classification:
                record.land_classification = canonical_classification(record.land_classification)

            audit.log_action(
                db, ActionType.FIELD_EXTRACTED, actor_label="extraction-service",
                entity_type="record", entity_id=record.record_id, document_id=document.id,
                ai_confidence=record.record_confidence,
                detail=(
                    f"{len([f for f in value.values() if f.value])} fields extracted, "
                    f"document type '{doc_type}' ({doc_type_confidence:.0%}), "
                    f"low-confidence fields: {outcome.low_confidence_fields or 'none'}"
                ),
            )
            audit.log_action(
                db, ActionType.CONFIDENCE_GENERATED, actor_label="confidence-engine",
                entity_type="record", entity_id=record.record_id, document_id=document.id,
                ai_confidence=record.record_confidence,
                detail=(
                    f"record confidence {record.record_confidence:.1f}% "
                    f"({len(outcome.low_confidence_fields)} field(s) below "
                    f"{settings.CONFIDENCE_MEDIUM:.0f}%)"
                ),
            )

            # ---------------- Stage 4: GIS linking (FR-9) ------------------
            gis = link_record(
                khasra_no=record.khasra_no,
                survey_no=record.survey_no,
                plot_no=record.plot_no,
                record_area_ha=record.area_hectare,
            )
            record.gis_linked = bool(gis["linked"])
            existing_geometry = record.geometry
            if existing_geometry:
                db.delete(existing_geometry)
                db.flush()
            if gis["linked"]:
                import uuid as _uuid

                db.add(SpatialGeometry(
                    record_id=record.id,
                    geometry_id=f"GEO-{_uuid.uuid4().hex[:10].upper()}",
                    plot_key=gis["plot_key"],
                    geojson=gis["geojson"],
                    area_sqm=round(gis["polygon_area_ha"] * 10_000, 2),
                    source_map_ref=gis["source_map_ref"],
                    match_type=gis["match_type"],
                    spatial_mismatch=bool(gis["spatial_mismatch"]),
                    area_delta_pct=gis["area_delta_pct"],
                ))
                audit.log_action(
                    db, ActionType.GIS_LINKED, actor_label="gis-service",
                    entity_type="record", entity_id=record.record_id, document_id=document.id,
                    detail=gis["message"],
                )
            else:
                audit.log_action(
                    db, ActionType.GIS_LINKED, actor_label="gis-service",
                    entity_type="record", entity_id=record.record_id, document_id=document.id,
                    detail=gis["message"],
                )

            # ---------------- Stage 5: validation (FR-6/7) -----------------
            document.status = DocumentStatus.VALIDATING
            document.progress_pct = _STAGE_PROGRESS[DocumentStatus.VALIDATING]
            db.commit()

            for existing_discrepancy in list(record.discrepancies):
                db.delete(existing_discrepancy)
            db.flush()

            parent_area = None
            if record.parent_khasra_no:
                parent = db.execute(
                    select(LandRecord).where(LandRecord.khasra_no == record.parent_khasra_no)
                    .where(LandRecord.id != record.id).limit(1)
                ).scalar_one_or_none()
                if parent:
                    parent_area = parent.area_hectare

            ctx = RecordContext(
                db=db,
                record=record,
                fields={
                    name: {
                        "value": item.value,
                        "normalized_value": item.normalized_value,
                        "confidence": item.confidence,
                        "bbox": item.bbox,
                        "evidence_text": item.evidence_text,
                        "signals": item.signals,
                    }
                    for name, item in value.items()
                },
                geometry=gis,
                parent_area_hectare=parent_area,
                sub_plots=outcome.sub_plots,
                document_id=document.id,
                doc_id=document.doc_id,
            )
            outcomes = run_all_rules(ctx)

            import uuid as _uuid2

            discrepancies = []
            for rule_outcome in outcomes:
                audit.log_action(
                    db, ActionType.VALIDATION_RULE_EXECUTED, actor_label="validation-engine",
                    entity_type="record", entity_id=record.record_id, document_id=document.id,
                    detail=(
                        f"{rule_outcome.rule_id} {rule_outcome.rule_name}: "
                        f"{'PASS' if rule_outcome.passed else 'FAIL'} "
                        f"({len(rule_outcome.discrepancies)} finding(s), "
                        f"{rule_outcome.latency_ms:.1f} ms)"
                        + (f" - {rule_outcome.detail}" if rule_outcome.detail else "")
                    ),
                )
                for discrepancy in rule_outcome.discrepancies:
                    row = _new_discrepancy(record.id, discrepancy)
                    db.add(row)
                    discrepancies.append(discrepancy)
                    audit.log_action(
                        db, ActionType.DISCREPANCY_CREATED, actor_label="validation-engine",
                        entity_type="discrepancy", entity_id=row.id if row.id else "",
                        document_id=document.id,
                        detail=f"{discrepancy.rule_id} [{discrepancy.severity}] {discrepancy.message}",
                    )
            db.flush()

            record.discrepancy_count = len(discrepancies)
            record.highest_severity = highest_severity(discrepancies)

            # ---------------- Stage 6: cross-database checks (FR-8) --------
            for existing_check in list(record.cross_db_results):
                db.delete(existing_check)
            db.flush()
            parties = [p for p in (record.previous_owner, record.new_owner, record.owner_name) if p]
            checks = external_adapters.run_all_checks(
                khasra_no=record.khasra_no,
                owner_name=record.owner_name,
                khata_no=record.khata_no,
                area_ha=record.area_hectare,
                registration_no=record.registration_no,
                parties=parties,
                village=record.village,
                tehsil=record.tehsil,
            )
            for check in checks:
                db.add(CrossCheckResult(
                    record_id=record.id,
                    source_system=check.source_system,
                    mode=check.mode,
                    match_status=check.match_status,
                    severity=check.severity,
                    external_reference=check.external_reference,
                    detail=check.detail,
                    payload=check.payload,
                ))
            mismatches = [c for c in checks if c.match_status != "matched"]
            record.cross_db_status = (
                "all_matched" if not mismatches else f"{len(mismatches)}_mismatch"
            )
            audit.log_action(
                db, ActionType.CROSS_DB_CHECK, actor_label="cross-db-service",
                entity_type="record", entity_id=record.record_id, document_id=document.id,
                detail="; ".join(
                    f"{c.source_system}={c.match_status}" for c in checks
                ),
            )

            # ---------------- Stage 7: queue for human review (FR-10) ------
            record.status = RecordStatus.PENDING_REVIEW
            document.status = (
                DocumentStatus.COMPLETED
                if not discrepancies else DocumentStatus.NEEDS_REVIEW
            )
            document.progress_pct = 100
            document.error_message = ""
            document.processed_at = audit.utcnow()
            document.total_latency_ms = int((time.perf_counter() - started) * 1000)
            if job:
                job.stage = "completed"
                job.finished_at = audit.utcnow()
                job.duration_ms = document.total_latency_ms

            db.commit()

            summary.update({
                "status": document.status.value,
                "record_id": record.record_id,
                "record_confidence": record.record_confidence,
                "discrepancies": len(discrepancies),
                "highest_severity": record.highest_severity,
                "gis_linked": record.gis_linked,
                "cross_db_status": record.cross_db_status,
                "latency_ms": document.total_latency_ms,
                "ocr_mean_confidence": ocr["mean_confidence"],
            })

            if record.highest_severity in (Severity.HIGH.value, Severity.CRITICAL.value):
                notifications.notify(
                    db,
                    title=f"{record.highest_severity.upper()} severity discrepancy on {record.record_id}",
                    message=(
                        f"Khasra {record.khasra_no} ({record.village}) failed "
                        f"{len(discrepancies)} business rule(s). Reviewer action required."
                    ),
                    role=Role.REVIEWER,
                    level="high",
                    link=f"#/review/{record.id}",
                )
            return summary

        except Exception as exc:
            db.rollback()
            document = db.get(SourceDocument, document_id)
            if document:
                document.status = DocumentStatus.FAILED
                document.error_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}"
                document.progress_pct = 0
                db.commit()
                audit.log_action(
                    db, ActionType.OCR_EXECUTED, actor_label="worker",
                    entity_type="document", entity_id=document.doc_id, document_id=document.id,
                    detail=f"pipeline failed: {document.error_message.splitlines()[0]}",
                )
            if job:
                job.stage = "failed"
                job.error = str(exc)[:500]
                job.finished_at = audit.utcnow()
                db.commit()
            return {"document_id": document_id, "error": str(exc)}


def _new_discrepancy(record_id: int, discrepancy) -> "DiscrepancyRow":
    from app.models import Discrepancy as DiscrepancyRow
    from app.models import Severity as SeverityEnum

    return DiscrepancyRow(
        record_id=record_id,
        rule_id=discrepancy.rule_id,
        rule_name=discrepancy.rule_name,
        severity=SeverityEnum(discrepancy.severity),
        message=discrepancy.message,
        expected=discrepancy.expected,
        actual=discrepancy.actual,
        conflicting_values=discrepancy.conflicting_values,
        evidence_refs=discrepancy.evidence_refs,
        recommended_action=discrepancy.recommended_action,
        status="open",
    )


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------
def enqueue(document_id: int) -> None:
    with session_scope() as db:
        db.add(ProcessingJob(document_id=document_id, stage="queued"))
    with _LOCK:
        _QUEUE.append(document_id)
    ensure_worker()


def queue_status() -> dict:
    with _LOCK:
        pending = list(_QUEUE)
    return {
        "queued": pending,
        "queue_length": len(pending),
        "worker_alive": bool(_WORKER and _WORKER.is_alive()),
    }


def _worker_loop() -> None:
    while not _STOP.is_set():
        document_id = None
        with _LOCK:
            if _QUEUE:
                document_id = _QUEUE.pop(0)
        if document_id is None:
            time.sleep(0.25)
            continue
        try:
            process_document(document_id)
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()


def ensure_worker() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker_loop, name="bhuverify-worker", daemon=True)
            _WORKER.start()


def process_now(document_id: int) -> dict:
    """Synchronous processing - used by the seeder and the test-suite."""
    return process_document(document_id)
