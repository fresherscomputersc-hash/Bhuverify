"""Record review, correction, verification and validation endpoints (FR-6, FR-10, FR-11, FR-16, FR-17)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import security
from app.config import settings
from app.database import get_db
from app.models import (
    ActionType,
    AuditLog,
    CorrectionDataset,
    Discrepancy,
    DocumentStatus,
    ExtractionResult,
    LandRecord,
    Notification,
    NotificationStatus,
    RecordStatus,
    Role,
    Severity,
    SourceDocument,
    User,
    iso_utc,
    utcnow,
)
from app.services import audit, external_adapters, notifications
from app.services.gis_service import layer, neighbours
from app.services.validation import (
    RULE_CATALOG,
    RecordContext,
    highest_severity,
    run_all_rules,
    scan_duplicates,
)

router = APIRouter(prefix="/api/v1/records", tags=["records"])

CORRECTABLE_FIELDS = [
    "owner_name", "guardian_name", "address", "khasra_no", "khata_no", "survey_no",
    "plot_no", "village", "tehsil", "district", "area", "area_value", "area_unit",
    "land_classification", "boundaries", "mutation_no", "mutation_date",
    "registration_no", "previous_owner", "new_owner",
]
# "area" is a reviewer-facing alias for the area_value + area_unit pair: the
# extraction pipeline stores one "area" field ("1.42 acre") while the record
# keeps the parsed value and unit in separate columns.


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def record_dict(record: LandRecord, detail: bool = False) -> dict:
    payload = {
        "id": record.id,
        "record_id": record.record_id,
        "doc_id": record.document.doc_id if record.document else None,
        "khasra_no": record.khasra_no,
        "khata_no": record.khata_no,
        "survey_no": record.survey_no,
        "plot_no": record.plot_no,
        "village": record.village,
        "tehsil": record.tehsil,
        "district": record.district,
        "state": record.state,
        "owner_name": record.owner_name,
        "guardian_name": record.guardian_name,
        "area_value": record.area_value,
        "area_unit": record.area_unit,
        "area_hectare": record.area_hectare,
        "land_classification": record.land_classification,
        "mutation_no": record.mutation_no,
        "mutation_date": record.mutation_date,
        "registration_no": record.registration_no,
        "previous_owner": record.previous_owner,
        "new_owner": record.new_owner,
        "document_type": record.document_type_label,
        "status": record.status.value,
        "record_confidence": round(record.record_confidence, 2),
        "discrepancy_count": record.discrepancy_count,
        "highest_severity": record.highest_severity,
        "gis_linked": record.gis_linked,
        "cross_db_status": record.cross_db_status,
        "assigned_to_id": record.assigned_to_id,
        "review_comment": record.review_comment,
        "reviewed_at": iso_utc(record.reviewed_at),
        "created_at": iso_utc(record.created_at),
    }
    if detail:
        payload.update({
            "address": record.address,
            "boundaries": record.boundaries,
            "parent_khasra_no": record.parent_khasra_no,
            "fields": [
                {
                    "field_name": f.field_name,
                    "label": f.field_label,
                    "value": f.value,
                    "normalized_value": f.normalized_value,
                    "confidence": round(f.confidence, 2),
                    "band": (
                        "high" if f.confidence >= settings.CONFIDENCE_HIGH
                        else "medium" if f.confidence >= settings.CONFIDENCE_MEDIUM
                        else "low"
                    ),
                    "source": f.source,
                    "evidence_text": f.evidence_text,
                    "bbox": f.bbox,
                    "is_low_confidence": f.is_low_confidence,
                    "corrected_value": f.corrected_value,
                }
                for f in sorted(record.extractions, key=lambda x: x.id)
            ],
            "discrepancies": [
                {
                    "id": d.id,
                    "rule_id": d.rule_id,
                    "rule_name": d.rule_name,
                    "severity": d.severity.value,
                    "message": d.message,
                    "expected": d.expected,
                    "actual": d.actual,
                    "conflicting_values": d.conflicting_values,
                    "evidence_refs": d.evidence_refs,
                    "recommended_action": d.recommended_action,
                    "status": d.status,
                }
                for d in record.discrepancies
            ],
            "cross_db_checks": [
                {
                    "source_system": c.source_system,
                    "mode": c.mode,
                    "match_status": c.match_status,
                    "severity": c.severity,
                    "external_reference": c.external_reference,
                    "detail": c.detail,
                    "payload": c.payload,
                    "checked_at": iso_utc(c.checked_at),
                }
                for c in record.cross_db_results
            ],
            "geometry": (
                {
                    "geometry_id": record.geometry.geometry_id,
                    "plot_key": record.geometry.plot_key,
                    "geojson": record.geometry.geojson,
                    "area_sqm": record.geometry.area_sqm,
                    "match_type": record.geometry.match_type,
                    "spatial_mismatch": record.geometry.spatial_mismatch,
                    "area_delta_pct": record.geometry.area_delta_pct,
                    "source_map_ref": record.geometry.source_map_ref,
                }
                if record.geometry else None
            ),
            "duplicates": scan_duplicates(db_session_of(record), record),
            "audit_trail": [
                audit.to_dict(entry) for entry in db_session_of(record).execute(
                    select(AuditLog)
                    .where(AuditLog.entity_id == record.record_id)
                    .order_by(AuditLog.created_at.desc())
                    .limit(60)
                ).scalars().all()
            ],
        })
    return payload


def db_session_of(record: LandRecord) -> Session:
    from sqlalchemy.orm import object_session

    session = object_session(record)
    if session is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Record is detached from the session.")
    return session


# ---------------------------------------------------------------------------
# FR-11  Search and retrieval
# ---------------------------------------------------------------------------
@router.get("")
def search_records(
    q: str | None = Query(default=None, description="Free-text across owner, khasra, village, tehsil"),
    owner: str | None = None,
    khasra_no: str | None = None,
    khata_no: str | None = None,
    survey_no: str | None = None,
    plot_no: str | None = None,
    village: str | None = None,
    tehsil: str | None = None,
    district: str | None = None,
    status: str | None = None,
    discrepancy_type: str | None = Query(default=None, description="Rule ID, e.g. BR-3"),
    low_confidence_only: bool = False,
    limit: int = 50,
    user: User = Depends(security.require("search:use")),
    db: Session = Depends(get_db),
):
    started = datetime.now()
    query = select(LandRecord)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(
            LandRecord.owner_name.ilike(pattern),
            LandRecord.khasra_no.ilike(pattern),
            LandRecord.khata_no.ilike(pattern),
            LandRecord.survey_no.ilike(pattern),
            LandRecord.plot_no.ilike(pattern),
            LandRecord.village.ilike(pattern),
            LandRecord.tehsil.ilike(pattern),
            LandRecord.district.ilike(pattern),
            LandRecord.record_id.ilike(pattern),
        ))
    for column, value in (
        (LandRecord.owner_name, owner), (LandRecord.khasra_no, khasra_no),
        (LandRecord.khata_no, khata_no), (LandRecord.survey_no, survey_no),
        (LandRecord.plot_no, plot_no), (LandRecord.village, village),
        (LandRecord.tehsil, tehsil), (LandRecord.district, district),
    ):
        if value:
            query = query.where(column.ilike(f"%{value}%"))
    if status:
        query = query.where(LandRecord.status == RecordStatus(status))
    if low_confidence_only:
        query = query.where(LandRecord.record_confidence < settings.CONFIDENCE_MEDIUM)
    if discrepancy_type:
        query = query.join(Discrepancy, Discrepancy.record_id == LandRecord.id).where(
            Discrepancy.rule_id == discrepancy_type
        )

    rows = db.execute(query.order_by(LandRecord.created_at.desc()).limit(min(limit, 500))).scalars().unique().all()
    elapsed_ms = (datetime.now() - started).total_seconds() * 1000

    audit.log_action(
        db, ActionType.SEARCH_EXECUTED, user_id=user.id, actor_label=user.username,
        entity_type="search", new_value=q or "",
        detail=f"{len(rows)} result(s) in {elapsed_ms:.0f} ms; filters="
               f"{ {k: v for k, v in dict(q=q, owner=owner, khasra_no=khasra_no, village=village, status=status).items() if v} }",
    )
    return {
        "count": len(rows),
        "elapsed_ms": round(elapsed_ms, 1),
        "budget_ms": settings.PERF_SEARCH_BUDGET_S * 1000,
        "records": [record_dict(row) for row in rows],
    }


@router.get("/review-queue")
def review_queue(
    severity: str | None = None,
    limit: int = 50,
    user: User = Depends(security.require("records:read")),
    db: Session = Depends(get_db),
):
    query = (
        select(LandRecord)
        .where(LandRecord.status.in_([RecordStatus.PENDING_REVIEW, RecordStatus.ESCALATED]))
    )
    if severity:
        query = query.where(LandRecord.highest_severity == severity)
    rows = db.execute(
        query.order_by(LandRecord.discrepancy_count.desc(), LandRecord.record_confidence.asc())
        .limit(min(limit, 200))
    ).scalars().all()
    return {
        "count": len(rows),
        "queue": [
            {
                **record_dict(row),
                "priority": (
                    "critical" if row.highest_severity == Severity.CRITICAL.value
                    else "high" if row.highest_severity == Severity.HIGH.value
                    else "normal"
                ),
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Metadata endpoints (declared before /{record_id} so the path is not shadowed)
# ---------------------------------------------------------------------------
@router.get("/meta/rules")
def rules_catalog(user: User = Depends(security.require("records:read"))):
    return {"rules": RULE_CATALOG}


@router.get("/meta/corrections")
def corrections_dataset(user: User = Depends(security.require("dashboard:read")),
                        db: Session = Depends(get_db)):
    rows = db.execute(
        select(CorrectionDataset).order_by(CorrectionDataset.created_at.desc()).limit(200)
    ).scalars().all()
    per_field = dict(db.execute(
        select(CorrectionDataset.field_name, func.count(CorrectionDataset.id))
        .group_by(CorrectionDataset.field_name)
    ).all())
    return {
        "count": len(rows),
        "per_field": per_field,
        "samples": [
            {
                "field_name": row.field_name,
                "ai_value": row.ai_value,
                "corrected_value": row.corrected_value,
                "ai_confidence": round(row.ai_confidence, 2),
                "language": row.language,
                "source_ref": row.source_ref,
                "created_at": iso_utc(row.created_at),
            }
            for row in rows
        ],
        "usage": "Exported as labeled training data for the next OCR/NER fine-tuning run (FR-16).",
    }


@router.get("/meta/cross-db-status")
def cross_db_status(user: User = Depends(security.require("records:read"))):
    return external_adapters.adapter_status()


@router.get("/meta/gis-status")
def gis_status(user: User = Depends(security.require("records:read"))):
    return layer().summary()


@router.get("/{record_id}")
def get_record(record_id: str, user: User = Depends(security.require("records:read")),
               db: Session = Depends(get_db)):
    record = db.execute(
        select(LandRecord).where(LandRecord.record_id == record_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return record_dict(record, detail=True)


@router.get("/{record_id}/discrepancies")
def record_discrepancies(record_id: str,
                         user: User = Depends(security.require("discrepancies:read")),
                         db: Session = Depends(get_db)):
    record = db.execute(
        select(LandRecord).where(LandRecord.record_id == record_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return {
        "record_id": record_id,
        "count": len(record.discrepancies),
        "highest_severity": record.highest_severity,
        "discrepancies": [
            {
                "id": d.id,
                "rule_id": d.rule_id,
                "rule_name": d.rule_name,
                "severity": d.severity.value,
                "message": d.message,
                "expected": d.expected,
                "actual": d.actual,
                "conflicting_values": d.conflicting_values,
                "evidence_refs": d.evidence_refs,
                "recommended_action": d.recommended_action,
                "status": d.status,
            }
            for d in record.discrepancies
        ],
        "rule_catalog": RULE_CATALOG,
    }


# ---------------------------------------------------------------------------
# FR-10 / FR-16  Inline correction with learning capture
# ---------------------------------------------------------------------------
class CorrectionPayload(BaseModel):
    corrections: dict[str, str]
    comment: str = ""


@router.patch("/{record_id}")
def correct_record(record_id: str, payload: CorrectionPayload,
                   user: User = Depends(security.require("records:correct")),
                   db: Session = Depends(get_db)):
    record = db.execute(
        select(LandRecord).where(LandRecord.record_id == record_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")

    invalid = [f for f in payload.corrections if f not in CORRECTABLE_FIELDS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Non-correctable field(s): {', '.join(invalid)}. "
                   f"Correctable: {', '.join(CORRECTABLE_FIELDS)}",
        )

    applied = []
    for field_name, new_value in payload.corrections.items():
        if field_name == "area":
            # Alias: "1.42 acre" -> area_value=1.42 + area_unit="acre".
            import re as _re

            from app.services.extraction import normalize_unit as _normalize_unit

            match = _re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z\u0900-\u097F\u0B00-\u0B7F]*)",
                               str(new_value).strip())
            if not match:
                raise HTTPException(
                    status_code=400,
                    detail=f"area must look like '1.42 acre', got '{new_value}'.",
                )
            old_value = f"{record.area_value} {record.area_unit}".strip()
            area_val = float(match.group(1))
            area_unit = _normalize_unit(match.group(2)) if match.group(2) else record.area_unit
            _apply_single_correction(
                db, record, user, "area", old_value,
                f"{area_val} {area_unit}".strip(),
                0.0, payload.comment,
            )
            record.area_value = area_val
            record.area_unit = area_unit
            extraction = db.execute(
                select(ExtractionResult).where(ExtractionResult.record_id == record.id)
                .where(ExtractionResult.field_name == "area")
            ).scalar_one_or_none()
            if extraction:
                extraction.value = str(new_value).strip()[:200]
                extraction.normalized_value = str(area_val)
                extraction.corrected_value = str(new_value).strip()[:200]
                extraction.confidence = max(extraction.confidence, settings.CONFIDENCE_HIGH + 5)
                extraction.is_low_confidence = False
                # NOTE: ExtractionResult has no signals column (signals live
                # only on the in-memory FieldExtraction); the unit is kept on
                # record.area_unit, which the revalidation step reads back.
            applied.append({
                "field_name": "area", "old_value": str(old_value),
                "new_value": f"{area_val} {area_unit}".strip(), "ai_confidence": 0.0,
            })
            continue

        old_value = getattr(record, field_name, "")
        if field_name == "area_value":
            try:
                new_value_typed: float | str = float(new_value)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"area_value must be numeric, got '{new_value}'.")
        else:
            new_value_typed = str(new_value).strip()
        if str(old_value) == str(new_value_typed):
            continue

        extraction = db.execute(
            select(ExtractionResult).where(ExtractionResult.record_id == record.id)
            .where(ExtractionResult.field_name == field_name)
        ).scalar_one_or_none()
        ai_confidence = extraction.confidence if extraction else 0.0
        if extraction:
            extraction.corrected_value = str(new_value_typed)
            # reviewer override raises the field to high confidence
            extraction.confidence = max(extraction.confidence, settings.CONFIDENCE_HIGH + 5)
            extraction.is_low_confidence = False

        setattr(record, field_name, new_value_typed)
        _apply_single_correction(
            db, record, user, field_name, old_value, new_value_typed,
            ai_confidence, payload.comment,
        )
        applied.append({
            "field_name": field_name, "old_value": str(old_value),
            "new_value": str(new_value_typed), "ai_confidence": round(ai_confidence, 2),
        })

    if applied:
        # recompute the record score: corrected fields are now trusted
        extractions = db.execute(
            select(ExtractionResult).where(ExtractionResult.record_id == record.id)
        ).scalars().all()
        scored = [e.confidence for e in extractions if e.value]
        record.record_confidence = round(sum(scored) / len(scored), 2) if scored else record.record_confidence
        audit.log_action(
            db, ActionType.CONFIDENCE_GENERATED, user_id=user.id, actor_label=user.username,
            entity_type="record", entity_id=record.record_id,
            ai_confidence=record.record_confidence,
            detail=f"Record confidence recomputed after {len(applied)} correction(s).",
        )
        # standardise the area after a touch (corrections store raw units)
        if any(a["field_name"] in {"area", "area_value", "area_unit"} for a in applied):
            from app.services.extraction import to_hectare

            record.area_hectare, _unit_ok = to_hectare(
                record.area_value or 0.0, record.area_unit or ""
            )
        revalidation = _revalidate_after_correction(db, record, user)
    else:
        revalidation = {"revalidated": False}
    db.commit()
    db.refresh(record)
    return {
        "record_id": record_id,
        "applied": applied,
        "corrections_captured": len(applied),
        "record_confidence": round(record.record_confidence, 2),
        "revalidation": revalidation,
        "record": record_dict(record, detail=True),
    }


def _apply_single_correction(db: Session, record: LandRecord, user: User,
                             field_name: str, old_value, new_value_typed,
                             ai_confidence: float, comment: str) -> None:
    """Shared audit + learning-capture for one corrected field."""
    audit.log_action(
        db, ActionType.FIELD_CORRECTED, user_id=user.id, actor_label=user.username,
        entity_type="record", entity_id=record.record_id, document_id=record.document_id,
        field_name=field_name, old_value=str(old_value), new_value=str(new_value_typed),
        ai_confidence=ai_confidence,
        detail=(f"Reviewer correction (AI suggested '{old_value}' at "
                f"{ai_confidence:.1f}% confidence)" + (f" - {comment}" if comment else "")),
        evidence_ref=record.document.doc_id if record.document else "",
    )
    if settings.enable_correction_capture:
        db.add(CorrectionDataset(
            record_id=record.id,
            field_name=field_name,
            ai_value=str(old_value),
            corrected_value=str(new_value_typed),
            ai_confidence=ai_confidence,
            language=record.document.language if record.document else "eng",
            source_ref=record.document.doc_id if record.document else "",
            corrected_by_id=user.id,
        ))


def _revalidate_after_correction(db: Session, record: LandRecord, user: User) -> dict:
    """Re-run BR-1..BR-10 after a reviewer correction.

    Without this, fixing the missing fields would never clear the block:
    approval is refused while a critical discrepancy is open, and open rows
    would otherwise stay open forever. Open rows are replaced by the fresh
    run; already-resolved history is kept.
    """
    fields = _fields_for_revalidation(record)
    geometry = None
    if record.geometry is not None:
        geometry = {
            "linked": True,
            "match_type": record.geometry.match_type,
            "plot_key": record.geometry.plot_key,
            "polygon_area_ha": round((record.geometry.area_sqm or 0.0) / 10_000.0, 6),
            "record_area_ha": round(record.area_hectare or 0.0, 6),
            "area_delta_pct": record.geometry.area_delta_pct,
            "spatial_mismatch": record.geometry.spatial_mismatch,
        }
    ctx = RecordContext(
        db=db, record=record, fields=fields, geometry=geometry,
        parent_area_hectare=None, document_id=record.document_id,
        doc_id=record.document.doc_id if record.document else "",
    )
    outcomes = run_all_rules(ctx)
    for stale in [d for d in record.discrepancies if d.status == "open"]:
        db.delete(stale)
    db.flush()
    fresh = [d for outcome in outcomes for d in outcome.discrepancies]
    for item in fresh:
        db.add(Discrepancy(
            record_id=record.id,
            rule_id=item.rule_id,
            rule_name=item.rule_name,
            severity=Severity(item.severity),
            message=item.message,
            expected=item.expected,
            actual=item.actual,
            conflicting_values=item.conflicting_values,
            evidence_refs=item.evidence_refs,
            recommended_action=item.recommended_action,
            status="open",
        ))
    db.flush()
    open_now = [d for d in record.discrepancies if d.status == "open"]
    record.discrepancy_count = len(record.discrepancies)
    record.highest_severity = highest_severity([
        type("D", (), {"severity": d.severity.value})() for d in open_now
    ]) if open_now else ""
    audit.log_action(
        db, ActionType.VALIDATION_RULE_EXECUTED, user_id=user.id, actor_label=user.username,
        entity_type="record", entity_id=record.record_id, document_id=record.document_id,
        detail=(f"Revalidation after correction: "
                f"{len([o for o in outcomes if o.passed])}/10 rules pass, "
                f"{len(open_now)} finding(s) still open."),
    )
    return {
        "revalidated": True,
        "passed": [o.rule_id for o in outcomes if o.passed],
        "failed": [o.rule_id for o in outcomes if not o.passed],
        "open_discrepancies": len(open_now),
        "highest_severity": record.highest_severity,
    }


def _fields_for_revalidation(record: LandRecord) -> dict:
    """Field dict for the rule engine, preferring reviewer-corrected values.

    Falls back to the record columns (which the correction endpoint keeps in
    sync) so revalidation also works when extraction rows are sparse.
    """
    fields: dict[str, dict] = {}

    def _entry(value: str, confidence: float, evidence: str = "") -> dict:
        return {
            "value": value, "normalized_value": value, "confidence": confidence,
            "bbox": {}, "evidence_text": evidence, "signals": {},
        }

    attr_map = {
        "khasra_no": record.khasra_no, "khata_no": record.khata_no,
        "survey_no": record.survey_no, "plot_no": record.plot_no,
        "village": record.village, "tehsil": record.tehsil,
        "district": record.district, "owner_name": record.owner_name,
        "guardian_name": record.guardian_name, "address": record.address,
        "land_classification": record.land_classification,
        "boundaries": record.boundaries, "mutation_no": record.mutation_no,
        "mutation_date": record.mutation_date,
        "registration_no": record.registration_no,
        "previous_owner": record.previous_owner, "new_owner": record.new_owner,
    }
    for name, val in attr_map.items():
        text = str(val or "")
        fields[name] = _entry(text, 95.0 if text else 0.0,
                              "reviewer-provided" if text else "")
    area_text = f"{record.area_value} {record.area_unit}".strip() if record.area_value else ""
    fields["area"] = _entry(area_text, 95.0 if area_text else 0.0,
                            "reviewer-provided" if area_text else "")
    if area_text:
        fields["area"]["signals"] = {"area_unit_detected": record.area_unit or ""}

    for ex in record.extractions or []:
        effective = ex.corrected_value or ex.normalized_value or ex.value or ""
        if not effective and fields.get(ex.field_name, {}).get("normalized_value"):
            continue  # keep the reviewer-filled record value over an empty AI row
        fields[ex.field_name] = {
            "value": ex.value or "",
            "normalized_value": effective,
            "confidence": ex.confidence,
            "bbox": ex.bbox or {},
            "evidence_text": ex.evidence_text or "",
            # ExtractionResult rows don't persist signals; the record-level
            # fallback above already seeds area_unit_detected for "area".
            "signals": {},
        }
    return fields


# ---------------------------------------------------------------------------
# FR-10  Approve / reject / escalate
# ---------------------------------------------------------------------------
class VerifyPayload(BaseModel):
    decision: str  # approve | reject | escalate
    comment: str = ""


@router.post("/{record_id}/verify")
def verify_record(record_id: str, payload: VerifyPayload,
                  user: User = Depends(security.require("records:verify")),
                  db: Session = Depends(get_db)):
    decision = payload.decision.lower().strip()
    if decision not in {"approve", "reject", "escalate"}:
        raise HTTPException(status_code=400, detail="decision must be approve, reject or escalate.")

    record = db.execute(
        select(LandRecord).where(LandRecord.record_id == record_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")

    open_critical = [
        d for d in record.discrepancies
        if d.status == "open" and d.severity == Severity.CRITICAL
    ]
    if decision == "approve" and open_critical:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot approve: {len(open_critical)} critical discrepancy/discrepancies still open "
                f"({', '.join(sorted({d.rule_id for d in open_critical}))}). Resolve or reject instead."
            ),
        )

    previous_status = record.status.value
    action_map = {
        "approve": (RecordStatus.APPROVED, ActionType.RECORD_APPROVED),
        "reject": (RecordStatus.REJECTED, ActionType.RECORD_REJECTED),
        "escalate": (RecordStatus.ESCALATED, ActionType.RECORD_ESCALATED),
    }
    new_status, action = action_map[decision]
    record.status = new_status
    record.reviewed_by_id = user.id
    record.reviewed_at = utcnow()
    record.review_comment = payload.comment

    for discrepancy in record.discrepancies:
        if discrepancy.status == "open":
            discrepancy.status = f"resolved_{decision}d" if decision != "escalate" else "escalated"
            discrepancy.resolved_by_id = user.id
            discrepancy.resolved_at = utcnow()

    if record.document:
        record.document.status = (
            DocumentStatus.COMPLETED if decision in {"approve", "reject"}
            else DocumentStatus.NEEDS_REVIEW
        )

    audit.log_action(
        db, action, user_id=user.id, actor_label=user.username,
        entity_type="record", entity_id=record.record_id, document_id=record.document_id,
        old_value=previous_status, new_value=new_status.value,
        ai_confidence=record.record_confidence,
        detail=(
            f"Reviewer {user.designation or user.full_name} {decision}d the record"
            + (f". Comment: {payload.comment}" if payload.comment else ".")
        ),
    )
    db.commit()

    if decision == "escalate":
        notifications.notify(
            db,
            title=f"Record {record.record_id} escalated for higher review",
            message=(
                f"{user.full_name} escalated khasra {record.khasra_no} ({record.village}). "
                f"Reason: {payload.comment or 'not specified'}"
            ),
            role=Role.SUPERVISOR, level="high", link=f"#/review/{record.id}",
        )
    elif decision == "approve":
        notifications.notify(
            db, title=f"Record {record.record_id} approved",
            message=f"Khasra {record.khasra_no} ({record.village}) is now a verified digital record.",
            role=Role.SUPERVISOR, level="info", link=f"#/records/{record.record_id}",
        )

    db.refresh(record)
    return {
        "record_id": record_id,
        "decision": decision,
        "status": record.status.value,
        "record": record_dict(record, detail=True),
    }


class AssignPayload(BaseModel):
    user_id: int


@router.post("/{record_id}/assign")
def assign_record(record_id: str, payload: AssignPayload,
                  user: User = Depends(security.require("records:assign")),
                  db: Session = Depends(get_db)):
    record = db.execute(
        select(LandRecord).where(LandRecord.record_id == record_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    assignee = db.get(User, payload.user_id)
    if assignee is None:
        raise HTTPException(status_code=404, detail="Assignee not found.")
    previous = record.assigned_to_id
    record.assigned_to_id = assignee.id
    audit.log_action(
        db, ActionType.RECORD_ASSIGNED, user_id=user.id, actor_label=user.username,
        entity_type="record", entity_id=record.record_id,
        old_value=str(previous or ""), new_value=str(assignee.id),
        detail=f"Assigned to {assignee.full_name} ({assignee.role.value})",
    )
    db.commit()
    notifications.notify(
        db, title=f"Record {record.record_id} assigned to you",
        message=f"Khasra {record.khasra_no} ({record.village}) needs verification.",
        user_id=assignee.id, level="info", link=f"#/review/{record.id}",
    )
    return {"record_id": record_id, "assigned_to": assignee.username}


# ---------------------------------------------------------------------------
# FR-17  Run validation rules on demand (stateless preview)
# ---------------------------------------------------------------------------
class ValidationPayload(BaseModel):
    fields: dict[str, dict | str]


@router.post("/validate")
def validate_payload(payload: ValidationPayload,
                     user: User = Depends(security.require("records:read")),
                     db: Session = Depends(get_db)):
    """Run BR-1..BR-10 against an ad-hoc field set (no persistence).

    Used by the demo to show a rule firing, and by integrating systems to
    pre-validate a record before submission.
    """
    normalised: dict[str, dict] = {}
    for name, item in payload.fields.items():
        if isinstance(item, dict):
            normalised[name] = {
                "value": str(item.get("value", "")),
                "normalized_value": str(item.get("normalized_value", item.get("value", ""))),
                "confidence": float(item.get("confidence", 80.0)),
                "bbox": item.get("bbox", {}),
                "evidence_text": item.get("evidence_text", ""),
                "signals": item.get("signals", {}),
            }
        else:
            normalised[name] = {
                "value": str(item), "normalized_value": str(item),
                "confidence": 80.0, "bbox": {}, "evidence_text": "", "signals": {},
            }

    scratch = LandRecord(record_id="ADHOC-PREVIEW", khasra_no="", status=RecordStatus.PENDING_REVIEW)
    ctx = RecordContext(
        db=db, record=scratch, fields=normalised, geometry=None,
        parent_area_hectare=None, document_id=0, doc_id="ad-hoc",
    )
    outcomes = run_all_rules(ctx)
    discrepancies = [d for outcome in outcomes for d in outcome.discrepancies]
    return {
        "passed": [o.rule_id for o in outcomes if o.passed],
        "failed": [o.rule_id for o in outcomes if not o.passed],
        "highest_severity": highest_severity(discrepancies),
        "discrepancies": [d.as_dict() for d in discrepancies],
        "rule_runtime": [
            {"rule_id": o.rule_id, "rule_name": o.rule_name, "passed": o.passed,
             "findings": len(o.discrepancies), "latency_ms": o.latency_ms, "detail": o.detail}
            for o in outcomes
        ],
        "total_latency_ms": round(sum(o.latency_ms for o in outcomes), 2),
    }


