import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import require_permission
from app.core.security import get_current_profile
from app.db.session import get_db
from app.htf.drive import GoogleDriveService, get_drive_service
from app.htf.schemas.submission import SubmissionMetadataOut
from app.htf.services import submission_service
from app.models.admin import AdminUser
from app.models.profile import Profile

router = APIRouter(tags=["HTF Submissions"])


@router.post(
    "/htf/applications/{application_id}/submission",
    response_model=SubmissionMetadataOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_ppt_submission(
    application_id: uuid.UUID,
    file: UploadFile = File(...),
    content_length: Optional[int] = Header(None, alias="Content-Length"),
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
    drive_service: GoogleDriveService = Depends(get_drive_service),
) -> SubmissionMetadataOut:
    return await submission_service.upload_submission(
        db,
        application_id=application_id,
        file=file,
        content_length=content_length,
        profile=profile,
        drive_service=drive_service,
    )


@router.get(
    "/htf/applications/{application_id}/submission",
    response_model=SubmissionMetadataOut,
)
async def get_current_submission_metadata(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
) -> SubmissionMetadataOut:
    return await submission_service.get_submission_metadata(
        db,
        application_id=application_id,
        profile=profile,
    )


@router.get(
    "/admin/htf/submissions/{submission_id}/download",
)
async def admin_download_submission(
    submission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission("htf-application-read")),
    drive_service: GoogleDriveService = Depends(get_drive_service),
) -> StreamingResponse:
    stream_generator, filename, mime_type = await submission_service.admin_get_submission_stream(
        db,
        submission_id=submission_id,
        admin_user_id=admin.user_id,
        drive_service=drive_service,
    )
    return StreamingResponse(
        content=stream_generator,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
