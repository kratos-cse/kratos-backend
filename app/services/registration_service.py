"""
POST /events/{event_id}/registrations creates the REGISTRATIONS row (and,
for a team, the TEAMS + leader TEAM_MEMBERS rows too) — it does NOT create
a PAYMENTS row. Initiating the actual Razorpay order is a separate
concern (API reference section 8, POST /payments/create-order) owned by
the Payments feature branch; that branch is expected to look up the
registration/team created here by id and create the payment against it.
"""
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.integrity_errors import raise_duplicate_registration
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ALREADY_REGISTERED, AppError
from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    PaymentStatus,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services import qr_service
from app.services.admin_ops_service import invalidate_dashboard_cache
from app.services.event_service import invalidate_spots_cache, is_registration_open, spots_remaining
from app.services import audit_service

_REGISTRATION_LOAD_OPTS = (
    selectinload(Registration.team).selectinload(Team.members),
    selectinload(Registration.payment),
)


async def _get_event_with_rules(
    db: AsyncSession, event_id: uuid.UUID, *, for_update: bool = False
) -> tuple[Event, Optional[EventRegistrationRule]]:
    stmt = select(Event).options(selectinload(Event.rules)).where(Event.id == event_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event, event.rules


async def _already_registered(db: AsyncSession, event_id: uuid.UUID, profile_id: uuid.UUID) -> bool:
    solo = await db.execute(
        select(Registration).where(
            Registration.event_id == event_id,
            Registration.profile_id == profile_id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
    )
    if solo.scalar_one_or_none() is not None:
        return True

    member = await db.execute(
        select(TeamMember).where(
            TeamMember.event_id == event_id,
            TeamMember.profile_id == profile_id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
    )
    return member.scalar_one_or_none() is not None


async def create_registration(
    db: AsyncSession,
    event_id: uuid.UUID,
    profile: Profile,
    payload: RegistrationCreateRequest,
) -> Registration:
    event, rules = await _get_event_with_rules(db, event_id, for_update=True)

    if event.visibility != EventVisibility.PUBLISHED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This event is not available")
    if event.registration_status != EventRegistrationStatus.OPEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration is not open for this event")

    remaining = await spots_remaining(db, event, rules)
    if not is_registration_open(event, rules, remaining):
        if remaining is not None and remaining <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This event has reached capacity")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration is not open for this event")

    if await _already_registered(db, event_id, profile.id):
        raise AppError(ALREADY_REGISTERED, "You are already registered for this event", status_code=409)

    if payload.registration_type == RegistrationType.SOLO:
        if rules and not rules.allow_individual:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Solo registration is not allowed for this event"
            )

        registration = Registration(event_id=event_id, profile_id=profile.id, status=RegistrationStatus.PENDING)
        db.add(registration)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise_duplicate_registration(exc, "You are already registered for this event")
        invalidate_spots_cache(event_id)
        invalidate_dashboard_cache()
        return await get_registration_or_404(db, registration.id)

    # TEAM
    if rules and rules.team_max_size <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This event does not support team registration"
        )

    team = Team(event_id=event_id, name=payload.team_name, leader_profile_id=profile.id, status=TeamStatus.FORMING)
    db.add(team)
    await db.flush()

    leader_member = TeamMember(
        team_id=team.id,
        event_id=event_id,
        profile_id=profile.id,
        role=TeamMemberRole.LEADER,
        status=TeamMemberStatus.PENDING_PAYMENT,
    )
    db.add(leader_member)

    registration = Registration(event_id=event_id, team_id=team.id, status=RegistrationStatus.PENDING)
    db.add(registration)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise_duplicate_registration(exc, "You are already registered for this event")
    invalidate_spots_cache(event_id)
    invalidate_dashboard_cache()
    return await get_registration_or_404(db, registration.id)


async def get_registration_or_404(db: AsyncSession, registration_id: uuid.UUID) -> Registration:
    result = await db.execute(
        select(Registration).options(*_REGISTRATION_LOAD_OPTS).where(Registration.id == registration_id)
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")
    return registration


async def assert_can_view_registration(
    db: AsyncSession, registration: Registration, profile: Profile, is_admin: bool
) -> None:
    if is_admin:
        return
    if registration.profile_id == profile.id:
        return
    if registration.team_id is not None:
        if registration.team and registration.team.leader_profile_id == profile.id:
            return
        result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == registration.team_id,
                TeamMember.profile_id == profile.id,
                TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
            )
        )
        if result.scalar_one_or_none() is not None:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this registration")


async def list_my_registrations(db: AsyncSession, profile: Profile) -> list[Registration]:
    solo_result = await db.execute(
        select(Registration).options(*_REGISTRATION_LOAD_OPTS).where(Registration.profile_id == profile.id)
    )
    solo_regs = list(solo_result.scalars().all())

    team_ids_result = await db.execute(
        select(TeamMember.team_id).where(
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
    )
    team_ids = [row[0] for row in team_ids_result.all()]

    team_regs: list[Registration] = []
    if team_ids:
        team_reg_result = await db.execute(
            select(Registration).options(*_REGISTRATION_LOAD_OPTS).where(Registration.team_id.in_(team_ids))
        )
        team_regs = list(team_reg_result.scalars().all())

    return solo_regs + team_regs


async def cancel_unpaid_registration(
    db: AsyncSession,
    registration_id: uuid.UUID,
    profile: Profile,
    is_admin: bool = False,
) -> Registration:
    result = await db.execute(
        select(Registration)
        .options(*_REGISTRATION_LOAD_OPTS)
        .where(Registration.id == registration_id)
        .with_for_update()
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    # Authorization check: only owner (or team leader for team reg) or admin can cancel
    if not is_admin:
        if registration.profile_id is not None:
            if registration.profile_id != profile.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to cancel this registration",
                )
        elif registration.team_id is not None:
            if registration.team is None or registration.team.leader_profile_id != profile.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only the team leader can cancel this registration",
                )

    # Status / Payment validity check
    if registration.status == RegistrationStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel a confirmed registration. Please contact the organizers.",
        )

    if registration.payment and registration.payment.status == PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel a paid registration.",
        )

    if registration.status == RegistrationStatus.CANCELLED:
        return registration

    registration.status = RegistrationStatus.CANCELLED
    await qr_service.deactivate_for_registration(db, registration.id)

    # Mark unpaid CREATED payments failed and unlink so a late Razorpay capture
    # cannot re-CONFIRM this cancelled registration via payment_id.
    if registration.payment and registration.payment.status == PaymentStatus.CREATED:
        registration.payment.status = PaymentStatus.FAILED
    registration.payment_id = None

    if registration.team_id is not None:
        team_result = await db.execute(select(Team).where(Team.id == registration.team_id).with_for_update())
        team = team_result.scalar_one_or_none()
        if team and team.status != TeamStatus.CANCELLED:
            team.status = TeamStatus.CANCELLED

        members_result = await db.execute(
            select(TeamMember).where(TeamMember.team_id == registration.team_id)
        )
        for member in members_result.scalars().all():
            if member.status not in (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED):
                member.status = TeamMemberStatus.REMOVED
                await qr_service.deactivate_for_team_member(db, member.id)

    await db.commit()
    await audit_service.log_activity(
        None,
        action="REGISTRATION_CANCELLED",
        resource_type="REGISTRATION",
        resource_id=registration.id,
        actor_user_id=profile.user_id,
        actor_profile_id=profile.id,
        actor_role="ADMIN" if is_admin else "PARTICIPANT",
        details={"event_id": str(registration.event_id), "team_id": str(registration.team_id) if registration.team_id else None},
    )
    invalidate_spots_cache(registration.event_id)
    invalidate_dashboard_cache()
    return await get_registration_or_404(db, registration.id)

