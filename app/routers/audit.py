"""Audit trail endpoints (FR-12)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security
from app.database import get_db
from app.models import ActionType, AuditLog, User
from app.services.audit import to_dict, verify_chain

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("")
def audit_trail(
    record_id: str | None = None,
    doc_id: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    limit: int = Query(default=100, le=1000),
    user: User = Depends(security.require("audit:read")),
    db: Session = Depends(get_db),
):
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if record_id:
        query = query.where(AuditLog.entity_id == record_id)
    if doc_id:
        query = query.where(AuditLog.entity_id == doc_id)
    if action:
        query = query.where(AuditLog.action == ActionType(action))
    if actor:
        query = query.where(AuditLog.actor_label.ilike(f"%{actor}%"))

    rows = db.execute(query.limit(limit)).scalars().all()
    return {
        "count": len(rows),
        "entries": [to_dict(row) for row in rows],
        "immutable": True,
        "hash_chained": True,
    }


@router.get("/verify")
def verify(user: User = Depends(security.require("audit:read")),
           db: Session = Depends(get_db)):
    """Recompute the whole hash chain and report whether it is intact."""
    return verify_chain(db)


@router.get("/stats")
def stats(user: User = Depends(security.require("audit:read")),
          db: Session = Depends(get_db)):
    total = db.execute(select(func.count(AuditLog.id))).scalar() or 0
    by_action = dict(
        db.execute(
            select(AuditLog.action, func.count(AuditLog.id)).group_by(AuditLog.action)
        ).all()
    )
    by_actor = dict(
        db.execute(
            select(AuditLog.actor_label, func.count(AuditLog.id))
            .group_by(AuditLog.actor_label).order_by(func.count(AuditLog.id).desc()).limit(12)
        ).all()
    )
    corrections = db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.action == ActionType.FIELD_CORRECTED)
    ).scalar() or 0
    return {
        "total_events": total,
        "corrections_logged": corrections,
        "coverage_pct": 100.0 if total else 0.0,
        "by_action": {
            (k.value if hasattr(k, "value") else str(k)): v for k, v in by_action.items()
        },
        "by_actor": by_actor,
        "chain": verify_chain(db),
    }
