"""HTF Application Service.

Handles HTF Application lifecycle, team readiness, draft updates, full validation,
idempotent submit, and status transitions.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import AppError
from app.core.htf_errors import (
    HTF_APPLICATION_ALREADY_EXISTS,
    HTF_APPLICATION_DEADLINE_PASSED,
    HTF_APPLICATION_NOT_EDITABLE,
    HTF_APPLICATION_NOT_FOUND,
    HTF_APPLICATION_NOT_OPEN,
    HTF_NOT_A_TEAM_MEMBER,
    HTF_TEAM_INCOMPLETE,
)
from app.core.permissions import REGISTRATION_READ
from app.models.enums import (
    EventVisibility,
    RegistrationFieldScope,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.htf_application import HtfApplication, HtfApplicationStatus
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.schemas.event_content import FieldResponseInput
from app.services.audit_service import log_activity
from app.services.field_response_validator import validate_and_prepare_responses

logger = logging.getLogger("htf_application_service")

_TERMINAL_MEMBER_STATUSES = (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)


async def get_htf_event(db: AsyncSession) -> Event:
    """Load HTF Event from settings or fallback to single active event."""
    event_id = settings.HTF_EVENT_ID
    if event_id:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
    else:
        # Fallback: query published event if HTF_EVENT_ID not set in env
        result = await db.execute(
            select(Event)
            .where(Event.visibility == EventVisibility.PUBLISHED)
            .order_by(Event.starts_at.desc())
            .limit(1)
        )
        event = result.scalar_one_or_none()

    if not event or event.visibility != EventVisibility.PUBLISHED:
        raise AppError(
            HTF_APPLICATION_NOT_OPEN,
            "HTF event is not available or published",
            status_code=404,
        )
    return event


async def get_htf_deadlines(db: AsyncSession, event: Event) -> dict[str, Any]:
    """Isolate HTF registration deadline reads."""
    # TODO(Person4): Swap in htf_event_config table when available
    result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id)
    )
    rules = result.scalar_one_or_none()
    return {
        "registration_opens_at": rules.registration_opens_at if rules else None,
        "registration_closes_at": rules.registration_closes_at if rules else None,
        "ppt_deadline": None,
    }


async def get_team_counts(db: AsyncSession, team_id: uuid.UUID) -> tuple[int, int]:
    """Return (active_member_count, required_member_count) for a team."""
    team_res = await db.execute(select(Team).where(Team.id == team_id))
    team = team_res.scalar_one_or_none()
    if not team:
        return (0, 1)

    rules_res = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == team.event_id)
    )
    rules = rules_res.scalar_one_or_none()
    required = rules.required_member_count if rules else 1

    members_res = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.status.notin_(_TERMINAL_MEMBER_STATUSES),
        )
    )
    members = members_res.scalars().all()
    active_count = 0
    for m in members:
        if m.role == TeamMemberRole.SUBSTITUTE:
            continue
        if m.status == TeamMemberStatus.ACTIVE or (
            m.role == TeamMemberRole.LEADER and m.status == TeamMemberStatus.PENDING_PAYMENT
        ):
            active_count += 1

    return (active_count, required)


async def get_team_readiness(db: AsyncSession, team: Team) -> bool:
    """Single place for team readiness logic. Delegated to htf_team_service if present."""
    # TODO(Person2): Delegate to app.services.htf_team_service.get_team_readiness if implemented
    try:
        from app.services.htf_team_service import get_team_readiness as person2_readiness
        return await person2_readiness(db, team)
    except (ImportError, AttributeError):
        pass

    if team.status == TeamStatus.CANCELLED:
        return False

    active_count, required = await get_team_counts(db, team.id)
    return active_count >= required


async def _get_caller_active_team(db: AsyncSession, event_id: uuid.UUID, profile: Profile) -> Team:
    """Locate caller's active team for the given event."""
    result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.team))
        .where(
            TeamMember.event_id == event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL_MEMBER_STATUSES),
        )
    )
    member = result.scalar_one_or_none()
    if not member or not member.team or member.team.status == TeamStatus.CANCELLED:
        raise AppError(
            HTF_NOT_A_TEAM_MEMBER,
            "You are not an active member of a team for this event",
            status_code=403,
        )
    return member.team


async def _is_admin_with_permission(db: AsyncSession, profile: Profile, permission: str) -> bool:
    """Check if profile user is an active admin with specified permission."""
    try:
        from app.api.deps_admin import admin_has_permission
        from app.models.admin import AdminUser, Role
        result = await db.execute(
            select(AdminUser)
            .options(selectinload(AdminUser.role).selectinload(Role.permissions))
            .where(AdminUser.user_id == profile.user_id, AdminUser.is_active.is_(True))
        )
        admin = result.scalar_one_or_none()
        if admin:
            return admin_has_permission(admin, permission)
    except Exception:
        pass
    return False


async def create_application(db: AsyncSession, profile: Profile) -> HtfApplication:
    """Create a DRAFT HTF Application for the caller's team."""
    htf_event = await get_htf_event(db)
    deadlines = await get_htf_deadlines(db, htf_event)
    now = datetime.now(timezone.utc)

    if deadlines.get("registration_closes_at") and now > deadlines["registration_closes_at"]:
        raise AppError(
            HTF_APPLICATION_DEADLINE_PASSED,
            "Application deadline has passed",
            status_code=400,
        )
    if deadlines.get("registration_opens_at") and now < deadlines["registration_opens_at"]:
        raise AppError(
            HTF_APPLICATION_NOT_OPEN,
            "Application window has not opened yet",
            status_code=400,
        )

    team = await _get_caller_active_team(db, htf_event.id, profile)

    is_ready = await get_team_readiness(db, team)
    if not is_ready:
        raise AppError(
            HTF_TEAM_INCOMPLETE,
            "Team does not meet the required member count for HTF application",
            status_code=400,
        )

    # Check for existing application
    existing_res = await db.execute(
        select(HtfApplication).where(
            HtfApplication.event_id == htf_event.id,
            HtfApplication.team_id == team.id,
        )
    )
    existing_app = existing_res.scalar_one_or_none()
    if existing_app:
        raise AppError(
            HTF_APPLICATION_ALREADY_EXISTS,
            "An application already exists for your team",
            status_code=409,
        )

    app = HtfApplication(
        event_id=htf_event.id,
        team_id=team.id,
        status=HtfApplicationStatus.DRAFT,
        created_by_profile_id=profile.id,
        application_data={"responses": {}},
    )
    db.add(app)
    try:
        await db.flush()
        await log_activity(
            db,
            action="HTF_APPLICATION_CREATED",
            resource_type="HTF_APPLICATION",
            resource_id=app.id,
            actor_profile_id=profile.id,
            actor_user_id=profile.user_id,
            details={"team_id": str(team.id), "event_id": str(htf_event.id)},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(
            HTF_APPLICATION_ALREADY_EXISTS,
            "An application already exists for your team",
            status_code=409,
        )

    await db.refresh(app)
    return app


async def get_my_application(db: AsyncSession, profile: Profile) -> HtfApplication:
    """Get caller's team HTF application."""
    htf_event = await get_htf_event(db)
    team = await _get_caller_active_team(db, htf_event.id, profile)

    result = await db.execute(
        select(HtfApplication).where(
            HtfApplication.event_id == htf_event.id,
            HtfApplication.team_id == team.id,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise AppError(
            HTF_APPLICATION_NOT_FOUND,
            "No HTF application found for your team",
            status_code=404,
        )
    return app


async def get_application(
    db: AsyncSession, application_id: uuid.UUID, profile: Profile
) -> HtfApplication:
    """
    Get application by ID with strict access control:
    creator OR active team member OR active admin with registration-read permission.
    Otherwise returns 404 without leaking existence.
    """
    result = await db.execute(
        select(HtfApplication).where(HtfApplication.id == application_id)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise AppError(HTF_APPLICATION_NOT_FOUND, "Application not found", status_code=404)

    # Authorized if creator
    if app.created_by_profile_id == profile.id:
        return app

    # Authorized if active team member
    member_res = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == app.team_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL_MEMBER_STATUSES),
        )
    )
    if member_res.scalar_one_or_none():
        return app

    # Authorized if active admin with REGISTRATION_READ
    if await _is_admin_with_permission(db, profile, REGISTRATION_READ):
        return app

    # Otherwise return 404 to avoid leaking existence
    raise AppError(HTF_APPLICATION_NOT_FOUND, "Application not found", status_code=404)


async def update_draft(
    db: AsyncSession,
    application_id: uuid.UUID,
    profile: Profile,
    responses: list[FieldResponseInput],
) -> HtfApplication:
    """Update draft application responses (lenient validation)."""
    app = await get_application(db, application_id, profile)

    if app.status != HtfApplicationStatus.DRAFT:
        raise AppError(
            HTF_APPLICATION_NOT_EDITABLE,
            "Only DRAFT applications can be edited",
            status_code=400,
        )

    htf_event = await get_htf_event(db)
    deadlines = await get_htf_deadlines(db, htf_event)
    now = datetime.now(timezone.utc)
    if deadlines.get("registration_closes_at") and now > deadlines["registration_closes_at"]:
        raise AppError(
            HTF_APPLICATION_DEADLINE_PASSED,
            "Application deadline has passed",
            status_code=400,
        )

    field_pairs = await validate_and_prepare_responses(
        db,
        app.event_id,
        RegistrationFieldScope.REGISTRATION,
        responses,
        profile=profile,
        enforce_required=False,
    )

    current_data = app.application_data or {}
    resp_dict = current_data.get("responses", {})
    for field, normalized_val in field_pairs:
        resp_dict[str(field.id)] = normalized_val

    app.application_data = {"responses": resp_dict}
    await db.commit()
    await db.refresh(app)
    return app


async def submit_application(
    db: AsyncSession, application_id: uuid.UUID, profile: Profile
) -> HtfApplication:
    """
    Submit application: run full validation (enforce_required=True),
    transition DRAFT -> SUBMITTED -> PPT_PENDING in ONE transaction.
    Idempotent: if already SUBMITTED or PPT_PENDING, returns existing application unchanged (200).
    """
    result = await db.execute(
        select(HtfApplication)
        .where(HtfApplication.id == application_id)
        .with_for_update()
    )
    app = result.scalar_one_or_none()
    if not app:
        raise AppError(HTF_APPLICATION_NOT_FOUND, "Application not found", status_code=404)

    # Access check
    if app.created_by_profile_id != profile.id:
        member_res = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == app.team_id,
                TeamMember.profile_id == profile.id,
                TeamMember.status.notin_(_TERMINAL_MEMBER_STATUSES),
            )
        )
        if not member_res.scalar_one_or_none() and not await _is_admin_with_permission(
            db, profile, REGISTRATION_READ
        ):
            raise AppError(HTF_APPLICATION_NOT_FOUND, "Application not found", status_code=404)

    # Idempotency check: if already submitted, return unchanged
    if app.status in (HtfApplicationStatus.SUBMITTED, HtfApplicationStatus.PPT_PENDING):
        return app

    if app.status != HtfApplicationStatus.DRAFT:
        raise AppError(
            HTF_APPLICATION_NOT_EDITABLE,
            f"Cannot submit application in {app.status} status",
            status_code=400,
        )

    htf_event = await get_htf_event(db)
    deadlines = await get_htf_deadlines(db, htf_event)
    now = datetime.now(timezone.utc)
    if deadlines.get("registration_closes_at") and now > deadlines["registration_closes_at"]:
        raise AppError(
            HTF_APPLICATION_DEADLINE_PASSED,
            "Application deadline has passed",
            status_code=400,
        )

    # Re-check team readiness
    team_res = await db.execute(select(Team).where(Team.id == app.team_id))
    team = team_res.scalar_one_or_none()
    if not team or not await get_team_readiness(db, team):
        raise AppError(
            HTF_TEAM_INCOMPLETE,
            "Team does not meet required member count",
            status_code=400,
        )

    # Run full validation on stored application_data responses
    stored_responses = (app.application_data or {}).get("responses", {})
    field_inputs = [
        FieldResponseInput(field_id=uuid.UUID(fid), value=val)
        for fid, val in stored_responses.items()
    ]

    await validate_and_prepare_responses(
        db,
        app.event_id,
        RegistrationFieldScope.REGISTRATION,
        field_inputs,
        profile=profile,
        enforce_required=True,
    )

    # Atomic transition: DRAFT -> SUBMITTED -> PPT_PENDING in ONE transaction
    app.submitted_at = now
    app.status = HtfApplicationStatus.PPT_PENDING

    await log_activity(
        db,
        action="HTF_APPLICATION_SUBMITTED",
        resource_type="HTF_APPLICATION",
        resource_id=app.id,
        actor_profile_id=profile.id,
        actor_user_id=profile.user_id,
        details={"team_id": str(app.team_id), "submitted_at": now.isoformat()},
    )
    await db.commit()
    await db.refresh(app)
    return app


_ALLOWED_TRANSITIONS: dict[HtfApplicationStatus, set[HtfApplicationStatus]] = {
    HtfApplicationStatus.DRAFT: {HtfApplicationStatus.SUBMITTED},
    HtfApplicationStatus.SUBMITTED: {HtfApplicationStatus.PPT_PENDING},
    HtfApplicationStatus.PPT_PENDING: {HtfApplicationStatus.PPT_SUBMITTED},
    HtfApplicationStatus.PPT_SUBMITTED: {HtfApplicationStatus.UNDER_SCREENING},
    HtfApplicationStatus.UNDER_SCREENING: {
        HtfApplicationStatus.SHORTLISTED,
        HtfApplicationStatus.NOT_SHORTLISTED,
    },
    HtfApplicationStatus.SHORTLISTED: {HtfApplicationStatus.PAYMENT_PENDING},
    HtfApplicationStatus.PAYMENT_PENDING: {HtfApplicationStatus.CONFIRMED},
}


async def transition_status(
    db: AsyncSession,
    application: HtfApplication,
    to_status: HtfApplicationStatus,
    *,
    actor_profile_id: Optional[uuid.UUID] = None,
    actor_user_id: Optional[uuid.UUID] = None,
    actor_role: str = "SYSTEM",
) -> HtfApplication:
    """
    State transition engine for HTF Applications.
    Teammates will call this to advance state.
    """
    if application.status == to_status:
        return application

    allowed = _ALLOWED_TRANSITIONS.get(application.status, set())
    if to_status not in allowed:
        raise AppError(
            HTF_APPLICATION_NOT_EDITABLE,
            f"Illegal status transition from {application.status} to {to_status}",
            status_code=400,
        )

    from_status = application.status
    application.status = to_status

    await log_activity(
        db,
        action="HTF_APPLICATION_STATUS_CHANGED",
        resource_type="HTF_APPLICATION",
        resource_id=application.id,
        actor_profile_id=actor_profile_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        details={"from_status": from_status, "to_status": to_status},
    )
    await db.commit()
    await db.refresh(application)
    return application
