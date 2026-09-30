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
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.integrity_errors import raise_duplicate_registration
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import is_db_configured
from app.core.errors import ALREADY_REGISTERED, AppError
from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    Gender,
    GenderCategory,
    PaymentStatus,
    RegistrationFieldScope,
    RegistrationStatus,
    TeamMemberEntrySource,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.event_content import EventRegistrationField, RegistrationFieldResponse
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.event_content import FieldResponseOut
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services import qr_service
from app.services.field_response_validator import persist_field_responses, validate_and_prepare_responses
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


async def _already_registered(db: AsyncSession, event_id: uuid.UUID, profile: Profile) -> bool:
    solo = await db.execute(
        select(Registration).where(
            Registration.event_id == event_id,
            Registration.profile_id == profile.id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
    )
    if solo.scalar_one_or_none() is not None:
        return True

    conditions = [TeamMember.profile_id == profile.id]
    if profile.phone and profile.phone.strip():
        conditions.append(TeamMember.phone == profile.phone.strip())
    if profile.contact_email and profile.contact_email.strip():
        conditions.append(TeamMember.contact_email == profile.contact_email.strip())

    member = await db.execute(
        select(TeamMember).where(
            TeamMember.event_id == event_id,
            or_(*conditions),
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

    if await _already_registered(db, event_id, profile):
        raise AppError(ALREADY_REGISTERED, "You are already registered for this event", status_code=409)

    field_pairs = await validate_and_prepare_responses(
        db,
        event_id,
        RegistrationFieldScope.REGISTRATION,
        payload.field_responses,
        profile=profile,
    )

    gender_cat = getattr(rules, "gender_category", None) or getattr(event, "gender_category", GenderCategory.OPEN)

    if payload.registration_type == RegistrationType.SOLO:
        if rules and not rules.allow_individual:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Solo registration is not allowed for this event"
            )
        if gender_cat == GenderCategory.MALE_ONLY and profile.gender != Gender.MALE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="This event is restricted to male participants"
            )
        elif gender_cat == GenderCategory.FEMALE_ONLY and profile.gender != Gender.FEMALE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="This event is restricted to female participants"
            )

        registration = Registration(event_id=event_id, profile_id=profile.id, status=RegistrationStatus.PENDING)
        db.add(registration)
        await db.flush()
        await persist_field_responses(db, field_pairs, registration_id=registration.id)
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

    roster_count = 1 + len(payload.roster_members or [])
    if rules and roster_count > rules.team_max_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Team size ({roster_count}) exceeds maximum allowed members ({rules.team_max_size})",
        )

    # Validate leader gender against event restriction
    if gender_cat == GenderCategory.MALE_ONLY and profile.gender != Gender.MALE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This event is restricted to male participants"
        )
    elif gender_cat == GenderCategory.FEMALE_ONLY and profile.gender != Gender.FEMALE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This event is restricted to female participants"
        )

    # Validate roster members (e.g. duo partner)
    for rm in (payload.roster_members or []):
        if gender_cat == GenderCategory.MALE_ONLY and rm.gender != Gender.MALE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Member '{rm.full_name}' must be male for this event",
            )
        elif gender_cat == GenderCategory.FEMALE_ONLY and rm.gender != Gender.FEMALE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Member '{rm.full_name}' must be female for this event",
            )
        elif gender_cat == GenderCategory.MIXED and (rules.team_max_size == 2 or roster_count == 2):
            if profile.gender and rm.gender and profile.gender == rm.gender:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Mixed duo event requires one male and one female participant",
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
        entry_source=TeamMemberEntrySource.LINKED_ACCOUNT,
        gender=profile.gender,
    )
    db.add(leader_member)
    await db.flush()
    await qr_service.generate_for_team_member(db, leader_member.id)

    # Add atomic partner/team members if provided
    for rm in (payload.roster_members or []):
        partner_member = TeamMember(
            team_id=team.id,
            event_id=event_id,
            profile_id=None,
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.PENDING_PAYMENT,
            entry_source=TeamMemberEntrySource.LEADER_ENTERED,
            full_name=rm.full_name,
            phone=rm.phone,
            contact_email=rm.contact_email,
            college_name=rm.college_name,
            year_of_study=rm.year_of_study,
            gender=rm.gender,
        )
        db.add(partner_member)
        await db.flush()
        await qr_service.generate_for_team_member(db, partner_member.id)

        if rm.field_responses:
            member_pairs = await validate_and_prepare_responses(
                db,
                event_id,
                RegistrationFieldScope.TEAM_MEMBER,
                rm.field_responses,
                team_member=partner_member,
            )
            await persist_field_responses(db, member_pairs, team_member_id=partner_member.id)

    registration = Registration(event_id=event_id, team_id=team.id, status=RegistrationStatus.PENDING)
    db.add(registration)
    await db.flush()
    await persist_field_responses(db, field_pairs, registration_id=registration.id)

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

    resp_result = await db.execute(
        select(RegistrationFieldResponse)
        .options(selectinload(RegistrationFieldResponse.field))
        .where(RegistrationFieldResponse.registration_id == registration_id)
    )
    responses = resp_result.scalars().all()
    registration.field_responses = [
        FieldResponseOut(
            field_id=r.field_id,
            field_key=r.field.field_key if r.field else None,
            label=r.field.label if r.field else None,
            value=r.value,
        )
        for r in responses
    ]
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
    if not is_db_configured():
        return []
    try:
        team_ids_subquery = select(TeamMember.team_id).where(
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
        result = await db.execute(
            select(Registration)
            .options(*_REGISTRATION_LOAD_OPTS)
            .where(
                or_(
                    Registration.profile_id == profile.id,
                    Registration.team_id.in_(team_ids_subquery),
                )
            )
            .order_by(Registration.created_at.desc())
        )
        return list(result.scalars().all())
    except Exception:
        return []


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

