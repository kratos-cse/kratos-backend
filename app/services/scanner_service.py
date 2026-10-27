"""Public scanner responses: minimal roster and scoped member check-in."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance import AttendanceCheckpoint, AttendanceScan
from app.models.enums import AttendanceScanResult, EventVisibility, TeamMemberRole, TeamMemberStatus
from app.models.event import Event
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.team import Team, TeamMember
from app.services import attendance_service
from app.services.scanner_capability import decode_team_capability, issue_team_capability

_TERMINAL = (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)


def member_matches_scope(
    *,
    member_team_id: uuid.UUID | None,
    member_event_id: uuid.UUID | None,
    member_status: TeamMemberStatus | None,
    scope_team_id: uuid.UUID,
    scope_event_id: uuid.UUID,
) -> bool:
    """A roster checkbox may only target a non-terminal member of the scanned team and event."""
    if member_team_id is None or member_event_id is None or member_status is None:
        return False
    if member_status in _TERMINAL:
        return False
    return member_team_id == scope_team_id and member_event_id == scope_event_id


def public_scan_message(result: AttendanceScanResult, note: Optional[str]) -> str:
    if result == AttendanceScanResult.SUCCESS:
        return "Checked in."
    if result == AttendanceScanResult.DUPLICATE:
        return "Already checked in at this checkpoint."
    if result == AttendanceScanResult.NOT_PAID:
        return "This participant is not eligible to check in."
    marker = (note or "").strip()
    if marker == "inactive_qr":
        return "This QR code is inactive."
    if marker == "wrong_event":
        return "This QR code is for a different event."
    if marker == "checkpoint_inactive":
        return "This checkpoint is not accepting scans."
    return "Invalid QR code."


def _role_label(role: TeamMemberRole) -> str:
    if role == TeamMemberRole.LEADER:
        return "Leader"
    return "Member"


def _member_name(member: TeamMember) -> str:
    if member.profile is not None and member.profile.full_name:
        return member.profile.full_name
    if member.full_name:
        return member.full_name
    return "Team member"


async def _checked_in_member_ids(
    db: AsyncSession,
    checkpoint_id: uuid.UUID,
    members: list[TeamMember],
) -> set[uuid.UUID]:
    if not members:
        return set()
    member_ids = [m.id for m in members]
    profile_ids = [m.profile_id for m in members if m.profile_id is not None]
    filters = [AttendanceScan.team_member_id.in_(member_ids)]
    if profile_ids:
        filters.append(AttendanceScan.profile_id.in_(profile_ids))
    rows = (
        await db.execute(
            select(AttendanceScan.team_member_id, AttendanceScan.profile_id).where(
                AttendanceScan.checkpoint_id == checkpoint_id,
                AttendanceScan.result == AttendanceScanResult.SUCCESS,
                or_(*filters),
            )
        )
    ).all()
    present_members = {row.team_member_id for row in rows if row.team_member_id is not None}
    present_profiles = {row.profile_id for row in rows if row.profile_id is not None}
    checked: set[uuid.UUID] = set()
    for member in members:
        if member.id in present_members or (member.profile_id and member.profile_id in present_profiles):
            checked.add(member.id)
    return checked


async def _team_roster_payload(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
    event_name: str,
    checkpoint_name: str,
) -> dict:
    team = (
        await db.execute(select(Team).where(Team.id == team_id))
    ).scalar_one_or_none()
    members = list(
        (
            await db.execute(
                select(TeamMember)
                .options(selectinload(TeamMember.profile))
                .where(
                    TeamMember.team_id == team_id,
                    TeamMember.status.notin_(_TERMINAL),
                )
            )
        ).scalars().all()
    )
    members.sort(key=lambda m: (0 if m.role == TeamMemberRole.LEADER else 1, _member_name(m).lower()))
    checked = await _checked_in_member_ids(db, checkpoint_id, members)
    college = ""
    for member in members:
        if member.role == TeamMemberRole.LEADER:
            if member.profile is not None and member.profile.college_name:
                college = member.profile.college_name
            elif member.college_name:
                college = member.college_name
            break
    if not college:
        for member in members:
            if member.college_name:
                college = member.college_name
                break
            if member.profile is not None and member.profile.college_name:
                college = member.profile.college_name
                break
    roster = [
        {
            "team_member_id": member.id,
            "name": _member_name(member),
            "role": _role_label(member.role),
            "checked_in": member.id in checked,
        }
        for member in members
    ]
    return {
        "team_name": team.name if team else "",
        "college_name": college,
        "event_name": event_name,
        "checkpoint_name": checkpoint_name,
        "checked_in_count": sum(1 for row in roster if row["checked_in"]),
        "member_count": len(roster),
        "roster": roster,
    }


async def _participant_name(db: AsyncSession, profile_id: Optional[uuid.UUID], team_member_id: Optional[uuid.UUID]) -> Optional[str]:
    if profile_id is not None:
        profile = (await db.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
        if profile and profile.full_name:
            return profile.full_name
    if team_member_id is not None:
        member = (
            await db.execute(
                select(TeamMember)
                .options(selectinload(TeamMember.profile))
                .where(TeamMember.id == team_member_id)
            )
        ).scalar_one_or_none()
        if member:
            return _member_name(member)
    return None


async def public_scan(db: AsyncSession, token: str, checkpoint_id: uuid.UUID) -> dict:
    outcome = await attendance_service.scan(db, token, checkpoint_id, admin_user_id=None)
    checkpoint = await attendance_service.get_checkpoint_or_404(db, checkpoint_id)
    event_name = await _event_name_for_scan(db, checkpoint, outcome)
    note = outcome.scan.note if outcome.scan is not None else None
    result = outcome.result
    payload: dict = {
        "result": result.value,
        "recorded": result == AttendanceScanResult.SUCCESS,
        "duplicate": result == AttendanceScanResult.DUPLICATE,
        "message": public_scan_message(result, note),
        "event_name": event_name,
        "checkpoint_name": checkpoint.name,
        "participant_name": None,
        "team": None,
        "capability": None,
    }

    if outcome.scan is None or result not in (AttendanceScanResult.SUCCESS, AttendanceScanResult.DUPLICATE):
        return payload

    payload["participant_name"] = await _participant_name(
        db, outcome.scan.profile_id, outcome.scan.team_member_id
    )
    if outcome.scan.team_member_id is None:
        return payload

    member = (
        await db.execute(select(TeamMember).where(TeamMember.id == outcome.scan.team_member_id))
    ).scalar_one_or_none()
    if member is None or member.status in _TERMINAL:
        return payload

    team_block = await _team_roster_payload(
        db,
        team_id=member.team_id,
        checkpoint_id=checkpoint.id,
        event_name=event_name,
        checkpoint_name=checkpoint.name,
    )
    payload["team"] = team_block
    payload["capability"] = issue_team_capability(
        team_id=member.team_id,
        event_id=member.event_id,
        checkpoint_id=checkpoint.id,
    )
    return payload


async def public_member_checkin(db: AsyncSession, capability: str, team_member_id: uuid.UUID) -> dict:
    scope = decode_team_capability(capability)
    member = (
        await db.execute(
            select(TeamMember)
            .options(selectinload(TeamMember.profile))
            .where(TeamMember.id == team_member_id)
        )
    ).scalar_one_or_none()
    if member is None or not member_matches_scope(
        member_team_id=member.team_id,
        member_event_id=member.event_id,
        member_status=member.status,
        scope_team_id=scope.team_id,
        scope_event_id=scope.event_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This member is not on the team from the scanned QR.",
        )

    qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.team_member_id == member.id,
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if qr is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This member does not have an active QR code.",
        )

    outcome = await attendance_service.scan(
        db,
        qr.token,
        scope.checkpoint_id,
        admin_user_id=None,
        note="scanner_roster",
    )
    note = outcome.scan.note if outcome.scan is not None else None
    checked = outcome.result in (AttendanceScanResult.SUCCESS, AttendanceScanResult.DUPLICATE)
    return {
        "team_member_id": member.id,
        "result": outcome.result.value,
        "recorded": outcome.result == AttendanceScanResult.SUCCESS,
        "duplicate": outcome.result == AttendanceScanResult.DUPLICATE,
        "checked_in": checked,
        "message": public_scan_message(outcome.result, note),
    }


async def list_scanner_events(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(Event.id, Event.name)
            .where(Event.visibility == EventVisibility.PUBLISHED)
            .order_by(Event.name)
            .limit(200)
        )
    ).all()
    return [{"id": row.id, "name": row.name} for row in rows]


async def _event_name_for_scan(db: AsyncSession, checkpoint, outcome) -> str:
    """Common checkpoints have no event. The scanned QR supplies the event name."""
    event_id = checkpoint.event_id
    if event_id is None and outcome.scan is not None:
        qr = (await db.execute(select(QRCode).where(QRCode.id == outcome.scan.qr_id))).scalar_one_or_none()
        if qr is not None and qr.registration_id is not None:
            from app.models.registration import Registration

            event_id = (
                await db.execute(select(Registration.event_id).where(Registration.id == qr.registration_id))
            ).scalar_one_or_none()
        elif qr is not None and qr.team_member_id is not None:
            event_id = (
                await db.execute(select(TeamMember.event_id).where(TeamMember.id == qr.team_member_id))
            ).scalar_one_or_none()
    if event_id is None:
        return ""
    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    return event.name if event else ""


async def list_scanner_checkpoints(db: AsyncSession, event_id: uuid.UUID | None = None) -> list[dict]:
    """Active common checkpoints (no event). These apply to every event's QR."""
    if event_id is not None:
        event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
        if event is None or event.visibility != EventVisibility.PUBLISHED:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")
    rows = (
        await db.execute(
            select(AttendanceCheckpoint)
            .where(
                AttendanceCheckpoint.is_active.is_(True),
                AttendanceCheckpoint.event_id.is_(None),
            )
            .order_by(AttendanceCheckpoint.name)
        )
    ).scalars().all()
    return [
        {
            "id": checkpoint.id,
            "name": checkpoint.name,
            "location": checkpoint.location,
            "event_id": None,
            "event_name": None,
        }
        for checkpoint in rows
    ]
