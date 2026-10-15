import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.htf.drive import GoogleDriveService
from app.htf.errors import (
    FILE_TOO_LARGE,
    INVALID_FILE_TYPE,
    NOT_TEAM_MEMBER,
    SUBMISSION_DEADLINE_PASSED,
    SUBMISSION_FAILED,
)
from app.htf.models.application import HTFApplication, HTFEventConfig
from app.htf.models.submission import HTFSubmission
from app.htf.services import submission_service
from app.models.enums import EventCategory, EventRegistrationStatus, EventVisibility, TeamMemberRole, TeamMemberStatus, TeamStatus
from app.models.event import Event
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.models.user import User
from tests.conftest import requires_db


def _make_valid_pptx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", "<p:presentation/>")
    return buf.getvalue()


VALID_PPTX_BYTES = _make_valid_pptx_bytes()
VALID_PPT_BYTES = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 504


class FakeDriveService(GoogleDriveService):
    def __init__(self):
        super().__init__()
        self.files: dict[str, dict] = {}
        self.deleted_files: list[str] = []
        self.fail_upload = False

    async def get_or_create_folder(self, name: str, parent_id: str | None = None) -> str:
        return f"fld_{name}"

    async def upload(self, fileobj, name: str, mime_type: str, folder_id: str) -> str:
        if self.fail_upload:
            raise RuntimeError("Google Drive upload network error")
        file_id = f"drive_file_{uuid.uuid4().hex[:8]}"
        fileobj.seek(0)
        content = fileobj.read()
        self.files[file_id] = {
            "name": name,
            "mime_type": mime_type,
            "folder_id": folder_id,
            "content": content,
        }
        return file_id

    async def get_file_bytes(self, file_id: str) -> bytes:
        if file_id in self.files:
            return self.files[file_id]["content"]
        raise FileNotFoundError(f"File {file_id} not in fake drive")

    async def stream_file(self, file_id: str, chunk_size: int = 1024 * 1024):
        if file_id in self.files:
            yield self.files[file_id]["content"]
        else:
            raise FileNotFoundError(f"File {file_id} not in fake drive")

    async def delete(self, file_id: str) -> bool:
        self.deleted_files.append(file_id)
        self.files.pop(file_id, None)
        return True


async def _create_test_setup(db: AsyncSession, *, app_status="SUBMITTED"):
    user = User(google_sub=f"sub-{uuid.uuid4().hex}", email=f"lead-{uuid.uuid4().hex[:6]}@test.local")
    db.add(user)
    await db.flush()

    leader = Profile(
        user_id=user.id,
        full_name="Leader User",
        contact_email=user.email,
        college_name="Test College",
    )
    db.add(leader)
    await db.flush()

    event = Event(
        name=f"HTF 2026 {uuid.uuid4().hex[:6]}",
        short_desc="Hackathon",
        category=EventCategory.TECHNICAL,
        fee=0,
        venue="Campus",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
    )
    db.add(event)
    await db.flush()

    team = Team(
        event_id=event.id,
        name="Team Alpha",
        leader_profile_id=leader.id,
        status=TeamStatus.FORMING,
    )
    db.add(team)
    await db.flush()

    member = TeamMember(
        team_id=team.id,
        event_id=event.id,
        profile_id=leader.id,
        role=TeamMemberRole.LEADER,
        status=TeamMemberStatus.ACTIVE,
    )
    db.add(member)
    await db.flush()

    app = HTFApplication(
        id=uuid.uuid4(),
        event_id=event.id,
        team_id=team.id,
        status=app_status,
        created_by_profile_id=leader.id,
    )
    db.add(app)
    await db.flush()

    config = HTFEventConfig(
        id=uuid.uuid4(),
        event_id=event.id,
        payment_amount_paise=50000,
        currency="INR",
        max_team_size=4,
    )
    db.add(config)
    await db.flush()

    return {"leader": leader, "event": event, "team": team, "app": app, "config": config}


def _make_upload_file(filename: str, content: bytes, content_type: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation"):
    return UploadFile(
        file=io.BytesIO(content),
        size=len(content),
        filename=filename,
        headers={"content-type": content_type},
    )


@requires_db
@pytest.mark.asyncio
async def test_upload_valid_pptx(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    res = await submission_service.upload_submission(
        db,
        application_id=data["app"].id,
        file=file,
        content_length=len(VALID_PPTX_BYTES),
        profile=data["leader"],
        drive_service=drive,
    )

    assert res.version == 1
    assert res.status == "SUBMITTED"
    assert res.original_filename == "deck.pptx"
    assert res.version_count == 1

    # Application should be PPT_SUBMITTED
    app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert app.status == "PPT_SUBMITTED"


@requires_db
@pytest.mark.asyncio
async def test_upload_valid_ppt(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    file = _make_upload_file("presentation.ppt", VALID_PPT_BYTES, content_type="application/vnd.ms-powerpoint")

    res = await submission_service.upload_submission(
        db,
        application_id=data["app"].id,
        file=file,
        content_length=len(VALID_PPT_BYTES),
        profile=data["leader"],
        drive_service=drive,
    )

    assert res.version == 1
    assert res.status == "SUBMITTED"


@requires_db
@pytest.mark.asyncio
async def test_rejects_wrong_extension(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    file = _make_upload_file("deck.pdf", b"%PDF-1.4 ...")

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(b"%PDF-1.4 ..."),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == INVALID_FILE_TYPE


@requires_db
@pytest.mark.asyncio
async def test_rejects_spoofed_magic_bytes(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    # Has .pptx name and ZIP header but missing ppt/presentation.xml
    fake_zip_buf = io.BytesIO()
    with zipfile.ZipFile(fake_zip_buf, "w") as zf:
        zf.writestr("not_a_presentation.txt", "hello")
    fake_zip = fake_zip_buf.getvalue()

    file = _make_upload_file("deck.pptx", fake_zip)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(fake_zip),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == INVALID_FILE_TYPE


@requires_db
@pytest.mark.asyncio
async def test_rejects_oversize_file(db: AsyncSession, monkeypatch):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    from app.htf.settings import htf_settings
    monkeypatch.setattr(htf_settings, "HTF_PPT_MAX_BYTES", 50)

    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == FILE_TOO_LARGE


@requires_db
@pytest.mark.asyncio
async def test_rejects_outside_deadline_window(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    # Set closes_at in the past
    data["config"].ppt_submission_closes_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.flush()

    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == SUBMISSION_DEADLINE_PASSED


@requires_db
@pytest.mark.asyncio
async def test_rejects_non_team_member(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    # Create outsider profile
    other_user = User(google_sub=f"sub-{uuid.uuid4().hex}", email="outsider@test.local")
    db.add(other_user)
    await db.flush()
    outsider = Profile(user_id=other_user.id, full_name="Outsider", contact_email=other_user.email, college_name="C")
    db.add(outsider)
    await db.flush()

    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=outsider,
            drive_service=drive,
        )
    assert exc.value.code == NOT_TEAM_MEMBER


@requires_db
@pytest.mark.asyncio
async def test_drive_upload_failure_leaves_state_unchanged(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    drive.fail_upload = True
    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == SUBMISSION_FAILED

    # App status should still be SUBMITTED
    app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert app.status == "SUBMITTED"


@requires_db
@pytest.mark.asyncio
async def test_versioning_replacement(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()

    # Upload v1
    file1 = _make_upload_file("v1.pptx", VALID_PPTX_BYTES)
    res1 = await submission_service.upload_submission(
        db,
        application_id=data["app"].id,
        file=file1,
        content_length=len(VALID_PPTX_BYTES),
        profile=data["leader"],
        drive_service=drive,
    )
    assert res1.version == 1
    assert res1.status == "SUBMITTED"

    # Upload v2 replacement
    file2 = _make_upload_file("v2.pptx", VALID_PPTX_BYTES)
    res2 = await submission_service.upload_submission(
        db,
        application_id=data["app"].id,
        file=file2,
        content_length=len(VALID_PPTX_BYTES),
        profile=data["leader"],
        drive_service=drive,
    )
    assert res2.version == 2
    assert res2.status == "SUBMITTED"
    assert res2.version_count == 2

    # Check database: v1 is REPLACED, v2 is SUBMITTED
    subs = (
        await db.scalars(
            select(HTFSubmission)
            .where(HTFSubmission.application_id == data["app"].id)
            .order_by(HTFSubmission.version)
        )
    ).all()
    assert len(subs) == 2
    assert subs[0].version == 1 and subs[0].status == "REPLACED"
    assert subs[1].version == 2 and subs[1].status == "SUBMITTED"

    # Metadata endpoint returns current v2
    meta = await submission_service.get_submission_metadata(
        db,
        application_id=data["app"].id,
        profile=data["leader"],
    )
    assert meta.version == 2
    assert meta.original_filename == "v2.pptx"
    assert meta.version_count == 2


@requires_db
@pytest.mark.asyncio
async def test_rejects_disallowed_mime(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES, content_type="image/png")

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == INVALID_FILE_TYPE


@requires_db
@pytest.mark.asyncio
async def test_db_failure_cleans_up_drive_file(db: AsyncSession, monkeypatch):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    # Monkeypatch db.commit to simulate database failure
    async def _failing_commit():
        raise RuntimeError("Simulated DB connection failure")

    monkeypatch.setattr(db, "commit", _failing_commit)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == SUBMISSION_FAILED
    assert len(drive.deleted_files) == 1


@requires_db
@pytest.mark.asyncio
async def test_missing_event_config_refuses_upload(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()
    await db.delete(data["config"])
    await db.flush()

    file = _make_upload_file("deck.pptx", VALID_PPTX_BYTES)

    with pytest.raises(AppError) as exc:
        await submission_service.upload_submission(
            db,
            application_id=data["app"].id,
            file=file,
            content_length=len(VALID_PPTX_BYTES),
            profile=data["leader"],
            drive_service=drive,
        )
    assert exc.value.code == SUBMISSION_DEADLINE_PASSED


@requires_db
@pytest.mark.asyncio
async def test_admin_download_streams_and_audits_user_id(db: AsyncSession):
    data = await _create_test_setup(db)
    drive = FakeDriveService()

    # Create admin user
    admin_user = User(google_sub=f"sub-admin-{uuid.uuid4().hex}", email="admin@test.local")
    db.add(admin_user)
    await db.flush()

    # Upload file
    file = _make_upload_file("final.pptx", VALID_PPTX_BYTES)
    res = await submission_service.upload_submission(
        db,
        application_id=data["app"].id,
        file=file,
        content_length=len(VALID_PPTX_BYTES),
        profile=data["leader"],
        drive_service=drive,
    )

    # Stream download
    stream_gen, filename, mime = await submission_service.admin_get_submission_stream(
        db,
        submission_id=res.id,
        admin_user_id=admin_user.id,
        drive_service=drive,
    )
    chunks = []
    async for chunk in stream_gen:
        chunks.append(chunk)

    assert b"".join(chunks) == VALID_PPTX_BYTES
    assert filename == "final.pptx"

    # Verify audit log actor_user_id is admin_user.id
    from app.models.audit_log import AuditLog
    audit = await db.scalar(
        select(AuditLog)
        .where(AuditLog.resource_id == str(res.id), AuditLog.action == "HTF_SUBMISSION_DOWNLOADED")
    )
    assert audit is not None
    assert audit.actor_user_id == admin_user.id
