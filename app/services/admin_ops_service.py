"""Admin operations — dashboard aggregates, team admin actions, Excel exports."""
import uuid
from io import BytesIO
from typing import Any, Optional

from fastapi import HTTPException, status
from openpyxl import Workbook
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance import AttendanceScan
from app.models.enums import (
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.services import qr_service
from app.services.roster_service import apply_roster_to_rules, sync_legacy_team_sizes


def apply_registration_mode_to_rules(rules: EventRegistrationRule, mode: RegistrationMode) -> None:
    rules.registration_mode = mode
    if mode == RegistrationMode.INDIVIDUAL_ONLY:
        rules.allow_individual = True
        rules.required_member_count = 1
        rules.substitute_count = 0
        sync_legacy_team_sizes(rules)
    elif mode == RegistrationMode.TEAM_ONLY:
        rules.allow_individual = False
    else:
        rules.allow_individual = True


async def status_counts(db: AsyncSession, column, model) -> dict[str, int]:
    result = await db.execute(select(column, func.count()).select_from(model).group_by(column))
    out: dict[str, int] = {}
    for row in result.all():
        key = row[0].value if hasattr(row[0], "value") else str(row[0])
        out[key] = int(row[1])
    return out


async def dashboard_payload(db: AsyncSession) -> dict[str, Any]:
    events_total = (await db.execute(select(func.count()).select_from(Event))).scalar_one()
    registrations = await status_counts(db, Registration.status, Registration)
    teams = await status_counts(db, Team.status, Team)
    payments = await status_counts(db, Payment.status, Payment)
    try:
        attendance_scans = (
            await db.execute(select(func.count()).select_from(AttendanceScan))
        ).scalar_one()
    except Exception:
        attendance_scans = 0
    return {
        "events_total": events_total,
        "registrations_by_status": registrations,
        "teams_by_status": teams,
        "payments_by_status": payments,
        "attendance_scans_total": attendance_scans,
    }


async def get_event_with_rules(db: AsyncSession, event_id: uuid.UUID) -> tuple[Event, EventRegistrationRule]:
    result = await db.execute(
        select(Event).options(selectinload(Event.rules)).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    if not event.rules:
        raise HTTPException(status_code=404, detail="Event registration rules not found.")
    return event, event.rules


def event_to_dict(event: Event, rules: EventRegistrationRule) -> dict[str, Any]:
    return {
        "id": event.id,
        "name": event.name,
        "tagline": event.tagline,
        "short_desc": event.short_desc,
        "long_desc": event.long_desc,
        "category": event.category,
        "coordinator": event.coordinator,
        "coord_contact": event.coord_contact,
        "fee": event.fee,
        "venue": event.venue,
        "capacity": event.capacity,
        "whatsapp_group_link": event.whatsapp_group_link,
        "whatsapp_group_available": bool(event.whatsapp_group_link),
        "google_sheet_id": event.google_sheet_id,
        "google_sheet_url": event.google_sheet_url,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "slot": event.slot,
        "status": event.status,
        "rules": {
            "registration_mode": rules.registration_mode,
            "team_min_size": rules.team_min_size,
            "team_max_size": rules.team_max_size,
            "required_member_count": getattr(rules, "required_member_count", rules.team_min_size),
            "substitute_count": getattr(rules, "substitute_count", max(0, rules.team_max_size - rules.team_min_size)),
            "allow_individual": rules.allow_individual,
            "allow_team_invite_flow": rules.allow_team_invite_flow,
            "requires_qr_checkin": rules.requires_qr_checkin,
            "capacity_type": rules.capacity_type,
            "member_registration_mode": rules.member_registration_mode,
            "custom_fields": rules.custom_fields,
            "registration_opens_at": rules.registration_opens_at,
            "registration_closes_at": rules.registration_closes_at,
        },
    }


async def transfer_team_leadership(
    db: AsyncSession, team_id: uuid.UUID, new_leader_profile_id: uuid.UUID
) -> dict[str, Any]:
    team_result = await db.execute(select(Team).where(Team.id == team_id).with_for_update())
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot transfer leadership on a cancelled team.")

    members_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
    )
    members = list(members_result.scalars().all())
    old_leader = next((m for m in members if m.role == TeamMemberRole.LEADER), None)
    new_leader = next((m for m in members if m.profile_id == new_leader_profile_id), None)

    if not new_leader:
        raise HTTPException(status_code=404, detail="New leader must be an active team member.")
    if new_leader.role == TeamMemberRole.LEADER:
        return {"team_id": team.id, "leader_profile_id": team.leader_profile_id}

    if old_leader and old_leader.id != new_leader.id:
        old_leader.role = TeamMemberRole.MEMBER
    new_leader.role = TeamMemberRole.LEADER
    team.leader_profile_id = new_leader_profile_id

    await db.commit()
    await db.refresh(team)
    return {"team_id": team.id, "leader_profile_id": team.leader_profile_id}


async def cancel_team_admin(db: AsyncSession, team_id: uuid.UUID) -> dict[str, Any]:
    team_result = await db.execute(select(Team).where(Team.id == team_id).with_for_update())
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    team.status = TeamStatus.CANCELLED

    reg_result = await db.execute(
        select(Registration).where(
            Registration.team_id == team.id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
    )
    for registration in reg_result.scalars().all():
        registration.status = RegistrationStatus.CANCELLED
        await qr_service.deactivate_for_registration(db, registration.id)

    members_result = await db.execute(select(TeamMember).where(TeamMember.team_id == team.id))
    for member in members_result.scalars().all():
        if member.status not in (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED):
            await qr_service.deactivate_for_team_member(db, member.id)

    await db.commit()
    return {"team_id": team.id, "status": team.status}


async def cancel_registration_admin(db: AsyncSession, registration_id: uuid.UUID) -> Registration:
    result = await db.execute(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    registration = result.scalar_one_or_none()
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found.")
    registration.status = RegistrationStatus.CANCELLED
    await qr_service.deactivate_for_registration(db, registration.id)
    if registration.team_id:
        team_result = await db.execute(select(Team).where(Team.id == registration.team_id))
        team = team_result.scalar_one_or_none()
        if team and team.status != TeamStatus.CANCELLED:
            team.status = TeamStatus.CANCELLED
        members = await db.execute(select(TeamMember).where(TeamMember.team_id == registration.team_id))
        for member in members.scalars().all():
            await qr_service.deactivate_for_team_member(db, member.id)
    await db.commit()
    await db.refresh(registration)
    return registration


def _workbook_bytes(headers: list[str], rows: list[list[Any]]) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


async def export_registrations_xlsx(db: AsyncSession, event_id: Optional[uuid.UUID] = None) -> BytesIO:
    q = (
        select(Registration)
        .options(selectinload(Registration.team), selectinload(Registration.payment))
        .order_by(Registration.created_at)
    )
    if event_id:
        q = q.where(Registration.event_id == event_id)
    result = await db.execute(q)
    registrations = result.scalars().all()

    profile_ids = {r.profile_id for r in registrations if r.profile_id}
    profiles: dict[uuid.UUID, Profile] = {}
    if profile_ids:
        prof_result = await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))
        profiles = {p.id: p for p in prof_result.scalars().all()}

    rows = []
    for r in registrations:
        prof = profiles.get(r.profile_id) if r.profile_id else None
        rows.append(
            [
                str(r.id),
                str(r.event_id),
                r.status.value if hasattr(r.status, "value") else r.status,
                str(r.profile_id) if r.profile_id else "",
                prof.full_name if prof else "",
                str(r.team_id) if r.team_id else "",
                r.team.name if r.team else "",
                str(r.payment_id) if r.payment_id else "",
                r.payment.status.value if r.payment else "",
                r.created_at.isoformat() if r.created_at else "",
            ]
        )
    return _workbook_bytes(
        [
            "registration_id",
            "event_id",
            "status",
            "profile_id",
            "full_name",
            "team_id",
            "team_name",
            "payment_id",
            "payment_status",
            "created_at",
        ],
        rows,
    )


async def export_payments_xlsx(db: AsyncSession) -> BytesIO:
    result = await db.execute(
        select(Payment)
        .where(
            Payment.payment_type.in_([PaymentType.SOLO_REGISTRATION, PaymentType.TEAM_REGISTRATION])
        )
        .order_by(Payment.created_at)
    )
    payments = result.scalars().all()
    rows = [
        [
            str(p.id),
            str(p.payer_profile_id),
            p.payment_type.value,
            p.status.value,
            p.amount_paise,
            p.currency,
            p.razorpay_order_id,
            p.razorpay_payment_id or "",
            p.created_at.isoformat() if p.created_at else "",
        ]
        for p in payments
    ]
    return _workbook_bytes(
        [
            "payment_id",
            "payer_profile_id",
            "payment_type",
            "status",
            "amount_paise",
            "currency",
            "razorpay_order_id",
            "razorpay_payment_id",
            "created_at",
        ],
        rows,
    )


async def export_attendance_xlsx(db: AsyncSession) -> BytesIO:
    try:
        result = await db.execute(select(AttendanceScan).order_by(AttendanceScan.scanned_at))
        scans = result.scalars().all()
    except Exception:
        scans = []
    rows = [
        [
            str(s.id),
            str(s.event_id) if s.event_id else "",
            str(s.profile_id) if s.profile_id else "",
            str(s.qr_code_id) if s.qr_code_id else "",
            s.scan_result.value,
            s.scanned_at.isoformat() if s.scanned_at else "",
            s.scanner_note or "",
        ]
        for s in scans
    ]
    return _workbook_bytes(
        [
            "scan_id",
            "event_id",
            "profile_id",
            "qr_code_id",
            "scan_result",
            "scanned_at",
            "scanner_note",
        ],
        rows,
    )


async def search_participant_profiles(
    db: AsyncSession,
    *,
    q: Optional[str],
    event_id: Optional[uuid.UUID],
    skip: int,
    limit: int,
) -> tuple[list[Profile], int]:
    base = select(Profile).distinct()
    if event_id:
        solo_ids = select(Registration.profile_id).where(
            Registration.event_id == event_id,
            Registration.profile_id.isnot(None),
            Registration.status != RegistrationStatus.CANCELLED,
        )
        team_profile_ids = select(TeamMember.profile_id).where(
            TeamMember.event_id == event_id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
        base = base.where(or_(Profile.id.in_(solo_ids), Profile.id.in_(team_profile_ids)))

    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                Profile.full_name.ilike(pattern),
                Profile.contact_email.ilike(pattern),
                Profile.phone.ilike(pattern),
                Profile.college_name.ilike(pattern),
            )
        )

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar_one()

    result = await db.execute(base.order_by(Profile.full_name).offset(skip).limit(limit))
    return list(result.scalars().all()), int(total)
