"""Attendance scanning and checkpoint management."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import AttendanceCheckpoint, AttendanceCorrection, AttendanceScan
from app.models.enums import (
    AttendanceScanResult,
    DuplicateScanBehavior,
    RegistrationStatus,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.services import qr_service


@dataclass
class ScanOutcome:
    scan: Optional[AttendanceScan]
    result: AttendanceScanResult


@dataclass
class _QrParticipant:
    profile_id: Optional[UUID]
    event_id: UUID
    team_id: Optional[UUID]
    team_member_id: Optional[UUID]


async def get_checkpoint_or_404(db: AsyncSession, checkpoint_id: UUID) -> AttendanceCheckpoint:
    result = await db.execute(select(AttendanceCheckpoint).where(AttendanceCheckpoint.id == checkpoint_id))
    checkpoint = result.scalar_one_or_none()
    if not checkpoint:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkpoint not found")
    return checkpoint


async def _resolve_qr_context(db: AsyncSession, qr: QRCode) -> Optional[_QrParticipant]:
    """Resolve participant identity from a QR — TeamMember.id for team seats."""
    if qr.registration_id:
        reg_res = await db.execute(select(Registration).where(Registration.id == qr.registration_id))
        reg = reg_res.scalar_one_or_none()
        if not reg or not reg.profile_id:
            return None
        return _QrParticipant(
            profile_id=reg.profile_id,
            event_id=reg.event_id,
            team_id=reg.team_id,
            team_member_id=None,
        )

    if qr.team_member_id:
        tm_res = await db.execute(select(TeamMember).where(TeamMember.id == qr.team_member_id))
        tm = tm_res.scalar_one_or_none()
        if not tm:
            return None
        return _QrParticipant(
            profile_id=tm.profile_id,
            event_id=tm.event_id,
            team_id=tm.team_id,
            team_member_id=tm.id,
        )

    return None


async def _is_paid_eligible(db: AsyncSession, qr: QRCode, participant: _QrParticipant) -> bool:
    if qr.registration_id:
        reg_res = await db.execute(select(Registration).where(Registration.id == qr.registration_id))
        reg = reg_res.scalar_one_or_none()
        return reg is not None and reg.status == RegistrationStatus.CONFIRMED

    if qr.team_member_id and participant.team_member_id:
        tm_res = await db.execute(select(TeamMember).where(TeamMember.id == participant.team_member_id))
        tm = tm_res.scalar_one_or_none()
        if not tm or tm.status != TeamMemberStatus.ACTIVE:
            return False
        team_res = await db.execute(select(Team).where(Team.id == tm.team_id))
        team = team_res.scalar_one_or_none()
        return (
            team is not None
            and team.event_id == participant.event_id
            and team.status in (TeamStatus.PAID, TeamStatus.COMPLETE)
        )

    return False


async def _prior_success_scan(
    db: AsyncSession,
    checkpoint_id: UUID,
    *,
    profile_id: Optional[UUID],
    team_member_id: Optional[UUID],
) -> Optional[AttendanceScan]:
    if team_member_id is not None:
        result = await db.execute(
            select(AttendanceScan)
            .join(QRCode, AttendanceScan.qr_id == QRCode.id)
            .where(
                AttendanceScan.checkpoint_id == checkpoint_id,
                QRCode.team_member_id == team_member_id,
                AttendanceScan.result == AttendanceScanResult.SUCCESS,
            )
            .order_by(AttendanceScan.created_at.desc())
            .limit(1)
        )
        prior = result.scalar_one_or_none()
        if prior is not None:
            return prior

    if profile_id is None:
        return None

    result = await db.execute(
        select(AttendanceScan)
        .where(
            AttendanceScan.checkpoint_id == checkpoint_id,
            AttendanceScan.profile_id == profile_id,
            AttendanceScan.result == AttendanceScanResult.SUCCESS,
        )
        .order_by(AttendanceScan.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _identity_filters(model, *, profile_id: Optional[UUID], team_member_id: Optional[UUID]):
    parts = []
    if team_member_id is not None:
        parts.append(model.team_member_id == team_member_id)
    if profile_id is not None:
        parts.append(model.profile_id == profile_id)
    return parts


async def latest_success_at(
    db: AsyncSession,
    checkpoint_id: UUID,
    *,
    profile_id: Optional[UUID],
    team_member_id: Optional[UUID],
):
    parts = _identity_filters(AttendanceScan, profile_id=profile_id, team_member_id=team_member_id)
    if not parts:
        return None
    result = await db.execute(
        select(func.max(AttendanceScan.created_at)).where(
            AttendanceScan.checkpoint_id == checkpoint_id,
            AttendanceScan.result == AttendanceScanResult.SUCCESS,
            or_(*parts),
        )
    )
    return result.scalar_one_or_none()


async def latest_absence_at(
    db: AsyncSession,
    checkpoint_id: UUID,
    *,
    profile_id: Optional[UUID],
    team_member_id: Optional[UUID],
):
    parts = _identity_filters(AttendanceCorrection, profile_id=profile_id, team_member_id=team_member_id)
    if not parts:
        return None
    result = await db.execute(
        select(func.max(AttendanceCorrection.created_at)).where(
            AttendanceCorrection.checkpoint_id == checkpoint_id,
            AttendanceCorrection.present.is_(False),
            or_(*parts),
        )
    )
    return result.scalar_one_or_none()


async def is_effectively_present(
    db: AsyncSession,
    checkpoint_id: UUID,
    *,
    profile_id: Optional[UUID],
    team_member_id: Optional[UUID],
) -> bool:
    """Present only when the newest successful check-in is newer than any absence correction."""
    success_at = await latest_success_at(
        db, checkpoint_id, profile_id=profile_id, team_member_id=team_member_id
    )
    absence_at = await latest_absence_at(
        db, checkpoint_id, profile_id=profile_id, team_member_id=team_member_id
    )
    return effectively_present(success_at, absence_at)


def effectively_present(success_at, absence_at) -> bool:
    if success_at is None:
        return False
    if absence_at is None:
        return True
    return success_at > absence_at


async def present_member_ids(db: AsyncSession, checkpoint_id: UUID, members: list) -> set[UUID]:
    """Team members whose latest successful check-in is newer than any absence correction."""
    if not members:
        return set()
    member_ids = [m.id for m in members]
    profile_ids = [m.profile_id for m in members if m.profile_id is not None]
    success_filters = [AttendanceScan.team_member_id.in_(member_ids)]
    absence_filters = [AttendanceCorrection.team_member_id.in_(member_ids)]
    if profile_ids:
        success_filters.append(AttendanceScan.profile_id.in_(profile_ids))
        absence_filters.append(AttendanceCorrection.profile_id.in_(profile_ids))
    success_rows = (
        await db.execute(
            select(AttendanceScan.team_member_id, AttendanceScan.profile_id, AttendanceScan.created_at).where(
                AttendanceScan.checkpoint_id == checkpoint_id,
                AttendanceScan.result == AttendanceScanResult.SUCCESS,
                or_(*success_filters),
            )
        )
    ).all()
    absence_rows = (
        await db.execute(
            select(
                AttendanceCorrection.team_member_id,
                AttendanceCorrection.profile_id,
                AttendanceCorrection.created_at,
            ).where(
                AttendanceCorrection.checkpoint_id == checkpoint_id,
                AttendanceCorrection.present.is_(False),
                or_(*absence_filters),
            )
        )
    ).all()

    def _latest(rows):
        by_member: dict[UUID, object] = {}
        by_profile: dict[UUID, object] = {}
        for row in rows:
            if row.team_member_id is not None:
                prev = by_member.get(row.team_member_id)
                if prev is None or row.created_at > prev:
                    by_member[row.team_member_id] = row.created_at
            if row.profile_id is not None:
                prev = by_profile.get(row.profile_id)
                if prev is None or row.created_at > prev:
                    by_profile[row.profile_id] = row.created_at
        return by_member, by_profile

    success_member, success_profile = _latest(success_rows)
    absence_member, absence_profile = _latest(absence_rows)
    present: set[UUID] = set()
    for member in members:
        success_at = success_member.get(member.id)
        if member.profile_id is not None:
            other = success_profile.get(member.profile_id)
            if other is not None and (success_at is None or other > success_at):
                success_at = other
        if success_at is None:
            continue
        absence_at = absence_member.get(member.id)
        if member.profile_id is not None:
            other_abs = absence_profile.get(member.profile_id)
            if other_abs is not None and (absence_at is None or other_abs > absence_at):
                absence_at = other_abs
        if absence_at is None or success_at > absence_at:
            present.add(member.id)
    return present


async def record_absence(
    db: AsyncSession,
    *,
    checkpoint_id: UUID,
    profile_id: Optional[UUID],
    team_member_id: Optional[UUID],
    source: str,
    admin_user_id: Optional[UUID] = None,
    note: Optional[str] = None,
) -> AttendanceCorrection:
    row = AttendanceCorrection(
        checkpoint_id=checkpoint_id,
        profile_id=profile_id,
        team_member_id=team_member_id,
        present=False,
        source=source,
        admin_user_id=admin_user_id,
        note=note,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _insert_scan(
    db: AsyncSession,
    *,
    checkpoint_id: UUID,
    qr_id: UUID,
    profile_id: Optional[UUID],
    team_member_id: Optional[UUID],
    result: AttendanceScanResult,
    scanned_by_admin_user_id: Optional[UUID] = None,
    note: Optional[str] = None,
) -> AttendanceScan:
    scan = AttendanceScan(
        checkpoint_id=checkpoint_id,
        qr_id=qr_id,
        profile_id=profile_id,
        team_member_id=team_member_id,
        result=result,
        scanned_by_admin_user_id=scanned_by_admin_user_id,
        note=note,
    )
    db.add(scan)
    await db.flush()
    await db.commit()
    await db.refresh(scan)
    return scan


async def scan(
    db: AsyncSession,
    token: str,
    checkpoint_id: UUID,
    admin_user_id: Optional[UUID] = None,
    note: Optional[str] = None,
) -> ScanOutcome:
    checkpoint = await get_checkpoint_or_404(db, checkpoint_id)

    qr = await qr_service.get_by_token(db, token)
    if qr is None:
        return ScanOutcome(scan=None, result=AttendanceScanResult.INVALID)

    participant = await _resolve_qr_context(db, qr)
    if participant is None:
        return ScanOutcome(scan=None, result=AttendanceScanResult.INVALID)

    scan_identity = {
        "profile_id": participant.profile_id,
        "team_member_id": participant.team_member_id,
    }

    if not qr.is_active:
        scan_row = await _insert_scan(
            db,
            checkpoint_id=checkpoint_id,
            qr_id=qr.id,
            result=AttendanceScanResult.INVALID,
            scanned_by_admin_user_id=admin_user_id,
            note=note or "inactive_qr",
            **scan_identity,
        )
        return ScanOutcome(scan=scan_row, result=AttendanceScanResult.INVALID)

    if not checkpoint.is_active:
        scan_row = await _insert_scan(
            db,
            checkpoint_id=checkpoint_id,
            qr_id=qr.id,
            result=AttendanceScanResult.INVALID,
            scanned_by_admin_user_id=admin_user_id,
            note=note or "checkpoint_inactive",
            **scan_identity,
        )
        return ScanOutcome(scan=scan_row, result=AttendanceScanResult.INVALID)

    if checkpoint.event_id is not None and participant.event_id != checkpoint.event_id:
        scan_row = await _insert_scan(
            db,
            checkpoint_id=checkpoint_id,
            qr_id=qr.id,
            result=AttendanceScanResult.INVALID,
            scanned_by_admin_user_id=admin_user_id,
            note=note or "wrong_event",
            **scan_identity,
        )
        return ScanOutcome(scan=scan_row, result=AttendanceScanResult.INVALID)

    if not await _is_paid_eligible(db, qr, participant):
        scan_row = await _insert_scan(
            db,
            checkpoint_id=checkpoint_id,
            qr_id=qr.id,
            result=AttendanceScanResult.NOT_PAID,
            scanned_by_admin_user_id=admin_user_id,
            note=note,
            **scan_identity,
        )
        return ScanOutcome(scan=scan_row, result=AttendanceScanResult.NOT_PAID)

    currently_present = await is_effectively_present(
        db,
        checkpoint_id,
        profile_id=participant.profile_id,
        team_member_id=participant.team_member_id,
    )
    if currently_present:
        if not checkpoint.allow_repeat_scan:
            scan_row = await _insert_scan(
                db,
                checkpoint_id=checkpoint_id,
                qr_id=qr.id,
                result=AttendanceScanResult.DUPLICATE,
                scanned_by_admin_user_id=admin_user_id,
                note=note,
                **scan_identity,
            )
            return ScanOutcome(scan=scan_row, result=AttendanceScanResult.DUPLICATE)

        behavior = checkpoint.duplicate_scan_behavior
        if behavior == DuplicateScanBehavior.REJECT:
            result = AttendanceScanResult.DUPLICATE
        elif behavior == DuplicateScanBehavior.WARN:
            result = AttendanceScanResult.DUPLICATE
        else:
            result = AttendanceScanResult.SUCCESS
        scan_row = await _insert_scan(
            db,
            checkpoint_id=checkpoint_id,
            qr_id=qr.id,
            result=result,
            scanned_by_admin_user_id=admin_user_id,
            note=note or ("repeat_scan" if result == AttendanceScanResult.DUPLICATE else None),
            **scan_identity,
        )
        return ScanOutcome(scan=scan_row, result=result)

    scan_row = await _insert_scan(
        db,
        checkpoint_id=checkpoint_id,
        qr_id=qr.id,
        result=AttendanceScanResult.SUCCESS,
        scanned_by_admin_user_id=admin_user_id,
        note=note,
        **scan_identity,
    )
    return ScanOutcome(scan=scan_row, result=AttendanceScanResult.SUCCESS)


async def manual_mark(
    db: AsyncSession,
    *,
    checkpoint_id: UUID,
    profile_id: UUID,
    admin_user_id: UUID,
    note: Optional[str] = None,
) -> ScanOutcome:
    checkpoint = await get_checkpoint_or_404(db, checkpoint_id)
    if not checkpoint.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Checkpoint is not active")

    reg_filters = [
        Registration.profile_id == profile_id,
        QRCode.is_active.is_(True),
    ]
    member_filters = [
        TeamMember.profile_id == profile_id,
        QRCode.is_active.is_(True),
    ]
    if checkpoint.event_id is not None:
        reg_filters.append(Registration.event_id == checkpoint.event_id)
        member_filters.append(TeamMember.event_id == checkpoint.event_id)
    reg_qr = await db.execute(
        select(QRCode)
        .join(Registration, QRCode.registration_id == Registration.id)
        .where(*reg_filters)
        .limit(1)
    )
    qr = reg_qr.scalar_one_or_none()
    if qr is None:
        member_qr = await db.execute(
            select(QRCode)
            .join(TeamMember, QRCode.team_member_id == TeamMember.id)
            .where(*member_filters)
            .limit(1)
        )
        qr = member_qr.scalar_one_or_none()
    if qr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active QR for this participant at this event")

    manual_note = f"manual_mark: {note}" if note else "manual_mark"
    return await scan(db, qr.token, checkpoint_id, admin_user_id=admin_user_id, note=manual_note)


def _activity_event_ids(
    event_id: Optional[UUID],
    scoped_event_ids: Optional[set[UUID]],
) -> Optional[set[UUID]]:
    if event_id is not None:
        return {event_id}
    return scoped_event_ids


def _scan_event_clause(event_ids: set[UUID]):
    member_on_event = exists(
        select(TeamMember.id).where(
            TeamMember.id == AttendanceScan.team_member_id,
            TeamMember.event_id.in_(event_ids),
        )
    )
    registration_on_event = exists(
        select(QRCode.id)
        .join(Registration, QRCode.registration_id == Registration.id)
        .where(QRCode.id == AttendanceScan.qr_id, Registration.event_id.in_(event_ids))
    )
    return or_(
        AttendanceCheckpoint.event_id.in_(event_ids),
        and_(
            AttendanceCheckpoint.event_id.is_(None),
            or_(member_on_event, registration_on_event),
        ),
    )


def _correction_event_clause(event_ids: set[UUID]):
    member_on_event = exists(
        select(TeamMember.id).where(
            TeamMember.id == AttendanceCorrection.team_member_id,
            TeamMember.event_id.in_(event_ids),
        )
    )
    profile_on_event = exists(
        select(Registration.id).where(
            Registration.profile_id == AttendanceCorrection.profile_id,
            Registration.event_id.in_(event_ids),
        )
    )
    return or_(
        AttendanceCheckpoint.event_id.in_(event_ids),
        and_(
            AttendanceCheckpoint.event_id.is_(None),
            or_(member_on_event, profile_on_event),
        ),
    )


async def list_scans(
    db: AsyncSession,
    *,
    event_id: Optional[UUID] = None,
    checkpoint_id: Optional[UUID] = None,
    profile_id: Optional[UUID] = None,
    result: Optional[AttendanceScanResult] = None,
    limit: int = 100,
    offset: int = 0,
    scoped_event_ids: Optional[set[UUID]] = None,
) -> list[AttendanceScan]:
    if scoped_event_ids is not None and not scoped_event_ids:
        return []
    q = select(AttendanceScan).order_by(AttendanceScan.created_at.desc())
    if checkpoint_id:
        q = q.where(AttendanceScan.checkpoint_id == checkpoint_id)
    if profile_id:
        q = q.where(AttendanceScan.profile_id == profile_id)
    if result:
        q = q.where(AttendanceScan.result == result)
    event_ids = _activity_event_ids(event_id, scoped_event_ids)
    if event_ids is not None:
        q = q.join(AttendanceCheckpoint, AttendanceScan.checkpoint_id == AttendanceCheckpoint.id).where(
            _scan_event_clause(event_ids)
        )
    q = q.limit(limit).offset(offset)
    rows = await db.execute(q)
    return list(rows.scalars().all())


async def list_checkpoints(
    db: AsyncSession,
    event_id: Optional[UUID] = None,
    scoped_event_ids: Optional[set[UUID]] = None,
) -> list[AttendanceCheckpoint]:
    if scoped_event_ids is not None and not scoped_event_ids:
        return []
    q = select(AttendanceCheckpoint).order_by(AttendanceCheckpoint.created_at.desc())
    if event_id:
        q = q.where(
            or_(
                AttendanceCheckpoint.event_id == event_id,
                AttendanceCheckpoint.event_id.is_(None),
            )
        )
    elif scoped_event_ids is not None:
        q = q.where(
            or_(
                AttendanceCheckpoint.event_id.in_(scoped_event_ids),
                AttendanceCheckpoint.event_id.is_(None),
            )
        )
    result = await db.execute(q)
    return list(result.scalars().all())


async def create_checkpoint(
    db: AsyncSession,
    *,
    event_id: Optional[UUID] = None,
    name: str,
    location: Optional[str] = None,
    is_active: bool = True,
    allow_repeat_scan: bool = False,
    duplicate_scan_behavior: DuplicateScanBehavior = DuplicateScanBehavior.REJECT,
) -> AttendanceCheckpoint:
    cp = AttendanceCheckpoint(
        event_id=event_id,
        name=name,
        location=location,
        is_active=is_active,
        allow_repeat_scan=allow_repeat_scan,
        duplicate_scan_behavior=duplicate_scan_behavior,
    )
    db.add(cp)
    await db.commit()
    await db.refresh(cp)
    return cp


async def update_checkpoint(
    db: AsyncSession,
    checkpoint_id: UUID,
    *,
    name: Optional[str] = None,
    location: Optional[str] = None,
    is_active: Optional[bool] = None,
    allow_repeat_scan: Optional[bool] = None,
    duplicate_scan_behavior: Optional[DuplicateScanBehavior] = None,
) -> AttendanceCheckpoint:
    cp = await get_checkpoint_or_404(db, checkpoint_id)
    if name is not None:
        cp.name = name
    if location is not None:
        cp.location = location
    if is_active is not None:
        cp.is_active = is_active
    if allow_repeat_scan is not None:
        cp.allow_repeat_scan = allow_repeat_scan
    if duplicate_scan_behavior is not None:
        cp.duplicate_scan_behavior = duplicate_scan_behavior
    await db.commit()
    await db.refresh(cp)
    return cp


def _activity_source(note: Optional[str]) -> str:
    text = note or ""
    if text.startswith("manual_mark") or text.startswith("admin_"):
        return "manual"
    if text.startswith("scanner_"):
        return "scanner_roster"
    return "qr"


def _activity_timestamp(value: Optional[datetime]) -> datetime:
    return value or datetime.min.replace(tzinfo=timezone.utc)


async def list_corrections(
    db: AsyncSession,
    *,
    event_id: Optional[UUID] = None,
    checkpoint_id: Optional[UUID] = None,
    profile_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
    scoped_event_ids: Optional[set[UUID]] = None,
) -> list[AttendanceCorrection]:
    if scoped_event_ids is not None and not scoped_event_ids:
        return []
    q = select(AttendanceCorrection).order_by(AttendanceCorrection.created_at.desc())
    if checkpoint_id:
        q = q.where(AttendanceCorrection.checkpoint_id == checkpoint_id)
    if profile_id:
        q = q.where(AttendanceCorrection.profile_id == profile_id)
    event_ids = _activity_event_ids(event_id, scoped_event_ids)
    if event_ids is not None:
        q = q.join(AttendanceCheckpoint, AttendanceCorrection.checkpoint_id == AttendanceCheckpoint.id).where(
            _correction_event_clause(event_ids)
        )
    q = q.limit(limit).offset(offset)
    rows = await db.execute(q)
    return list(rows.scalars().all())


async def list_attendance_activity(
    db: AsyncSession,
    *,
    event_id: Optional[UUID] = None,
    checkpoint_id: Optional[UUID] = None,
    profile_id: Optional[UUID] = None,
    result: Optional[AttendanceScanResult] = None,
    limit: int = 100,
    offset: int = 0,
    scoped_event_ids: Optional[set[UUID]] = None,
) -> list[dict]:
    if result is not None:
        scan_rows = await list_scans(
            db,
            event_id=event_id,
            checkpoint_id=checkpoint_id,
            profile_id=profile_id,
            result=result,
            limit=limit,
            offset=offset,
            scoped_event_ids=scoped_event_ids,
        )
        page: list[tuple[str, AttendanceScan | AttendanceCorrection]] = [("scan", scan) for scan in scan_rows]
    else:
        window = offset + limit
        scan_rows = await list_scans(
            db,
            event_id=event_id,
            checkpoint_id=checkpoint_id,
            profile_id=profile_id,
            result=None,
            limit=window,
            offset=0,
            scoped_event_ids=scoped_event_ids,
        )
        correction_rows = await list_corrections(
            db,
            event_id=event_id,
            checkpoint_id=checkpoint_id,
            profile_id=profile_id,
            limit=window,
            offset=0,
            scoped_event_ids=scoped_event_ids,
        )
        merged = sorted(
            [("scan", scan, _activity_timestamp(scan.created_at)) for scan in scan_rows]
            + [("correction", row, _activity_timestamp(row.created_at)) for row in correction_rows],
            key=lambda item: item[2],
            reverse=True,
        )
        page = [(kind, obj) for kind, obj, _ts in merged[offset : offset + limit]]
    if not page:
        return []
    scans = [obj for kind, obj in page if kind == "scan"]
    corrections = [obj for kind, obj in page if kind == "correction"]
    qr_ids = {s.qr_id for s in scans}
    cp_ids = {s.checkpoint_id for s in scans} | {c.checkpoint_id for c in corrections}
    qrs = (
        {
            row.id: row
            for row in (await db.execute(select(QRCode).where(QRCode.id.in_(qr_ids)))).scalars().all()
        }
        if qr_ids
        else {}
    )
    member_ids = {s.team_member_id for s in scans if s.team_member_id} | {
        q.team_member_id for q in qrs.values() if q.team_member_id
    } | {c.team_member_id for c in corrections if c.team_member_id}
    profile_ids = {s.profile_id for s in scans if s.profile_id} | {c.profile_id for c in corrections if c.profile_id}
    members = {}
    if member_ids:
        members = {
            m.id: m
            for m in (await db.execute(select(TeamMember).where(TeamMember.id.in_(member_ids)))).scalars().all()
        }
        profile_ids |= {m.profile_id for m in members.values() if m.profile_id}
    profiles = {}
    if profile_ids:
        profiles = {
            p.id: p
            for p in (await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))).scalars().all()
        }
    team_ids = {m.team_id for m in members.values()}
    teams = {}
    if team_ids:
        teams = {t.id: t for t in (await db.execute(select(Team).where(Team.id.in_(team_ids)))).scalars().all()}
    checkpoints = {
        c.id: c
        for c in (
            await db.execute(select(AttendanceCheckpoint).where(AttendanceCheckpoint.id.in_(cp_ids)))
        ).scalars().all()
    }
    event_ids = {c.event_id for c in checkpoints.values() if c.event_id} | {m.event_id for m in members.values()}
    events = {}
    if event_ids:
        events = {e.id: e for e in (await db.execute(select(Event).where(Event.id.in_(event_ids)))).scalars().all()}

    present_cache: dict[tuple, bool] = {}
    rows: list[dict] = []
    for kind, item in page:
        scan = item if kind == "scan" else None
        correction = item if kind == "correction" else None
        member = None
        if scan is not None and scan.team_member_id:
            member = members.get(scan.team_member_id)
        if member is None and scan is not None:
            qr = qrs.get(scan.qr_id)
            if qr and qr.team_member_id:
                member = members.get(qr.team_member_id)
        if member is None and correction is not None and correction.team_member_id:
            member = members.get(correction.team_member_id)
        profile_id_value = None
        if scan is not None:
            profile_id_value = scan.profile_id
        elif correction is not None:
            profile_id_value = correction.profile_id
        profile = profiles.get(profile_id_value) if profile_id_value else None
        if profile is None and member and member.profile_id:
            profile = profiles.get(member.profile_id)
        name = profile.full_name if profile and profile.full_name else None
        if not name and member and member.full_name:
            name = member.full_name
        team = teams.get(member.team_id) if member else None
        checkpoint_id_value = scan.checkpoint_id if scan is not None else correction.checkpoint_id
        checkpoint = checkpoints.get(checkpoint_id_value)
        event = events.get(member.event_id) if member else None
        if event is None and checkpoint and checkpoint.event_id:
            event = events.get(checkpoint.event_id)
        identity_member = (scan.team_member_id if scan is not None else correction.team_member_id) or (
            member.id if member else None
        )
        identity_profile = profile_id_value or (member.profile_id if member else None)
        key = (checkpoint_id_value, identity_member, identity_profile)
        if key not in present_cache:
            present_cache[key] = await is_effectively_present(
                db,
                checkpoint_id_value,
                profile_id=identity_profile,
                team_member_id=identity_member,
            )
        role = None
        if member is not None:
            role_value = member.role.value if hasattr(member.role, "value") else str(member.role)
            role = "Leader" if role_value == "LEADER" else "Member"
        if scan is not None:
            result_value = scan.result.value if hasattr(scan.result, "value") else str(scan.result)
            source = _activity_source(scan.note)
            created_at = scan.created_at
            row_id = scan.id
        else:
            result_value = "ABSENT" if not correction.present else "PRESENT"
            source = "correction"
            created_at = correction.created_at
            row_id = correction.id
        rows.append(
            {
                "id": row_id,
                "kind": kind,
                "participant_name": name or "Participant",
                "team_name": team.name if team else None,
                "role": role,
                "event_name": event.name if event else None,
                "checkpoint_id": checkpoint_id_value,
                "checkpoint_name": checkpoint.name if checkpoint else None,
                "result": result_value,
                "checked_in": present_cache[key],
                "source": source,
                "created_at": created_at,
                "team_member_id": identity_member,
                "profile_id": identity_profile,
            }
        )
    return rows


async def set_admin_attendance_state(
    db: AsyncSession,
    *,
    checkpoint_id: UUID,
    admin_user_id: UUID,
    checked_in: bool,
    profile_id: Optional[UUID] = None,
    team_member_id: Optional[UUID] = None,
) -> dict:
    if (profile_id is None) == (team_member_id is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide profile_id or team_member_id")
    checkpoint = await get_checkpoint_or_404(db, checkpoint_id)
    if not checkpoint.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Checkpoint is not active")
    if not checked_in:
        await record_absence(
            db,
            checkpoint_id=checkpoint_id,
            profile_id=profile_id,
            team_member_id=team_member_id,
            source="admin",
            admin_user_id=admin_user_id,
            note="admin_absent",
        )
        return {"checked_in": False, "result": "ABSENT"}
    if team_member_id is not None:
        member = (await db.execute(select(TeamMember).where(TeamMember.id == team_member_id))).scalar_one_or_none()
        if member is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Team member not found")
        if checkpoint.event_id is not None and member.event_id != checkpoint.event_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Member is not on this checkpoint's event")
        qr = (
            await db.execute(
                select(QRCode).where(QRCode.team_member_id == member.id, QRCode.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if qr is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No active QR for this participant")
        outcome = await scan(db, qr.token, checkpoint_id, admin_user_id=admin_user_id, note="admin_present")
        present = await is_effectively_present(
            db, checkpoint_id, profile_id=member.profile_id, team_member_id=member.id
        )
        return {"checked_in": present, "result": outcome.result.value}
    outcome = await manual_mark(
        db,
        checkpoint_id=checkpoint_id,
        profile_id=profile_id,
        admin_user_id=admin_user_id,
    )
    present = await is_effectively_present(db, checkpoint_id, profile_id=profile_id, team_member_id=None)
    return {"checked_in": present, "result": outcome.result.value}
