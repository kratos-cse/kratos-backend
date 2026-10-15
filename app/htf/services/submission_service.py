"""HTF PPT submission service.

Handles validation, magic bytes verification, Google Drive uploads, versioning,
transactional DB persistence, audit logging, and admin streaming downloads.
"""
from __future__ import annotations

import io
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.htf.contracts import is_active_member
from app.htf.drive import GoogleDriveService
from app.htf.errors import (
    APPLICATION_NOT_EDITABLE,
    APPLICATION_NOT_FOUND,
    FILE_TOO_LARGE,
    INVALID_FILE_TYPE,
    NOT_TEAM_MEMBER,
    SUBMISSION_DEADLINE_PASSED,
    SUBMISSION_FAILED,
    SUBMISSION_NOT_FOUND,
    htf_error,
)
from app.htf.models.application import HTFApplication, HTFEventConfig
from app.htf.models.submission import HTFSubmission
from app.htf.schemas.submission import SubmissionMetadataOut
from app.htf.settings import htf_settings
from app.models.profile import Profile
from app.models.team import Team
from app.services.audit_service import log_activity

logger = logging.getLogger("htf.submissions")

PPT_ALLOWED_MIME_TYPES = frozenset({
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/octet-stream",
    "application/zip",
    "application/x-mspowerpoint",
})

OLE2_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
ZIP_MAGIC = b"PK\x03\x04"


def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name)
    sanitized = re.sub(r"[^\w\.\-]", "_", base)
    return sanitized[:255] if sanitized else "presentation.pptx"


def _validate_presentation_bytes(content: bytes, ext: str) -> None:
    if ext == ".pptx":
        if not content.startswith(ZIP_MAGIC):
            raise htf_error(INVALID_FILE_TYPE, "Invalid .pptx file: missing ZIP header signature")
        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                namelist = zf.namelist()
                if "ppt/presentation.xml" not in namelist:
                    raise htf_error(
                        INVALID_FILE_TYPE,
                        "Invalid .pptx file: missing ppt/presentation.xml entry",
                    )
        except zipfile.BadZipFile:
            raise htf_error(INVALID_FILE_TYPE, "Invalid .pptx file: corrupt archive")
    elif ext == ".ppt":
        if not content.startswith(OLE2_MAGIC):
            raise htf_error(INVALID_FILE_TYPE, "Invalid .ppt file: missing OLE2 compound header signature")
    else:
        raise htf_error(INVALID_FILE_TYPE, "Only .ppt and .pptx formats are permitted")


async def upload_submission(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    file: UploadFile,
    content_length: Optional[int],
    profile: Profile,
    drive_service: GoogleDriveService,
) -> SubmissionMetadataOut:
    max_bytes = htf_settings.HTF_PPT_MAX_BYTES

    # 1. Early Content-Length check
    if content_length is not None and content_length > max_bytes:
        raise htf_error(FILE_TOO_LARGE, f"File exceeds maximum allowed size of {max_bytes} bytes", status_code=413)

    # 2. MIME allowlist validation
    content_type = file.content_type or ""
    if content_type not in PPT_ALLOWED_MIME_TYPES:
        raise htf_error(
            INVALID_FILE_TYPE,
            f"Content-Type '{content_type}' is not allowed for presentation uploads",
        )

    # 3. Filename extension validation
    filename = file.filename or "presentation.pptx"
    _, raw_ext = os.path.splitext(filename)
    ext = raw_ext.lower()
    if ext not in {".ppt", ".pptx"}:
        raise htf_error(INVALID_FILE_TYPE, "File must have a .ppt or .pptx extension")

    # 4. Read body with streaming size enforcement
    chunk_size = 64 * 1024
    total_read = 0
    buffer = bytearray()

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > max_bytes:
            raise htf_error(FILE_TOO_LARGE, f"File exceeds maximum allowed size of {max_bytes} bytes", status_code=413)
        buffer.extend(chunk)

    file_bytes = bytes(buffer)
    if len(file_bytes) == 0:
        raise htf_error(INVALID_FILE_TYPE, "Uploaded file is empty")

    # 5. Magic bytes inspection
    _validate_presentation_bytes(file_bytes, ext)

    # 6. Read-only pre-checks before Drive upload (do NOT hold lock across Drive upload)
    app_result = await db.execute(
        select(HTFApplication).where(HTFApplication.id == application_id)
    )
    application = app_result.scalar_one_or_none()
    if not application:
        raise htf_error(APPLICATION_NOT_FOUND, "HTF application not found", status_code=404)

    is_member = await is_active_member(db, application.team_id, profile.id)
    if not is_member:
        raise htf_error(NOT_TEAM_MEMBER, "Caller is not an active member of this team", status_code=403)

    if application.status not in {"SUBMITTED", "PPT_SUBMITTED"}:
        raise htf_error(
            APPLICATION_NOT_EDITABLE,
            f"PPT submission not allowed when application is in {application.status} state",
            status_code=409,
        )

    config_result = await db.execute(
        select(HTFEventConfig).where(HTFEventConfig.event_id == application.event_id)
    )
    config = config_result.scalar_one_or_none()
    if not config:
        raise htf_error(SUBMISSION_DEADLINE_PASSED, "Event configuration not found", status_code=400)

    now = datetime.now(timezone.utc)
    if config.ppt_submission_opens_at and now < config.ppt_submission_opens_at:
        raise htf_error(SUBMISSION_DEADLINE_PASSED, "PPT submission window has not opened yet", status_code=403)
    if config.ppt_submission_closes_at and now > config.ppt_submission_closes_at:
        raise htf_error(SUBMISSION_DEADLINE_PASSED, "PPT submission deadline has passed", status_code=403)

    # Get team identifier for folder naming
    team_query = await db.execute(select(Team).where(Team.id == application.team_id))
    team = team_query.scalar_one_or_none()
    team_code = getattr(team, "team_code", None) or f"TEAM-{application.team_id.hex[:8].upper()}"

    # Calculate next version
    version_result = await db.execute(
        select(func.coalesce(func.max(HTFSubmission.version), 0)).where(
            HTFSubmission.application_id == application_id
        )
    )
    max_version = version_result.scalar() or 0
    new_version = max_version + 1
    drive_filename = f"submission-v{new_version}{ext}"
    sanitized_filename = _sanitize_filename(filename)

    # 7. Upload to Google Drive outside of DB transaction/lock
    drive_file_id = None
    ppt_folder_id = None
    try:
        root_id = htf_settings.GOOGLE_DRIVE_ROOT_FOLDER_ID or None
        app_folder_id = await drive_service.get_or_create_folder("Applications", parent_id=root_id)
        team_folder_id = await drive_service.get_or_create_folder(team_code, parent_id=app_folder_id)
        ppt_folder_id = await drive_service.get_or_create_folder("PPT", parent_id=team_folder_id)

        drive_file_id = await drive_service.upload(
            fileobj=io.BytesIO(file_bytes),
            name=drive_filename,
            mime_type=content_type,
            folder_id=ppt_folder_id,
        )
    except Exception as drive_exc:
        logger.exception("Google Drive upload failed for application %s: %s", application_id, drive_exc)
        raise htf_error(SUBMISSION_FAILED, "Failed to upload submission to storage", status_code=500)

    # 8. Short atomic database transaction with row lock
    try:
        # Re-fetch with FOR UPDATE
        lock_result = await db.execute(
            select(HTFApplication).where(HTFApplication.id == application_id).with_for_update()
        )
        locked_app = lock_result.scalar_one_or_none()
        if not locked_app or locked_app.status not in {"SUBMITTED", "PPT_SUBMITTED"}:
            # Application state mutated while uploading to Drive
            raise htf_error(
                APPLICATION_NOT_EDITABLE,
                "Application state changed during upload",
                status_code=409,
            )

        # Mark previous active submissions as REPLACED
        await db.execute(
            update(HTFSubmission)
            .where(
                HTFSubmission.application_id == application_id,
                HTFSubmission.status == "SUBMITTED",
            )
            .values(status="REPLACED", updated_at=now)
        )

        submission = HTFSubmission(
            id=uuid.uuid4(),
            application_id=application_id,
            version=new_version,
            original_filename=sanitized_filename,
            mime_type=content_type,
            size_bytes=len(file_bytes),
            google_drive_file_id=drive_file_id,
            google_drive_folder_id=ppt_folder_id or "root",
            status="SUBMITTED",
            uploaded_by_profile_id=profile.id,
            submitted_at=now,
        )
        db.add(submission)

        locked_app.status = "PPT_SUBMITTED"
        locked_app.updated_at = now

        action = "HTF_PPT_REPLACED" if max_version > 0 else "HTF_PPT_SUBMITTED"
        await log_activity(
            db,
            action=action,
            resource_type="HTF_SUBMISSION",
            resource_id=submission.id,
            actor_profile_id=profile.id,
            details={
                "application_id": str(application_id),
                "version": new_version,
                "original_filename": sanitized_filename,
                "size_bytes": len(file_bytes),
            },
        )

        await db.commit()
    except Exception as db_exc:
        logger.exception("DB transaction failed after Drive upload for application %s: %s", application_id, db_exc)
        await db.rollback()
        # Clean up orphaned Drive file
        if drive_file_id:
            try:
                await drive_service.delete(drive_file_id)
            except Exception as cleanup_exc:
                logger.warning("Failed to clean up orphaned Drive file %s: %s", drive_file_id, cleanup_exc)
        if isinstance(db_exc, AppError):
            raise
        raise htf_error(SUBMISSION_FAILED, "Failed to record submission metadata", status_code=500)

    return SubmissionMetadataOut(
        id=submission.id,
        version=submission.version,
        original_filename=submission.original_filename,
        mime_type=submission.mime_type,
        size_bytes=submission.size_bytes,
        status=submission.status,
        submitted_at=submission.submitted_at,
        version_count=new_version,
    )


async def get_submission_metadata(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    profile: Profile,
) -> SubmissionMetadataOut:
    app_result = await db.execute(
        select(HTFApplication).where(HTFApplication.id == application_id)
    )
    application = app_result.scalar_one_or_none()
    if not application:
        raise htf_error(APPLICATION_NOT_FOUND, "HTF application not found", status_code=404)

    is_member = await is_active_member(db, application.team_id, profile.id)
    if not is_member:
        raise htf_error(NOT_TEAM_MEMBER, "Caller is not an active member of this team", status_code=403)

    sub_result = await db.execute(
        select(HTFSubmission)
        .where(
            HTFSubmission.application_id == application_id,
            HTFSubmission.status == "SUBMITTED",
        )
        .order_by(HTFSubmission.version.desc())
    )
    current_sub = sub_result.scalar_one_or_none()
    if not current_sub:
        raise htf_error(SUBMISSION_NOT_FOUND, "No active submission found for this application", status_code=404)

    count_result = await db.execute(
        select(func.count(HTFSubmission.id)).where(HTFSubmission.application_id == application_id)
    )
    version_count = count_result.scalar() or 1

    return SubmissionMetadataOut(
        id=current_sub.id,
        version=current_sub.version,
        original_filename=current_sub.original_filename,
        mime_type=current_sub.mime_type,
        size_bytes=current_sub.size_bytes,
        status=current_sub.status,
        submitted_at=current_sub.submitted_at,
        version_count=version_count,
    )


async def admin_get_submission_stream(
    db: AsyncSession,
    *,
    submission_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    drive_service: GoogleDriveService,
) -> tuple[AsyncGenerator[bytes, None], str, str]:
    sub_result = await db.execute(
        select(HTFSubmission).where(HTFSubmission.id == submission_id)
    )
    submission = sub_result.scalar_one_or_none()
    if not submission:
        raise htf_error(SUBMISSION_NOT_FOUND, "Submission not found", status_code=404)

    # Actor user ID must be admin.user_id (User foreign key)
    await log_activity(
        db,
        action="HTF_SUBMISSION_DOWNLOADED",
        resource_type="HTF_SUBMISSION",
        resource_id=submission.id,
        actor_user_id=admin_user_id,
        actor_role="ADMIN",
        details={
            "application_id": str(submission.application_id),
            "version": submission.version,
            "filename": submission.original_filename,
        },
    )
    await db.commit()

    stream_generator = drive_service.stream_file(submission.google_drive_file_id)
    return stream_generator, submission.original_filename, submission.mime_type
