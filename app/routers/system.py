"""System, user-management and notification endpoints (FR-14, FR-15)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.config import settings
from app.database import get_db
from app.models import ActionType, Role, User
from app.services import audit, notifications
from app.services.external_adapters import adapter_status
from app.services.gis_service import layer
from app.services.llm_groq import status as llm_status
from app.services.ocr_service import engine_status
from app.services.worker import queue_status

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status")
def status(user: User = Depends(security.get_current_user),
           db: Session = Depends(get_db)):
    from app.models import Discrepancy, LandRecord, SourceDocument
    from sqlalchemy import func

    return {
        "app": {
            "name": settings.app_name,
            "subtitle": settings.app_subtitle,
            "problem_statement": settings.problem_statement,
            "theme": settings.theme,
            "team": settings.team,
            "version": settings.version,
            "tier": "prototype",
        },
        "ocr": engine_status(),
        "llm": llm_status(),
        "gis": layer().summary(),
        "cross_db": adapter_status(),
        "queue": queue_status(),
        "confidence_bands": {
            "high_from": settings.CONFIDENCE_HIGH,
            "medium_from": settings.CONFIDENCE_MEDIUM,
            "low_below": settings.CONFIDENCE_MEDIUM,
        },
        "performance_budgets": {
            "ocr_extraction_s": settings.PERF_OCR_EXTRACTION_BUDGET_S,
            "validation_s": settings.PERF_VALIDATION_BUDGET_S,
            "search_s": settings.PERF_SEARCH_BUDGET_S,
        },
        "counts": {
            "documents": db.execute(select(func.count(SourceDocument.id))).scalar() or 0,
            "records": db.execute(select(func.count(LandRecord.id))).scalar() or 0,
            "discrepancies": db.execute(select(func.count(Discrepancy.id))).scalar() or 0,
            "audit_events": db.execute(
                select(func.count(audit.AuditLog.id))
            ).scalar() or 0,
        },
    }


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    designation: str = ""
    district_scope: str = ""


@router.get("/users")
def list_users(user: User = Depends(security.require("users:read")),
               db: Session = Depends(get_db)):
    rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return {
        "count": len(rows),
        "users": [
            {
                "id": row.id,
                "username": row.username,
                "full_name": row.full_name,
                "role": row.role.value,
                "role_label": security.ROLE_LABELS.get(row.role, row.role.value),
                "designation": row.designation,
                "district_scope": row.district_scope,
            }
            for row in rows
        ],
    }


@router.post("/users")
def create_user(payload: UserCreate, user: User = Depends(security.require("users:manage")),
                db: Session = Depends(get_db)):
    if db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Username '{payload.username}' already exists.")
    try:
        role = Role(payload.role)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role '{payload.role}'. Valid: {[r.value for r in Role]}",
        )
    new_user = User(
        username=payload.username,
        password_hash=security.hash_password(payload.password),
        full_name=payload.full_name,
        role=role,
        designation=payload.designation,
        district_scope=payload.district_scope or settings.district,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    audit.log_action(
        db, ActionType.API_ACCESSED, user_id=user.id, actor_label=user.username,
        entity_type="user", entity_id=str(new_user.id), new_value=new_user.username,
        detail=f"Created user '{new_user.username}' with role {role.value}",
    )
    return {"id": new_user.id, "username": new_user.username, "role": role.value}


# ---------------------------------------------------------------------------
# Notifications (FR-15)
# ---------------------------------------------------------------------------
@router.get("/notifications")
def my_notifications(user: User = Depends(security.require("notifications:read")),
                     db: Session = Depends(get_db)):
    return {
        "unread": notifications.unread_count(db, user),
        "notifications": notifications.list_for_user(db, user),
    }


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int,
                      user: User = Depends(security.require("notifications:read")),
                      db: Session = Depends(get_db)):
    if not notifications.mark_read(db, notification_id, user):
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"ok": True}


@router.post("/reseed")
def reseed(user: User = Depends(security.require("system:reseed")),
           db: Session = Depends(get_db)):
    """Rebuild the demo dataset from scratch (judges can replay the demo)."""
    from app.seed import reseed_all

    report = reseed_all(process_documents=True)
    audit.log_action(
        db, ActionType.API_ACCESSED, user_id=user.id, actor_label=user.username,
        entity_type="system", entity_id="reseed",
        detail=f"Demo dataset rebuilt: {report}",
    )
    return {"ok": True, "report": report}
