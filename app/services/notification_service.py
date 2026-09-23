"""Email notifications via SMTP (aiosmtplib). SMTP failures never propagate to callers."""
import asyncio
import html
import logging
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Optional
from uuid import UUID

import aiosmtplib
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


async def _send_smtp(
    to_email: str,
    subject: str,
    body: str,
    *,
    html_body: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        return False, "SMTP not configured (SMTP_HOST / SMTP_FROM missing)"

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        await asyncio.wait_for(
            aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                start_tls=settings.SMTP_TLS,
                timeout=3.0,
            ),
            timeout=4.0,
        )
        return True, None
    except Exception as exc:
        logger.exception("SMTP send failed to=%s subject=%s", to_email, subject)
        return False, str(exc)


async def _deliver_notification_bg(
    notification_id: UUID,
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
) -> None:
    from app.db.session import AsyncSessionLocal

    ok, err = await _send_smtp(to_email, subject, body, html_body=html_body)
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
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        logger.warning("notify_payment_confirmed: payment %s not found", payment_id)
        return

    reg_result = await db.execute(select(Registration).where(Registration.payment_id == payment_id))
    registration = reg_result.scalar_one_or_none()
    event_name = "your event"
    registration_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    venue = ""
    wa = ""

    if registration:
        registration_id = registration.id
        team_id = registration.team_id
        ev = await db.execute(select(Event).where(Event.id == registration.event_id))
        event = ev.scalar_one_or_none()
        if event:
            event_name = event.name
            venue = event.venue or ""
            wa = event.whatsapp_group_link or ""

    receipt_res = await db.execute(select(Receipt).where(Receipt.payment_id == payment_id))
    receipt = receipt_res.scalar_one_or_none()
    receipt_no = receipt.receipt_number if receipt else None
    from app.services.receipt_service import receipt_html_url

    receipt_link = receipt_html_url(payment_id)

    amount = _amount_inr(payment.amount_paise)
    subject = f"Payment confirmed — {event_name}"
    body_lines = [
        f"Your payment of {amount} for {event_name} has been received.",
        "",
        "Your registration is confirmed. Sign in to KRATOS to view your QR code for check-in.",
        f"Receipt (sign in required): {receipt_link}",
        _app_url(),
    ]
    if receipt_no:
        body_lines.insert(3, f"Receipt number: {receipt_no}")
    if venue:
        body_lines.append(f"Venue: {venue}")
    if wa:
        body_lines.append(f"Event WhatsApp group: {wa}")
    body = "\n".join(body_lines) + "\n"

    details: list[tuple[str, str]] = [
        ("Event", event_name),
        ("Amount paid", amount),
        ("Payment ID", str(payment.id)),
    ]
    if receipt_no:
        details.insert(2, ("Receipt number", receipt_no))
    if venue:
        details.append(("Venue", venue))

    team_paragraphs: list[str] = []
    if team_id:
        team_paragraphs.append(
            "Your team payment is confirmed. Open KRATOS to share your invite link — "
            "members can join only after you have paid."
        )

    html_body = render_email_html(
        title="Payment confirmed",
        greeting=f"You're all set for {event_name}.",
        paragraphs=[
            f"We've received your payment of {amount}. Your registration is confirmed.",
            *team_paragraphs,
            "Sign in to KRATOS to open your receipt and QR check-in pass. "
            "Bring the QR on event day — scanners verify it at the gate.",
            *( [f"Join the event WhatsApp group: {wa}"] if wa else [] ),
        ],
        details=details,
        cta_url=receipt_link,
        cta_label="View receipt & pass",
        footnote="Receipt links require your KRATOS sign-in. Do not forward this email to share access.",
    )

    await create_and_send(
        db,
        payment.payer_profile_id,
        NotificationKind.PAYMENT_CONFIRMATION,
        subject,
        body,
        payment_id=payment_id,
        registration_id=registration_id,
        team_id=team_id,
        payload={"amount_paise": payment.amount_paise, "receipt_number": receipt_no},
        html_body=html_body,
    )


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
    tm_res = await db.execute(select(TeamMember).where(TeamMember.id == team_member_id))
    member = tm_res.scalar_one_or_none()
    if not member:
        return

    team_res = await db.execute(select(Team).where(Team.id == member.team_id))
    team = team_res.scalar_one_or_none()
    ev_res = await db.execute(select(Event).where(Event.id == member.event_id))
    event = ev_res.scalar_one_or_none()

    from app.models.qr_code import QRCode

    qr_res = await db.execute(
        select(QRCode).where(QRCode.team_member_id == team_member_id, QRCode.is_active.is_(True))
    )
    qr = qr_res.scalar_one_or_none()

    event_name = event.name if event else "the event"
    team_name = team.name if team else "your team"
    wa = event.whatsapp_group_link if event and event.whatsapp_group_link else ""
    venue = event.venue if event and event.venue else ""

    subject = f"You're registered — {event_name}"
    body_lines = [
        f"You have joined {team_name} for {event_name}.",
        "",
        "Your individual QR code is available in the KRATOS app for event check-in.",
        _app_url(),
    ]
    if qr:
        body_lines.extend(["", f"QR token (for scanners): {qr.token}"])
    if venue:
        body_lines.extend(["", f"Venue: {venue}"])
    if wa:
        body_lines.extend(["", f"Event WhatsApp group: {wa}"])
    body = "\n".join(body_lines) + "\n"

    details = [("Event", event_name), ("Team", team_name)]
    if venue:
        details.append(("Venue", venue))

    html_body = render_email_html(
        title="You're on the team",
        greeting=f"Welcome to {team_name} for {event_name}.",
        paragraphs=[
            "Your spot is confirmed. Sign in to KRATOS to open your personal QR check-in pass.",
            *( [f"Join the event WhatsApp group: {wa}"] if wa else [] ),
        ],
        details=details,
        cta_url=_app_url(),
        cta_label="Open KRATOS",
        footnote="Keep your QR private — it is your check-in credential.",
    )

    await create_and_send(
        db,
        member.profile_id,
        NotificationKind.MEMBER_CONFIRMATION,
        subject,
        body,
        team_id=member.team_id,
        payload={"team_member_id": str(team_member_id)},
        html_body=html_body,
    )


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


async def send_announcement(
    db: AsyncSession,
    event_id: UUID,
    subject: str,
    body: str,
) -> list[Notification]:
    profile_ids = await _eligible_event_profile_ids(db, event_id)
    html_body = render_email_html(
        title=subject,
        greeting="Announcement from KRATOS'26",
        paragraphs=[p for p in body.split("\n") if p.strip()],
        cta_url=_app_url(),
        cta_label="Open KRATOS",
    )
    sent: list[Notification] = []
    for pid in profile_ids:
        n = await create_and_send(
            db,
            pid,
            NotificationKind.ANNOUNCEMENT,
            subject,
            body,
            payload={"event_id": str(event_id)},
            html_body=html_body,
        )
        sent.append(n)
    return sent


async def send_reminder(
    db: AsyncSession,
    event_id: UUID,
    subject: str,
    body: str,
) -> list[Notification]:
    profile_ids = await _eligible_event_profile_ids(db, event_id)
    html_body = render_email_html(
        title=subject,
        greeting="Reminder from KRATOS'26",
        paragraphs=[p for p in body.split("\n") if p.strip()],
        cta_url=_app_url(),
        cta_label="Open KRATOS",
    )
    sent: list[Notification] = []
    for pid in profile_ids:
        n = await create_and_send(
            db,
            pid,
            NotificationKind.REMINDER,
            subject,
            body,
            payload={"event_id": str(event_id)},
            html_body=html_body,
        )
        sent.append(n)
    return sent
