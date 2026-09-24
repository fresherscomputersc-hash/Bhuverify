"""Notification service (SRS FR-15).

Prototype tier: in-app notification store. The channel abstraction keeps the
signature stable so email / SMS gateway / push can be added in the pilot tier
without touching any caller.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, NotificationStatus, Role, User, iso_utc
from app.services import audit
from app.models import ActionType


def notify(
    db: Session,
    *,
    title: str,
    message: str,
    role: Role | str | None = None,
    user_id: int | None = None,
    level: str = "info",
    link: str = "",
) -> Notification:
    """Create one notification row for a user, or fan out to a role."""
    target_users: list[User] = []
    if user_id is not None:
        user = db.get(User, user_id)
        if user:
            target_users = [user]
    elif role is not None:
        role_value = role.value if isinstance(role, Role) else role
        target_users = list(db.execute(select(User).where(User.role == role_value)).scalars().all())

    created: Notification | None = None
    for user in target_users:
        created = Notification(
            user_id=user.id,
            role_target="",
            level=level,
            title=title[:160],
            message=message[:2000],
            link=link,
            status=NotificationStatus.UNREAD,
        )
        db.add(created)
    db.commit()
    if created is not None:
        db.refresh(created)
        audit.log_action(
            db, ActionType.NOTIFICATION_SENT,
            actor_label="notification-service",
            entity_type="notification",
            entity_id=str(created.id),
            detail=f"{level}: {title} -> "
                   + (f"user#{created.user_id}" if created.user_id else f"role:{role}"),
        )
    return created  # type: ignore[return-value]


def list_for_user(db: Session, user: User, limit: int = 25) -> list[dict]:
    rows = db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [to_dict(row) for row in rows]


def unread_count(db: Session, user: User) -> int:
    return len(db.execute(
        select(Notification).where(Notification.user_id == user.id)
        .where(Notification.status == NotificationStatus.UNREAD)
    ).scalars().all())


def mark_read(db: Session, notification_id: int, user: User) -> bool:
    row = db.get(Notification, notification_id)
    if row is None or (row.user_id != user.id and user.role != Role.ADMIN):
        return False
    row.status = NotificationStatus.READ
    db.commit()
    return True


def to_dict(row: Notification) -> dict:
    return {
        "id": row.id,
        "level": row.level,
        "title": row.title,
        "message": row.message,
        "link": row.link,
        "status": row.status.value,
        "created_at": iso_utc(row.created_at),
    }
