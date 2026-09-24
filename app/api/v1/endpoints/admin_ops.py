"""Phase 5 admin operations — dashboard, events, participants, teams, exports."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps_admin import (
    get_current_active_admin,
    require_permission,
    require_super_admin,
)
from app.db.session import get_db
from app.models.admin import AdminUser
from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    PaymentStatus,
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team
from app.schemas.admin_ops import (
    AdminEventCreate,
    AdminEventUpdate,
    AdminManualRegister,
    AdminParticipantUpdate,
    AdminRegistrationRulesUpdate,
    AdminRegistrationUpdate,
    AdminTeamUpdate,
    TransferLeadershipBody,
)
from app.services import admin_delete_service, admin_ops_service as ops
from app.services.registration_service import _already_registered
from app.services.event_service import invalidate_events_list_cache, invalidate_spots_cache
from app.services.event_state import (
    close_registration,
    open_registration,
    publish_event,
    unpublish_event,
)

router = APIRouter(prefix="/admin", tags=["Admin Operations"])

_REG_LOAD = (
    selectinload(Registration.team).selectinload(Team.members),
    selectinload(Registration.payment),
)


def _success(data):
    return {"status": "success", "data": data}


@router.get("/dashboard")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    return _success(await ops.dashboard_payload(db))


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_event(
    body: AdminEventCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event = Event(
        name=body.name,
        tagline=body.tagline,
        short_desc=body.short_desc,
        long_desc=body.long_desc,
        category=body.category,
        coordinator=body.coordinator,
        coord_contact=body.coord_contact,
        fee=body.fee,
        venue=body.venue,
        capacity=body.capacity,
        whatsapp_group_link=body.whatsapp_group_link,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        slot=body.slot,
        visibility=EventVisibility.UNPUBLISHED,
        registration_status=EventRegistrationStatus.CLOSED,
    )
    db.add(event)
    await db.flush()

    rules = EventRegistrationRule(
        event_id=event.id,
        team_min_size=body.team_min_size,
        team_max_size=body.team_max_size,
        allow_team_invite_flow=body.allow_team_invite_flow,
        requires_qr_checkin=body.requires_qr_checkin,
        capacity_type=body.capacity_type,
        member_registration_mode=body.member_registration_mode,
        custom_fields=body.custom_fields,
    )
    ops.apply_registration_mode_to_rules(rules, body.registration_mode)
    if body.registration_mode != RegistrationMode.INDIVIDUAL_ONLY:
        ops.apply_roster_to_rules(
            rules,
            required_member_count=body.required_member_count,
            substitute_count=body.substitute_count,
            team_min_size=body.team_min_size,
            team_max_size=body.team_max_size,
        )
    db.add(rules)
    await db.commit()
    await db.refresh(event)
    await db.refresh(rules)
    invalidate_events_list_cache()
    invalidate_spots_cache(event.id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.get("/events")
async def list_admin_events(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    result = await db.execute(
        select(Event, EventRegistrationRule)
        .outerjoin(EventRegistrationRule, EventRegistrationRule.event_id == Event.id)
        .order_by(Event.starts_at.nulls_last())
    )
    items = []
    for event, rules in result.all():
        if not rules:
            continue
        payload = await ops.event_to_dict_with_state(db, event, rules)
        items.append(payload)
    return _success(items)


@router.get("/events/{event_id}")
async def get_admin_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(get_current_active_admin),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.patch("/events/{event_id}")
async def patch_event(
    event_id: UUID,
    body: AdminEventUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    await db.commit()
    await db.refresh(event)
    await db.refresh(rules)
    if body.capacity is not None:
        invalidate_spots_cache(event_id)
    invalidate_events_list_cache()
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.patch("/events/{event_id}/registration-rules")
async def patch_registration_rules(
    event_id: UUID,
    body: AdminRegistrationRulesUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    data = body.model_dump(exclude_unset=True)
    mode = data.pop("registration_mode", None)
    roster_keys = (
        "required_member_count",
        "substitute_count",
        "team_min_size",
        "team_max_size",
    )
    roster_patch = {k: data.pop(k) for k in roster_keys if k in data}
    for field, value in data.items():
        setattr(rules, field, value)
    if mode is not None:
        ops.apply_registration_mode_to_rules(rules, mode)
    if roster_patch and rules.registration_mode != RegistrationMode.INDIVIDUAL_ONLY:
        ops.apply_roster_to_rules(rules, **roster_patch)
    await db.commit()
    await db.refresh(rules)
    invalidate_events_list_cache()
    invalidate_spots_cache(event_id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.post("/events/{event_id}/publish")
async def publish_admin_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    publish_event(event)
    await db.commit()
    await db.refresh(event)
    invalidate_events_list_cache()
    invalidate_spots_cache(event.id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.post("/events/{event_id}/unpublish")
async def unpublish_admin_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    unpublish_event(event)
    await db.commit()
    await db.refresh(event)
    invalidate_events_list_cache()
    invalidate_spots_cache(event.id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.post("/events/{event_id}/open-registration")
async def open_event_registration(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    open_registration(event)
    await db.commit()
    await db.refresh(event)
    invalidate_events_list_cache()
    invalidate_spots_cache(event.id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.post("/events/{event_id}/close-registration")
async def close_event_registration(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("event-management")),
):
    event, rules = await ops.get_event_with_rules(db, event_id)
    close_registration(event)
    await db.commit()
    await db.refresh(event)
    invalidate_events_list_cache()
    invalidate_spots_cache(event.id)
    return _success(await ops.event_to_dict_with_state(db, event, rules))


@router.delete("/events/{event_id}")
async def delete_event_record(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    return _success(await admin_delete_service.admin_delete_event(db, event_id, admin))


@router.get("/participants")
async def list_participants(
    q: Optional[str] = Query(default=None),
    event_id: Optional[UUID] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("participant-read")),
):
    profiles, total = await ops.search_participant_profiles(
        db, q=q, event_id=event_id, skip=skip, limit=min(limit, 100)
    )
    data = [
        {
            "profile_id": p.id,
            "user_id": p.user_id,
            "full_name": p.full_name,
            "contact_email": p.contact_email,
            "phone": p.phone,
            "college_name": p.college_name,
            "department": p.department,
            "year_of_study": p.year_of_study,
        }
        for p in profiles
    ]
    return _success({"items": data, "total": total, "skip": skip, "limit": limit})


@router.delete("/participants/{profile_id}")
async def delete_participant_record(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    return _success(await admin_delete_service.admin_delete_participant(db, profile_id, admin))


@router.patch("/participants/{profile_id}")
async def update_participant(
    profile_id: UUID,
    body: AdminParticipantUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("participant-edit")),
):
    result = await db.execute(select(Profile).where(Profile.id == profile_id))
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return _success(
        {
            "profile_id": profile.id,
            "full_name": profile.full_name,
            "contact_email": profile.contact_email,
            "phone": profile.phone,
            "college_name": profile.college_name,
            "department": profile.department,
            "year_of_study": profile.year_of_study,
        }
    )


@router.post("/participants", status_code=status.HTTP_201_CREATED)
async def manual_register_participant(
    body: AdminManualRegister,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("participant-registration", "participant-edit")),
):
    profile_result = await db.execute(select(Profile).where(Profile.id == body.profile_id))
    if not profile_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Profile not found.")
    event_result = await db.execute(select(Event).where(Event.id == body.event_id))
    if not event_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Event not found.")
    if await _already_registered(db, body.event_id, body.profile_id):
        raise HTTPException(status_code=409, detail="Profile is already registered for this event.")

    reg_status = (
        RegistrationStatus.CONFIRMED if body.mark_paid else RegistrationStatus.PENDING
    )
    registration = Registration(
        event_id=body.event_id,
        profile_id=body.profile_id,
        status=reg_status,
    )
    db.add(registration)
    await db.commit()
    await db.refresh(registration)
    return _success(
        {
            "registration_id": registration.id,
            "event_id": registration.event_id,
            "profile_id": registration.profile_id,
            "status": registration.status,
        }
    )


@router.get("/teams")
async def list_teams(
    event_id: Optional[UUID] = Query(default=None),
    team_status: Optional[TeamStatus] = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("team-read")),
):
    q = select(Team).order_by(Team.created_at.desc())
    if event_id:
        q = q.where(Team.event_id == event_id)
    if team_status:
        q = q.where(Team.status == team_status)
    q = q.offset(skip).limit(min(limit, 100))
    result = await db.execute(q)
    teams = result.scalars().all()
    return _success(
        [
            {
                "id": t.id,
                "event_id": t.event_id,
                "name": t.name,
                "leader_profile_id": t.leader_profile_id,
                "status": t.status,
                "created_at": t.created_at,
            }
            for t in teams
        ]
    )


@router.patch("/teams/{team_id}")
async def patch_team(
    team_id: UUID,
    body: AdminTeamUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("team-edit")),
):
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot edit a cancelled team.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(team, field, value)
    await db.commit()
    await db.refresh(team)
    return _success(
        {
            "id": team.id,
            "event_id": team.event_id,
            "name": team.name,
            "leader_profile_id": team.leader_profile_id,
            "status": team.status,
        }
    )


@router.post("/teams/{team_id}/transfer-leadership")
async def transfer_leadership(
    team_id: UUID,
    body: TransferLeadershipBody,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("leadership-transfer")),
):
    return _success(await ops.transfer_team_leadership(db, team_id, body.new_leader_profile_id))


@router.post("/teams/{team_id}/cancel")
async def cancel_team(
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    return _success(await ops.cancel_team_admin(db, team_id))


@router.delete("/teams/{team_id}")
async def delete_team_record(
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    return _success(await admin_delete_service.admin_delete_team(db, team_id, admin))


@router.get("/registrations")
async def list_registrations(
    event_id: Optional[UUID] = Query(default=None),
    reg_status: Optional[RegistrationStatus] = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("registration-read")),
):
    q = select(Registration).options(*_REG_LOAD).order_by(Registration.created_at.desc())
    if event_id:
        q = q.where(Registration.event_id == event_id)
    if reg_status:
        q = q.where(Registration.status == reg_status)
    q = q.offset(skip).limit(min(limit, 100))
    result = await db.execute(q)
    registrations = result.scalars().all()
    items = []
    for r in registrations:
        items.append(
            {
                "id": r.id,
                "event_id": r.event_id,
                "profile_id": r.profile_id,
                "team_id": r.team_id,
                "status": r.status,
                "payment_id": r.payment_id,
                "payment_status": r.payment.status if r.payment else None,
                "created_at": r.created_at,
            }
        )
    return _success({"items": items, "skip": skip, "limit": limit})


@router.patch("/registrations/{registration_id}")
async def patch_registration(
    registration_id: UUID,
    body: AdminRegistrationUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("registration-edit")),
):
    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    registration = result.scalar_one_or_none()
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found.")
    if body.status is not None:
        registration.status = body.status
    await db.commit()
    await db.refresh(registration)
    return _success(
        {
            "id": registration.id,
            "event_id": registration.event_id,
            "status": registration.status,
        }
    )


@router.post("/registrations/{registration_id}/cancel")
async def cancel_registration(
    registration_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_super_admin),
):
    registration = await ops.cancel_registration_admin(db, registration_id)
    return _success({"id": registration.id, "status": registration.status})


@router.delete("/registrations/{registration_id}")
async def delete_registration_record(
    registration_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    return _success(await admin_delete_service.admin_delete_registration(db, registration_id, admin))


@router.get("/payments")
async def list_payments(
    payment_type: Optional[PaymentType] = Query(default=None),
    payment_status: Optional[PaymentStatus] = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("payment-read")),
):
    q = (
        select(Payment)
        .where(
            Payment.payment_type.in_([PaymentType.SOLO_REGISTRATION, PaymentType.TEAM_REGISTRATION])
        )
        .order_by(Payment.created_at.desc())
    )
    if payment_type:
        q = q.where(Payment.payment_type == payment_type)
    if payment_status:
        q = q.where(Payment.status == payment_status)
    q = q.offset(skip).limit(min(limit, 100))
    result = await db.execute(q)
    payments = result.scalars().all()
    return _success(
        {
            "items": [
                {
                    "id": p.id,
                    "payer_profile_id": p.payer_profile_id,
                    "payment_type": p.payment_type,
                    "amount_paise": p.amount_paise,
                    "currency": p.currency,
                    "status": p.status,
                    "razorpay_order_id": p.razorpay_order_id,
                    "razorpay_payment_id": p.razorpay_payment_id,
                    "created_at": p.created_at,
                }
                for p in payments
            ],
            "skip": skip,
            "limit": limit,
        }
    )


@router.get("/exports/registrations")
async def export_registrations(
    event_id: Optional[UUID] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("export")),
):
    buf = await ops.export_registrations_xlsx(db, event_id=event_id)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=registrations.xlsx"},
    )


@router.get("/exports/payments")
async def export_payments(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("export")),
):
    buf = await ops.export_payments_xlsx(db)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=payments.xlsx"},
    )


@router.get("/exports/attendance")
async def export_attendance(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("export")),
):
    buf = await ops.export_attendance_xlsx(db)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=attendance.xlsx"},
    )
