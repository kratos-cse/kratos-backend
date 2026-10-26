"""Authorised admin resend of registration confirmation emails (SMTP, sync delivery)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import NotificationStatus, PaymentStatus, RegistrationStatus, TeamMemberStatus
from app.models.event import Event
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.services import confirmation_email, notification_service

logger = logging.getLogger("admin_confirmation_resend")

ResendScope = Literal["all_members", "member"]


@dataclass
class ResendRecipientPreview:
    team_member_id: Optional[UUID]
    profile_id: Optional[UUID]
    participant_name: str
    email: Optional[str]
    has_qr: bool
    qr_error: Optional[str] = None


@dataclass
class ResendPreview:
    eligible: bool
    reason: Optional[str]
    registration_id: UUID
    registration_status: str
    payment_status: Optional[str]
    event_name: str
    registration_type: str
    recipients: list[ResendRecipientPreview]


@dataclass
class ResendRecipientResult:
    team_member_id: Optional[UUID]
    participant_name: str
    email: str
    delivered: bool
    notification_id: Optional[UUID]
    error: Optional[str]


@dataclass
class ResendOutcome:
    idempotent_replay: bool
    results: list[ResendRecipientResult]
    delivered_count: int
    failed_count: int


@dataclass
class EventResendPreview:
    event_id: UUID
    event_name: str
    confirmed_registration_count: int
    recipient_count: int
    deliverable_recipient_count: int
    skipped_registration_count: int
    reason: Optional[str]


@dataclass
class EventResendOutcome:
    idempotent_replay: bool
    event_id: UUID
    event_name: str
    registrations_processed: int
    delivered_count: int
    failed_count: int
    results: list[ResendRecipientResult]


async def _admin_resend_request_exists(db: AsyncSession, client_request_id: str) -> bool:
    if not client_request_id:
        return False
    result = await db.execute(
        select(Notification.id)
        .where(Notification.payload["admin_resend_request_id"].as_string() == client_request_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _load_registration(db: AsyncSession, registration_id: UUID) -> Registration:
    result = await db.execute(
        select(Registration)
        .options(
            selectinload(Registration.event),
            selectinload(Registration.payment),
            selectinload(Registration.team).selectinload(Team.members).selectinload(TeamMember.profile),
        )
        .where(Registration.id == registration_id)
    )
    registration = result.scalar_one_or_none()
    if not registration:
        raise ValueError("Registration not found")
    return registration


def _payment_eligible(payment: Optional[Payment]) -> tuple[bool, Optional[str]]:
    if not payment:
        return False, "No payment is linked to this registration."
    if payment.status != PaymentStatus.PAID:
        return False, f"Payment is {payment.status.value}; confirmation can only be resent after payment is PAID."
    return True, None


async def _recipient_for_solo(
    db: AsyncSession, registration: Registration, event: Event
) -> list[ResendRecipientPreview]:
    if not registration.profile_id:
        return []
    profile = (
        await db.execute(select(Profile).where(Profile.id == registration.profile_id))
    ).scalar_one_or_none()
    email = await notification_service._resolve_email(db, registration.profile_id)
    qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.registration_id == registration.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    name = confirmation_email._participant_name(profile)
    return [
        ResendRecipientPreview(
            team_member_id=None,
            profile_id=registration.profile_id,
            participant_name=name,
            email=email,
            has_qr=qr is not None,
            qr_error=None if qr else "Active QR code is missing for this registration.",
        )
    ]


async def _ensure_member_qr(db: AsyncSession, member: TeamMember) -> bool:
    """Idempotent: ensure an active QR exists for a roster participant before resend."""
    from app.payments.handoffs import trigger_qr

    existing = (
        await db.execute(
            select(QRCode).where(
                QRCode.team_member_id == member.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if existing:
        return True
    try:
        await trigger_qr(db, team_member_id=member.id)
        await db.flush()
    except Exception:
        logger.exception("ensure_member_qr_failed team_member_id=%s", member.id)
        return False
    return (
        await db.execute(
            select(QRCode).where(
                QRCode.team_member_id == member.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none() is not None


async def _recipients_for_team(
    db: AsyncSession, registration: Registration, team: Team
) -> list[ResendRecipientPreview]:
    previews: list[ResendRecipientPreview] = []
    if team.members:
        members = confirmation_email.roster_members_for_confirmation(team)
    else:
        members = await confirmation_email.load_roster_members_for_confirmation(db, team.id)
    for member in members:
        email = await confirmation_email._resolve_team_member_email(db, member)
        qr = (
            await db.execute(
                select(QRCode).where(
                    QRCode.team_member_id == member.id,
                    QRCode.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        previews.append(
            ResendRecipientPreview(
                team_member_id=member.id,
                profile_id=member.profile_id,
                participant_name=confirmation_email._participant_name(member.profile, member),
                email=email,
                has_qr=qr is not None,
                qr_error=None if qr else "Active QR code is missing for this team member.",
            )
        )
    return previews


async def preview_resend_confirmation(
    db: AsyncSession, registration_id: UUID
) -> ResendPreview:
    registration = await _load_registration(db, registration_id)
    event = registration.event
    if not event:
        raise ValueError("Event not found for registration")

    payment = registration.payment
    pay_ok, pay_reason = _payment_eligible(payment)
    reg_status = registration.status.value if hasattr(registration.status, "value") else str(registration.status)

    if registration.status != RegistrationStatus.CONFIRMED:
        return ResendPreview(
            eligible=False,
            reason=f"Registration is {reg_status}; only CONFIRMED registrations can receive confirmation resends.",
            registration_id=registration_id,
            registration_status=reg_status,
            payment_status=payment.status.value if payment else None,
            event_name=event.name,
            registration_type="TEAM" if registration.team_id else "SOLO",
            recipients=[],
        )

    if not pay_ok:
        return ResendPreview(
            eligible=False,
            reason=pay_reason,
            registration_id=registration_id,
            registration_status=reg_status,
            payment_status=payment.status.value if payment else None,
            event_name=event.name,
            registration_type="TEAM" if registration.team_id else "SOLO",
            recipients=[],
        )

    if registration.team_id and registration.team:
        recipients = await _recipients_for_team(db, registration, registration.team)
    else:
        recipients = await _recipient_for_solo(db, registration, event)

    eligible = any(r.email and r.has_qr for r in recipients)
    reason = None
    if not recipients:
        reason = "No recipients found for this registration."
    elif not eligible:
        reason = "No recipient has both a deliverable email address and an active QR code."

    return ResendPreview(
        eligible=eligible,
        reason=reason,
        registration_id=registration_id,
        registration_status=reg_status,
        payment_status=payment.status.value if payment else None,
        event_name=event.name,
        registration_type="TEAM" if registration.team_id else "SOLO",
        recipients=recipients,
    )


async def _outcome_from_request_id(db: AsyncSession, client_request_id: str) -> ResendOutcome:
    existing = await db.execute(
        select(Notification).where(
            Notification.payload["admin_resend_request_id"].as_string() == client_request_id
        )
    )
    rows = existing.scalars().all()
    results: list[ResendRecipientResult] = []
    for r in rows:
        tm_id: Optional[UUID] = None
        if r.payload and r.payload.get("team_member_id"):
            try:
                tm_id = UUID(str(r.payload["team_member_id"]))
            except ValueError:
                tm_id = None
        results.append(
            ResendRecipientResult(
                team_member_id=tm_id,
                participant_name=str((r.payload or {}).get("participant_name") or "Participant"),
                email=str((r.payload or {}).get("to_email") or ""),
                delivered=r.status == NotificationStatus.SENT,
                notification_id=r.id,
                error=r.error,
            )
        )
    delivered = sum(1 for r in results if r.delivered)
    return ResendOutcome(
        idempotent_replay=True,
        results=results,
        delivered_count=delivered,
        failed_count=len(results) - delivered,
    )


async def _confirmed_registrations_for_event(db: AsyncSession, event_id: UUID) -> list[Registration]:
    result = await db.execute(
        select(Registration)
        .options(
            selectinload(Registration.event),
            selectinload(Registration.payment),
            selectinload(Registration.team).selectinload(Team.members).selectinload(TeamMember.profile),
        )
        .where(
            Registration.event_id == event_id,
            Registration.status == RegistrationStatus.CONFIRMED,
        )
    )
    return list(result.scalars().all())


async def preview_event_resend_confirmation(db: AsyncSession, event_id: UUID) -> EventResendPreview:
    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event:
        raise ValueError("Event not found")

    registrations = await _confirmed_registrations_for_event(db, event_id)
    recipient_count = 0
    deliverable = 0
    skipped = 0
    for reg in registrations:
        preview = await preview_resend_confirmation(db, reg.id)
        if not preview.eligible:
            skipped += 1
            continue
        recipient_count += len(preview.recipients)
        deliverable += sum(1 for r in preview.recipients if r.email and r.has_qr)

    reason = None
    if not registrations:
        reason = "No confirmed registrations for this event."
    elif deliverable == 0:
        reason = "No participants have both an email address and an active QR code."

    return EventResendPreview(
        event_id=event_id,
        event_name=event.name,
        confirmed_registration_count=len(registrations),
        recipient_count=recipient_count,
        deliverable_recipient_count=deliverable,
        skipped_registration_count=skipped,
        reason=reason,
    )


async def execute_event_resend_confirmation(
    db: AsyncSession,
    *,
    event_id: UUID,
    admin_user_id: UUID,
    client_request_id: str,
) -> EventResendOutcome:
    if not client_request_id or len(client_request_id) < 8:
        raise ValueError("client_request_id is required for resend idempotency.")

    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event:
        raise ValueError("Event not found")

    if await _admin_resend_request_exists(db, client_request_id):
        aggregated = await _outcome_from_request_id(db, client_request_id)
        return EventResendOutcome(
            idempotent_replay=True,
            event_id=event_id,
            event_name=event.name,
            registrations_processed=0,
            delivered_count=aggregated.delivered_count,
            failed_count=aggregated.failed_count,
            results=aggregated.results,
        )

    event_preview = await preview_event_resend_confirmation(db, event_id)
    if event_preview.deliverable_recipient_count == 0:
        raise ValueError(event_preview.reason or "No deliverable recipients for this event.")

    registrations = await _confirmed_registrations_for_event(db, event_id)
    all_results: list[ResendRecipientResult] = []
    processed = 0
    for reg in registrations:
        reg_preview = await preview_resend_confirmation(db, reg.id)
        if not reg_preview.eligible:
            continue
        processed += 1
        outcome = await execute_resend_confirmation(
            db,
            registration_id=reg.id,
            admin_user_id=admin_user_id,
            client_request_id=client_request_id,
            dedupe_segment=f"{client_request_id}:{reg.id}",
            skip_idempotency_check=True,
            extra_payload={"admin_resend_event_id": str(event_id)},
        )
        all_results.extend(outcome.results)

    delivered_count = sum(1 for r in all_results if r.delivered)
    return EventResendOutcome(
        idempotent_replay=False,
        event_id=event_id,
        event_name=event.name,
        registrations_processed=processed,
        delivered_count=delivered_count,
        failed_count=len(all_results) - delivered_count,
        results=all_results,
    )


async def execute_resend_confirmation(
    db: AsyncSession,
    *,
    registration_id: UUID,
    admin_user_id: UUID,
    client_request_id: str,
    scope: ResendScope = "all_members",
    team_member_id: Optional[UUID] = None,
    dedupe_segment: Optional[str] = None,
    skip_idempotency_check: bool = False,
    extra_payload: Optional[dict[str, Any]] = None,
) -> ResendOutcome:
    if not client_request_id or len(client_request_id) < 8:
        raise ValueError("client_request_id is required for resend idempotency.")

    if not skip_idempotency_check and await _admin_resend_request_exists(db, client_request_id):
        return await _outcome_from_request_id(db, client_request_id)

    preview = await preview_resend_confirmation(db, registration_id)
    if not preview.eligible:
        raise ValueError(preview.reason or "Registration is not eligible for confirmation resend.")

    registration = await _load_registration(db, registration_id)
    payment = registration.payment
    if not payment:
        raise ValueError("Payment not found")

    event = registration.event
    if not event:
        raise ValueError("Event not found")

    targets = preview.recipients
    if scope == "member":
        if not team_member_id:
            raise ValueError("team_member_id is required when scope is member.")
        targets = [t for t in targets if t.team_member_id == team_member_id]
        if not targets:
            raise ValueError("Team member not found on this registration.")

    results: list[ResendRecipientResult] = []
    for target in targets:
        if not target.email:
            results.append(
                ResendRecipientResult(
                    team_member_id=target.team_member_id,
                    participant_name=target.participant_name,
                    email="",
                    delivered=False,
                    notification_id=None,
                    error="No email address for recipient.",
                )
            )
            continue
        if not target.has_qr and target.team_member_id:
            member_row = (
                await db.execute(select(TeamMember).where(TeamMember.id == target.team_member_id))
            ).scalar_one_or_none()
            if member_row and await _ensure_member_qr(db, member_row):
                target.has_qr = True
                target.qr_error = None
        if not target.has_qr:
            results.append(
                ResendRecipientResult(
                    team_member_id=target.team_member_id,
                    participant_name=target.participant_name,
                    email=target.email,
                    delivered=False,
                    notification_id=None,
                    error=target.qr_error or "Missing QR code.",
                )
            )
            continue

        recipient_key = str(target.team_member_id or target.profile_id or target.email)
        segment = dedupe_segment or client_request_id
        dedupe = confirmation_email.dedupe_key_admin_resend(segment, recipient_key)
        extra: dict[str, Any] = {
            "admin_resend": True,
            "admin_resend_request_id": client_request_id,
            "initiated_by_admin_id": str(admin_user_id),
            "participant_name": target.participant_name,
        }
        if extra_payload:
            extra.update(extra_payload)

        notif = await _deliver_one_target(
            db,
            registration=registration,
            payment=payment,
            event=event,
            target=target,
            dedupe_key=dedupe,
            extra_payload=extra,
        )
        if notif is None:
            results.append(
                ResendRecipientResult(
                    team_member_id=target.team_member_id,
                    participant_name=target.participant_name,
                    email=target.email,
                    delivered=False,
                    notification_id=None,
                    error="Duplicate dedupe key; resend skipped.",
                )
            )
            continue
        results.append(
            ResendRecipientResult(
                team_member_id=target.team_member_id,
                participant_name=target.participant_name,
                email=target.email,
                delivered=notif.status == NotificationStatus.SENT,
                notification_id=notif.id,
                error=notif.error,
            )
        )

    delivered_count = sum(1 for r in results if r.delivered)
    return ResendOutcome(
        idempotent_replay=False,
        results=results,
        delivered_count=delivered_count,
        failed_count=len(results) - delivered_count,
    )


async def _deliver_one_target(
    db: AsyncSession,
    *,
    registration: Registration,
    payment: Payment,
    event: Event,
    target: ResendRecipientPreview,
    dedupe_key: str,
    extra_payload: dict[str, Any],
) -> Optional[Notification]:
    if target.team_member_id:
        member = next(
            (m for m in (registration.team.members if registration.team else []) if m.id == target.team_member_id),
            None,
        )
        if not member:
            return None
        qr = (
            await db.execute(
                select(QRCode).where(
                    QRCode.team_member_id == member.id,
                    QRCode.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not qr:
            return None
        team = registration.team
        members_count = len(confirmation_email.roster_members_for_confirmation(team))
        receipt_number, receipt_html, receipt_pdf = await confirmation_email._receipt_links(
            db, payment, member.profile_id
        )
        profile_id = member.profile_id or (team.leader_profile_id if team else registration.profile_id)
        if not profile_id:
            return None
        content = confirmation_email.render_confirmation_email_with_qr(
            qr_token=qr.token,
            participant_name=target.participant_name,
            event=event,
            registration_type="TEAM",
            registration_id=registration.id,
            payment=payment,
            receipt_number=receipt_number,
            receipt_html=receipt_html,
            receipt_pdf=receipt_pdf,
            registration_page_url=confirmation_email._registration_page_url(registration.id),
            team_name=team.name if team else None,
            role_label=confirmation_email._role_label(member.role),
            team_member_count=members_count,
        )
        return await confirmation_email._send_one(
            db,
            dedupe_key=dedupe_key,
            profile_id=profile_id,
            to_email=target.email or "",
            content=content,
            payment_id=payment.id,
            registration_id=registration.id,
            team_id=registration.team_id,
            team_member_id=member.id,
            skip_dedupe=True,
            deliver_sync=True,
            extra_payload=extra_payload,
        )

    qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.registration_id == registration.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not qr or not registration.profile_id or not target.email:
        return None
    profile = (
        await db.execute(select(Profile).where(Profile.id == registration.profile_id))
    ).scalar_one_or_none()
    receipt_number, receipt_html, receipt_pdf = await confirmation_email._receipt_links(
        db, payment, registration.profile_id
    )
    content = confirmation_email.render_confirmation_email_with_qr(
        qr_token=qr.token,
        participant_name=confirmation_email._participant_name(profile),
        event=event,
        registration_type="SOLO",
        registration_id=registration.id,
        payment=payment,
        receipt_number=receipt_number,
        receipt_html=receipt_html,
        receipt_pdf=receipt_pdf,
        registration_page_url=confirmation_email._registration_page_url(registration.id),
    )
    return await confirmation_email._send_one(
        db,
        dedupe_key=dedupe_key,
        profile_id=registration.profile_id,
        to_email=target.email,
        content=content,
        payment_id=payment.id,
        registration_id=registration.id,
        team_id=None,
        team_member_id=None,
        skip_dedupe=True,
        deliver_sync=True,
        extra_payload=extra_payload,
    )
