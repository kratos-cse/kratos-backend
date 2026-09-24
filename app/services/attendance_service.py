"""Attendance scanning and checkpoint management."""
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import AttendanceCheckpoint, AttendanceScan
from app.models.enums import (
    AttendanceScanResult,
    DuplicateScanBehavior,
    RegistrationStatus,
    TeamMemberStatus,
    TeamStatus,
)
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

    if participant.event_id != checkpoint.event_id:
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

    prior = await _prior_success_scan(
        db,
        checkpoint_id,
        profile_id=participant.profile_id,
        team_member_id=participant.team_member_id,
    )
    if prior is not None:
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

    reg_qr = await db.execute(
        select(QRCode)
        .join(Registration, QRCode.registration_id == Registration.id)
        .where(
            Registration.profile_id == profile_id,
            Registration.event_id == checkpoint.event_id,
            QRCode.is_active.is_(True),
        )
        .limit(1)
    )
    qr = reg_qr.scalar_one_or_none()
    if qr is None:
        member_qr = await db.execute(
            select(QRCode)
            .join(TeamMember, QRCode.team_member_id == TeamMember.id)
            .where(
                TeamMember.profile_id == profile_id,
                TeamMember.event_id == checkpoint.event_id,
                QRCode.is_active.is_(True),
            )
            .limit(1)
        )
        qr = member_qr.scalar_one_or_none()
    if qr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active QR for this participant at this event")

    manual_note = f"manual_mark: {note}" if note else "manual_mark"
    return await scan(db, qr.token, checkpoint_id, admin_user_id=admin_user_id, note=manual_note)


async def list_scans(
    db: AsyncSession,
    *,
    event_id: Optional[UUID] = None,
    checkpoint_id: Optional[UUID] = None,
    profile_id: Optional[UUID] = None,
    result: Optional[AttendanceScanResult] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AttendanceScan]:
    q = select(AttendanceScan).order_by(AttendanceScan.created_at.desc())
    if checkpoint_id:
        q = q.where(AttendanceScan.checkpoint_id == checkpoint_id)
    if profile_id:
        q = q.where(AttendanceScan.profile_id == profile_id)
    if result:
        q = q.where(AttendanceScan.result == result)
    if event_id:
        q = q.join(AttendanceCheckpoint, AttendanceScan.checkpoint_id == AttendanceCheckpoint.id).where(
            AttendanceCheckpoint.event_id == event_id
        )
    q = q.limit(limit).offset(offset)
    rows = await db.execute(q)
    return list(rows.scalars().all())


async def list_checkpoints(db: AsyncSession, event_id: Optional[UUID] = None) -> list[AttendanceCheckpoint]:
    q = select(AttendanceCheckpoint).order_by(AttendanceCheckpoint.created_at.desc())
    if event_id:
        q = q.where(AttendanceCheckpoint.event_id == event_id)
    result = await db.execute(q)
    return list(result.scalars().all())


async def create_checkpoint(
    db: AsyncSession,
    *,
    event_id: UUID,
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
