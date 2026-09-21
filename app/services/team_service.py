"""
Teams module (§5–§6). Async SQLAlchemy + real auth profile identity.
"""
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminUser
from app.models.enums import (
    EventStatus,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.services import notification_service, qr_service
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamInvitation, TeamMember
from app.schemas.team import TeamUpdateRequest

_TERMINAL = (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)


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


async def _to_member_out(db: AsyncSession, member: TeamMember) -> dict:
    result = await db.execute(select(Profile).where(Profile.id == member.profile_id))
    profile = result.scalar_one_or_none()
    return {
        "id": member.id,
        "team_id": member.team_id,
        "profile_id": member.profile_id,
        "role": member.role,
        "status": member.status,
        "joined_at": member.joined_at,
        "full_name": profile.full_name if profile else None,
    }


async def _to_detail(db: AsyncSession, team: Team, rules: EventRegistrationRule) -> dict:
    result = await db.execute(select(TeamMember).where(TeamMember.team_id == team.id))
    members = list(result.scalars().all())
    return {
        "id": team.id,
        "event_id": team.event_id,
        "name": team.name,
        "leader_profile_id": team.leader_profile_id,
        "status": team.status,
        "created_at": team.created_at,
        "active_member_count": await _active_member_count(db, team.id),
        "team_max_size": rules.team_max_size,
        "members": [await _to_member_out(db, m) for m in members],
    }


async def create_team(db: AsyncSession, event_id: uuid.UUID, profile: Profile, name: str) -> dict:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if event.status != EventStatus.OPEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration is not open for this event")

    rules = await _get_rules_or_404(db, event_id)
    if rules.team_max_size <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event does not support team registration")

    now = datetime.now(timezone.utc)
    if rules.registration_opens_at and now < rules.registration_opens_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration has not opened yet")
    if rules.registration_closes_at and now > rules.registration_closes_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration has closed")

    existing = await db.execute(
        select(TeamMember).where(
            TeamMember.event_id == event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_(_TERMINAL),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a registration for this event")

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
    result = await db.execute(select(TeamMember).where(TeamMember.team_id == team.id))
    return [await _to_member_out(db, m) for m in result.scalars().all()]


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

    return {
        "team_id": team.id,
        "team_name": team.name,
        "event_id": team.event_id,
        "event_name": event.name if event else "",
        "leader_name": leader.full_name if leader else "",
        "active_member_count": active_count,
        "team_max_size": rules.team_max_size,
        "is_full": active_count >= rules.team_max_size,
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
        return {
            "team": team,
            "member": await _to_member_out(db, existing_member),
        }

    active_count = await _active_member_count(db, team.id)
    if active_count >= rules.team_max_size:
        raise HTTPException(status.HTTP_409_CONFLICT, "Team is full")

    member = TeamMember(
        team_id=team.id,
        event_id=team.event_id,
        profile_id=profile.id,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
    )
    db.add(member)
    await db.flush()

    await qr_service.generate_for_team_member(db, member.id)

    team_became_complete = False
    new_active_count = await _active_member_count(db, team.id)
    if new_active_count >= rules.team_max_size and team.status != TeamStatus.CANCELLED:
        team.status = TeamStatus.COMPLETE
        team_became_complete = True

    leader_profile_id = team.leader_profile_id
    event_id = team.event_id
    member_id = member.id
    member_profile_id = profile.id
    team_id = team.id

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not join team - please retry")

    await db.refresh(member)
    await db.refresh(team)

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

    return {
        "team": team,
        "member": await _to_member_out(db, member),
    }


async def leave_team(
    db: AsyncSession, team_id: uuid.UUID, member_id: uuid.UUID, profile: Profile
) -> dict:
    team = await _get_team_or_404(db, team_id)
    result = await db.execute(
        select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team.id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")
    if member.profile_id != profile.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only remove yourself this way")
    if member.status in _TERMINAL:
        return await _to_member_out(db, member)
    member.status = TeamMemberStatus.LEFT
    await db.commit()
    await db.refresh(member)
    return await _to_member_out(db, member)


async def remove_member(
    db: AsyncSession, team_id: uuid.UUID, member_id: uuid.UUID, profile: Profile
) -> dict:
    team = await _get_team_or_404(db, team_id)
    await _require_leader_or_admin(db, team, profile)

    result = await db.execute(
        select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team.id)
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
        return await _to_member_out(db, member)
    member.status = TeamMemberStatus.REMOVED
    await db.commit()
    await db.refresh(member)
    return await _to_member_out(db, member)
