"""Authentication endpoints (FR-14)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import security
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

DEMO_ACCOUNTS = [
    {"username": "operator", "password": "operator123", "role": "operator"},
    {"username": "reviewer", "password": "reviewer123", "role": "reviewer"},
    {"username": "supervisor", "password": "supervisor123", "role": "supervisor"},
    {"username": "survey", "password": "survey123", "role": "survey_official"},
    {"username": "auditor", "password": "auditor123", "role": "auditor"},
    {"username": "admin", "password": "admin123", "role": "admin"},
]


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else ""
    user = security.authenticate(db, payload.username, payload.password, ip)
    token = security.create_session(db, user, ip)
    return {
        "token": token,
        "user": security.user_dict(user),
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(request: Request, user: User = Depends(security.get_current_user),
           db: Session = Depends(get_db)):
    security.destroy_session(db, getattr(request.state, "token", ""))
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(security.get_current_user)):
    return security.user_dict(user)


@router.get("/demo-accounts")
def demo_accounts():
    """Seeded prototype accounts, surfaced for the judge-facing demo."""
    return {"accounts": DEMO_ACCOUNTS, "note": "Prototype tier only - production uses department SSO + MFA."}


@router.get("/roles")
def roles():
    return {
        "roles": [
            {
                "role": role.value,
                "label": security.ROLE_LABELS.get(role, role.value),
                "permissions": sorted(permissions),
            }
            for role, permissions in security.PERMISSIONS.items()
        ]
    }
