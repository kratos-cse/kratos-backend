import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import require_permission
from app.db.session import get_db
from app.models.admin import AdminUser
from app.models.enums import AttendanceScanResult, DuplicateScanBehavior
from app.services import attendance_service

router = APIRouter(tags=["Attendance"])


class ScanRequest(BaseModel):
    token: str = Field(min_length=1)
    checkpoint_id: uuid.UUID


class ManualMarkRequest(BaseModel):
    checkpoint_id: uuid.UUID
    profile_id: uuid.UUID
    note: Optional[str] = None


class CheckpointCreateRequest(BaseModel):
    event_id: uuid.UUID
    name: str = Field(min_length=1)
    location: Optional[str] = None
    is_active: bool = True
    allow_repeat_scan: bool = False
    duplicate_scan_behavior: DuplicateScanBehavior = DuplicateScanBehavior.REJECT


class CheckpointPatchRequest(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None
    allow_repeat_scan: Optional[bool] = None
    duplicate_scan_behavior: Optional[DuplicateScanBehavior] = None


def _scan_out(scan) -> dict | None:
    if scan is None:
        return None
    return {
        "id": scan.id,
        "checkpoint_id": scan.checkpoint_id,
        "qr_id": scan.qr_id,
        "profile_id": scan.profile_id,
        "result": scan.result,
        "scanned_by_admin_user_id": scan.scanned_by_admin_user_id,
        "note": scan.note,
        "created_at": scan.created_at,
    }


def _checkpoint_out(cp) -> dict:
    return {
        "id": cp.id,
        "event_id": cp.event_id,
        "name": cp.name,
        "location": cp.location,
        "is_active": cp.is_active,
        "allow_repeat_scan": cp.allow_repeat_scan,
        "duplicate_scan_behavior": cp.duplicate_scan_behavior,
        "created_at": cp.created_at,
    }


@router.post("/attendance/scan")
async def attendance_scan(
    body: ScanRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission("attendance-scan")),
):
    outcome = await attendance_service.scan(
        db, body.token, body.checkpoint_id, admin_user_id=admin.id
    )
    return {
        "status": "success",
        "data": {"result": outcome.result, "scan": _scan_out(outcome.scan)},
    }


@router.get("/admin/attendance")
async def list_attendance_scans(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("attendance-read")),
    event_id: Optional[uuid.UUID] = Query(None),
    checkpoint_id: Optional[uuid.UUID] = Query(None),
    profile_id: Optional[uuid.UUID] = Query(None),
    result: Optional[AttendanceScanResult] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    scans = await attendance_service.list_scans(
        db,
        event_id=event_id,
        checkpoint_id=checkpoint_id,
        profile_id=profile_id,
        result=result,
        limit=limit,
        offset=offset,
    )
    return {"status": "success", "data": [_scan_out(s) for s in scans]}


@router.get("/admin/attendance/checkpoints")
async def list_checkpoints(
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("checkpoint-management", "attendance-read")),
    event_id: Optional[uuid.UUID] = Query(None),
):
    checkpoints = await attendance_service.list_checkpoints(db, event_id=event_id)
    return {"status": "success", "data": [_checkpoint_out(c) for c in checkpoints]}


@router.post("/admin/attendance/checkpoints", status_code=201)
async def create_checkpoint(
    body: CheckpointCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("checkpoint-management")),
):
    cp = await attendance_service.create_checkpoint(
        db,
        event_id=body.event_id,
        name=body.name,
        location=body.location,
        is_active=body.is_active,
        allow_repeat_scan=body.allow_repeat_scan,
        duplicate_scan_behavior=body.duplicate_scan_behavior,
    )
    return {"status": "success", "data": _checkpoint_out(cp)}


@router.patch("/admin/attendance/checkpoints/{checkpoint_id}")
async def patch_checkpoint(
    checkpoint_id: uuid.UUID,
    body: CheckpointPatchRequest,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_permission("checkpoint-management")),
):
    cp = await attendance_service.update_checkpoint(
        db,
        checkpoint_id,
        name=body.name,
        location=body.location,
        is_active=body.is_active,
        allow_repeat_scan=body.allow_repeat_scan,
        duplicate_scan_behavior=body.duplicate_scan_behavior,
    )
    return {"status": "success", "data": _checkpoint_out(cp)}


@router.post("/admin/attendance/manual")
async def manual_attendance(
    body: ManualMarkRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission("manual-attendance")),
):
    outcome = await attendance_service.manual_mark(
        db,
        checkpoint_id=body.checkpoint_id,
        profile_id=body.profile_id,
        admin_user_id=admin.id,
        note=body.note,
    )
    return {
        "status": "success",
        "data": {"result": outcome.result, "scan": _scan_out(outcome.scan)},
    }
