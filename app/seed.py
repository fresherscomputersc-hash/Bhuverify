"""
Demo dataset seeding (SRS section 10 - Prototype Demo Dataset).

Creates the six seeded roles/accounts, the sample cadastral layer and the six
demo documents, then runs each one through the real processing pipeline so the
prototype boots straight into a populated reviewer queue.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from sqlalchemy import func, select

from app.config import UPLOAD_DIR, settings
from app.database import Base, engine, init_db, session_scope
from app.models import (
    ActionType,
    CorrectionDataset,
    Discrepancy,
    DocumentStatus,
    ExtractionResult,
    LandRecord,
    ProcessingJob,
    RecordStatus,
    Role,
    SourceDocument,
    SpatialGeometry,
    User,
)
from app.sample_documents import generate_samples
from app.sample_geojson import write_sample_layer
from app.security import hash_password
from app.services import audit
from app.services.gis_service import reload_layer
from app.services.worker import process_now

SEED_USERS = [
    ("operator", "operator123", "Ananya Das", Role.OPERATOR,
     "Digitization Operator", "Khordha"),
    ("reviewer", "reviewer123", "Prakash Mohanty", Role.REVIEWER,
     "Revenue Inspector", "Khordha"),
    ("supervisor", "supervisor123", "Srikant Rout", Role.SUPERVISOR,
     "Tehsildar / District Supervisor", "Khordha"),
    ("survey", "survey123", "Debasish Patra", Role.SURVEY_OFFICIAL,
     "Survey Department Official", "Khordha"),
    ("auditor", "auditor123", "Rituparna Sahu", Role.AUDITOR,
     "Auditor - Revenue Directorate", "Odisha"),
    ("admin", "admin123", "System Administrator", Role.ADMIN,
     "System Administrator", "Odisha"),
]


def seed_users() -> dict[str, User]:
    """Create the seeded accounts. Returns username -> User."""
    with session_scope() as db:
        users: dict[str, User] = {}
        for username, password, full_name, role, designation, scope in SEED_USERS:
            user = db.execute(
                select(User).where(User.username == username)
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    full_name=full_name,
                    role=role,
                    designation=designation,
                    district_scope=scope,
                )
                db.add(user)
                db.flush()
                audit.log_action(
                    db, ActionType.API_ACCESSED, actor_label="seeder",
                    entity_type="user", entity_id=str(user.id), new_value=username,
                    detail=f"Seeded {role.value} account '{username}' ({designation})",
                )
            else:
                user.full_name = full_name
                user.role = role
                user.designation = designation
                user.district_scope = scope
            users[username] = user
        return users


def seed_geojson() -> Path:
    path = write_sample_layer()
    reload_layer()
    return path


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_documents(users: dict[str, User], process: bool = True) -> list[dict]:
    """Copy the rendered demo documents into the repository and process them."""
    samples = generate_samples()
    operator = users.get("operator")
    created = []

    with session_scope() as db:
        batch_id = "BATCH-DEMO-001"
        for index, sample in enumerate(samples, start=1):
            source = Path(sample["path"])
            file_hash = _file_hash(source)
            existing = db.execute(
                select(SourceDocument).where(SourceDocument.file_hash == file_hash)
            ).scalar_one_or_none()
            if existing:
                created.append({
                    "doc_id": existing.doc_id, "filename": sample["filename"],
                    "status": existing.status.value, "skipped": "already_seeded",
                })
                continue

            doc_id = f"DOC-DEMO{index:03d}"
            stored = UPLOAD_DIR / f"{doc_id}{source.suffix.lower()}"
            shutil.copyfile(source, stored)

            document = SourceDocument(
                doc_id=doc_id,
                batch_id=batch_id,
                original_filename=sample["filename"],
                stored_path=str(stored),
                mime_type="image/jpeg" if source.suffix.lower() == ".jpg" else "image/png",
                file_hash=file_hash,
                size_bytes=source.stat().st_size,
                doc_type=sample["doc_type"],
                language=sample["language"],
                uploader_id=operator.id if operator else None,
                status=DocumentStatus.QUEUED,
            )
            db.add(document)
            db.flush()
            audit.log_action(
                db, ActionType.DOCUMENT_UPLOADED,
                user_id=operator.id if operator else None,
                actor_label=operator.username if operator else "seeder",
                entity_type="document", entity_id=doc_id, document_id=document.id,
                new_value=sample["filename"],
                detail=f"Seeded demo document ({sample['notes']})",
            )
            created.append({
                "doc_id": doc_id, "filename": sample["filename"],
                "status": "queued", "skipped": "", "notes": sample["notes"],
                "document_pk": document.id,
            })

    if process:
        for item in created:
            if item.get("document_pk"):
                result = process_now(item["document_pk"])
                item["result"] = result
                item["status"] = result.get("status", "failed")
    return created


def seed_example_correction(users: dict[str, User]) -> dict | None:
    """Demonstrate the correction feedback loop on the handwritten document.

    The Devanagari register page is read by the HTR path, and handwritten
    recognition of an owner name is exactly where a human reviewer adds value.
    A reviewer "corrects" it against the Bhulekh entry, which is the FR-16
    capture the SRS asks for.
    """
    reviewer = users.get("reviewer")
    with session_scope() as db:
        # locate by source document, not by extracted value: the whole point is
        # that the extracted khasra may itself be wrong.
        document = db.execute(
            select(SourceDocument).where(SourceDocument.doc_id == "DOC-DEMO003")
        ).scalar_one_or_none()
        if document is None or document.record is None:
            return None
        record = document.record

        extraction = db.execute(
            select(ExtractionResult).where(ExtractionResult.record_id == record.id)
            .where(ExtractionResult.field_name == "owner_name")
        ).scalar_one_or_none()
        if extraction is None:
            return None

        canonical = "Prafulla Kumar Sahoo"
        ai_value = record.owner_name
        corrected_fields = []

        if ai_value != canonical:
            record.owner_name = canonical
            extraction.corrected_value = canonical
            extraction.confidence = 96.0
            extraction.is_low_confidence = False
            corrected_fields.append({
                "field": "owner_name", "ai_value": ai_value,
                "corrected_value": canonical,
                "ai_confidence": round(extraction.confidence, 2),
            })
            _log_correction(db, record, reviewer, "owner_name", ai_value, canonical,
                            extraction.confidence)

        # the HTR khasra reading (e.g. 88/4) is corrected to the true 88/1,
        # which is what lets the record link to its cadastral polygon.
        khasra_extraction = db.execute(
            select(ExtractionResult).where(ExtractionResult.record_id == record.id)
            .where(ExtractionResult.field_name == "khasra_no")
        ).scalar_one_or_none()
        if record.khasra_no != "88/1":
            previous_khasra = record.khasra_no
            record.khasra_no = "88/1"
            if khasra_extraction:
                khasra_extraction.corrected_value = "88/1"
                khasra_extraction.confidence = 95.0
                khasra_extraction.is_low_confidence = False
            corrected_fields.append({
                "field": "khasra_no", "ai_value": previous_khasra,
                "corrected_value": "88/1",
                "ai_confidence": round(khasra_extraction.confidence if khasra_extraction else 0, 2),
            })
            _log_correction(db, record, reviewer, "khasra_no", previous_khasra, "88/1",
                            khasra_extraction.confidence if khasra_extraction else 0.0)
            # re-link GIS now that the identifier is right (FR-9 + FR-10)
            from app.services.gis_service import link_record

            gis = link_record(khasra_no="88/1", survey_no=record.survey_no,
                              plot_no=record.plot_no, record_area_ha=record.area_hectare)
            record.gis_linked = bool(gis["linked"])
            if gis["linked"] and record.geometry is None:
                import uuid

                from app.models import SpatialGeometry

                db.add(SpatialGeometry(
                    record_id=record.id,
                    geometry_id=f"GEO-{uuid.uuid4().hex[:10].upper()}",
                    plot_key=gis["plot_key"],
                    geojson=gis["geojson"],
                    area_sqm=round(gis["polygon_area_ha"] * 10_000, 2),
                    source_map_ref=gis["source_map_ref"],
                    match_type=gis["match_type"],
                    spatial_mismatch=bool(gis["spatial_mismatch"]),
                    area_delta_pct=gis["area_delta_pct"],
                ))

        if not corrected_fields:
            return {"record_id": record.record_id, "already_correct": True}

        extractions = db.execute(
            select(ExtractionResult).where(ExtractionResult.record_id == record.id)
        ).scalars().all()
        scored = [e.confidence for e in extractions if e.value]
        record.record_confidence = round(sum(scored) / len(scored), 2) if scored else 0.0
        return {"record_id": record.record_id, "corrections": corrected_fields}


def _log_correction(db, record: LandRecord, reviewer: User | None,
                    field_name: str, old_value: str, new_value: str,
                    ai_confidence: float) -> None:
    audit.log_action(
        db, ActionType.FIELD_CORRECTED,
        user_id=reviewer.id if reviewer else None,
        actor_label=reviewer.username if reviewer else "seeder",
        entity_type="record", entity_id=record.record_id, document_id=record.document_id,
        field_name=field_name, old_value=old_value, new_value=new_value,
        ai_confidence=ai_confidence,
        detail="Reviewer correction of a handwritten (HTR) reading against the Bhulekh entry.",
        evidence_ref=record.document.doc_id if record.document else "",
    )
    db.add(CorrectionDataset(
        record_id=record.id,
        field_name=field_name,
        ai_value=old_value,
        corrected_value=new_value,
        ai_confidence=ai_confidence,
        language="hin",
        source_ref=record.document.doc_id if record.document else "",
        corrected_by_id=reviewer.id if reviewer else None,
    ))


def already_seeded() -> bool:
    with session_scope() as db:
        count = db.execute(select(func.count(SourceDocument.id))).scalar() or 0
        return count > 0


def ensure_seeded() -> dict:
    """Idempotent boot-time seeding: only runs when the repository is empty.

    Set BHUVERIFY_SKIP_SEED=1 to suppress it - the test-suite does, so that
    uploads of the sample files are not rejected as duplicate hashes.
    """
    import os

    init_db()
    geojson = seed_geojson()
    if os.getenv("BHUVERIFY_SKIP_SEED") == "1":
        return {"seeded": False, "reason": "seeding suppressed (BHUVERIFY_SKIP_SEED)", "geojson": str(geojson)}
    if already_seeded():
        return {"seeded": False, "reason": "repository already populated", "geojson": str(geojson)}
    users = seed_users()
    documents = seed_documents(users, process=True)
    correction = seed_example_correction(users)
    return {
        "seeded": True,
        "users": [u for u, _, _, _, _, _ in SEED_USERS],
        "documents": documents,
        "example_correction": correction,
        "geojson": str(geojson),
    }


def reseed_all(process_documents: bool = True) -> dict:
    """Wipe the prototype data and rebuild it (admin endpoint / demo replay)."""
    Base.metadata.drop_all(bind=engine)
    init_db()
    for directory in (UPLOAD_DIR,):
        if directory.exists():
            for child in directory.iterdir():
                if child.is_file():
                    child.unlink()
    from app.config import PROCESSED_DIR

    if PROCESSED_DIR.exists():
        for child in PROCESSED_DIR.iterdir():
            if child.is_file():
                child.unlink()

    geojson = seed_geojson()
    users = seed_users()
    documents = seed_documents(users, process=process_documents)
    correction = seed_example_correction(users)
    return {
        "users": [u for u, _, _, _, _, _ in SEED_USERS],
        "documents": documents,
        "example_correction": correction,
        "geojson": str(geojson),
    }
