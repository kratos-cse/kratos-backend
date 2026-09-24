"""
Teams module (§5–§6). Async SQLAlchemy + real auth profile identity.
Roster: mandatory members + optional substitutes (backend-enforced).
"""
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.integrity_errors import raise_duplicate_registration, raise_duplicate_team_member
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    ALREADY_REGISTERED,
    LEADER_ENTRY_NOT_ALLOWED,
    ROSTER_INVALID,
    TEAM_FULL,
    TEAM_MANDATORY_FULL,
    TEAM_SUBSTITUTE_LIMIT,
    AppError,
)
from app.models.admin import AdminUser
from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    MemberRegistrationMode,
    RegistrationStatus,
    TeamMemberEntrySource,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.services import notification_service, qr_service
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamInvitation, TeamMember
from app.schemas.team import RosterAddRequest, TeamUpdateRequest
from app.services.event_service import invalidate_spots_cache, spots_remaining
from app.services.roster_service import (
    can_add_role,
    count_mandatory,
    count_substitutes,
    mandatory_met,
    next_join_role,
    roster_limits,
)

_TERMINAL = (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)
_ACTIVE = (TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT)


async def _is_active_admin(db: AsyncSession, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    result = await db.execute(
        select(AdminUser).where(AdminUser.user_id == user_id, AdminUser.is_active.is_(True))
    )
    return result.scalar_one_or_none() is not None


async def _get_team_or_404(db: AsyncSession, team_id: uuid.UUID) -> Team:
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    return team


async def _get_rules_or_404(db: AsyncSession, event_id: uuid.UUID) -> EventRegistrationRule:
    result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == event_id)
    )
    rules = result.scalar_one_or_none()
    if not rules:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event registration rules not configured")
    return rules


async def _active_member_count(db: AsyncSession, team_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            TeamMember.status.in_([TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT]),
        )
    )
    return int(result.scalar() or 0)


async def _require_membership(
    db: AsyncSession,
    team: Team,
    profile: Profile,
    *,
    allow_admin: bool = True,
) -> TeamMember | None:
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL),
        )
    )
    member = result.scalar_one_or_none()
    if member:
        return member
    if allow_admin and await _is_active_admin(db, profile.user_id):
        return None
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")


async def _require_leader_or_admin(db: AsyncSession, team: Team, profile: Profile) -> None:
    if team.leader_profile_id == profile.id:
        return
    if await _is_active_admin(db, profile.user_id):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the team leader can do this")


async def _to_member_out(member: TeamMember) -> dict:
    profile = member.profile if "profile" in member.__dict__ else None
    linked_name = profile.full_name if profile else None
    return {
        "id": member.id,
        "team_id": member.team_id,
        "profile_id": member.profile_id,
        "role": member.role,
        "status": member.status,
        "joined_at": member.joined_at,
        "entry_source": getattr(member, "entry_source", TeamMemberEntrySource.LINKED_ACCOUNT),
        "full_name": linked_name or getattr(member, "full_name", None),
        "phone": getattr(member, "phone", None) or (profile.phone if profile else None),
        "contact_email": getattr(member, "contact_email", None)
        or (profile.contact_email if profile else None),
        "college_name": getattr(member, "college_name", None)
        or (profile.college_name if profile else None),
        "year_of_study": getattr(member, "year_of_study", None)
        or (profile.year_of_study if profile else None),
    }


async def _load_members_with_profiles(db: AsyncSession, team_id: uuid.UUID) -> list[TeamMember]:
    result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(TeamMember.team_id == team_id)
    )
    return list(result.scalars().all())


def _active_members(members: list[TeamMember]) -> list[TeamMember]:
    return [m for m in members if m.status in _ACTIVE]


async def _to_detail(db: AsyncSession, team: Team, rules: EventRegistrationRule) -> dict:
    members = await _load_members_with_profiles(db, team.id)
    active = _active_members(members)
    required, max_subs, total = roster_limits(rules)
    return {
        "id": team.id,
        "event_id": team.event_id,
        "name": team.name,
        "leader_profile_id": team.leader_profile_id,
        "status": team.status,
        "created_at": team.created_at,
        "active_member_count": len(active),
        "team_max_size": total,
        "required_member_count": required,
        "substitute_count": max_subs,
        "mandatory_filled": count_mandatory(active),
        "substitutes_filled": count_substitutes(active),
        "members": [await _to_member_out(m) for m in members],
    }


async def create_team(db: AsyncSession, event_id: uuid.UUID, profile: Profile, name: str) -> dict:
    result = await db.execute(select(Event).where(Event.id == event_id).with_for_update())
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if event.visibility != EventVisibility.PUBLISHED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event is not available")
    if event.registration_status != EventRegistrationStatus.OPEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration is not open for this event")

    rules = await _get_rules_or_404(db, event_id)
    _, _, total = roster_limits(rules)
    if total <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event does not support team registration")

    remaining = await spots_remaining(db, event, rules)
    if remaining is not None and remaining <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event has reached capacity")

    existing = await db.execute(
        select(TeamMember).where(
            TeamMember.event_id == event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL),
        )
    )
    if existing.scalar_one_or_none():
        raise AppError(ALREADY_REGISTERED, "You already have a registration for this event", status_code=409)

    team = Team(event_id=event_id, name=name, leader_profile_id=profile.id, status=TeamStatus.FORMING)
    db.add(team)
    await db.flush()

    db.add(
        TeamMember(
            team_id=team.id,
            event_id=event_id,
            profile_id=profile.id,
            role=TeamMemberRole.LEADER,
            status=TeamMemberStatus.PENDING_PAYMENT,
            entry_source=TeamMemberEntrySource.LINKED_ACCOUNT,
        )
    )
    # Team registrations use team_id XOR profile_id — required so
    # POST /payments/create-order (TEAM_REGISTRATION) can attach payment_id.
    db.add(
        Registration(
            event_id=event_id,
            team_id=team.id,
            status=RegistrationStatus.PENDING,
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise_duplicate_registration(exc, "Could not create team - already registered")
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not create team - please retry")

    await db.refresh(team)
    return await _to_detail(db, team, rules)


async def get_team(db: AsyncSession, team_id: uuid.UUID, profile: Profile) -> dict:
    team = await _get_team_or_404(db, team_id)
    await _require_membership(db, team, profile)
    rules = await _get_rules_or_404(db, team.event_id)
    return await _to_detail(db, team, rules)


async def list_team_members(db: AsyncSession, team_id: uuid.UUID, profile: Profile) -> list[dict]:
    team = await _get_team_or_404(db, team_id)
    await _require_membership(db, team, profile)
    members = await _load_members_with_profiles(db, team.id)
    return [await _to_member_out(m) for m in members]


async def update_team(
    db: AsyncSession, team_id: uuid.UUID, profile: Profile, patch: TeamUpdateRequest
) -> dict:
    team = await _get_team_or_404(db, team_id)
    await _require_leader_or_admin(db, team, profile)
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot edit a cancelled team")
    if patch.name is not None:
        team.name = patch.name
    await db.commit()
    await db.refresh(team)
    rules = await _get_rules_or_404(db, team.event_id)
    return await _to_detail(db, team, rules)


async def create_invitation(db: AsyncSession, team_id: uuid.UUID, profile: Profile) -> TeamInvitation:
    team = await _get_team_or_404(db, team_id)
    await _require_leader_or_admin(db, team, profile)

    existing = await db.execute(
        select(TeamInvitation).where(
            TeamInvitation.team_id == team.id, TeamInvitation.is_active.is_(True)
        )
    )
    invitation = existing.scalar_one_or_none()
    if invitation:
        return invitation

    invitation = TeamInvitation(
        team_id=team.id,
        code=secrets.token_urlsafe(12),
        is_active=True,
        created_by_profile_id=profile.id,
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    return invitation


async def get_invitation_public(db: AsyncSession, invite_code: str) -> dict:
    result = await db.execute(select(TeamInvitation).where(TeamInvitation.code == invite_code))
    invitation = result.scalar_one_or_none()
    if not invitation or not invitation.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or no longer active")

    team = await _get_team_or_404(db, invitation.team_id)
    event_result = await db.execute(select(Event).where(Event.id == team.event_id))
    event = event_result.scalar_one_or_none()
    rules = await _get_rules_or_404(db, team.event_id)
    leader_result = await db.execute(select(Profile).where(Profile.id == team.leader_profile_id))
    leader = leader_result.scalar_one_or_none()
    active_count = await _active_member_count(db, team.id)
    required, max_subs, total = roster_limits(rules)

    return {
        "team_id": team.id,
        "team_name": team.name,
        "event_id": team.event_id,
        "event_name": event.name if event else "",
        "leader_name": leader.full_name if leader else "",
        "active_member_count": active_count,
        "team_max_size": total,
        "required_member_count": required,
        "substitute_count": max_subs,
        "is_full": active_count >= total,
        "is_active": invitation.is_active,
    }


async def join_via_invitation(db: AsyncSession, invite_code: str, profile: Profile) -> dict:
    result = await db.execute(select(TeamInvitation).where(TeamInvitation.code == invite_code))
    invitation = result.scalar_one_or_none()
    if not invitation or not invitation.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or no longer active")

    team_result = await db.execute(
        select(Team).where(Team.id == invitation.team_id).with_for_update()
    )
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This team has been cancelled")
    # Members join after leader payment (docs §24).
    if team.status not in (TeamStatus.PAID, TeamStatus.COMPLETE):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Team must be paid before members can join",
        )

    rules = await _get_rules_or_404(db, team.event_id)
    _, _, total = roster_limits(rules)

    existing = await db.execute(
        select(TeamMember).where(
            TeamMember.event_id == team.event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL),
        )
    )
    existing_member = existing.scalar_one_or_none()
    if existing_member:
        if existing_member.team_id != team.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You already belong to a different team for this event",
            )
        reg_result = await db.execute(select(Registration.id).where(Registration.team_id == team.id))
        registration_id = reg_result.scalar_one_or_none()
        await db.refresh(existing_member, attribute_names=["profile"])
        return {
            "team": team,
            "member": await _to_member_out(existing_member),
            "registration_id": registration_id,
        }

    members = await _load_members_with_profiles(db, team.id)
    active = _active_members(members)
    if len(active) >= total:
        raise AppError(TEAM_FULL, "Team is full", status_code=409)

    try:
        role = next_join_role(active, rules)
    except ValueError:
        raise AppError(TEAM_FULL, "Team roster is full", status_code=409)

    member = TeamMember(
        team_id=team.id,
        event_id=team.event_id,
        profile_id=profile.id,
        role=role,
        status=TeamMemberStatus.ACTIVE,
        entry_source=TeamMemberEntrySource.LINKED_ACCOUNT,
    )
    db.add(member)
    await db.flush()

    # Re-check under the same FOR UPDATE lock after insert (concurrency safety).
    members = await _load_members_with_profiles(db, team.id)
    active = _active_members(members)
    if len(active) > total:
        await db.rollback()
        raise AppError(TEAM_FULL, "Team is full", status_code=409)

    await qr_service.generate_for_team_member(db, member.id)

    team_became_complete = False
    if mandatory_met(active, rules) and team.status == TeamStatus.PAID:
        team.status = TeamStatus.COMPLETE
        team_became_complete = True

    leader_profile_id = team.leader_profile_id
    event_id = team.event_id
    member_id = member.id
    member_profile_id = profile.id
    team_id = team.id

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise_duplicate_team_member(exc, "Could not join team - already a member")
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not join team - please retry")

    invalidate_spots_cache(event_id)

    await db.refresh(member)
    await db.refresh(team)
    await db.refresh(member, attribute_names=["profile"])

    # Notifications after commit (failures must not fail join).
    try:
        await notification_service.notify_member_confirmation(db, member_id)
        await notification_service.notify_member_joined(
            db,
            leader_profile_id=leader_profile_id,
            team_id=team_id,
            member_profile_id=member_profile_id,
            event_id=event_id,
        )
        if team_became_complete:
            await notification_service.notify_team_completed(db, leader_profile_id, team_id)
    except Exception:
        pass

    reg_result = await db.execute(select(Registration.id).where(Registration.team_id == team.id))
    registration_id = reg_result.scalar_one_or_none()
    return {
        "team": team,
        "member": await _to_member_out(member),
        "registration_id": registration_id,
    }


async def add_roster_member(
    db: AsyncSession,
    team_id: uuid.UUID,
    profile: Profile,
    payload: RosterAddRequest,
) -> dict:
    """Leader adds a mandatory member or substitute (linked account and/or details)."""
    team_result = await db.execute(select(Team).where(Team.id == team_id).with_for_update())
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    await _require_leader_or_admin(db, team, profile)
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot edit a cancelled team")

    rules = await _get_rules_or_404(db, team.event_id)
    role = payload.role

    members = await _load_members_with_profiles(db, team.id)
    active = _active_members(members)
    required, max_subs, total = roster_limits(rules)

    if len(active) >= total:
        raise AppError(TEAM_FULL, "Team roster is full", status_code=409)

    if not can_add_role(active, rules, role):
        if role == TeamMemberRole.SUBSTITUTE:
            raise AppError(TEAM_SUBSTITUTE_LIMIT, "No substitute slots remaining", status_code=409)
        raise AppError(TEAM_MANDATORY_FULL, "Mandatory roster is already full", status_code=409)

    entry_source = TeamMemberEntrySource.LEADER_ENTERED

    if payload.profile_id:
        entry_source = TeamMemberEntrySource.LINKED_ACCOUNT
        pref = await db.execute(select(Profile).where(Profile.id == payload.profile_id))
        if not pref.scalar_one_or_none():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
        existing = await db.execute(
            select(TeamMember).where(
                TeamMember.event_id == team.event_id,
                TeamMember.profile_id == payload.profile_id,
                TeamMember.status.notin_(_TERMINAL),
            )
        )
        if existing.scalar_one_or_none():
            raise AppError(ALREADY_REGISTERED, "That participant is already on a team for this event", status_code=409)
    else:
        if rules.member_registration_mode != MemberRegistrationMode.LEADER_MANAGED:
            raise AppError(
                LEADER_ENTRY_NOT_ALLOWED,
                "This event requires members to register themselves",
                status_code=400,
            )
        if not payload.full_name or not payload.phone:
            raise AppError(ROSTER_INVALID, "full_name and phone are required for leader-entered members", status_code=400)

    member = TeamMember(
        team_id=team.id,
        event_id=team.event_id,
        profile_id=payload.profile_id,
        role=role,
        status=TeamMemberStatus.ACTIVE,
        entry_source=entry_source,
        full_name=payload.full_name if entry_source == TeamMemberEntrySource.LEADER_ENTERED else None,
        phone=payload.phone if entry_source == TeamMemberEntrySource.LEADER_ENTERED else None,
        contact_email=payload.contact_email if entry_source == TeamMemberEntrySource.LEADER_ENTERED else None,
        college_name=payload.college_name if entry_source == TeamMemberEntrySource.LEADER_ENTERED else None,
        year_of_study=payload.year_of_study if entry_source == TeamMemberEntrySource.LEADER_ENTERED else None,
    )
    db.add(member)
    await db.flush()

    members = await _load_members_with_profiles(db, team.id)
    active = _active_members(members)
    over_total = len(active) > total
    over_subs = role == TeamMemberRole.SUBSTITUTE and count_substitutes(active) > max_subs
    over_mandatory = role in (TeamMemberRole.MEMBER, TeamMemberRole.LEADER) and count_mandatory(active) > required
    if over_total or over_subs or over_mandatory:
        await db.rollback()
        if role == TeamMemberRole.SUBSTITUTE:
            raise AppError(TEAM_SUBSTITUTE_LIMIT, "No substitute slots remaining", status_code=409)
        raise AppError(TEAM_MANDATORY_FULL, "Mandatory roster is already full", status_code=409)

    if member.profile_id:
        await qr_service.generate_for_team_member(db, member.id)

    team_became_complete = False
    if mandatory_met(active, rules) and team.status == TeamStatus.PAID:
        team.status = TeamStatus.COMPLETE
        team_became_complete = True

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise_duplicate_team_member(exc, "Could not add member - already registered")
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not add roster member - please retry")

    invalidate_spots_cache(team.event_id)

    await db.refresh(member)
    if member.profile_id:
        await db.refresh(member, attribute_names=["profile"])

    if team_became_complete:
        try:
            await notification_service.notify_team_completed(db, team.leader_profile_id, team.id)
        except Exception:
            pass

    if team.status in (TeamStatus.PAID, TeamStatus.COMPLETE) and member.profile_id:
        try:
            await notification_service.notify_member_confirmation(db, member.id)
        except Exception:
            pass

    return await _to_detail(db, team, rules)


async def leave_team(
    db: AsyncSession, team_id: uuid.UUID, member_id: uuid.UUID, profile: Profile
) -> dict:
    team = await _get_team_or_404(db, team_id)
    result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(TeamMember.id == member_id, TeamMember.team_id == team.id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")
    if member.profile_id != profile.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only remove yourself this way")
    if member.status in _TERMINAL:
        return await _to_member_out(member)
    member.status = TeamMemberStatus.LEFT
    await db.commit()
    invalidate_spots_cache(team.event_id)
    await db.refresh(member)
    return await _to_member_out(member)


async def remove_member(
    db: AsyncSession, team_id: uuid.UUID, member_id: uuid.UUID, profile: Profile
) -> dict:
    team = await _get_team_or_404(db, team_id)
    await _require_leader_or_admin(db, team, profile)

    result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(TeamMember.id == member_id, TeamMember.team_id == team.id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")
    if member.role == TeamMemberRole.LEADER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot remove the team leader - use admin leadership transfer instead",
        )
    if member.status in _TERMINAL:
        return await _to_member_out(member)
    member.status = TeamMemberStatus.REMOVED
    await db.commit()
    invalidate_spots_cache(team.event_id)
    await db.refresh(member)
    return await _to_member_out(member)


async def link_member_profile(
    db: AsyncSession,
    team_member_id: uuid.UUID,
    profile: Profile,
) -> TeamMember:
    """
    Attach a registered profile to an existing roster seat (e.g. leader-entered member
    who later created an account). Preserves TeamMember.id and any existing QR history.
    """
    result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(TeamMember.id == team_member_id)
        .with_for_update()
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team member not found")
    if member.status in _TERMINAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot link a removed team member")

    if member.profile_id is not None:
        if member.profile_id == profile.id:
            return member
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This roster seat is already linked to another profile",
        )

    conflict = await db.execute(
        select(TeamMember).where(
            TeamMember.event_id == member.event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL),
            TeamMember.id != member.id,
        )
    )
    if conflict.scalar_one_or_none():
        raise AppError(ALREADY_REGISTERED, "That profile is already on a team for this event", status_code=409)

    member.profile_id = profile.id
    member.entry_source = TeamMemberEntrySource.LINKED_ACCOUNT
    await db.flush()

    team_result = await db.execute(select(Team).where(Team.id == member.team_id))
    team = team_result.scalar_one_or_none()
    if team and team.status in (TeamStatus.PAID, TeamStatus.COMPLETE):
        await qr_service.generate_for_team_member(db, member.id)

    await db.commit()
    await db.refresh(member)
    await db.refresh(member, attribute_names=["profile"])
    return member
