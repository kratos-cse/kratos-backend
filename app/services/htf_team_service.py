"""
HTF 2026 – Team service (Member 2 domain).

Responsibilities
----------------
1. ensure_htf_team_meta      – Creates an HTFTeamMeta row whenever a team is
                               created under the HTF event.  Called by the
                               create_team hook (htf_teams.py endpoint).

2. get_team_readiness        – Returns the full readiness report for the
                               calling participant's team.  Used by:
                               • Member 1's dashboard (next_action engine)
                               • The /htf/teams/me/readiness endpoint

3. select_problem_statement  – Leader picks / changes a PS (allowed only while
                               application_status == DRAFT).

4. list_problem_statements   – Returns active PS list for an event.

5. get_htf_pass              – Returns the participant pass + QR token for the
                               authenticated member once the team is CONFIRMED.

6. join_htf_team_via_invite  – Wraps the existing invite join with HTF-specific
                               guards (roster lock, one-active-team rule, capacity).

7. leave_htf_team /
   remove_htf_member         – Wrappers that enforce roster-lock before delegating
                               to the generic team_service functions.

Isolation contract
------------------
This module imports from `team_service` for shared logic (invite resolution,
member loading, QR generation triggers).  It NEVER mutates `teams.status`
directly; that column is owned by the payment flow (TL / Member 3).
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    AppError,
    HTF_ALREADY_IN_TEAM,
    HTF_APPLICATION_NOT_FOUND,
    HTF_INVITE_INVALID,
    HTF_LEADER_ONLY,
    HTF_PASS_NOT_AVAILABLE,
    HTF_PS_ALREADY_SELECTED,
    HTF_PS_NOT_FOUND,
    HTF_ROSTER_LOCKED,
    HTF_TEAM_FULL,
    HTF_TEAM_NOT_READY,
)
from app.models.enums import (
    HTFApplicationStatus,
    HTFPassStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event
from app.models.htf_team import HTFProblemStatement, HTFTeamMeta
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.team import Team, TeamInvitation, TeamMember
from app.schemas.htf_team import (
    HTFMemberReadiness,
    HTFPassMemberOut,
    HTFPassOut,
    HTFProblemStatementOut,
    HTFTeamReadinessOut,
)
from app.services import qr_service, team_service

# Statuses where the roster is effectively frozen
_LOCKED_STATUSES = {
    HTFApplicationStatus.SUBMITTED,
    HTFApplicationStatus.SHORTLISTED,
    HTFApplicationStatus.NOT_SHORTLISTED,
    HTFApplicationStatus.CONFIRMED,
}

_TERMINAL_MEMBER = (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)

# Mandatory profile fields that must be non-null/non-empty
_REQUIRED_PROFILE_FIELDS: list[tuple[str, str]] = [
    ("full_name", "Full Name"),
    ("phone", "Phone Number"),
    ("college_name", "College / Institution"),
    ("department", "Department / Branch"),
    ("year_of_study", "Year of Study"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_meta_for_team(
    db: AsyncSession, team_id: uuid.UUID
) -> Optional[HTFTeamMeta]:
    result = await db.execute(
        select(HTFTeamMeta).where(HTFTeamMeta.team_id == team_id)
    )
    return result.scalar_one_or_none()


async def _get_meta_or_404(db: AsyncSession, team_id: uuid.UUID) -> HTFTeamMeta:
    meta = await _get_meta_for_team(db, team_id)
    if not meta:
        raise AppError(
            HTF_APPLICATION_NOT_FOUND,
            "HTF application metadata not found for this team",
            status_code=404,
        )
    return meta


async def _get_my_active_team(
    db: AsyncSession, profile_id: uuid.UUID, event_id: uuid.UUID
) -> Optional[Team]:
    """Return the team (if any) where this profile is an active non-terminal member."""
    result = await db.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            Team.event_id == event_id,
            TeamMember.profile_id == profile_id,
            TeamMember.status.notin_(_TERMINAL_MEMBER),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


def _check_profile_complete(profile: Profile) -> list[str]:
    """Return list of missing mandatory profile field names."""
    missing = []
    for field_attr, label in _REQUIRED_PROFILE_FIELDS:
        val = getattr(profile, field_attr, None)
        if not val or (isinstance(val, str) and not val.strip()):
            missing.append(label)
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# 1. ensure_htf_team_meta  (called right after team creation)
# ─────────────────────────────────────────────────────────────────────────────

async def ensure_htf_team_meta(
    db: AsyncSession, *, team_id: uuid.UUID, event_id: uuid.UUID
) -> HTFTeamMeta:
    """
    Idempotently create an HTFTeamMeta row for a newly created team.
    Must be called inside the same transaction as team creation and flushed
    before commit so the team_id FK is satisfied.
    """
    existing = await _get_meta_for_team(db, team_id)
    if existing:
        return existing

    meta = HTFTeamMeta(
        team_id=team_id,
        event_id=event_id,
        application_status=HTFApplicationStatus.DRAFT,
        roster_locked=False,
    )
    db.add(meta)
    await db.flush()
    return meta


# ─────────────────────────────────────────────────────────────────────────────
# 2. list_problem_statements
# ─────────────────────────────────────────────────────────────────────────────

async def list_problem_statements(
    db: AsyncSession, event_id: uuid.UUID
) -> list[HTFProblemStatementOut]:
    """Return all active Problem Statements for the given HTF event."""
    result = await db.execute(
        select(HTFProblemStatement)
        .where(
            HTFProblemStatement.event_id == event_id,
            HTFProblemStatement.is_active.is_(True),
        )
        .order_by(HTFProblemStatement.code)
    )
    ps_list = list(result.scalars().all())

    # Count how many teams have claimed each PS
    claim_counts_result = await db.execute(
        select(HTFTeamMeta.ps_id, func.count(HTFTeamMeta.id))
        .where(
            HTFTeamMeta.event_id == event_id,
            HTFTeamMeta.ps_id.isnot(None),
            HTFTeamMeta.application_status.notin_(
                [HTFApplicationStatus.WITHDRAWN]
            ),
        )
        .group_by(HTFTeamMeta.ps_id)
    )
    claim_map: dict[uuid.UUID, int] = {
        row[0]: row[1] for row in claim_counts_result.all()
    }

    return [
        HTFProblemStatementOut(
            id=ps.id,
            event_id=ps.event_id,
            code=ps.code,
            title=ps.title,
            description=ps.description,
            domain=ps.domain,
            is_active=ps.is_active,
            unique_claim=ps.unique_claim,
            claim_count=claim_map.get(ps.id, 0),
        )
        for ps in ps_list
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 3. select_problem_statement
# ─────────────────────────────────────────────────────────────────────────────

async def select_problem_statement(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    ps_id: uuid.UUID,
    profile: Profile,
) -> HTFTeamReadinessOut:
    """
    Team leader selects (or changes) the Problem Statement.

    Rules:
    - Only allowed when application_status == DRAFT (roster not locked).
    - Only the team leader can call this.
    - If the PS has unique_claim=True, we acquire a row lock on the PS row and
      verify no OTHER non-withdrawn team has already claimed it.
    """
    # Load team and verify caller is leader
    team_result = await db.execute(
        select(Team).where(Team.id == team_id).with_for_update()
    )
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")

    if team.leader_profile_id != profile.id:
        raise AppError(HTF_LEADER_ONLY, "Only the team leader can select a Problem Statement", status_code=403)

    meta = await _get_meta_or_404(db, team_id)

    if meta.roster_locked:
        raise AppError(
            HTF_ROSTER_LOCKED,
            "Problem Statement cannot be changed once the application is submitted",
            status_code=409,
        )

    # Fetch and lock the PS row
    ps_result = await db.execute(
        select(HTFProblemStatement)
        .where(
            HTFProblemStatement.id == ps_id,
            HTFProblemStatement.is_active.is_(True),
        )
        .with_for_update()
    )
    ps = ps_result.scalar_one_or_none()
    if not ps:
        raise AppError(HTF_PS_NOT_FOUND, "Problem Statement not found or inactive", status_code=404)

    # If the PS requires a unique claim, check if another (non-withdrawn) team has it
    if ps.unique_claim:
        conflict_result = await db.execute(
            select(HTFTeamMeta).where(
                HTFTeamMeta.ps_id == ps_id,
                HTFTeamMeta.team_id != team_id,
                HTFTeamMeta.application_status.notin_(
                    [HTFApplicationStatus.WITHDRAWN]
                ),
            )
        )
        if conflict_result.scalar_one_or_none():
            raise AppError(
                HTF_PS_ALREADY_SELECTED,
                f"Problem Statement '{ps.code}' has already been claimed by another team",
                status_code=409,
            )

    meta.ps_id = ps_id
    await db.commit()
    await db.refresh(meta)

    return await get_team_readiness(db, profile=profile, event_id=team.event_id)


# ─────────────────────────────────────────────────────────────────────────────
# 4. get_team_readiness
# ─────────────────────────────────────────────────────────────────────────────

async def get_team_readiness(
    db: AsyncSession,
    *,
    profile: Profile,
    event_id: uuid.UUID,
) -> HTFTeamReadinessOut:
    """
    Compute and return the full readiness report for the calling participant's
    team on the given HTF event.

    Returns a structured report so Member 1's dashboard can derive next_action
    purely from the `is_ready` flag + `blockers` list without re-querying.
    """
    # Find this participant's team
    team = await _get_my_active_team(db, profile.id, event_id)
    if not team:
        raise AppError(
            HTF_APPLICATION_NOT_FOUND,
            "You are not part of any team for this HTF event",
            status_code=404,
        )

    meta = await _get_meta_or_404(db, team.id)

    # Load event for size limits
    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event or not event.rules:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event rules not configured")

    rules = event.rules
    min_size = rules.team_min_size
    max_size = rules.team_max_size

    # Load active members with their profiles
    members_result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(
            TeamMember.team_id == team.id,
            TeamMember.status.notin_(_TERMINAL_MEMBER),
        )
    )
    active_members = list(members_result.scalars().all())
    member_count = len(active_members)

    # Build per-member readiness
    member_readiness_list: list[HTFMemberReadiness] = []
    all_complete = True
    for tm in active_members:
        p = tm.profile
        if p is None:
            # Linked account whose profile row doesn't exist yet (edge case)
            missing = [f for _, f in _REQUIRED_PROFILE_FIELDS]
            all_complete = False
        else:
            missing = _check_profile_complete(p)
            if missing:
                all_complete = False

        member_readiness_list.append(
            HTFMemberReadiness(
                profile_id=tm.profile_id or uuid.uuid4(),  # fallback UUID for display
                full_name=p.full_name if p else "(no profile)",
                role=tm.role,
                status=tm.status,
                is_profile_complete=(len(missing) == 0),
                missing_fields=missing,
            )
        )

    # PS info
    ps: Optional[HTFProblemStatement] = None
    if meta.ps_id:
        ps_result = await db.execute(
            select(HTFProblemStatement).where(HTFProblemStatement.id == meta.ps_id)
        )
        ps = ps_result.scalar_one_or_none()

    # Determine blockers
    blockers: list[str] = []
    capacity_ok = min_size <= member_count <= max_size

    if not capacity_ok:
        if member_count < min_size:
            blockers.append(
                f"Team needs at least {min_size} members (currently {member_count})"
            )
        else:
            blockers.append(
                f"Team exceeds max allowed size of {max_size} members"
            )

    if not all_complete:
        incomplete_names = [
            m.full_name
            for m in member_readiness_list
            if not m.is_profile_complete
        ]
        blockers.append(
            f"Incomplete profiles: {', '.join(incomplete_names)}"
        )

    if not meta.ps_id:
        blockers.append("No Problem Statement selected")

    if meta.application_status == HTFApplicationStatus.WITHDRAWN:
        blockers.append("Team has been withdrawn")

    is_ready = (
        capacity_ok
        and all_complete
        and meta.ps_id is not None
        and meta.application_status not in (
            HTFApplicationStatus.WITHDRAWN,
        )
    )

    return HTFTeamReadinessOut(
        team_id=team.id,
        team_name=team.name,
        event_id=event_id,
        application_status=meta.application_status,
        roster_locked=meta.roster_locked,
        active_member_count=member_count,
        min_team_size=min_size,
        max_team_size=max_size,
        capacity_ok=capacity_ok,
        members=member_readiness_list,
        all_profiles_complete=all_complete,
        ps_selected=(meta.ps_id is not None),
        ps_code=ps.code if ps else None,
        ps_title=ps.title if ps else None,
        ps_domain=ps.domain if ps else None,
        is_ready=is_ready,
        blockers=blockers,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_htf_pass
# ─────────────────────────────────────────────────────────────────────────────

async def get_htf_pass(
    db: AsyncSession,
    *,
    profile: Profile,
    event_id: uuid.UUID,
) -> HTFPassOut:
    """
    Return the participant's digital pass for the HTF event.

    Conditions to return an ACTIVE pass:
    - Caller must be an active member of an HTF team.
    - Team application_status must be CONFIRMED.
    - Caller must have a QR code generated (linked to their TeamMember row).

    If conditions are not met, the pass_status is NOT_AVAILABLE and most
    fields are still returned so the frontend can show context (e.g., "you
    are shortlisted — please pay to activate your pass").
    """
    team = await _get_my_active_team(db, profile.id, event_id)
    if not team:
        raise AppError(
            HTF_PASS_NOT_AVAILABLE,
            "You are not part of any team for this HTF event",
            status_code=404,
        )

    meta = await _get_meta_or_404(db, team.id)

    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    # Load active members + profiles
    members_result = await db.execute(
        select(TeamMember)
        .options(selectinload(TeamMember.profile))
        .where(
            TeamMember.team_id == team.id,
            TeamMember.status.notin_(_TERMINAL_MEMBER),
        )
    )
    active_members = list(members_result.scalars().all())

    # Find the calling participant's own TeamMember row
    my_member: Optional[TeamMember] = next(
        (m for m in active_members if m.profile_id == profile.id), None
    )
    if not my_member:
        raise AppError(
            HTF_PASS_NOT_AVAILABLE,
            "Your membership was not found for this event",
            status_code=403,
        )

    # Determine pass status
    if meta.application_status == HTFApplicationStatus.CONFIRMED:
        pass_status = HTFPassStatus.ACTIVE
    else:
        pass_status = HTFPassStatus.NOT_AVAILABLE

    # Fetch the caller's QR token (only when pass is ACTIVE)
    my_qr_token: Optional[str] = None
    if pass_status == HTFPassStatus.ACTIVE:
        qr_result = await db.execute(
            select(QRCode).where(
                QRCode.team_member_id == my_member.id,
                QRCode.is_active.is_(True),
            )
        )
        qr = qr_result.scalar_one_or_none()
        my_qr_token = qr.token if qr else None

    # Build PS info
    ps: Optional[HTFProblemStatement] = None
    if meta.ps_id:
        ps_result = await db.execute(
            select(HTFProblemStatement).where(HTFProblemStatement.id == meta.ps_id)
        )
        ps = ps_result.scalar_one_or_none()

    # Build member list (no QR tokens for teammates)
    member_out_list: list[HTFPassMemberOut] = []
    for tm in active_members:
        p = tm.profile
        member_out_list.append(
            HTFPassMemberOut(
                profile_id=tm.profile_id or uuid.UUID(int=0),
                full_name=p.full_name if p else "(unknown)",
                role=tm.role,
                college_name=p.college_name if p else None,
                department=p.department if p else None,
                year_of_study=p.year_of_study if p else None,
                contact_email=p.contact_email if p else None,
                # Only inject QR token for the requesting participant
                qr_token=my_qr_token if tm.profile_id == profile.id else None,
                team_member_id=tm.id,
            )
        )

    return HTFPassOut(
        pass_status=pass_status,
        my_profile_id=profile.id,
        my_full_name=profile.full_name,
        my_role=my_member.role,
        my_college=profile.college_name,
        my_department=profile.department,
        my_year=profile.year_of_study,
        my_qr_token=my_qr_token,
        team_id=team.id,
        team_name=team.name,
        ps_code=ps.code if ps else None,
        ps_title=ps.title if ps else None,
        ps_domain=ps.domain if ps else None,
        members=member_out_list,
        event_id=event.id,
        event_name=event.name,
        event_venue=event.venue,
        event_starts_at=event.starts_at,
        event_ends_at=event.ends_at,
        application_status=meta.application_status,
        payment_deadline=meta.payment_deadline,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. join_htf_team_via_invite  (HTF-specific guards)
# ─────────────────────────────────────────────────────────────────────────────

async def join_htf_team_via_invite(
    db: AsyncSession,
    *,
    invite_code: str,
    profile: Profile,
    event_id: uuid.UUID,
) -> dict:
    """
    HTF-specific join wrapper around the generic invite flow.

    Additional guards applied BEFORE delegating to team_service:
    1. Roster lock check — if the team's application is past DRAFT, deny.
    2. One-active-team rule — user must not already be in another HTF team.
    3. Team capacity — re-verified under FOR UPDATE lock to prevent races.

    After joining, if the participant profile is complete, a QR code is
    pre-generated (it becomes active only after payment confirmation by TL).
    """
    # Resolve invite code → team
    invite_result = await db.execute(
        select(TeamInvitation)
        .options(selectinload(TeamInvitation.team))
        .where(TeamInvitation.code == invite_code)
    )
    invitation = invite_result.scalar_one_or_none()
    if not invitation or not invitation.is_active:
        raise AppError(HTF_INVITE_INVALID, "This invite link is invalid or has been revoked", status_code=404)

    team = invitation.team
    if team is None or team.event_id != event_id:
        raise AppError(HTF_INVITE_INVALID, "This invite is not for the HTF event", status_code=400)

    # Check roster lock
    meta = await _get_meta_for_team(db, team.id)
    if meta and meta.roster_locked:
        raise AppError(
            HTF_ROSTER_LOCKED,
            "This team's application has been submitted — no new members can join",
            status_code=409,
        )

    # One-active-team rule (HTF-specific)
    existing_team = await _get_my_active_team(db, profile.id, event_id)
    if existing_team:
        if existing_team.id == team.id:
            raise AppError(
                HTF_ALREADY_IN_TEAM,
                "You are already a member of this team",
                status_code=409,
            )
        raise AppError(
            HTF_ALREADY_IN_TEAM,
            "You already belong to another HTF team. Leave that team first.",
            status_code=409,
        )

    # Capacity check under lock
    team_locked_result = await db.execute(
        select(Team).where(Team.id == team.id).with_for_update()
    )
    team_locked = team_locked_result.scalar_one_or_none()
    if not team_locked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")

    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event or not event.rules:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event rules not configured")

    rules = event.rules
    active_count_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team.id,
            TeamMember.status.notin_(_TERMINAL_MEMBER),
        )
    )
    active_count = int(active_count_result.scalar() or 0)
    if active_count >= rules.team_max_size:
        raise AppError(HTF_TEAM_FULL, "This team is already at full capacity", status_code=409)

    # Delegate to the generic join — this handles field responses, QR, notifications
    return await team_service.join_via_invitation(
        db,
        invite_code=invite_code,
        profile=profile,
        field_responses=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. leave_htf_team / remove_htf_member  (with roster-lock guard)
# ─────────────────────────────────────────────────────────────────────────────

async def leave_htf_team(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    profile: Profile,
) -> dict:
    """Leave an HTF team — blocked once the application is submitted."""
    meta = await _get_meta_for_team(db, team_id)
    if meta and meta.roster_locked:
        raise AppError(
            HTF_ROSTER_LOCKED,
            "You cannot leave once the application has been submitted",
            status_code=409,
        )
    return await team_service.leave_team(db, team_id=team_id, member_id=member_id, profile=profile)


async def remove_htf_member(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    profile: Profile,
) -> dict:
    """Leader removes a member — blocked once the application is submitted."""
    meta = await _get_meta_for_team(db, team_id)
    if meta and meta.roster_locked:
        raise AppError(
            HTF_ROSTER_LOCKED,
            "You cannot remove members once the application has been submitted",
            status_code=409,
        )
    return await team_service.remove_member(db, team_id=team_id, member_id=member_id, profile=profile)


# ─────────────────────────────────────────────────────────────────────────────
# 8. create_htf_team  (thin wrapper — adds meta row creation)
# ─────────────────────────────────────────────────────────────────────────────

async def create_htf_team(
    db: AsyncSession,
    *,
    event_id: uuid.UUID,
    profile: Profile,
    name: str,
) -> dict:
    """
    Create an HTF team.

    Difference from generic create_team:
    - Leader's TeamMember.status is set to ACTIVE immediately (no payment gate).
    - HTFTeamMeta row is created in the same transaction.
    - The generic Registration row is still created (needed by payment flow later).

    Two registration entry modes for HTF:
    MODE A (Leader fills all member details): The leader creates the team and
            adds teammates via the roster add endpoint. This mode is allowed
            because HTF requires each person to self-register (LINKED_ACCOUNT).
            So the leader can only add people who already have KRATOS accounts.
            Essentially they share the invite code and teammates join themselves.

    MODE B (Invite code): Leader sends invite link; each teammate joins autonomously.

    Both modes converge at join_htf_team_via_invite / team_service.add_roster_member.
    The roster_add flow is restricted in htf_teams.py endpoint to LINKED_ACCOUNT only.
    """
    # Verify the caller is not already in an HTF team for this event
    existing = await _get_my_active_team(db, profile.id, event_id)
    if existing:
        raise AppError(
            HTF_ALREADY_IN_TEAM,
            "You are already part of an HTF team for this event. Leave that team first.",
            status_code=409,
        )

    # Delegate team row creation to generic service
    team_detail = await team_service.create_team(db, event_id=event_id, profile=profile, name=name)

    # After commit inside create_team we need to fetch the team row again
    team_result = await db.execute(
        select(Team).where(Team.id == team_detail["id"])
    )
    team = team_result.scalar_one()

    # Immediately activate the leader's TeamMember (no payment barrier for HTF)
    leader_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.role == TeamMemberRole.LEADER,
        )
    )
    leader_member = leader_result.scalar_one_or_none()
    if leader_member and leader_member.status == TeamMemberStatus.PENDING_PAYMENT:
        leader_member.status = TeamMemberStatus.ACTIVE

    # Create HTFTeamMeta
    meta = HTFTeamMeta(
        team_id=team.id,
        event_id=event_id,
        application_status=HTFApplicationStatus.DRAFT,
        roster_locked=False,
    )
    db.add(meta)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await db.refresh(team)

    # Refresh and return updated team detail
    from app.services.team_service import _get_rules_or_404, _to_detail  # local import to avoid circular
    rules = await _get_rules_or_404(db, event_id)
    return await _to_detail(db, team, rules)
