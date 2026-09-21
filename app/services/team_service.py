"""
Business logic for the Teams module (§5 Teams, §6 Team Member
Management of the API reference).

Deliberately does NOT create a REGISTRATIONS row anywhere in here.
Whoever owns POST /events/{event_id}/registrations should call
create_team() and create the REGISTRATIONS row on their side - this
was flagged as an open decision and needs to be settled with them.

Deliberately does NOT call any notification/email code directly.
Where a notification should fire (member joined, team completed), a
TODO marks the spot - wire it to the real notification service's
function once that module exists, don't call SMTP/requests directly
from here.
"""
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import FeeChargeModel, TeamMemberRole, TeamMemberStatus, TeamStatus
from app.models.temp_external_subs import Event, EventRegistrationRules, Profile  # TODO: swap for real models
from app.models.team import Team, TeamInvitation, TeamMember
from app.schemas.team import TeamCreateRequest, TeamUpdateRequest
from app.services import notification_service


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------

def _get_team_or_404(db: Session, team_id: uuid.UUID) -> Team:
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    return team


def _get_rules_or_404(db: Session, event_id: uuid.UUID) -> EventRegistrationRules:
    rules = db.query(EventRegistrationRules).filter(EventRegistrationRules.event_id == event_id).first()
    if not rules:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event registration rules not configured")
    return rules


def _active_member_count(db: Session, team_id: uuid.UUID) -> int:
    return (
        db.query(func.count(TeamMember.id))
        .filter(
            TeamMember.team_id == team_id,
            TeamMember.status.in_([TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT]),
        )
        .scalar()
    ) or 0


def _require_membership(db: Session, team: Team, profile_id: uuid.UUID) -> TeamMember:
    """404s (not 403) if the caller isn't on this team, so team
    existence isn't leaked to outsiders. Applies uniformly across the
    module - see roadmap note on 403 vs 404 convention."""
    member = (
        db.query(TeamMember)
        .filter(
            TeamMember.team_id == team.id,
            TeamMember.profile_id == profile_id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
        .first()
    )
    if not member:
        # TODO: also allow if caller is an authorized admin, once RBAC
        # deps exist - e.g. `if not is_admin(profile): raise 404`.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    return member


def _require_leader(team: Team, profile_id: uuid.UUID) -> None:
    # TODO: also allow authorized admin once RBAC deps exist.
    if team.leader_profile_id != profile_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the team leader can do this")


def _to_member_out_dict(db: Session, member: TeamMember) -> dict:
    profile = db.query(Profile).filter(Profile.id == member.profile_id).first()
    return {
        "id": member.id,
        "team_id": member.team_id,
        "profile_id": member.profile_id,
        "role": member.role,
        "status": member.status,
        "joined_at": member.joined_at,
        "full_name": profile.full_name if profile else None,
    }


def _to_detail_dict(db: Session, team: Team, rules: EventRegistrationRules) -> dict:
    members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()
    return {
        "id": team.id,
        "event_id": team.event_id,
        "name": team.name,
        "leader_profile_id": team.leader_profile_id,
        "status": team.status,
        "created_at": team.created_at,
        "active_member_count": _active_member_count(db, team.id),
        "team_max_size": rules.team_max_size,
        "members": [_to_member_out_dict(db, m) for m in members],
    }


# ---------------------------------------------------------------------
# POST /events/{event_id}/teams
# ---------------------------------------------------------------------

def create_team(db: Session, event_id: uuid.UUID, profile: Profile, name: str) -> dict:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if event.status != "OPEN":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration is not open for this event")

    rules = _get_rules_or_404(db, event_id)
    if rules.team_max_size <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event does not support team registration")

    now = datetime.now(timezone.utc)
    if rules.registration_opens_at and now < rules.registration_opens_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration has not opened yet")
    if rules.registration_closes_at and now > rules.registration_closes_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Registration has closed")

    # A participant cannot belong to multiple teams in the same event.
    existing = (
        db.query(TeamMember)
        .filter(
            TeamMember.event_id == event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a registration for this event")

    team = Team(event_id=event_id, name=name, leader_profile_id=profile.id, status=TeamStatus.FORMING)
    db.add(team)
    db.flush()  # get team.id without committing yet

    leader_member = TeamMember(
        team_id=team.id,
        event_id=event_id,
        profile_id=profile.id,
        role=TeamMemberRole.LEADER,
        status=TeamMemberStatus.PENDING_PAYMENT,
    )
    db.add(leader_member)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not create team - please retry")

    db.refresh(team)
    return _to_detail_dict(db, team, rules)


# ---------------------------------------------------------------------
# GET /teams/{team_id}
# ---------------------------------------------------------------------

def get_team(db: Session, team_id: uuid.UUID, profile: Profile) -> dict:
    team = _get_team_or_404(db, team_id)
    _require_membership(db, team, profile.id)
    rules = _get_rules_or_404(db, team.event_id)
    return _to_detail_dict(db, team, rules)


# ---------------------------------------------------------------------
# GET /teams/{team_id}/members
# ---------------------------------------------------------------------

def list_team_members(db: Session, team_id: uuid.UUID, profile: Profile) -> list[dict]:
    team = _get_team_or_404(db, team_id)
    _require_membership(db, team, profile.id)
    members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()
    return [_to_member_out_dict(db, m) for m in members]


# ---------------------------------------------------------------------
# PATCH /teams/{team_id}
# ---------------------------------------------------------------------

def update_team(db: Session, team_id: uuid.UUID, profile: Profile, patch: TeamUpdateRequest) -> dict:
    team = _get_team_or_404(db, team_id)
    _require_leader(team, profile.id)
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot edit a cancelled team")

    if patch.name is not None:
        team.name = patch.name

    db.commit()
    db.refresh(team)
    rules = _get_rules_or_404(db, team.event_id)
    return _to_detail_dict(db, team, rules)


# ---------------------------------------------------------------------
# POST /teams/{team_id}/invitations
# ---------------------------------------------------------------------

def create_invitation(db: Session, team_id: uuid.UUID, profile: Profile) -> TeamInvitation:
    team = _get_team_or_404(db, team_id)
    _require_leader(team, profile.id)

    # Reusable/idempotent: if an active invitation already exists,
    # return it rather than minting a new one. Confirm this product
    # choice with your team - the alternative is always-rotate.
    existing = (
        db.query(TeamInvitation)
        .filter(TeamInvitation.team_id == team.id, TeamInvitation.is_active.is_(True))
        .first()
    )
    if existing:
        return existing

    invitation = TeamInvitation(
        team_id=team.id,
        code=secrets.token_urlsafe(12),
        is_active=True,
        created_by_profile_id=profile.id,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return invitation


# ---------------------------------------------------------------------
# GET /team-invitations/{invite_code}   (public)
# ---------------------------------------------------------------------

def get_invitation_public(db: Session, invite_code: str) -> dict:
    invitation = db.query(TeamInvitation).filter(TeamInvitation.code == invite_code).first()
    if not invitation or not invitation.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or no longer active")

    team = _get_team_or_404(db, invitation.team_id)
    event = db.query(Event).filter(Event.id == team.event_id).first()
    rules = _get_rules_or_404(db, team.event_id)
    leader = db.query(Profile).filter(Profile.id == team.leader_profile_id).first()
    active_count = _active_member_count(db, team.id)

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


# ---------------------------------------------------------------------
# POST /team-invitations/{invite_code}/join
# ---------------------------------------------------------------------

def join_via_invitation(db: Session, invite_code: str, profile: Profile) -> dict:
    invitation = db.query(TeamInvitation).filter(TeamInvitation.code == invite_code).first()
    if not invitation or not invitation.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or no longer active")

    # Lock the team row so concurrent joins against the last open slot
    # serialize instead of racing. Requires this to run inside a
    # transaction (the default for a request-scoped Session).
    team = (
        db.query(Team)
        .filter(Team.id == invitation.team_id)
        .with_for_update()
        .first()
    )   
    if not team:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    if team.status == TeamStatus.CANCELLED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This team has been cancelled")

    rules = _get_rules_or_404(db, team.event_id)

    # Idempotency: if this profile already has a non-terminal
    # membership row for this event, return it instead of erroring -
    # covers retries/double-clicks.
    existing = (
        db.query(TeamMember)
        .filter(
            TeamMember.event_id == team.event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
        )
        .first()
    )
    if existing:
        if existing.team_id != team.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You already belong to a different team for this event",
            )
        return {
            "team": team,
            "member": _to_member_out_dict(db, existing),
            "requires_payment": existing.status == TeamMemberStatus.PENDING_PAYMENT,
        }

    active_count = _active_member_count(db, team.id)
    if active_count >= rules.team_max_size:
        raise HTTPException(status.HTTP_409_CONFLICT, "Team is full")

    is_per_member = rules.fee_charge_model == FeeChargeModel.PER_MEMBER
    member = TeamMember(
        team_id=team.id,
        event_id=team.event_id,
        profile_id=profile.id,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.PENDING_PAYMENT if is_per_member else TeamMemberStatus.ACTIVE,
    )
    db.add(member)
    db.flush()

    # Team completion detection - only meaningful once this member
    # counts as filling a slot (PER_TEAM members count immediately;
    # PER_MEMBER members already counted above via PENDING_PAYMENT).
    new_active_count = _active_member_count(db, team.id)
    if new_active_count >= rules.team_max_size and team.status != TeamStatus.CANCELLED:
        team.status = TeamStatus.COMPLETE
        # TODO: notification_service.send_team_completed(team) - notify leader.

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not join team - please retry")

    db.refresh(member)
    db.refresh(team)


    new_active_count = _active_member_count(db, team.id)
    just_completed = new_active_count >= rules.team_max_size and team.status != TeamStatus.CANCELLED
    if just_completed:
        team.status = TeamStatus.COMPLETE

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not join team - please retry")

    db.refresh(member)
    db.refresh(team)

    # Fire-and-forget style: emails never roll back the transaction
    # above (it already committed), and notification_service itself
    # swallows send failures rather than raising - see its _send().
    event = db.query(Event).filter(Event.id == team.event_id).first()
    leader = db.query(Profile).filter(Profile.id == team.leader_profile_id).first()
    event_name = event.name if event else ""

    notification_service.send_member_confirmation(
        to_email=profile.email if hasattr(profile, "email") else "",
        member_name=getattr(profile, "full_name", "Team Member"),
        team_name=team.name,
        event_name=event_name,
    )
    if leader:
        notification_service.notify_leader_member_joined(
            to_email=leader.email,
            leader_name=leader.full_name,
            member_name=getattr(profile, "full_name", "A new member"),
            team_name=team.name,
        )
        if just_completed:
            notification_service.send_team_completed(
                to_email=leader.email,
                leader_name=leader.full_name,
                team_name=team.name,
            )


    return {
        "team": team,
        "member": _to_member_out_dict(db, member),
        "requires_payment": is_per_member,
    }


# ---------------------------------------------------------------------
# POST /teams/{team_id}/members/{member_id}/leave
# ---------------------------------------------------------------------

def leave_team(db: Session, team_id: uuid.UUID, member_id: uuid.UUID, profile: Profile) -> dict:
    team = _get_team_or_404(db, team_id)
    member = db.query(TeamMember).filter(TeamMember.id == member_id, TeamMember.team_id == team.id).first()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")

    # Only the member themself may leave via this endpoint.
    if member.profile_id != profile.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only remove yourself this way")

    if member.status in (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED):
        return _to_member_out_dict(db, member)  # already terminal - idempotent no-op

    member.status = TeamMemberStatus.LEFT
    db.commit()
    db.refresh(member)
    return _to_member_out_dict(db, member)


# ---------------------------------------------------------------------
# POST /teams/{team_id}/members/{member_id}/remove
# ---------------------------------------------------------------------

def remove_member(db: Session, team_id: uuid.UUID, member_id: uuid.UUID, profile: Profile) -> dict:
    team = _get_team_or_404(db, team_id)
    _require_leader(team, profile.id)

    member = db.query(TeamMember).filter(TeamMember.id == member_id, TeamMember.team_id == team.id).first()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")

    if member.role == TeamMemberRole.LEADER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot remove the team leader - use admin leadership transfer instead",
        )

    if member.status in (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED):
        return _to_member_out_dict(db, member)  # already terminal - idempotent no-op

    member.status = TeamMemberStatus.REMOVED
    db.commit()
    db.refresh(member)
    return _to_member_out_dict(db, member)