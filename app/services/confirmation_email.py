"""Post-payment registration confirmation emails — per-participant QR + receipt links."""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.branding.documents import render_email_shell_html
from app.core.receipt_token import create_receipt_access_token
from app.models.enums import (
    NotificationKind,
    NotificationStatus,
    PaymentStatus,
    PaymentType,
    TeamMemberRole,
    TeamMemberStatus,
)
from app.models.event import Event
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.services import notification_service
from app.services.email_content import (
    coordinator_contact_line,
    event_category_label,
    format_event_schedule,
    payment_reference_lines,
    sanitize_whatsapp_group_url,
    whatsapp_cta_html,
)
from app.services.receipt_service import (
    generate_qr_png_bytes,
    receipt_html_url,
    receipt_pdf_url,
)

logger = logging.getLogger("confirmation_email")

QR_CONTENT_ID = "participant-qr"
_ACTIVE_MEMBER = (TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT)
_TERMINAL_MEMBER = (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)


def roster_members_for_confirmation(team: Team) -> list[TeamMember]:
    """All roster participants who should receive entry passes (leader-entered, invite, linked)."""
    return [m for m in (team.members or []) if m.status not in _TERMINAL_MEMBER]


async def load_roster_members_for_confirmation(db: AsyncSession, team_id: UUID) -> list[TeamMember]:
    result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(
            TeamMember.team_id == team_id,
            TeamMember.status.notin_(_TERMINAL_MEMBER),
        )
    )
    return list(result.scalars().all())


def _app_url() -> str:
    return notification_service._app_url()


def _amount_inr(paise: int) -> str:
    return notification_service._amount_inr(paise)


def _role_label(role: TeamMemberRole) -> str:
    return {
        TeamMemberRole.LEADER: "Team Leader",
        TeamMemberRole.MEMBER: "Team Member",
        TeamMemberRole.SUBSTITUTE: "Substitute",
    }.get(role, str(role.value if hasattr(role, "value") else role))


def dedupe_key_solo(payment_id: UUID, profile_id: UUID) -> str:
    return f"reg_confirm:payment:{payment_id}:profile:{profile_id}"


def dedupe_key_team_member(payment_id: UUID, team_member_id: UUID) -> str:
    return f"reg_confirm:payment:{payment_id}:team_member:{team_member_id}"


def dedupe_key_member_join(team_member_id: UUID) -> str:
    return f"reg_confirm:team_member:{team_member_id}"


def dedupe_key_admin_resend(client_request_id: str, recipient_key: str) -> str:
    """Distinct dedupe key per authorised admin resend attempt (does not block original send key)."""
    return f"reg_confirm:admin_resend:{client_request_id}:{recipient_key}"


async def confirmation_was_sent(db: AsyncSession, dedupe_key: str) -> bool:
    result = await db.execute(
        select(Notification.id)
        .where(
            Notification.kind == NotificationKind.REGISTRATION_CONFIRMATION,
            Notification.payload["dedupe_key"].as_string() == dedupe_key,
            Notification.status.in_((NotificationStatus.SENT, NotificationStatus.PENDING)),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _participant_name(profile: Optional[Profile], member: Optional[TeamMember] = None) -> str:
    if profile and profile.full_name:
        return profile.full_name
    if member and member.full_name:
        return member.full_name
    return "Participant"


async def _resolve_team_member_email(db: AsyncSession, member: TeamMember) -> Optional[str]:
    if member.profile_id:
        return await notification_service._resolve_email(db, member.profile_id)
    if member.contact_email:
        return member.contact_email.strip()
    return None


def _qr_checkin_url(token: str) -> str:
    return f"{_app_url().rstrip('/')}/checkin?token={token}"


def _registration_page_url(registration_id: UUID) -> str:
    return f"{_app_url().rstrip('/')}/registrations/{registration_id}"


@dataclass
class ConfirmationEmailContent:
    subject: str
    text_body: str
    html_body: str
    qr_png: bytes


def render_confirmation_email(
    *,
    participant_name: str,
    event: Event,
    registration_type: str,
    registration_id: UUID,
    payment: Optional[Payment],
    receipt_number: Optional[str],
    receipt_html: Optional[str],
    receipt_pdf: Optional[str],
    registration_page_url: str,
    team_name: Optional[str] = None,
    role_label: Optional[str] = None,
    team_member_count: Optional[int] = None,
) -> ConfirmationEmailContent:
    event_date, event_time, event_end = format_event_schedule(event)
    time_display = event_time
    if event_end:
        time_display = f"{event_time} – {event_end}"
    reg_type_display = "TEAM" if registration_type == "TEAM" else "SOLO"
    whatsapp_url = sanitize_whatsapp_group_url(event.whatsapp_group_link)
    category = event_category_label(event)
    coord_line = coordinator_contact_line(event)

    subject = f"Registration confirmed — {event.name}"

    text_lines = [
        "KRATOS'26 — REGISTRATION CONFIRMED",
        "",
        f"Hello {participant_name},",
        f"Your registration for {event.name} has been successfully confirmed.",
        "",
        "EVENT",
        f"  {event.name}",
    ]
    if category:
        text_lines.append(f"  Category: {category}")
    text_lines.extend([f"  Date: {event_date}", f"  Time: {time_display}"])
    if event.venue:
        text_lines.append(f"  Venue: {event.venue}")
    if event.short_desc:
        text_lines.append(f"  Instructions: {event.short_desc.strip()}")
    if coord_line:
        text_lines.append(f"  Contact: {coord_line}")
    text_lines.extend(
        [
            "",
            "REGISTRATION",
            f"  Registration ID: {registration_id}",
            f"  Participant: {participant_name}",
            f"  Type: {reg_type_display}",
        ]
    )
    if team_name:
        text_lines.extend([f"  Team: {team_name}", f"  Role: {role_label or 'Team Member'}"])
    if team_member_count is not None:
        text_lines.append(f"  Team size: {team_member_count}")

    pay_rows = payment_reference_lines(payment)
    if pay_rows:
        text_lines.append("")
        text_lines.append("PAYMENT")
        for k, v in pay_rows:
            text_lines.append(f"  {k}: {v}")
    if receipt_number:
        text_lines.append(f"  Receipt: {receipt_number}")

    text_lines.extend(
        [
            "",
            "YOUR ENTRY QR is attached to this email.",
            "Please present your personal QR at event check-in.",
            "",
            f"View my registration: {registration_page_url}",
        ]
    )
    if whatsapp_url:
        text_lines.extend(["", f"Join the Event WhatsApp Group: {whatsapp_url}"])
    if receipt_html:
        text_lines.extend(["", f"View receipt: {receipt_html}", f"Download receipt: {receipt_pdf}"])
    text_lines.extend(
        [
            "",
            "IMPORTANT:",
            "- Keep this email for event entry.",
            "- Bring valid college ID.",
            "- Arrive before the event start time.",
            "",
            "KRATOS'26 · Association of Computer Engineers · EEC · CSE",
        ]
    )
    text_body = "\n".join(text_lines)

    payment_rows = list(pay_rows)
    if receipt_number:
        payment_rows.insert(0, ("Receipt number", receipt_number))

    reg_rows = [
        ("Registration ID", str(registration_id)),
        ("Participant", participant_name),
        ("Registration type", reg_type_display),
    ]
    if team_name:
        reg_rows.append(("Team", team_name))
    if role_label:
        reg_rows.append(("Role", role_label))
    if team_member_count is not None:
        reg_rows.append(("Team members", str(team_member_count)))

    event_rows: list[tuple[str, str]] = [("Event", event.name)]
    if category:
        event_rows.append(("Category", category))
    event_rows.append(("Date", event_date))
    event_rows.append(("Time", time_display))
    if event.venue:
        event_rows.append(("Venue", event.venue))
    if event.short_desc:
        event_rows.append(("Instructions", event.short_desc.strip()))
    if coord_line:
        event_rows.append(("Coordinator", coord_line))

    def _table(rows: list[tuple[str, str]]) -> str:
        cells = "".join(
            "<tr>"
            f'<td style="padding:6px 0;font-size:13px;color:#64748b;width:40%;">{html.escape(k)}</td>'
            f'<td style="padding:6px 0;font-size:14px;color:#0f172a;font-weight:600;">{html.escape(v)}</td>'
            "</tr>"
            for k, v in rows
        )
        return (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="margin:0 0 16px;border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;">'
            f"{cells}</table>"
        )

    receipt_cta = ""
    if receipt_html and receipt_pdf:
        receipt_cta = (
            '<p style="margin:16px 0 8px;font-size:13px;font-weight:700;color:#0f172a;">RECEIPT</p>'
            '<p style="margin:0 0 16px;">'
            f'<a href="{html.escape(receipt_html)}" style="display:inline-block;margin-right:10px;'
            'background:#0f172a;color:#f8fafc;text-decoration:none;font-weight:600;font-size:13px;'
            'padding:10px 16px;border-radius:8px;">View receipt</a>'
            f'<a href="{html.escape(receipt_pdf)}" style="display:inline-block;background:#f1f5f9;color:#0f172a;'
            'text-decoration:none;font-weight:600;font-size:13px;padding:10px 16px;border-radius:8px;'
            'border:1px solid #cbd5e1;">Download PDF</a></p>'
        )

    body_html = f"""
          <p style="margin:0 0 6px;font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#b91c1c;font-weight:700;">
            KRATOS&apos;26</p>
          <h1 style="margin:0 0 8px;font-size:22px;line-height:1.25;color:#0f172a;font-weight:800;">
            Registration Confirmed</h1>
          <p style="margin:0 0 20px;font-size:15px;line-height:1.55;color:#334155;">
            Hello {html.escape(participant_name)}, your registration for
            <strong>{html.escape(event.name)}</strong> has been successfully confirmed.</p>

          <p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:0.08em;color:#64748b;">EVENT</p>
          {_table(event_rows)}

          <p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:0.08em;color:#64748b;">REGISTRATION</p>
          {_table(reg_rows)}

          {f'<p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:0.08em;color:#64748b;">PAYMENT</p>{_table(payment_rows)}' if payment_rows else ''}

          <p style="margin:12px 0 16px;">
            <a href="{html.escape(registration_page_url)}" style="display:inline-block;background:#0f172a;color:#f8fafc;
            text-decoration:none;font-weight:600;font-size:13px;padding:10px 16px;border-radius:8px;">View My Registration</a>
          </p>

          <p style="margin:20px 0 10px;font-size:13px;font-weight:700;color:#0f172a;">YOUR ENTRY QR</p>
          <p style="margin:0 0 12px;font-size:14px;color:#334155;">Present this personal QR at event check-in.</p>
          <p style="margin:0 0 16px;text-align:center;">
            <img src="cid:{QR_CONTENT_ID}" alt="Your event entry QR code" width="200" height="200"
                 style="display:inline-block;border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;" />
          </p>
          <p style="margin:0 0 20px;font-size:13px;color:#64748b;">
            Can&apos;t see the QR?
            <a href="{html.escape(registration_page_url)}" style="color:#0f172a;font-weight:600;">
              Open your registration page</a> to view your pass.</p>

          {receipt_cta}
          {whatsapp_cta_html(whatsapp_url) if whatsapp_url else ""}

          <p style="margin:24px 0 0;font-size:12px;line-height:1.55;color:#94a3b8;">
            Keep this email for event entry · Bring valid college ID · Arrive before the event starts.
            Receipt links are personal — do not forward.</p>
    """

    html_body = render_email_shell_html(
        title="Registration confirmed",
        body_html=body_html,
        footer_note="KRATOS'26 · Easwari Engineering College · Chennai · kratos.cse@gmail.com",
    )

    return ConfirmationEmailContent(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        qr_png=b"",
    )


def render_confirmation_email_with_qr(
    *,
    qr_token: str,
    participant_name: str,
    event: Event,
    registration_type: str,
    registration_id: UUID,
    payment: Optional[Payment],
    receipt_number: Optional[str],
    receipt_html: Optional[str],
    receipt_pdf: Optional[str],
    registration_page_url: str,
    team_name: Optional[str] = None,
    role_label: Optional[str] = None,
    team_member_count: Optional[int] = None,
) -> ConfirmationEmailContent:
    content = render_confirmation_email(
        participant_name=participant_name,
        event=event,
        registration_type=registration_type,
        registration_id=registration_id,
        payment=payment,
        receipt_number=receipt_number,
        receipt_html=receipt_html,
        receipt_pdf=receipt_pdf,
        registration_page_url=registration_page_url,
        team_name=team_name,
        role_label=role_label,
        team_member_count=team_member_count,
    )
    content.qr_png = generate_qr_png_bytes(_qr_checkin_url(qr_token))
    return content


async def _receipt_links(
    db: AsyncSession,
    payment: Payment,
    profile_id: Optional[UUID],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    receipt_res = await db.execute(select(Receipt).where(Receipt.payment_id == payment.id))
    receipt = receipt_res.scalar_one_or_none()
    receipt_number = receipt.receipt_number if receipt else None
    if profile_id is None:
        return receipt_number, receipt_html_url(payment.id), receipt_pdf_url(payment.id)
    token, _ = create_receipt_access_token(payment.id, profile_id)
    return (
        receipt_number,
        receipt_html_url(payment.id, token),
        receipt_pdf_url(payment.id, token),
    )


async def _send_one(
    db: AsyncSession,
    *,
    dedupe_key: str,
    profile_id: UUID,
    to_email: str,
    content: ConfirmationEmailContent,
    payment_id: Optional[UUID],
    registration_id: Optional[UUID],
    team_id: Optional[UUID],
    team_member_id: Optional[UUID],
    skip_dedupe: bool = False,
    deliver_sync: bool = False,
    extra_payload: Optional[dict[str, Any]] = None,
) -> Optional[Notification]:
    if not skip_dedupe and await confirmation_was_sent(db, dedupe_key):
        logger.info("confirmation_email_skipped dedupe_key=%s", dedupe_key)
        return None

    payload: dict[str, Any] = {
        "dedupe_key": dedupe_key,
        "to_email": to_email,
    }
    if team_member_id:
        payload["team_member_id"] = str(team_member_id)
    if extra_payload:
        payload.update(extra_payload)

    if deliver_sync:
        return await notification_service.create_and_deliver_inline_image_sync(
            db,
            profile_id=profile_id,
            kind=NotificationKind.REGISTRATION_CONFIRMATION,
            subject=content.subject,
            body=content.text_body,
            html_body=content.html_body,
            inline_images=[(QR_CONTENT_ID, "image/png", content.qr_png)],
            payment_id=payment_id,
            registration_id=registration_id,
            team_id=team_id,
            payload=payload,
            to_email=to_email,
        )

    return await notification_service.create_and_send_with_inline_image(
        db,
        profile_id=profile_id,
        kind=NotificationKind.REGISTRATION_CONFIRMATION,
        subject=content.subject,
        body=content.text_body,
        html_body=content.html_body,
        inline_images=[(QR_CONTENT_ID, "image/png", content.qr_png)],
        payment_id=payment_id,
        registration_id=registration_id,
        team_id=team_id,
        payload=payload,
    )


async def send_solo_payment_confirmation(db: AsyncSession, payment_id: UUID) -> None:
    payment = (
        await db.execute(select(Payment).where(Payment.id == payment_id))
    ).scalar_one_or_none()
    if not payment or payment.payment_type != PaymentType.SOLO_REGISTRATION:
        return

    registration = (
        await db.execute(select(Registration).where(Registration.payment_id == payment_id))
    ).scalar_one_or_none()
    if not registration or not registration.profile_id:
        return

    event = (
        await db.execute(select(Event).where(Event.id == registration.event_id))
    ).scalar_one_or_none()
    if not event:
        return

    qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.registration_id == registration.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not qr:
        logger.warning("solo_confirmation_missing_qr payment_id=%s", payment_id)
        return

    profile = (
        await db.execute(select(Profile).where(Profile.id == registration.profile_id))
    ).scalar_one_or_none()
    to_email = await notification_service._resolve_email(db, registration.profile_id)
    if not to_email:
        return

    receipt_number, receipt_html, receipt_pdf = await _receipt_links(db, payment, registration.profile_id)
    content = render_confirmation_email_with_qr(
        qr_token=qr.token,
        participant_name=_participant_name(profile),
        event=event,
        registration_type="SOLO",
        registration_id=registration.id,
        payment=payment,
        receipt_number=receipt_number,
        receipt_html=receipt_html,
        receipt_pdf=receipt_pdf,
        registration_page_url=_registration_page_url(registration.id),
    )
    await _send_one(
        db,
        dedupe_key=dedupe_key_solo(payment_id, registration.profile_id),
        profile_id=registration.profile_id,
        to_email=to_email,
        content=content,
        payment_id=payment_id,
        registration_id=registration.id,
        team_id=None,
        team_member_id=None,
    )


async def send_team_payment_confirmations(db: AsyncSession, payment_id: UUID) -> None:
    payment = (
        await db.execute(select(Payment).where(Payment.id == payment_id))
    ).scalar_one_or_none()
    if not payment or payment.payment_type != PaymentType.TEAM_REGISTRATION:
        return

    registration = (
        await db.execute(select(Registration).where(Registration.payment_id == payment_id))
    ).scalar_one_or_none()
    if not registration or not registration.team_id:
        return

    event = (
        await db.execute(select(Event).where(Event.id == registration.event_id))
    ).scalar_one_or_none()
    team = (
        await db.execute(select(Team).where(Team.id == registration.team_id))
    ).scalar_one_or_none()
    if not event or not team:
        return

    members = await load_roster_members_for_confirmation(db, team.id)
    active_count = len(members)
    receipt_number, _, _ = await _receipt_links(db, payment, payment.payer_profile_id)

    for member in members:
        try:
            await _send_team_member_confirmation_email(
                db,
                payment_id=payment_id,
                payment=payment,
                event=event,
                team=team,
                registration=registration,
                member=member,
                active_count=active_count,
                receipt_number=receipt_number,
            )
        except Exception:
            logger.exception(
                "team_confirmation_member_failed payment_id=%s team_member_id=%s",
                payment_id,
                member.id,
            )


async def _send_team_member_confirmation_email(
    db: AsyncSession,
    *,
    payment_id: UUID,
    payment: Payment,
    event: Event,
    team: Team,
    registration: Registration,
    member: TeamMember,
    active_count: int,
    receipt_number: Optional[str],
) -> None:
    qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.team_member_id == member.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not qr:
        logger.warning(
            "team_confirmation_missing_qr payment_id=%s team_member_id=%s",
            payment_id,
            member.id,
        )
        return

    to_email = await _resolve_team_member_email(db, member)
    if not to_email:
        return

    profile_id = member.profile_id or team.leader_profile_id
    receipt_number_m, receipt_html, receipt_pdf = await _receipt_links(
        db, payment, member.profile_id
    )
    content = render_confirmation_email_with_qr(
        qr_token=qr.token,
        participant_name=_participant_name(member.profile, member),
        event=event,
        registration_type="TEAM",
        registration_id=registration.id,
        payment=payment,
        receipt_number=receipt_number_m or receipt_number,
        receipt_html=receipt_html,
        receipt_pdf=receipt_pdf,
        registration_page_url=_registration_page_url(registration.id),
        team_name=team.name,
        role_label=_role_label(member.role),
        team_member_count=active_count,
    )
    await _send_one(
        db,
        dedupe_key=dedupe_key_team_member(payment_id, member.id),
        profile_id=profile_id,
        to_email=to_email,
        content=content,
        payment_id=payment_id,
        registration_id=registration.id,
        team_id=team.id,
        team_member_id=member.id,
    )


async def send_team_member_confirmation(
    db: AsyncSession,
    team_member_id: UUID,
    *,
    payment_id: Optional[UUID] = None,
) -> None:
    """Invite-later / post-join confirmation for one team member."""
    member = (
        await db.execute(
            select(TeamMember)
            .options(selectinload(TeamMember.profile))
            .where(TeamMember.id == team_member_id)
        )
    ).scalar_one_or_none()
    if not member or member.status in _TERMINAL_MEMBER:
        return

    team = (
        await db.execute(select(Team).where(Team.id == member.team_id))
    ).scalar_one_or_none()
    event = (
        await db.execute(select(Event).where(Event.id == member.event_id))
    ).scalar_one_or_none()
    registration = (
        await db.execute(select(Registration).where(Registration.team_id == member.team_id))
    ).scalar_one_or_none()
    if not team or not event or not registration:
        return

    qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.team_member_id == member.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not qr:
        return

    to_email = await _resolve_team_member_email(db, member)
    if not to_email:
        return

    payment: Optional[Payment] = None
    receipt_number: Optional[str] = None
    receipt_html: Optional[str] = None
    receipt_pdf: Optional[str] = None
    if registration.payment_id:
        payment = (
            await db.execute(select(Payment).where(Payment.id == registration.payment_id))
        ).scalar_one_or_none()
        if payment and payment.status == PaymentStatus.PAID:
            receipt_number, receipt_html, receipt_pdf = await _receipt_links(
                db, payment, member.profile_id
            )

    members_count = roster_members_for_confirmation(team) if team else []

    dedupe = (
        dedupe_key_team_member(registration.payment_id, member.id)
        if registration.payment_id
        else dedupe_key_member_join(member.id)
    )

    profile_id = member.profile_id or team.leader_profile_id
    content = render_confirmation_email_with_qr(
        qr_token=qr.token,
        participant_name=_participant_name(member.profile, member),
        event=event,
        registration_type="TEAM",
        registration_id=registration.id,
        payment=payment,
        receipt_number=receipt_number,
        receipt_html=receipt_html,
        receipt_pdf=receipt_pdf,
        registration_page_url=_registration_page_url(registration.id),
        team_name=team.name,
        role_label=_role_label(member.role),
        team_member_count=len(members_count) if isinstance(members_count, list) else members_count,
    )
    await _send_one(
        db,
        dedupe_key=dedupe,
        profile_id=profile_id,
        to_email=to_email,
        content=content,
        payment_id=registration.payment_id,
        registration_id=registration.id,
        team_id=team.id,
        team_member_id=member.id,
    )


async def process_payment_confirmation_emails(db: AsyncSession, payment_id: UUID) -> None:
    """Idempotent per-participant confirmation emails after verified payment."""
    try:
        payment = (
            await db.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        if not payment:
            return
        if payment.payment_type == PaymentType.SOLO_REGISTRATION:
            await send_solo_payment_confirmation(db, payment_id)
        elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
            await send_team_payment_confirmations(db, payment_id)
    except Exception:
        logger.exception("process_payment_confirmation_emails_failed payment_id=%s", payment_id)
