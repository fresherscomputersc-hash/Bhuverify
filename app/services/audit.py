"""
Immutable, hash-chained audit trail (SRS FR-12).

Every AI decision and every human action writes one append-only row. Each row
stores the SHA-256 of the previous row, so tampering with any historical entry
breaks the chain and is detectable via `verify_chain()`.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActionType, AuditLog, utcnow, iso_utc

GENESIS = "0" * 64

# Prototype tier runs an in-process thread worker plus request threads in one
# process. The hash chain is read-last-then-append, so two threads racing
# would read the same prev_hash and fork the chain. A process-wide lock
# serialises appends. Pilot tier (Redis/Celery + Postgres) must move this to
# a DB serialisable transaction / advisory lock instead.
_CHAIN_LOCK = threading.Lock()


def _canonical_timestamp(value: datetime | None) -> str:
    """Timestamp form used inside the hash.

    SQLite stores DATETIME without a UTC offset, so an entry hashed from an
    aware datetime in memory would not match the same row read back from the
    database. Normalising to naive UTC before hashing makes the chain verify
    identically before and after persistence.
    """
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _hash_payload(entry: AuditLog) -> str:
    payload = "|".join(
        [
            entry.log_id,
            str(entry.user_id or ""),
            entry.actor_label or "",
            entry.action.value if isinstance(entry.action, ActionType) else str(entry.action),
            entry.entity_type or "",
            entry.entity_id or "",
            entry.field_name or "",
            entry.old_value or "",
            entry.new_value or "",
            f"{entry.ai_confidence:.4f}",
            entry.detail or "",
            _canonical_timestamp(entry.created_at),
            entry.prev_hash or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log_action(
    db: Session,
    action: ActionType,
    *,
    actor_label: str = "system",
    user_id: int | None = None,
    entity_type: str = "",
    entity_id: str = "",
    document_id: int | None = None,
    field_name: str = "",
    old_value: str = "",
    new_value: str = "",
    ai_confidence: float = 0.0,
    detail: str = "",
    evidence_ref: str = "",
    ip_address: str = "",
    commit: bool = True,
) -> AuditLog:
    with _CHAIN_LOCK:
        last = db.execute(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
        ).scalar_one_or_none()
        prev_hash = last.entry_hash if last and last.entry_hash else GENESIS

        entry = AuditLog(
            log_id=f"AUD-{uuid.uuid4().hex[:12].upper()}",
            user_id=user_id,
            actor_label=actor_label,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            document_id=document_id,
            field_name=field_name,
            old_value=str(old_value)[:2000],
            new_value=str(new_value)[:2000],
            ai_confidence=float(ai_confidence or 0.0),
            detail=detail[:4000],
            evidence_ref=evidence_ref,
            ip_address=ip_address,
            prev_hash=prev_hash,
            created_at=utcnow(),
        )
        entry.entry_hash = _hash_payload(entry)
        db.add(entry)
        # Flush inside the lock so the row has an id and the next append in
        # this process sees it when sharing a session; cross-session
        # visibility still requires commit (see note on _CHAIN_LOCK).
        db.flush()
        if commit:
            db.commit()
            db.refresh(entry)
        return entry


def verify_chain(db: Session) -> dict:
    """Recompute every hash and report the first broken link, if any."""
    entries = db.execute(select(AuditLog).order_by(AuditLog.id.asc())).scalars().all()
    expected_prev = GENESIS
    broken_at: int | None = None
    for entry in entries:
        recomputed = _hash_payload(entry)
        if entry.prev_hash != expected_prev or recomputed != entry.entry_hash:
            broken_at = entry.id
            break
        expected_prev = entry.entry_hash
    return {
        "entries_checked": len(entries),
        "chain_intact": broken_at is None,
        "broken_at_log_id": broken_at,
        "head_hash": entries[-1].entry_hash if entries else GENESIS,
    }


def to_dict(entry: AuditLog) -> dict:
    return {
        "log_id": entry.log_id,
        "action": entry.action.value if isinstance(entry.action, ActionType) else entry.action,
        "actor": entry.actor_label,
        "user_id": entry.user_id,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "field_name": entry.field_name,
        "old_value": entry.old_value,
        "new_value": entry.new_value,
        "ai_confidence": entry.ai_confidence,
        "detail": entry.detail,
        "evidence_ref": entry.evidence_ref,
        "ip_address": entry.ip_address,
        "entry_hash": entry.entry_hash,
        "prev_hash": entry.prev_hash,
        "timestamp": iso_utc(entry.created_at),
    }
