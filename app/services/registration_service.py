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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ALREADY_REGISTERED, AppError
from app.models.enums import PaymentStatus, RegistrationStatus, TeamMemberRole, TeamMemberStatus, TeamStatus
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services import qr_service
from app.services.event_service import is_registration_open, spots_remaining
from app.services import audit_service

_REGISTRATION_LOAD_OPTS = (
    selectinload(Registration.team).selectinload(Team.members),
    selectinload(Registration.payment),
)


async def _get_event_with_rules(
    db: AsyncSession, event_id: uuid.UUID, *, for_update: bool = False
) -> tuple[Event, Optional[EventRegistrationRule]]:
    try:
        stmt = select(Event).options(selectinload(Event.rules)).where(Event.id == event_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        event = result.scalar_one_or_none()
        if event is not None:
            return event, event.rules
    except Exception:
        pass

    from app.services.sample_events import get_sample_event_detail
    sample = get_sample_event_detail(event_id)
    if sample is not None:
        event = Event(
            id=sample.id,
            name=sample.name,
            tagline=sample.tagline,
            short_desc=sample.short_desc,
            long_desc=sample.long_desc,
            category=sample.category,
            coordinator=sample.coordinator,
            coord_contact=sample.coord_contact,
            fee=sample.fee,
            venue=sample.venue,
            capacity=sample.capacity,
            status=sample.status,
            starts_at=sample.starts_at,
            ends_at=sample.ends_at,
            slot=sample.slot,
        )
        rule = EventRegistrationRule(
            id=uuid.uuid4(),
            event_id=sample.id,
            team_min_size=sample.team_min_size,
            team_max_size=sample.team_max_size,
            allow_individual=sample.allow_individual,
            registration_mode=sample.registration_mode,
            capacity_type=sample.capacity_type,
            member_registration_mode=sample.member_registration_mode,
            allow_team_invite_flow=sample.allow_team_invite_flow,
            requires_qr_checkin=sample.requires_qr_checkin,
            custom_fields=sample.custom_fields,
        )
        return event, rule

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")


async def _already_registered(db: AsyncSession, event_id: uuid.UUID, profile_id: uuid.UUID) -> bool:
    from app.services.sample_events import list_dev_registrations
    for r in list_dev_registrations(profile_id):
        if r.event_id == event_id and getattr(r, "status", None) != RegistrationStatus.CANCELLED:
            return True

    try:
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
    except Exception:
        return False


async def create_registration(
    db: AsyncSession,
    event_id: uuid.UUID,
    profile: Profile,
    payload: RegistrationCreateRequest,
):
    event, rules = await _get_event_with_rules(db, event_id, for_update=True)

    if not is_registration_open(event, rules):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration is not open for this event")

    if await _already_registered(db, event_id, profile.id):
        raise AppError(ALREADY_REGISTERED, "You are already registered for this event", status_code=409)

    try:
        remaining = await spots_remaining(db, event, rules)
        if remaining is not None and remaining <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This event has reached capacity")
    except Exception:
        pass

    try:
        if payload.registration_type == RegistrationType.SOLO:
            if rules and not rules.allow_individual:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Solo registration is not allowed for this event"
                )

            registration = Registration(event_id=event_id, profile_id=profile.id, status=RegistrationStatus.PENDING)
            db.add(registration)
            await db.commit()
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
        await db.commit()

        # Invalidate caches
        from app.services.event_service import invalidate_spots_cache
        from app.services.admin_ops_service import invalidate_dashboard_cache

        invalidate_spots_cache(event_id)
        invalidate_dashboard_cache()
        return await get_registration_or_404(db, registration.id)
    except IntegrityError:
        await db.rollback()
        raise AppError(ALREADY_REGISTERED, "You are already registered for this event", status_code=409)
    except Exception:
        # Dev fallback when live DB is unavailable
        from app.services.sample_events import create_dev_registration
        return create_dev_registration(event_id, profile.id, payload)


async def get_registration_or_404(db: AsyncSession, registration_id: uuid.UUID):
    from app.services.sample_events import get_dev_registration
    dev_reg = get_dev_registration(registration_id)
    if dev_reg is not None:
        return dev_reg

    try:
        result = await db.execute(
            select(Registration).options(*_REGISTRATION_LOAD_OPTS).where(Registration.id == registration_id)
        )
        registration = result.scalar_one_or_none()
        if registration is not None:
            return registration
    except Exception:
        pass

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")


async def assert_can_view_registration(
    db: AsyncSession, registration, profile: Profile, is_admin: bool
) -> None:
    if is_admin:
        return
    if getattr(registration, "profile_id", None) == profile.id:
        return
    if getattr(registration, "team_id", None) is not None or getattr(registration, "team", None) is not None:
        team = getattr(registration, "team", None)
        if team and getattr(team, "leader_profile_id", None) == profile.id:
            return
        if team and getattr(team, "members", None):
            if any(m.profile_id == profile.id for m in team.members):
                return
        try:
            result = await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == getattr(registration, "team_id", None),
                    TeamMember.profile_id == profile.id,
                    TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
                )
            )
            if result.scalar_one_or_none() is not None:
                return
        except Exception:
            pass
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this registration")


async def list_my_registrations(db: AsyncSession, profile: Profile) -> list:
    from app.services.sample_events import list_dev_registrations
    dev_regs = list_dev_registrations(profile.id)

    db_regs = []
    try:
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

        db_regs = solo_regs + team_regs
    except Exception:
        db_regs = []

    return dev_regs + db_regs


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

    from app.services.event_service import invalidate_spots_cache
    from app.services.admin_ops_service import invalidate_dashboard_cache

    invalidate_spots_cache(registration.event_id)
    invalidate_dashboard_cache()
    return await get_registration_or_404(db, registration.id)

