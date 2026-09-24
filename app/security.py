"""
Authentication + role-based access control (SRS FR-14).

Prototype tier: opaque bearer tokens + seeded demo accounts, argon2id password
hashes. Production tier swaps `get_current_user` for the department SSO / MFA
flow without touching the route handlers - they only ever see a `User`.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ActionType, Role, Session as SessionRow, User, utcnow
from app.services import audit

_hasher = PasswordHasher()

# Permission matrix (SRS FR-14 "Roles and Permissions")
PERMISSIONS: dict[Role, set[str]] = {
    Role.OPERATOR: {
        "documents:upload", "documents:read", "records:read", "search:use",
        "map:read", "dashboard:read", "notifications:read",
    },
    Role.REVIEWER: {
        "documents:read", "records:read", "records:correct", "records:verify",
        "discrepancies:read", "search:use", "map:read", "dashboard:read",
        "notifications:read", "corrections:write",
    },
    Role.SUPERVISOR: {
        "documents:read", "records:read", "records:assign", "records:verify",
        "discrepancies:read", "search:use", "map:read", "dashboard:read",
        "dashboard:export", "notifications:read", "users:read",
    },
    Role.SURVEY_OFFICIAL: {
        "documents:read", "records:read", "map:read", "gis:manage", "search:use",
        "dashboard:read", "notifications:read",
    },
    Role.AUDITOR: {
        "audit:read", "records:read", "discrepancies:read", "dashboard:read",
        "dashboard:export", "notifications:read", "search:use",
    },
    Role.ADMIN: {
        "documents:upload", "documents:read", "records:read", "records:correct",
        "records:verify", "records:assign", "discrepancies:read", "search:use",
        "map:read", "gis:manage", "dashboard:read", "dashboard:export",
        "audit:read", "users:manage", "users:read", "system:read", "system:reseed",
        "notifications:read", "corrections:write",
    },
}

ROLE_LABELS = {
    Role.OPERATOR: "Digitization Operator",
    Role.REVIEWER: "Revenue Inspector / Reviewer",
    Role.SUPERVISOR: "Tehsildar / District Supervisor",
    Role.SURVEY_OFFICIAL: "Survey Department Official",
    Role.AUDITOR: "Auditor",
    Role.ADMIN: "System Administrator",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def can(user: User, permission: str) -> bool:
    return permission in PERMISSIONS.get(user.role, set())


def require(permission: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if not can(user, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{user.role.value}' lacks permission '{permission}'.",
            )
        return user

    return dependency


def create_session(db: Session, user: User, ip_address: str = "") -> str:
    token = secrets.token_urlsafe(32)
    db.add(SessionRow(
        token=token,
        user_id=user.id,
        expires_at=utcnow() + timedelta(minutes=settings.token_ttl_minutes),
        ip_address=ip_address[:64],
    ))
    db.commit()
    audit.log_action(
        db, ActionType.LOGIN, user_id=user.id, actor_label=f"{user.username} ({user.role.value})",
        entity_type="user", entity_id=str(user.id), ip_address=ip_address[:64],
        detail=f"Session issued for {user.designation or user.full_name}",
    )
    return token


def destroy_session(db: Session, token: str) -> None:
    row = db.get(SessionRow, token)
    if row:
        db.delete(row)
        db.commit()


def _extract_token(request: Request, authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get("bhuverify_token")


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    token = _extract_token(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    session_row = db.get(SessionRow, token)
    if session_row is None:
        raise HTTPException(status_code=401, detail="Session not found.")
    if session_row.expires_at.replace(tzinfo=utcnow().tzinfo) < utcnow():
        db.delete(session_row)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired.")
    user = db.get(User, session_row.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists.")
    request.state.user = user
    request.state.token = token
    return user


def user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role.value,
        "role_label": ROLE_LABELS.get(user.role, user.role.value),
        "designation": user.designation,
        "district_scope": user.district_scope,
        "permissions": sorted(PERMISSIONS.get(user.role, set())),
    }


def authenticate(db: Session, username: str, password: str, ip_address: str = "") -> User:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        audit.log_action(
            db, ActionType.LOGIN_FAILED, actor_label=username or "(blank)",
            entity_type="user", entity_id="", ip_address=ip_address[:64],
            detail="Invalid credentials",
        )
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return user
