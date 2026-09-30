"""Admin operations — dashboard aggregates, team admin actions, Excel exports."""
import csv
import json
import uuid
from io import BytesIO, StringIO
from typing import Any, Optional

from fastapi import HTTPException, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance import AttendanceScan
from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    PaymentStatus,
    PaymentType,
    RegistrationFieldScope,
    RegistrationFieldSource,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.event_content import (
    EventContentSection,
    EventCoordinator,
    EventRegistrationField,
    RegistrationFieldResponse,
)
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.event_content import FieldResponseOut
from app.services import qr_service
from app.services.event_slot import derive_event_slot
from app.services.roster_service import (
    apply_roster_to_rules,
    count_mandatory,
    count_substitutes,
    roster_limits,
    sync_legacy_team_sizes,
)

_ACTIVE_MEMBER_STATUSES = (TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT)

ADMIN_REGISTRATION_LIST_LOAD = (
    selectinload(Registration.team).selectinload(Team.members),
    selectinload(Registration.payment),
    selectinload(Registration.event).selectinload(Event.rules),
)


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


import time

_DASHBOARD_CACHE_TTL_SEC = 10.0
_dashboard_cache: dict[str, Any] = {"expires_at": 0.0, "payload": None}


def invalidate_dashboard_cache() -> None:
    _dashboard_cache["expires_at"] = 0.0
    _dashboard_cache["payload"] = None
    _dashboard_cache["cache_key"] = None


async def dashboard_payload(
    db: AsyncSession,
    *,
    scoped_event_ids: Optional[set[uuid.UUID]] = None,
) -> dict[str, Any]:
    now = time.monotonic()
    cache_key = "all" if scoped_event_ids is None else f"scoped:{sorted(scoped_event_ids)}"
    if (
        _dashboard_cache.get("cache_key") == cache_key
        and _dashboard_cache["payload"] is not None
        and now < float(_dashboard_cache["expires_at"] or 0)
    ):
        return _dashboard_cache["payload"]

    event_filter = []
    if scoped_event_ids is not None:
        if not scoped_event_ids:
            return {
                "events_total": 0,
                "active_events": 0,
                "open_registrations": 0,
                "registrations_by_status": {},
                "teams_by_status": {},
                "payments_by_status": {},
                "attendance_scans_total": 0,
                "paid_revenue_paise": 0,
                "event_operations": [],
                "recent_registrations": [],
                "recent_payments": [],
            }
        event_filter.append(Event.id.in_(scoped_event_ids))

    events_q = select(func.count()).select_from(Event)
    if event_filter:
        events_q = events_q.where(*event_filter)
    events_total = (await db.execute(events_q)).scalar_one()

    active_q = select(func.count()).select_from(Event).where(Event.visibility == EventVisibility.PUBLISHED)
    if event_filter:
        active_q = active_q.where(*event_filter)
    active_events = (await db.execute(active_q)).scalar_one()

    open_q = select(func.count()).select_from(Event).where(
        Event.visibility == EventVisibility.PUBLISHED,
        Event.registration_status == EventRegistrationStatus.OPEN,
    )
    if event_filter:
        open_q = open_q.where(*event_filter)
    open_registrations = (await db.execute(open_q)).scalar_one()

    reg_q = select(Registration.status, func.count()).select_from(Registration).group_by(Registration.status)
    if scoped_event_ids is not None:
        reg_q = reg_q.where(Registration.event_id.in_(scoped_event_ids))
    reg_result = await db.execute(reg_q)
    registrations = {row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in reg_result.all()}

    team_q = select(Team.status, func.count()).select_from(Team).group_by(Team.status)
    if scoped_event_ids is not None:
        team_q = team_q.where(Team.event_id.in_(scoped_event_ids))
    team_result = await db.execute(team_q)
    teams = {row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in team_result.all()}

    pay_q = select(Payment.status, func.count()).select_from(Payment).group_by(Payment.status)
    pay_result = await db.execute(pay_q)
    payments = {row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in pay_result.all()}

    revenue_q = select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(Payment.status == PaymentStatus.PAID)
    paid_revenue_paise = int((await db.execute(revenue_q)).scalar_one())

    try:
        attendance_scans = (
            await db.execute(select(func.count()).select_from(AttendanceScan))
        ).scalar_one()
    except Exception:
        attendance_scans = 0

    event_operations = await build_event_operations_rows(db, scoped_event_ids=scoped_event_ids)
    recent_registrations = await recent_registration_activity(db, scoped_event_ids=scoped_event_ids, limit=8)
    recent_payments = await recent_payment_activity(db, limit=8)

    payload = {
        "events_total": events_total,
        "active_events": active_events,
        "open_registrations": open_registrations,
        "registrations_by_status": registrations,
        "teams_by_status": teams,
        "payments_by_status": payments,
        "attendance_scans_total": attendance_scans,
        "paid_revenue_paise": paid_revenue_paise,
        "event_operations": event_operations,
        "recent_registrations": recent_registrations,
        "recent_payments": recent_payments,
    }
    _dashboard_cache["payload"] = payload
    _dashboard_cache["cache_key"] = cache_key
    _dashboard_cache["expires_at"] = now + _DASHBOARD_CACHE_TTL_SEC
    return payload


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
        "visibility": event.visibility,
        "registration_status": event.registration_status,
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
        },
    }


async def admin_event_config_summary(db: AsyncSession, event_id: uuid.UUID) -> dict[str, int]:
    coordinators = (
        await db.execute(select(func.count()).select_from(EventCoordinator).where(EventCoordinator.event_id == event_id))
    ).scalar_one()
    sections = (
        await db.execute(
            select(func.count()).select_from(EventContentSection).where(EventContentSection.event_id == event_id)
        )
    ).scalar_one()
    reg_fields = (
        await db.execute(
            select(func.count())
            .select_from(EventRegistrationField)
            .where(
                EventRegistrationField.event_id == event_id,
                EventRegistrationField.scope == RegistrationFieldScope.REGISTRATION,
            )
        )
    ).scalar_one()
    member_fields = (
        await db.execute(
            select(func.count())
            .select_from(EventRegistrationField)
            .where(
                EventRegistrationField.event_id == event_id,
                EventRegistrationField.scope == RegistrationFieldScope.TEAM_MEMBER,
            )
        )
    ).scalar_one()
    return {
        "coordinators_count": int(coordinators),
        "content_sections_count": int(sections),
        "registration_fields_count": int(reg_fields),
        "team_member_fields_count": int(member_fields),
    }


async def event_to_dict_with_state(
    db: AsyncSession,
    event: Event,
    rules: EventRegistrationRule,
    *,
    include_config_summary: bool = False,
) -> dict[str, Any]:
    """Admin + public clients: same derived registration fields as GET /events."""
    from app.services.event_projection import build_event_state

    payload = event_to_dict(event, rules)
    payload.update(await build_event_state(db, event, rules))
    if include_config_summary:
        payload["config_summary"] = await admin_event_config_summary(db, event.id)
    return payload


def apply_event_slot_from_schedule(event: Event) -> None:
    """Set slot from starts_at/ends_at when not already set on the row."""
    if event.starts_at is not None:
        event.slot = derive_event_slot(event.starts_at, event.ends_at)


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


def _format_worksheet(ws, headers: list[str], rows: list[list[Any]]):
    ws.append(headers)

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment

    data_font = Font(name="Calibri", size=10)
    data_alignment = Alignment(vertical="center")

    for r_idx, row in enumerate(rows, start=2):
        ws.append(row)
        for col_idx in range(1, len(row) + 1):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.font = data_font
            cell.alignment = data_alignment

    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if "\n" in val_str:
                val_str = max(val_str.split("\n"), key=len)
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 60)

    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions


def _multi_sheet_workbook_bytes(
    headers: list[str],
    sheets_data: dict[str, list[list[Any]]],
) -> BytesIO:
    wb = Workbook()
    
    # Remove default sheet
    if "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])
        
    for title, rows in sheets_data.items():
        # Sheet titles max 31 chars and no invalid chars
        safe_title = "".join(c for c in title if c not in r"\/?*[]")[:31]
        if not safe_title:
            safe_title = "Sheet"
        # Ensure unique title
        base_title = safe_title
        counter = 1
        while safe_title in wb.sheetnames:
            suffix = f" {counter}"
            safe_title = base_title[:31 - len(suffix)] + suffix
            counter += 1
            
        ws = wb.create_sheet(title=safe_title)
        _format_worksheet(ws, headers, rows)
        
    # If no sheets created, create an empty one
    if not wb.sheetnames:
        ws = wb.create_sheet(title="Registrations")
        _format_worksheet(ws, headers, [])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
def _workbook_bytes(
    headers: list[str],
    rows: list[list[Any]],
    sheet_title: str = "Sheet1",
) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    _format_worksheet(ws, headers, rows)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _csv_bytes(headers: list[str], rows: list[list[Any]]) -> BytesIO:
    buf = BytesIO()
    # Removed UTF-8 BOM as it can cause Google Sheets to parse all info in a single column
    text_io = StringIO()
    writer = csv.writer(text_io, dialect="excel")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    buf.write(text_io.getvalue().encode("utf-8"))
    buf.seek(0)
    return buf


REGISTRATION_EXPORT_HEADERS = [
    "Registration ID",
    "Event ID",
    "Event Name",
    "Registration Type",
    "Registration Status",
    "Participant / Leader Name",
    "Contact Email",
    "Phone Number",
    "College Name",
    "Department",
    "Year of Study",
    "Team ID",
    "Team Name",
    "Active Members Count",
    "Team Members Roster",
    "Payment ID",
    "Payment Status",
    "Amount (INR)",
    "Payment Type",
    "Razorpay Order ID",
    "Razorpay Payment ID",
    "Created At",
]


async def _get_registration_export_data(
    db: AsyncSession, event_id: Optional[uuid.UUID] = None
) -> tuple[list[str], list[list[Any]]]:
    q = (
        select(Registration)
        .options(
            selectinload(Registration.event),
            selectinload(Registration.team).selectinload(Team.members),
            selectinload(Registration.payment),
        )
        .order_by(Registration.created_at.desc())
    )
    if event_id:
        q = q.where(Registration.event_id == event_id)
    result = await db.execute(q)
    registrations = result.scalars().all()

    profile_ids: set[uuid.UUID] = set()
    for r in registrations:
        if r.profile_id:
            profile_ids.add(r.profile_id)
        if r.team:
            if r.team.leader_profile_id:
                profile_ids.add(r.team.leader_profile_id)
            for m in (r.team.members or []):
                if m.profile_id:
                    profile_ids.add(m.profile_id)

    profiles: dict[uuid.UUID, Profile] = {}
    if profile_ids:
        prof_result = await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))
        profiles = {p.id: p for p in prof_result.scalars().all()}

    custom_fields: list[EventRegistrationField] = []
    field_resp_map: dict[tuple[uuid.UUID, uuid.UUID], Any] = {}
    if event_id and registrations:
        cf_res = await db.execute(
            select(EventRegistrationField)
            .where(
                EventRegistrationField.event_id == event_id,
                EventRegistrationField.source == RegistrationFieldSource.CUSTOM,
            )
            .order_by(EventRegistrationField.display_order)
        )
        custom_fields = list(cf_res.scalars().all())

        reg_ids = [r.id for r in registrations]
        fr_res = await db.execute(
            select(RegistrationFieldResponse).where(RegistrationFieldResponse.registration_id.in_(reg_ids))
        )
        for fr in fr_res.scalars().all():
            field_resp_map[(fr.registration_id, fr.field_id)] = fr.value

    headers = list(REGISTRATION_EXPORT_HEADERS)
    for cf in custom_fields:
        headers.append(f"Field: {cf.label}")

    rows = []
    for r in registrations:
        reg_type = derive_registration_type(r)
        event_name = r.event.name if r.event else ""

        # Main contact: solo participant or team leader
        main_profile: Optional[Profile] = None
        if r.profile_id:
            main_profile = profiles.get(r.profile_id)
        elif r.team and r.team.leader_profile_id:
            main_profile = profiles.get(r.team.leader_profile_id)

        participant_name = main_profile.full_name if main_profile else ""
        contact_email = main_profile.contact_email if main_profile else ""
        phone = main_profile.phone if main_profile else ""
        college = main_profile.college_name if main_profile else ""
        department = main_profile.department if main_profile else ""
        year = str(main_profile.year_of_study) if (main_profile and main_profile.year_of_study is not None) else ""

        team_id_str = str(r.team_id) if r.team_id else ""
        team_name = r.team.name if r.team else ""
        active_count = len(_active_team_members(r.team)) if r.team else ""

        roster_str = ""
        if r.team:
            members_summary = []
            for m in _active_team_members(r.team):
                m_prof = profiles.get(m.profile_id) if m.profile_id else None
                m_name = (m_prof.full_name if m_prof else m.full_name) or "Unknown"
                m_role = m.role.value if hasattr(m.role, "value") else str(m.role)
                phone_val = (m_prof.phone if m_prof else m.phone) or ""
                m_phone = f", {phone_val}" if phone_val else ""
                members_summary.append(f"{m_name} ({m_role}{m_phone})")
            roster_str = "; ".join(members_summary)

        payment_id_str = str(r.payment_id) if r.payment_id else ""
        payment_status_str = (
            r.payment.status.value
            if (r.payment and hasattr(r.payment.status, "value"))
            else (str(r.payment.status) if r.payment else "")
        )
        amount_inr = (
            f"{r.payment.amount_paise / 100:.2f}"
            if (r.payment and r.payment.amount_paise is not None)
            else ""
        )
        payment_type_str = (
            r.payment.payment_type.value
            if (r.payment and hasattr(r.payment.payment_type, "value"))
            else (str(r.payment.payment_type) if r.payment else "")
        )
        order_id = r.payment.razorpay_order_id if r.payment else ""
        payment_tx_id = r.payment.razorpay_payment_id or "" if r.payment else ""

        status_val = r.status.value if hasattr(r.status, "value") else str(r.status)
        created_str = r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""

        row = [
            str(r.id),
            str(r.event_id),
            event_name,
            reg_type,
            status_val,
            participant_name,
            contact_email,
            phone,
            college,
            department,
            year,
            team_id_str,
            team_name,
            active_count,
            roster_str,
            payment_id_str,
            payment_status_str,
            amount_inr,
            payment_type_str,
            order_id,
            payment_tx_id,
            created_str,
        ]
        for cf in custom_fields:
            val = field_resp_map.get((r.id, cf.id), "")
            if isinstance(val, (list, dict)):
                val = json.dumps(val)
            row.append(val)
        rows.append(row)

    return headers, rows


async def export_registrations_xlsx(db: AsyncSession, event_id: Optional[uuid.UUID] = None) -> BytesIO:
    headers, rows = await _get_registration_export_data(db, event_id=event_id)
    
    # Event name is at index 2 (REGISTRATION_EXPORT_HEADERS = ["Registration ID", "Event ID", "Event Name", ...])
    event_name_idx = 2
    
    if event_id and rows:
        # Single event export
        event_name = rows[0][event_name_idx] or "Registrations"
        return _workbook_bytes(headers, rows, sheet_title=event_name)
    else:
        # Multi-event export
        from collections import defaultdict
        grouped = defaultdict(list)
        for row in rows:
            ename = row[event_name_idx] or "Unknown Event"
            grouped[ename].append(row)
        
        return _multi_sheet_workbook_bytes(headers, dict(grouped))


async def export_registrations_csv(db: AsyncSession, event_id: Optional[uuid.UUID] = None) -> BytesIO:
    headers, rows = await _get_registration_export_data(db, event_id=event_id)
    return _csv_bytes(headers, rows)


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


def derive_registration_type(registration: Registration) -> str:
    if registration.team_id is not None:
        return "TEAM"
    return "SOLO"


def _active_team_members(team: Team) -> list[TeamMember]:
    return [m for m in (team.members or []) if m.status in _ACTIVE_MEMBER_STATUSES]


def serialize_admin_registration_payment(payment: Payment | None) -> dict[str, Any] | None:
    if payment is None:
        return None
    return {
        "id": payment.id,
        "payer_profile_id": payment.payer_profile_id,
        "payment_type": payment.payment_type,
        "amount_paise": payment.amount_paise,
        "currency": payment.currency,
        "status": payment.status,
        "razorpay_order_id": payment.razorpay_order_id,
        "razorpay_payment_id": payment.razorpay_payment_id,
        "created_at": payment.created_at,
    }


def serialize_admin_registration_team(
    team: Team,
    rules: EventRegistrationRule | None,
) -> dict[str, Any]:
    active = _active_team_members(team)
    if rules is not None:
        required, max_subs, total = roster_limits(rules)
    else:
        required, max_subs, total = 1, 0, 1
    return {
        "id": team.id,
        "name": team.name,
        "status": team.status,
        "leader_profile_id": team.leader_profile_id,
        "active_member_count": len(active),
        "required_member_count": required,
        "substitute_count": max_subs,
        "team_max_size": total,
        "mandatory_filled": count_mandatory(active),
        "substitutes_filled": count_substitutes(active),
    }


async def attach_field_responses_to_registrations(
    db: AsyncSession, registrations: list[Registration]
) -> None:
    if not registrations:
        return
    reg_ids = [r.id for r in registrations]
    result = await db.execute(
        select(RegistrationFieldResponse)
        .options(selectinload(RegistrationFieldResponse.field))
        .where(RegistrationFieldResponse.registration_id.in_(reg_ids))
    )
    grouped: dict[uuid.UUID, list[FieldResponseOut]] = {rid: [] for rid in reg_ids}
    for row in result.scalars().all():
        grouped[row.registration_id].append(
            FieldResponseOut(
                field_id=row.field_id,
                field_key=row.field.field_key if row.field else None,
                label=row.field.label if row.field else None,
                value=row.value,
            )
        )
    for registration in registrations:
        registration.field_responses = grouped.get(registration.id, [])


def serialize_admin_registration(registration: Registration) -> dict[str, Any]:
    registration_type = derive_registration_type(registration)
    payment = registration.payment
    rules = registration.event.rules if registration.event is not None else None
    team_payload = None
    if registration_type == "TEAM" and registration.team is not None:
        team_payload = serialize_admin_registration_team(registration.team, rules)

    field_responses = getattr(registration, "field_responses", []) or []

    return {
        "id": registration.id,
        "event_id": registration.event_id,
        "event_name": registration.event.name if registration.event is not None else None,
        "profile_id": registration.profile_id,
        "team_id": registration.team_id,
        "registration_type": registration_type,
        "status": registration.status,
        "payment_id": registration.payment_id,
        "payment_status": payment.status if payment is not None else None,
        "payment": serialize_admin_registration_payment(payment),
        "team": team_payload,
        "field_responses": [
            fr.model_dump() if hasattr(fr, "model_dump") else fr for fr in field_responses
        ],
        "created_at": registration.created_at,
        "participant": getattr(registration, "_participant", None),
    }


def _profile_contact_dict(profile: Profile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "profile_id": profile.id,
        "full_name": profile.full_name,
        "email": profile.contact_email,
        "phone": profile.phone,
        "college": profile.college_name,
        "department": profile.department,
        "year": profile.year_of_study,
    }


async def attach_participants_to_registrations(
    db: AsyncSession, registrations: list[Registration]
) -> None:
    if not registrations:
        return
    profile_ids: set[uuid.UUID] = set()
    for r in registrations:
        if r.profile_id:
            profile_ids.add(r.profile_id)
        elif r.team and r.team.leader_profile_id:
            profile_ids.add(r.team.leader_profile_id)
    profiles: dict[uuid.UUID, Profile] = {}
    if profile_ids:
        result = await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))
        profiles = {p.id: p for p in result.scalars().all()}
    for r in registrations:
        pid = r.profile_id or (r.team.leader_profile_id if r.team else None)
        r._participant = _profile_contact_dict(profiles.get(pid) if pid else None)


async def get_admin_registration_detail(db: AsyncSession, registration_id: uuid.UUID) -> dict[str, Any]:
    result = await db.execute(
        select(Registration)
        .options(*ADMIN_REGISTRATION_LIST_LOAD)
        .where(Registration.id == registration_id)
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=404, detail="Registration not found.")
    await attach_field_responses_to_registrations(db, [registration])
    await attach_participants_to_registrations(db, [registration])
    payload = serialize_admin_registration(registration)
    payload["event"] = (
        {
            "id": registration.event.id,
            "name": registration.event.name,
            "category": registration.event.category,
        }
        if registration.event
        else None
    )
    if registration.team:
        members_result = await db.execute(
            select(TeamMember).where(TeamMember.team_id == registration.team_id)
        )
        members = list(members_result.scalars().all())
        member_ids = [m.id for m in members]
        member_field_map: dict[uuid.UUID, list[dict[str, Any]]] = {}
        if member_ids:
            fr_res = await db.execute(
                select(RegistrationFieldResponse)
                .options(selectinload(RegistrationFieldResponse.field))
                .where(RegistrationFieldResponse.team_member_id.in_(member_ids))
            )
            for fr in fr_res.scalars().all():
                member_field_map.setdefault(fr.team_member_id, []).append(
                    FieldResponseOut(
                        field_id=fr.field_id,
                        field_key=fr.field.field_key if fr.field else None,
                        label=fr.field.label if fr.field else None,
                        value=fr.value,
                    ).model_dump()
                )
        profile_ids = {m.profile_id for m in members if m.profile_id}
        profiles: dict[uuid.UUID, Profile] = {}
        if profile_ids:
            prof_res = await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))
            profiles = {p.id: p for p in prof_res.scalars().all()}
        payload["team"]["members"] = [
            {
                "id": m.id,
                "profile_id": m.profile_id,
                "full_name": (profiles[m.profile_id].full_name if m.profile_id and m.profile_id in profiles else m.full_name),
                "phone": (profiles[m.profile_id].phone if m.profile_id and m.profile_id in profiles else m.phone),
                "contact_email": (
                    profiles[m.profile_id].contact_email if m.profile_id and m.profile_id in profiles else m.contact_email
                ),
                "college_name": (
                    profiles[m.profile_id].college_name if m.profile_id and m.profile_id in profiles else m.college_name
                ),
                "year_of_study": (
                    profiles[m.profile_id].year_of_study if m.profile_id and m.profile_id in profiles else m.year_of_study
                ),
                "role": m.role,
                "status": m.status,
                "entry_source": m.entry_source,
                "joined_at": m.joined_at,
                "field_responses": member_field_map.get(m.id, []),
            }
            for m in members
        ]
    return payload


async def get_admin_team_detail(db: AsyncSession, team_id: uuid.UUID) -> dict[str, Any]:
    result = await db.execute(
        select(Team).options(selectinload(Team.members)).where(Team.id == team_id)
    )
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    event_result = await db.execute(
        select(Event).options(selectinload(Event.rules)).where(Event.id == team.event_id)
    )
    event = event_result.scalar_one_or_none()
    rules = event.rules if event else None
    summary = serialize_admin_registration_team(team, rules)
    member_ids = [m.id for m in (team.members or [])]
    member_field_map: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if member_ids:
        fr_res = await db.execute(
            select(RegistrationFieldResponse)
            .options(selectinload(RegistrationFieldResponse.field))
            .where(RegistrationFieldResponse.team_member_id.in_(member_ids))
        )
        for fr in fr_res.scalars().all():
            member_field_map.setdefault(fr.team_member_id, []).append(
                FieldResponseOut(
                    field_id=fr.field_id,
                    field_key=fr.field.field_key if fr.field else None,
                    label=fr.field.label if fr.field else None,
                    value=fr.value,
                ).model_dump()
            )
    profile_ids = {m.profile_id for m in (team.members or []) if m.profile_id}
    profiles: dict[uuid.UUID, Profile] = {}
    if profile_ids:
        prof_res = await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))
        profiles = {p.id: p for p in prof_res.scalars().all()}
    members_payload = []
    for m in team.members or []:
        prof = profiles.get(m.profile_id) if m.profile_id else None
        members_payload.append(
            {
                "id": m.id,
                "profile_id": m.profile_id,
                "full_name": prof.full_name if prof else m.full_name,
                "phone": prof.phone if prof else m.phone,
                "contact_email": prof.contact_email if prof else m.contact_email,
                "college_name": prof.college_name if prof else m.college_name,
                "year_of_study": prof.year_of_study if prof else m.year_of_study,
                "role": m.role,
                "status": m.status,
                "entry_source": m.entry_source,
                "joined_at": m.joined_at,
                "field_responses": member_field_map.get(m.id, []),
            }
        )
    leader = profiles.get(team.leader_profile_id) if team.leader_profile_id else None
    return {
        "id": team.id,
        "event_id": team.event_id,
        "event_name": event.name if event else None,
        "name": team.name,
        "status": team.status,
        "leader_profile_id": team.leader_profile_id,
        "leader": _profile_contact_dict(leader),
        "created_at": team.created_at,
        "active_member_count": summary["active_member_count"],
        "required_member_count": summary["required_member_count"],
        "substitute_count": summary["substitute_count"],
        "team_max_size": summary["team_max_size"],
        "mandatory_filled": summary["mandatory_filled"],
        "substitutes_filled": summary["substitutes_filled"],
        "members": members_payload,
    }


async def event_operations_metrics(db: AsyncSession, event_id: uuid.UUID) -> dict[str, Any]:
    event, rules = await get_event_with_rules(db, event_id)
    from app.services.event_service import spots_remaining

    reg_result = await db.execute(
        select(Registration.status, func.count())
        .where(Registration.event_id == event_id)
        .group_by(Registration.status)
    )
    reg_by_status = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in reg_result.all()
    }
    team_result = await db.execute(
        select(Team.status, func.count()).where(Team.event_id == event_id).group_by(Team.status)
    )
    team_by_status = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in team_result.all()
    }
    pay_result = await db.execute(
        select(Payment.status, func.count())
        .join(Registration, Registration.payment_id == Payment.id)
        .where(Registration.event_id == event_id)
        .group_by(Payment.status)
    )
    pay_by_status = {
        row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in pay_result.all()
    }
    remaining = await spots_remaining(db, event, rules)
    capacity = event.capacity
    used = None
    if capacity is not None and remaining is not None:
        used = max(0, capacity - remaining)
    return {
        "event_id": event.id,
        "registrations": reg_by_status,
        "registrations_total": sum(reg_by_status.values()),
        "teams": team_by_status,
        "teams_total": sum(team_by_status.values()),
        "payments": pay_by_status,
        "capacity": capacity,
        "capacity_used": used,
        "spots_remaining": remaining,
    }


async def build_event_operations_rows(
    db: AsyncSession, *, scoped_event_ids: Optional[set[uuid.UUID]] = None, limit: int = 50
) -> list[dict[str, Any]]:
    q = (
        select(Event, EventRegistrationRule)
        .join(EventRegistrationRule, EventRegistrationRule.event_id == Event.id)
        .order_by(Event.starts_at.nulls_last())
        .limit(limit)
    )
    if scoped_event_ids is not None:
        q = q.where(Event.id.in_(scoped_event_ids))
    result = await db.execute(q)
    rows = []
    for event, rules in result.all():
        metrics = await event_operations_metrics(db, event.id)
        rows.append(
            {
                "event_id": event.id,
                "event_name": event.name,
                "category": event.category,
                "starts_at": event.starts_at,
                "visibility": event.visibility,
                "registration_status": event.registration_status,
                "registrations_confirmed": metrics["registrations"].get("CONFIRMED", 0),
                "registrations_pending": metrics["registrations"].get("PENDING", 0),
                "teams_complete": metrics["teams"].get("COMPLETE", 0),
                "teams_forming": metrics["teams"].get("FORMING", 0),
                "payments_paid": metrics["payments"].get("PAID", 0),
                "payments_pending": metrics["payments"].get("CREATED", 0),
                "capacity_used": metrics["capacity_used"],
                "capacity": metrics["capacity"],
            }
        )
    return rows


async def recent_registration_activity(
    db: AsyncSession, *, scoped_event_ids: Optional[set[uuid.UUID]] = None, limit: int = 8
) -> list[dict[str, Any]]:
    q = (
        select(Registration)
        .options(selectinload(Registration.event), selectinload(Registration.team))
        .order_by(Registration.created_at.desc())
        .limit(limit)
    )
    if scoped_event_ids is not None:
        q = q.where(Registration.event_id.in_(scoped_event_ids))
    result = await db.execute(q)
    items = []
    for r in result.scalars().all():
        label = r.event.name if r.event else "Event"
        if r.team:
            detail = f"Team {r.team.name} registered"
        else:
            detail = "New registration"
        items.append(
            {
                "registration_id": r.id,
                "event_id": r.event_id,
                "event_name": label,
                "summary": f"{label} — {detail}",
                "status": r.status,
                "created_at": r.created_at,
            }
        )
    return items


async def recent_payment_activity(db: AsyncSession, limit: int = 8) -> list[dict[str, Any]]:
    q = (
        select(Payment, Registration, Event.name)
        .outerjoin(Registration, Registration.payment_id == Payment.id)
        .outerjoin(Event, Event.id == Registration.event_id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    items = []
    for payment, _reg, event_name in result.all():
        items.append(
            {
                "payment_id": payment.id,
                "event_name": event_name or "Unknown event",
                "summary": f"{event_name or 'Payment'} — {payment.status.value if hasattr(payment.status, 'value') else payment.status}",
                "status": payment.status,
                "amount_paise": payment.amount_paise,
                "created_at": payment.created_at,
            }
        )
    return items
