"""Document ingestion + repository endpoints (FR-1)."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from app.database import get_db
from app.models import (
    ActionType,
    AuditLog,
    Batch,
    DocumentStatus,
    RecordStatus,
    SourceDocument,
    User,
    iso_utc,
)
from app.services import audit
from app.services.worker import enqueue, process_now, queue_status

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retry_allowed(existing: SourceDocument) -> bool:
    """A same-bytes re-upload is accepted when the previous attempt is dead:
    its record was rejected, or the document itself failed processing.
    Anything still live (queued through approved) keeps the dedup skip, and
    the superseded document stays in the repository for the audit trail."""
    if existing.status == DocumentStatus.FAILED:
        return True
    record = existing.record
    if record is not None and record.status == RecordStatus.REJECTED:
        return True
    return False


def document_dict(document: SourceDocument, include_ocr: bool = False) -> dict:
    payload = {
        "id": document.id,
        "doc_id": document.doc_id,
        "batch_id": document.batch_id,
        "original_filename": document.original_filename,
        "mime_type": document.mime_type,
        "doc_type": document.doc_type,
        "language": document.language,
        "status": document.status.value,
        "progress_pct": document.progress_pct,
        "error_message": document.error_message,
        "file_hash": document.file_hash,
        "size_bytes": document.size_bytes,
        "ocr_engine": document.ocr_engine,
        "ocr_language": document.ocr_language,
        "ocr_word_count": document.ocr_word_count,
        "ocr_mean_confidence": round(document.ocr_mean_confidence, 2),
        "ocr_latency_ms": document.ocr_latency_ms,
        "total_latency_ms": document.total_latency_ms,
        "layout": document.layout_json,
        "preprocessing_stats": document.preprocessing_stats,
        "has_record": document.record is not None,
        "record_id": document.record.record_id if document.record else None,
        "created_at": iso_utc(document.created_at),
        "processed_at": iso_utc(document.processed_at),
    }
    if include_ocr:
        payload["ocr_text"] = document.ocr_text
    return payload


@router.post("")
async def upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    doc_type: str = Form("ror"),
    language: str = Form("eng+hin+ori"),
    batch_label: str = Form(""),
    process_sync: bool = Form(False),
    user: User = Depends(security.require("documents:upload")),
    db: Session = Depends(get_db),
):
    """Bulk upload. Duplicate files are skipped by SHA-256 hash (FR-1)."""
    if not files:
        raise HTTPException(status_code=400, detail="No files supplied.")

    batch_id = f"BATCH-{uuid.uuid4().hex[:8].upper()}"
    batch = Batch(batch_id=batch_id, label=batch_label or batch_id, uploader_id=user.id)
    db.add(batch)
    db.flush()

    accepted, skipped = [], []
    created_documents: list[SourceDocument] = []

    for upload in files:
        filename = upload.filename or "unnamed"
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}' has an unsupported extension '{suffix}'. "
                       f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"'{filename}' exceeds 25 MB.")
        if not data:
            raise HTTPException(status_code=400, detail=f"'{filename}' is empty.")

        file_hash = _sha256(data)
        existing = db.execute(
            select(SourceDocument).where(SourceDocument.file_hash == file_hash)
        ).scalar_one_or_none()
        if existing and not _retry_allowed(existing):
            skipped.append({
                "filename": filename,
                "reason": "duplicate_file_hash",
                "existing_doc_id": existing.doc_id,
            })
            audit.log_action(
                db, ActionType.DOCUMENT_DUPLICATE_FILE, user_id=user.id,
                actor_label=user.username, entity_type="document",
                entity_id=existing.doc_id, document_id=existing.id,
                old_value=filename, new_value=existing.doc_id,
                detail="Upload skipped - identical file hash already in the repository.",
            )
            continue

        doc_id = f"DOC-{uuid.uuid4().hex[:10].upper()}"
        stored = UPLOAD_DIR / f"{doc_id}{suffix}"
        stored.write_bytes(data)

        document = SourceDocument(
            doc_id=doc_id,
            batch_id=batch_id,
            original_filename=filename,
            stored_path=str(stored),
            mime_type=upload.content_type or "",
            file_hash=file_hash,
            size_bytes=len(data),
            doc_type=doc_type,
            language=language,
            uploader_id=user.id,
            status=DocumentStatus.QUEUED,
        )
        db.add(document)
        db.flush()
        audit.log_action(
            db, ActionType.DOCUMENT_UPLOADED, user_id=user.id, actor_label=user.username,
            entity_type="document", entity_id=doc_id, document_id=document.id,
            new_value=filename,
            detail=f"{len(data)} bytes, type {doc_type}, language {language}, batch {batch_id}",
        )
        created_documents.append(document)
        accepted.append(doc_id)

    batch.file_count = len(created_documents)
    batch.skipped_duplicates = len(skipped)
    db.commit()

    for document in created_documents:
        if process_sync:
            process_now(document.id)
        else:
            enqueue(document.id)

    # The worker writes through its own session, so this request's identity map
    # is stale - expire it or the response reports pre-processing values
    # (status "queued", total_latency_ms 0, no record).
    db.expire_all()
    refreshed = [db.get(SourceDocument, d.id) for d in created_documents]
    return {
        "batch_id": batch_id,
        "accepted": accepted,
        "skipped_duplicates": skipped,
        "processing_mode": "synchronous" if process_sync else "queued",
        "documents": [document_dict(d) for d in refreshed if d],
        "queue": queue_status(),
    }


@router.get("")
def list_documents(
    status: str | None = None,
    batch_id: str | None = None,
    limit: int = 50,
    user: User = Depends(security.require("documents:read")),
    db: Session = Depends(get_db),
):
    query = select(SourceDocument).order_by(SourceDocument.created_at.desc())
    if status:
        query = query.where(SourceDocument.status == DocumentStatus(status))
    if batch_id:
        query = query.where(SourceDocument.batch_id == batch_id)
    rows = db.execute(query.limit(min(limit, 500))).scalars().all()
    return {"count": len(rows), "documents": [document_dict(d) for d in rows]}


@router.get("/queue")
def queue(user: User = Depends(security.require("documents:read"))):
    return queue_status()


@router.get("/batches")
def batches(user: User = Depends(security.require("documents:read")),
            db: Session = Depends(get_db)):
    rows = db.execute(select(Batch).order_by(Batch.created_at.desc()).limit(50)).scalars().all()
    out = []
    for batch in rows:
        documents = db.execute(
            select(SourceDocument).where(SourceDocument.batch_id == batch.batch_id)
        ).scalars().all()
        out.append({
            "batch_id": batch.batch_id,
            "label": batch.label,
            "file_count": batch.file_count,
            "skipped_duplicates": batch.skipped_duplicates,
            "created_at": iso_utc(batch.created_at),
            "statuses": sorted({d.status.value for d in documents}),
            "documents": [document_dict(d) for d in documents],
        })
    return {"count": len(out), "batches": out}


@router.get("/{doc_id}")
def get_document(doc_id: str, user: User = Depends(security.require("documents:read")),
                 db: Session = Depends(get_db)):
    document = db.execute(
        select(SourceDocument).where(SourceDocument.doc_id == doc_id)
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    payload = document_dict(document, include_ocr=True)
    entries = db.execute(
        select(AuditLog)
        .where(AuditLog.document_id == document.id)
        .order_by(AuditLog.created_at.desc())
    ).scalars().all()
    payload["audit_trail"] = [audit.to_dict(entry) for entry in entries]
    return payload


@router.post("/{doc_id}/process")
def reprocess(doc_id: str, user: User = Depends(security.require("documents:upload")),
              db: Session = Depends(get_db)):
    document = db.execute(
        select(SourceDocument).where(SourceDocument.doc_id == doc_id)
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    document.status = DocumentStatus.QUEUED
    document.progress_pct = 0
    document.error_message = ""
    db.commit()
    return process_now(document.id)


@router.get("/{doc_id}/media/{variant}")
def media(doc_id: str, variant: str, user: User = Depends(security.require("documents:read")),
          db: Session = Depends(get_db)):
    document = db.execute(
        select(SourceDocument).where(SourceDocument.doc_id == doc_id)
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    mapping = {
        "original": document.stored_path,
        "enhanced": document.enhanced_path,
        "preview": document.preview_path,
    }
    path = mapping.get(variant)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"Variant '{variant}' is not available yet.")
    import mimetypes

    media_type, _ = mimetypes.guess_type(path)
    if media_type is None:
        media_type = "application/pdf" if path.lower().endswith(".pdf") else "image/png"
    return FileResponse(path, media_type=media_type, filename=Path(path).name)
