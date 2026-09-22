"""Email notifications via SMTP (aiosmtplib). SMTP failures never propagate to callers."""
import asyncio
import logging
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Optional
from uuid import UUID

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services import qr_service

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


async def _send_smtp(to_email: str, subject: str, body: str) -> tuple[bool, Optional[str]]:
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        return False, "SMTP not configured (SMTP_HOST / SMTP_FROM missing)"

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

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


async def _deliver_notification_bg(notification_id: UUID, to_email: str, subject: str, body: str) -> None:
    from app.db.session import AsyncSessionLocal
    from app.services import audit_service

    ok, err = await _send_smtp(to_email, subject, body)
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Notification).where(Notification.id == notification_id))
            notif = result.scalar_one_or_none()
            if notif:
                if ok:
                    notif.status = NotificationStatus.SENT
                    notif.sent_at = datetime.now(timezone.utc)
                    notif.error = None
                    await audit_service.log_activity(
                        session,
                        action="EMAIL_SENT_SUCCESS",
                        resource_type="NOTIFICATION",
                        resource_id=notification_id,
                        actor_profile_id=notif.profile_id,
                        actor_role="SYSTEM",
                        status="SUCCESS",
                        details={
                            "to_email": to_email,
                            "subject": subject,
                            "kind": notif.kind.value if hasattr(notif.kind, "value") else str(notif.kind),
                            "payment_id": str(notif.payment_id) if notif.payment_id else None,
                        },
                    )
                else:
                    notif.status = NotificationStatus.FAILED
                    notif.error = err
                    # Categorize failure diagnostic
                    err_str = err or "Unknown delivery error"
                    if "SMTP not configured" in err_str:
                        category = "SMTP_UNCONFIGURED"
                        diag = "SMTP_HOST or SMTP_FROM is not set in backend environment variables."
                    elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                        category = "SMTP_TIMEOUT"
                        diag = "SMTP server connection timed out after 3.0s."
                    elif any(w in err_str.lower() for w in ("auth", "login", "credentials", "password", "535")):
                        category = "SMTP_AUTH_ERROR"
                        diag = "SMTP server rejected authentication credentials."
                    else:
                        category = "SMTP_DELIVERY_ERROR"
                        diag = err_str

                    await audit_service.log_activity(
                        session,
                        action="EMAIL_DELIVERY_FAILED",
                        resource_type="NOTIFICATION",
                        resource_id=notification_id,
                        actor_profile_id=notif.profile_id,
                        actor_role="SYSTEM",
                        status="FAILURE",
                        details={
                            "category": category,
                            "diagnostic": diag,
                            "error": err_str,
                            "to_email": to_email,
                            "subject": subject,
                            "kind": notif.kind.value if hasattr(notif.kind, "value") else str(notif.kind),
                            "payment_id": str(notif.payment_id) if notif.payment_id else None,
                        },
                    )
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
) -> Notification:
    from app.services import audit_service

    notification = Notification(
        profile_id=profile_id,
        kind=kind,
        channel=channel,
        status=NotificationStatus.PENDING,
        subject=subject,
        body=body,
        payload=payload,
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
        await audit_service.log_activity(
            db,
            action="EMAIL_DELIVERY_FAILED",
            resource_type="NOTIFICATION",
            resource_id=notification.id,
            actor_profile_id=profile_id,
            actor_role="SYSTEM",
            status="FAILURE",
            details={
                "category": "MISSING_RECIPIENT_EMAIL",
                "diagnostic": "Profile has no contact email and associated user has no email address.",
                "error": "No contact email for profile",
                "subject": subject,
                "kind": kind.value if hasattr(kind, "value") else str(kind),
                "payment_id": str(payment_id) if payment_id else None,
            },
        )
        await db.commit()
        return notification

    await audit_service.log_activity(
        db,
        action="EMAIL_QUEUED",
        resource_type="NOTIFICATION",
        resource_id=notification.id,
        actor_profile_id=profile_id,
        actor_role="SYSTEM",
        status="PENDING",
        details={
            "to_email": to_email,
            "subject": subject,
            "kind": kind.value if hasattr(kind, "value") else str(kind),
            "payment_id": str(payment_id) if payment_id else None,
        },
    )

    await db.commit()
    await db.refresh(notification)

    # Deliver asynchronously in background so callers never block on SMTP
    asyncio.create_task(_deliver_notification_bg(notification.id, to_email, subject, body))
    return notification


async def resend(db: AsyncSession, notification_id: UUID) -> Notification:
    from app.services import audit_service

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
        await audit_service.log_activity(
            db,
            action="EMAIL_DELIVERY_FAILED",
            resource_type="NOTIFICATION",
            resource_id=notification.id,
            actor_profile_id=notification.profile_id,
            actor_role="SYSTEM",
            status="FAILURE",
            details={
                "category": "MISSING_RECIPIENT_EMAIL",
                "diagnostic": "Profile has no contact email and associated user has no email address.",
                "error": "No contact email for profile",
                "subject": notification.subject,
                "kind": notification.kind.value if hasattr(notification.kind, "value") else str(notification.kind),
            },
        )
        await db.commit()
        return notification

    await audit_service.log_activity(
        db,
        action="EMAIL_RESEND_QUEUED",
        resource_type="NOTIFICATION",
        resource_id=notification.id,
        actor_profile_id=notification.profile_id,
        actor_role="SYSTEM",
        status="PENDING",
        details={
            "to_email": to_email,
            "subject": notification.subject,
        },
    )

    await db.commit()
    await db.refresh(notification)

    asyncio.create_task(_deliver_notification_bg(notification.id, to_email, notification.subject, notification.body))
    return notification



def _amount_inr(paise: int) -> str:
    return f"₹{paise / 100:.2f}"


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

    if registration:
        registration_id = registration.id
        team_id = registration.team_id
        ev = await db.execute(select(Event).where(Event.id == registration.event_id))
        event = ev.scalar_one_or_none()
        if event:
            event_name = event.name

    subject = f"Payment confirmed — {event_name}"
    body = (
        f"Your payment of {_amount_inr(payment.amount_paise)} for {event_name} has been received.\n\n"
        f"Your registration is confirmed. Sign in to KRATOS to view your QR code for check-in.\n"
        f"{settings.APP_PUBLIC_BASE_URL}\n"
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
        payload={"amount_paise": payment.amount_paise},
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
        f"View your team in the KRATOS app.\n{settings.APP_PUBLIC_BASE_URL}\n"
    )
    await create_and_send(
        db,
        leader_profile_id,
        NotificationKind.MEMBER_JOINED,
        subject,
        body,
        team_id=team_id,
        payload={"member_profile_id": str(member_profile_id)},
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

    qr_res = await db.execute(select(QRCode).where(QRCode.team_member_id == team_member_id, QRCode.is_active.is_(True)))
    qr = qr_res.scalar_one_or_none()

    event_name = event.name if event else "the event"
    team_name = team.name if team else "your team"
    wa = event.whatsapp_group_link if event and event.whatsapp_group_link else ""

    subject = f"You're registered — {event_name}"
    body_lines = [
        f"You have joined {team_name} for {event_name}.",
        "",
        "Your individual QR code is available in the KRATOS app for event check-in.",
        settings.APP_PUBLIC_BASE_URL,
    ]
    if qr:
        body_lines.extend(["", f"QR token (for scanners): {qr.token}"])
    if wa:
        body_lines.extend(["", f"Event WhatsApp group: {wa}"])
    body = "\n".join(body_lines) + "\n"

    await create_and_send(
        db,
        member.profile_id,
        NotificationKind.MEMBER_CONFIRMATION,
        subject,
        body,
        team_id=member.team_id,
        payload={"team_member_id": str(team_member_id)},
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
        f"{settings.APP_PUBLIC_BASE_URL}\n"
    )
    await create_and_send(
        db,
        leader_profile_id,
        NotificationKind.TEAM_COMPLETED,
        subject,
        body,
        team_id=team_id,
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
    subject = "Refund processed — KRATOS"
    body = f"A refund of {_amount_inr(amount)} has been issued to your original payment method.\n"
    if reason:
        body += f"\nReason: {reason}\n"

    await create_and_send(
        db,
        payment.payer_profile_id,
        NotificationKind.REFUND,
        subject,
        body,
        payment_id=payment_id,
        payload={"reason": reason, "amount_paise": amount},
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
    sent: list[Notification] = []
    for pid in profile_ids:
        n = await create_and_send(
            db,
            pid,
            NotificationKind.ANNOUNCEMENT,
            subject,
            body,
            payload={"event_id": str(event_id)},
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
    sent: list[Notification] = []
    for pid in profile_ids:
        n = await create_and_send(
            db,
            pid,
            NotificationKind.REMINDER,
            subject,
            body,
            payload={"event_id": str(event_id)},
        )
        sent.append(n)
    return sent