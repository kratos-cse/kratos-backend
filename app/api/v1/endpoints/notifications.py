import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import admin_has_permission, get_current_active_admin, require_permission
from app.core.security import get_current_profile, get_current_user
from app.db.session import get_db
from app.models.admin import AdminUser
from app.models.notification import Notification
from app.models.profile import Profile
from app.models.user import User
from app.services import notification_service

router = APIRouter(tags=["Notifications"])


class ResendRequest(BaseModel):
    notification_id: uuid.UUID


class EventBroadcastRequest(BaseModel):
    event_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)


def _notification_out(n: Notification) -> dict:
    return {
        "id": n.id,
        "profile_id": n.profile_id,
        "kind": n.kind,
        "channel": n.channel,
        "status": n.status,
        "subject": n.subject,
        "payment_id": n.payment_id,
        "registration_id": n.registration_id,
        "team_id": n.team_id,
        "error": n.error,
        "created_at": n.created_at,
        "sent_at": n.sent_at,
    }


async def _optional_admin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdminUser | None:
    try:
        return await get_current_active_admin(current_user=current_user, db=db)
    except HTTPException:
        return None


@router.post("/notifications/resend")
async def resend_notification(
    body: ResendRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
    admin: AdminUser | None = Depends(_optional_admin),
):
    result = await db.execute(select(Notification).where(Notification.id == body.notification_id))
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")

    is_owner = notification.profile_id == profile.id
    is_admin = admin is not None and admin_has_permission(admin, "notification")
    if not is_owner and not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot resend this notification")

    updated = await notification_service.resend(db, body.notification_id)
    return {"status": "success", "data": _notification_out(updated)}


@router.post("/admin/notifications/announcement")
async def admin_announcement(
    body: EventBroadcastRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("announcement", "notification")),
):
    sent = await notification_service.send_announcement(db, body.event_id, body.subject, body.body)
    return {
        "status": "success",
        "data": {"sent_count": len(sent), "notifications": [_notification_out(n) for n in sent[:20]]},
    }


@router.post("/admin/notifications/reminder")
async def admin_reminder(
    body: EventBroadcastRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("reminder", "notification")),
):
    sent = await notification_service.send_reminder(db, body.event_id, body.subject, body.body)
    return {
        "status": "success",
        "data": {"sent_count": len(sent), "notifications": [_notification_out(n) for n in sent[:20]]},
    }
