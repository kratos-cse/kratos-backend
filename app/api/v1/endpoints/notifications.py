import uuid

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
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


class TemplatePreviewRequest(BaseModel):
    event_id: uuid.UUID
    template_id: str = Field(min_length=1, max_length=64)
    fields: dict[str, Any] = Field(default_factory=dict)


class RecipientPreviewRequest(BaseModel):
    event_id: uuid.UUID


class TestSendRequest(BaseModel):
    event_id: uuid.UUID
    template_id: str = Field(min_length=1, max_length=64)
    to_email: EmailStr
    fields: dict[str, Any] = Field(default_factory=dict)


class CampaignSendRequest(BaseModel):
    event_id: uuid.UUID
    template_id: str = Field(min_length=1, max_length=64)
    fields: dict[str, Any] = Field(default_factory=dict)
    client_request_id: Optional[str] = Field(default=None, max_length=128)


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


@router.get("/admin/notifications/templates")
async def list_email_templates(
    _: AdminUser = Depends(require_permission("announcement", "reminder", "notification")),
):
    from app.services.email_templates import list_templates

    return {"status": "success", "data": list_templates()}


@router.post("/admin/notifications/preview")
async def preview_email_template(
    body: TemplatePreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("announcement", "reminder", "notification")),
):
    from app.services.email_templates import render_template, validate_template_inputs

    try:
        event = await notification_service._load_event_for_email(db, body.event_id)
        inputs = validate_template_inputs(body.template_id, body.fields)
        rendered = render_template(body.template_id, event, inputs)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown template")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    return {
        "status": "success",
        "data": {
            "subject": rendered.subject,
            "text_body": rendered.text_body,
            "html_body": rendered.html_body,
        },
    }


@router.post("/admin/notifications/recipient-preview")
async def preview_recipient_count(
    body: RecipientPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("announcement", "reminder", "notification")),
):
    try:
        count = await notification_service.count_eligible_recipients(db, body.event_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return {"status": "success", "data": {"recipient_count": count}}


@router.post("/admin/notifications/test-send")
async def test_send_email(
    body: TestSendRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("announcement", "reminder", "notification")),
):
    from app.services.email_templates import get_template, render_template, validate_template_inputs

    tpl = get_template(body.template_id)
    if tpl.preview_only:
        pass
    try:
        event = await notification_service._load_event_for_email(db, body.event_id)
        inputs = validate_template_inputs(body.template_id, body.fields)
        rendered = render_template(body.template_id, event, inputs)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown template")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    ok, err = await notification_service.send_test_template_email(
        to_email=str(body.to_email),
        subject=f"[TEST] {rendered.subject}",
        text_body=rendered.text_body,
        html_body=rendered.html_body,
    )
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, err or "SMTP send failed")
    return {"status": "success", "data": {"delivered": True}}


@router.post("/admin/notifications/campaign")
async def send_email_campaign(
    body: CampaignSendRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission("announcement", "reminder", "notification")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    from app.services.email_templates import render_template, validate_template_inputs

    client_id = body.client_request_id or idempotency_key
    try:
        event = await notification_service._load_event_for_email(db, body.event_id)
        inputs = validate_template_inputs(body.template_id, body.fields)
        rendered = render_template(body.template_id, event, inputs)
        outcome = await notification_service.send_template_campaign(
            db,
            event_id=body.event_id,
            template_id=body.template_id,
            rendered=rendered,
            initiated_by_admin_id=admin.id,
            client_request_id=client_id,
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown template")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    return {
        "status": "success",
        "data": {
            "campaign_id": outcome["campaign_id"],
            "attempted": outcome["attempted"],
            "queued": outcome["sent"],
            "failed_immediate": outcome["failed"],
            "notifications": [_notification_out(n) for n in outcome["notifications"][:20]],
        },
    }


@router.get("/admin/notifications/campaigns")
async def list_email_campaigns(
    event_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("announcement", "reminder", "notification")),
):
    rows = await notification_service.list_recent_campaigns(db, event_id=event_id)
    return {"status": "success", "data": rows}
