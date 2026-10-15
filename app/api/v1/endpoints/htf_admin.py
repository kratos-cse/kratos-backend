from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.admin import AdminUser
from app.api.deps_admin import get_current_active_admin, require_permission
from app.core.permissions import (
    HTF_APPLICATION_READ,
    HTF_APPLICATION_MANAGE,
    HTF_SCREENING,
    HTF_PAYMENT_READ,
    HTF_ANNOUNCEMENT,
    HTF_EXPORT
)

router = APIRouter(prefix="/admin/htf", tags=["HTF Admin Operations"])

@router.get("/applications")
async def list_htf_applications(
    skip: int = 0,
    limit: int = 20,
    status: str | None = None,
    college: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_APPLICATION_READ)(admin)
    # TODO: Implement filtering, pagination, and joining with teams
    return {"status": "success", "data": []}

@router.get("/applications/{application_id}")
async def get_htf_application(
    application_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_APPLICATION_READ)(admin)
    # TODO: Fetch full application details including team, members, PPT, and screening status
    return {"status": "success", "data": {}}

@router.post("/applications/{application_id}/screening")
async def submit_screening_result(
    application_id: UUID,
    # result_in: HTFScreeningCreate, # TODO: Import from schemas once created
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_SCREENING)(admin)
    # TODO: Implement screening logic (SHORTLISTED or NOT_SHORTLISTED), audit logs, and notifications
    return {"status": "success", "data": {}}

@router.get("/applications/{application_id}/submission")
async def get_htf_submission(
    application_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_APPLICATION_READ)(admin)
    # TODO: Return PPT submission metadata
    return {"status": "success", "data": {}}

@router.get("/submissions/{submission_id}/download")
async def download_htf_submission(
    submission_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_APPLICATION_READ)(admin)
    # TODO: Authenticate with Google Drive, fetch file securely, and stream response
    return {"status": "success", "message": "Streaming not implemented yet."}

@router.get("/payments")
async def list_htf_payments(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_PAYMENT_READ)(admin)
    # TODO: Fetch HTF specific payment linkages
    return {"status": "success", "data": []}

@router.get("/teams")
async def list_htf_teams(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_APPLICATION_READ)(admin)
    # TODO: Filter teams that belong to the HTF event
    return {"status": "success", "data": []}

@router.post("/announcements")
async def create_htf_announcement(
    # announcement_in: HTFAnnouncementCreate, # TODO: Import from schemas
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_ANNOUNCEMENT)(admin)
    # TODO: Persist announcement and broadcast to HTF participants
    return {"status": "success", "data": {}}

@router.get("/exports/applications")
async def export_htf_applications(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_EXPORT)(admin)
    # TODO: Generate CSV/Excel export for applications
    return {"status": "success", "message": "Export not implemented yet."}

@router.get("/exports/payments")
async def export_htf_payments(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_active_admin),
):
    require_permission(HTF_EXPORT)(admin)
    # TODO: Generate CSV/Excel export for payments
    return {"status": "success", "message": "Export not implemented yet."}
