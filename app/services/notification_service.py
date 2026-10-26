"""Email notifications via SMTP (aiosmtplib). SMTP failures never propagate to callers."""
import asyncio
import html
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.branding.documents import render_email_shell_html
from app.core.config import settings
from app.models.enums import (
    NotificationKind,
    NotificationStatus,
    RegistrationStatus,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.models.team import Team, TeamMember

logger = logging.getLogger("notification_service")


async def _resolve_email(db: AsyncSession, profile_id: UUID) -> Optional[str]:
    result = await db.execute(
        select(Profile)
        .options(selectinload(Profile.user))
        .where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        return None
    if profile.contact_email:
        return profile.contact_email
    if profile.user:
        return profile.user.email
    return None


def _amount_inr(paise: int) -> str:
    return f"₹{paise / 100:.2f}"


def _app_url() -> str:
    return settings.APP_PUBLIC_BASE_URL.rstrip("/")


def render_email_html(
    *,
    title: str,
    greeting: str,
    paragraphs: list[str],
    details: Optional[list[tuple[str, str]]] = None,
    cta_url: Optional[str] = None,
    cta_label: Optional[str] = None,
    footnote: Optional[str] = None,
    extra_body_html: Optional[str] = None,
) -> str:
    """Branded multipart HTML body for KRATOS transactional mail."""
    paras = "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:#334155;">'
        f"{html.escape(p)}</p>"
        for p in paragraphs
        if p
    )
    detail_rows = ""
    if details:
        cells = "".join(
            "<tr>"
            f'<td style="padding:8px 0;font-size:13px;color:#64748b;width:38%;">'
            f"{html.escape(k)}</td>"
            f'<td style="padding:8px 0;font-size:14px;color:#0f172a;font-weight:600;">'
            f"{html.escape(v)}</td>"
            "</tr>"
            for k, v in details
        )
        detail_rows = (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="margin:8px 0 20px;border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;">'
            f"{cells}</table>"
        )
    cta = ""
    if cta_url and cta_label:
        cta = (
            '<p style="margin:24px 0 8px;">'
            f'<a href="{html.escape(cta_url)}" '
            'style="display:inline-block;background:#0f172a;color:#f8fafc;'
            "text-decoration:none;font-weight:600;font-size:14px;"
            'padding:12px 22px;border-radius:8px;">'
            f"{html.escape(cta_label)}</a></p>"
        )
    note = ""
    if footnote:
        note = (
            f'<p style="margin:28px 0 0;font-size:12px;line-height:1.5;color:#94a3b8;">'
            f"{html.escape(footnote)}</p>"
        )

    body_html = f"""
          <h1 style="margin:0 0 16px;font-size:20px;line-height:1.3;color:#0f172a;font-weight:700;">
            {html.escape(title)}</h1>
          <p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:#334155;">
            {html.escape(greeting)}</p>
          {paras}
          {detail_rows}
          {cta}
          {extra_body_html or ""}
          {note}
    """
    return render_email_shell_html(
        title=title,
        body_html=body_html,
        footer_note="Association of Computer Engineers · Department of CSE · Easwari Engineering College",
    )


def _html_from_plain(body: str, subject: str) -> str:
    parts = [p.strip() for p in body.split("\n\n") if p.strip()]
    paragraphs = []
    for block in parts:
        paragraphs.extend(line for line in block.split("\n") if line.strip())
    return render_email_html(
        title=subject,
        greeting=paragraphs[0] if paragraphs else "Hello,",
        paragraphs=paragraphs[1:] if len(paragraphs) > 1 else [],
        cta_url=_app_url(),
        cta_label="Open KRATOS",
    )


async def _send_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    html_body: Optional[str] = None,
    inline_images: Optional[Sequence[tuple[str, str, bytes]]] = None,
) -> tuple[bool, Optional[str]]:
    from app.services.email_service import send_email

    return await send_email(
        to_email=to_email,
        subject=subject,
        text_body=body,
        html_body=html_body,
        inline_images=inline_images,
    )


async def _deliver_notification_bg(
    notification_id: UUID,
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    inline_images: Optional[Sequence[tuple[str, str, bytes]]] = None,
) -> None:
    from app.db.session import AsyncSessionLocal

    ok, err = await _send_email(
        to_email, subject, body, html_body=html_body, inline_images=inline_images
    )
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Notification).where(Notification.id == notification_id))
            notif = result.scalar_one_or_none()
            if notif:
                if ok:
                    notif.status = NotificationStatus.SENT
                    notif.sent_at = datetime.now(timezone.utc)
                    notif.error = None
                else:
                    notif.status = NotificationStatus.FAILED
                    notif.error = err
                await session.commit()
    except Exception as exc:
        logger.warning("Failed to update notification status for %s: %s", notification_id, exc)


async def create_and_send(
    db: AsyncSession,
    profile_id: UUID,
    kind: NotificationKind,
    subject: str,
    body: str,
    *,
    channel: str = "EMAIL",
    payload: Optional[dict[str, Any]] = None,
    payment_id: Optional[UUID] = None,
    registration_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    html_body: Optional[str] = None,
) -> Notification:
    stored_payload = dict(payload or {})
    if html_body:
        stored_payload["html_body"] = html_body

    notification = Notification(
        profile_id=profile_id,
        kind=kind,
        channel=channel,
        status=NotificationStatus.PENDING,
        subject=subject,
        body=body,
        payload=stored_payload or None,
        payment_id=payment_id,
        registration_id=registration_id,
        team_id=team_id,
    )
    db.add(notification)
    await db.flush()

    to_email = await _resolve_email(db, profile_id)
    if not to_email:
        notification.status = NotificationStatus.FAILED
        notification.error = "No contact email for profile"
        await db.commit()
        return notification

    await db.commit()
    await db.refresh(notification)

    effective_html = html_body or _html_from_plain(body, subject)
    asyncio.create_task(
        _deliver_notification_bg(notification.id, to_email, subject, body, effective_html)
    )
    return notification


async def create_and_send_with_inline_image(
    db: AsyncSession,
    profile_id: UUID,
    kind: NotificationKind,
    subject: str,
    body: str,
    *,
    html_body: str,
    inline_images: Sequence[tuple[str, str, bytes]],
    channel: str = "EMAIL",
    payload: Optional[dict[str, Any]] = None,
    payment_id: Optional[UUID] = None,
    registration_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
) -> Notification:
    stored_payload = dict(payload or {})
    stored_payload["html_body"] = html_body
    stored_payload["has_inline_qr"] = True

    notification = Notification(
        profile_id=profile_id,
        kind=kind,
        channel=channel,
        status=NotificationStatus.PENDING,
        subject=subject,
        body=body,
        payload=stored_payload or None,
        payment_id=payment_id,
        registration_id=registration_id,
        team_id=team_id,
    )
    db.add(notification)
    await db.flush()

    to_email = stored_payload.get("to_email") or await _resolve_email(db, profile_id)
    if not to_email:
        notification.status = NotificationStatus.FAILED
        notification.error = "No contact email for profile"
        await db.commit()
        return notification

    await db.commit()
    await db.refresh(notification)

    asyncio.create_task(
        _deliver_notification_bg(
            notification.id,
            to_email,
            subject,
            body,
            html_body,
            inline_images=inline_images,
        )
    )
    return notification


async def create_and_deliver_inline_image_sync(
    db: AsyncSession,
    profile_id: UUID,
    kind: NotificationKind,
    subject: str,
    body: str,
    *,
    html_body: str,
    inline_images: Sequence[tuple[str, str, bytes]],
    to_email: str,
    channel: str = "EMAIL",
    payload: Optional[dict[str, Any]] = None,
    payment_id: Optional[UUID] = None,
    registration_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
) -> Notification:
    """Create notification row and deliver via SMTP in-request (for admin resend)."""
    stored_payload = dict(payload or {})
    stored_payload["html_body"] = html_body
    stored_payload["has_inline_qr"] = True
    stored_payload["to_email"] = to_email

    notification = Notification(
        profile_id=profile_id,
        kind=kind,
        channel=channel,
        status=NotificationStatus.PENDING,
        subject=subject,
        body=body,
        payload=stored_payload or None,
        payment_id=payment_id,
        registration_id=registration_id,
        team_id=team_id,
    )
    db.add(notification)
    await db.flush()

    if not to_email.strip():
        notification.status = NotificationStatus.FAILED
        notification.error = "No contact email for recipient"
        await db.commit()
        return notification

    ok, err = await _send_email(
        to_email,
        subject,
        body,
        html_body=html_body,
        inline_images=inline_images,
    )
    if ok:
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(timezone.utc)
        notification.error = None
    else:
        notification.status = NotificationStatus.FAILED
        notification.error = err
    await db.commit()
    await db.refresh(notification)
    return notification


async def resend(db: AsyncSession, notification_id: UUID) -> Notification:
    result = await db.execute(select(Notification).where(Notification.id == notification_id))
    notification = result.scalar_one_or_none()
    if notification is None:
        raise ValueError(f"Notification {notification_id} not found")

    notification.status = NotificationStatus.PENDING
    notification.error = None
    notification.sent_at = None
    await db.flush()

    to_email = await _resolve_email(db, notification.profile_id)
    if not to_email:
        notification.status = NotificationStatus.FAILED
        notification.error = "No contact email for profile"
        await db.commit()
        return notification

    await db.commit()
    await db.refresh(notification)

    html_body = None
    if isinstance(notification.payload, dict):
        html_body = notification.payload.get("html_body")
    if not html_body:
        html_body = _html_from_plain(notification.body, notification.subject)

    asyncio.create_task(
        _deliver_notification_bg(
            notification.id,
            to_email,
            notification.subject,
            notification.body,
            html_body,
        )
    )
    return notification


async def notify_payment_confirmed(db: AsyncSession, payment_id: UUID) -> None:
    """Per-participant registration confirmation with embedded QR (post-commit)."""
    from app.services.confirmation_email import process_payment_confirmation_emails

    await process_payment_confirmation_emails(db, payment_id)


async def notify_member_joined(
    db: AsyncSession,
    *,
    leader_profile_id: UUID,
    team_id: UUID,
    member_profile_id: UUID,
    event_id: UUID,
) -> None:
    member_res = await db.execute(select(Profile).where(Profile.id == member_profile_id))
    member = member_res.scalar_one_or_none()
    team_res = await db.execute(select(Team).where(Team.id == team_id))
    team = team_res.scalar_one_or_none()
    ev_res = await db.execute(select(Event).where(Event.id == event_id))
    event = ev_res.scalar_one_or_none()

    member_name = member.full_name if member else "A member"
    team_name = team.name if team else "your team"
    event_name = event.name if event else "the event"

    subject = f"{member_name} joined {team_name}"
    body = (
        f"{member_name} has joined {team_name} for {event_name}.\n\n"
        f"View your team in the KRATOS app.\n{_app_url()}\n"
    )
    html_body = render_email_html(
        title="New team member",
        greeting=f"{member_name} just joined {team_name}.",
        paragraphs=[
            f"They're now on your roster for {event_name}.",
            "Open KRATOS to review the full team and invite status.",
        ],
        details=[("Team", team_name), ("Event", event_name), ("Member", member_name)],
        cta_url=_app_url(),
        cta_label="View team",
    )
    await create_and_send(
        db,
        leader_profile_id,
        NotificationKind.MEMBER_JOINED,
        subject,
        body,
        team_id=team_id,
        payload={"member_profile_id": str(member_profile_id)},
        html_body=html_body,
    )


async def notify_member_confirmation(db: AsyncSession, team_member_id: UUID) -> None:
    """Per-member registration confirmation with embedded personal QR."""
    from app.services.confirmation_email import send_team_member_confirmation

    await send_team_member_confirmation(db, team_member_id)


async def notify_team_completed(db: AsyncSession, leader_profile_id: UUID, team_id: UUID) -> None:
    team_res = await db.execute(select(Team).where(Team.id == team_id))
    team = team_res.scalar_one_or_none()
    if not team:
        return
    ev_res = await db.execute(select(Event).where(Event.id == team.event_id))
    event = ev_res.scalar_one_or_none()

    event_name = event.name if event else "the event"
    team_name = team.name if team else "Your team"

    subject = f"{team_name} is complete — {event_name}"
    body = (
        f"Great news! {team_name} has reached the required size for {event_name}.\n\n"
        f"{_app_url()}\n"
    )
    html_body = render_email_html(
        title="Team complete",
        greeting=f"{team_name} is ready for {event_name}.",
        paragraphs=[
            "You've reached the required roster size. Make sure every member has their QR pass before event day.",
        ],
        details=[("Team", team_name), ("Event", event_name)],
        cta_url=_app_url(),
        cta_label="View team",
    )
    await create_and_send(
        db,
        leader_profile_id,
        NotificationKind.TEAM_COMPLETED,
        subject,
        body,
        team_id=team_id,
        html_body=html_body,
    )


async def notify_refund(
    db: AsyncSession,
    payment_id: UUID,
    *,
    reason: Optional[str] = None,
    amount_paise: Optional[int] = None,
) -> None:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        return

    amount = amount_paise if amount_paise is not None else payment.amount_paise
    amount_str = _amount_inr(amount)
    subject = "Refund processed — KRATOS'26"
    body = f"A refund of {amount_str} has been issued to your original payment method.\n"
    if reason:
        body += f"\nReason: {reason}\n"

    details: list[tuple[str, str]] = [("Refund amount", amount_str), ("Payment ID", str(payment.id))]
    if reason:
        details.append(("Reason", reason))

    html_body = render_email_html(
        title="Refund processed",
        greeting="A refund has been issued for your KRATOS'26 payment.",
        paragraphs=[
            f"{amount_str} will return to your original payment method. "
            "Bank timelines vary — allow a few business days.",
            *( [f"Reason: {reason}"] if reason else [] ),
        ],
        details=details,
        cta_url=_app_url(),
        cta_label="Open KRATOS",
        footnote="If the amount does not appear within 7 business days, reply to this email with your payment ID.",
    )

    await create_and_send(
        db,
        payment.payer_profile_id,
        NotificationKind.REFUND,
        subject,
        body,
        payment_id=payment_id,
        payload={"reason": reason, "amount_paise": amount},
        html_body=html_body,
    )


async def _eligible_event_profile_ids(db: AsyncSession, event_id: UUID) -> set[UUID]:
    profile_ids: set[UUID] = set()

    solo_regs = await db.execute(
        select(Registration).where(
            Registration.event_id == event_id,
            Registration.profile_id.isnot(None),
            Registration.status == RegistrationStatus.CONFIRMED,
        )
    )
    for reg in solo_regs.scalars().all():
        if reg.profile_id:
            profile_ids.add(reg.profile_id)

    members = await db.execute(
        select(TeamMember)
        .join(Team, TeamMember.team_id == Team.id)
        .where(
            TeamMember.event_id == event_id,
            TeamMember.status == TeamMemberStatus.ACTIVE,
            Team.status.in_([TeamStatus.PAID, TeamStatus.COMPLETE]),
        )
    )
    for tm in members.scalars().all():
        profile_ids.add(tm.profile_id)
    return profile_ids


async def _load_event_for_email(db: AsyncSession, event_id: UUID) -> Event:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Event).options(selectinload(Event.rules)).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise ValueError("Event not found")
    return event


def _kind_for_template(template_id: str) -> NotificationKind:
    if template_id == "event_reminder" or template_id == "submission_deadline_reminder":
        return NotificationKind.REMINDER
    return NotificationKind.ANNOUNCEMENT


async def count_eligible_recipients(db: AsyncSession, event_id: UUID) -> int:
    return len(await _eligible_event_profile_ids(db, event_id))


async def campaign_exists(db: AsyncSession, client_request_id: str) -> bool:
    if not client_request_id:
        return False
    result = await db.execute(
        select(Notification.id)
        .where(Notification.payload["client_request_id"].as_string() == client_request_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def send_announcement(
    db: AsyncSession,
    event_id: UUID,
    subject: str,
    body: str,
) -> list[Notification]:
    from app.services.email_templates.catalog import TemplateInputs, render_template

    event = await _load_event_for_email(db, event_id)
    rendered = render_template(
        "event_announcement",
        event,
        TemplateInputs(subject=subject, body=body),
    )
    outcome = await send_template_campaign(
        db,
        event_id=event_id,
        template_id="event_announcement",
        rendered=rendered,
        initiated_by_admin_id=None,
        client_request_id=None,
    )
    return outcome["notifications"]


async def send_reminder(
    db: AsyncSession,
    event_id: UUID,
    subject: str,
    body: str,
) -> list[Notification]:
    from app.services.email_templates.catalog import TemplateInputs, render_template

    event = await _load_event_for_email(db, event_id)
    rendered = render_template(
        "event_reminder",
        event,
        TemplateInputs(subject=subject, body=body),
    )
    outcome = await send_template_campaign(
        db,
        event_id=event_id,
        template_id="event_reminder",
        rendered=rendered,
        initiated_by_admin_id=None,
        client_request_id=None,
    )
    return outcome["notifications"]


async def send_template_campaign(
    db: AsyncSession,
    *,
    event_id: UUID,
    template_id: str,
    rendered,
    initiated_by_admin_id: Optional[UUID],
    client_request_id: Optional[str],
) -> dict[str, Any]:
    from uuid import uuid4

    from app.services.email_service import smtp_configuration_error
    from app.services.email_templates.catalog import get_template

    tpl = get_template(template_id)
    if tpl.preview_only:
        raise ValueError("This template cannot be sent as a bulk campaign.")
    smtp_err = smtp_configuration_error()
    if smtp_err:
        raise ValueError(smtp_err)

    if client_request_id and await campaign_exists(db, client_request_id):
        raise ValueError("Duplicate campaign request (idempotency key already used).")

    profile_ids = await _eligible_event_profile_ids(db, event_id)
    campaign_id = str(uuid4())
    kind = _kind_for_template(template_id)
    notifications: list[Notification] = []
    for pid in profile_ids:
        payload: dict[str, Any] = {
            "event_id": str(event_id),
            "template_id": template_id,
            "campaign_id": campaign_id,
        }
        if initiated_by_admin_id:
            payload["initiated_by_admin_id"] = str(initiated_by_admin_id)
        if client_request_id:
            payload["client_request_id"] = client_request_id
        n = await create_and_send(
            db,
            pid,
            kind,
            rendered.subject,
            rendered.text_body,
            payload=payload,
            html_body=rendered.html_body,
        )
        notifications.append(n)

    sent = sum(1 for n in notifications if n.status != NotificationStatus.FAILED)
    failed = len(notifications) - sent
    return {
        "campaign_id": campaign_id,
        "attempted": len(notifications),
        "sent": sent,
        "failed": failed,
        "notifications": notifications,
    }


async def send_test_template_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> tuple[bool, Optional[str]]:
    from app.services.email_service import send_email, smtp_configuration_error

    err = smtp_configuration_error()
    if err:
        return False, err
    return await send_email(
        to_email=to_email.strip(),
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


async def list_recent_campaigns(
    db: AsyncSession, *, event_id: Optional[UUID] = None, limit: int = 30
) -> list[dict]:
    result = await db.execute(
        select(Notification)
        .where(Notification.payload["campaign_id"].as_string().isnot(None))
        .order_by(Notification.created_at.desc())
        .limit(500)
    )
    campaigns: dict[str, dict] = {}
    for n in result.scalars().all():
        payload = n.payload or {}
        cid = payload.get("campaign_id")
        if not cid or payload.get("test"):
            continue
        ev = payload.get("event_id")
        if event_id and ev != str(event_id):
            continue
        if cid not in campaigns:
            campaigns[cid] = {
                "campaign_id": cid,
                "template_id": payload.get("template_id"),
                "event_id": ev,
                "created_at": n.created_at,
                "attempted": 0,
                "sent": 0,
                "failed": 0,
                "sample_errors": [],
            }
        row = campaigns[cid]
        row["attempted"] += 1
        if n.status == NotificationStatus.SENT:
            row["sent"] += 1
        elif n.status == NotificationStatus.FAILED:
            row["failed"] += 1
            if n.error and len(row["sample_errors"]) < 3:
                row["sample_errors"].append(n.error[:200])
    ordered = sorted(campaigns.values(), key=lambda r: r["created_at"], reverse=True)
    return ordered[:limit]
