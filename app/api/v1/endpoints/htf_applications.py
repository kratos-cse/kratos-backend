import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile
from app.db.session import get_db
from app.models.htf_application import HtfApplication, HtfApplicationStatus
from app.models.profile import Profile
from app.schemas.htf_application import (
    HtfApplicationOut,
    HtfApplicationUpdate,
)
from app.services import htf_application_service

router = APIRouter(prefix="/htf", tags=["HTF Applications"])


def _to_out(app: HtfApplication) -> HtfApplicationOut:
    data = app.application_data or {}
    responses = data.get("responses", {})
    return HtfApplicationOut(
        id=app.id,
        event_id=app.event_id,
        team_id=app.team_id,
        status=app.status,
        submitted_at=app.submitted_at,
        responses=responses,
        is_editable=(app.status == HtfApplicationStatus.DRAFT),
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


@router.post(
    "/applications",
    response_model=HtfApplicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_application(
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    app = await htf_application_service.create_application(db, profile)
    return _to_out(app)


@router.get(
    "/applications/me",
    response_model=HtfApplicationOut,
)
async def get_my_application(
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    app = await htf_application_service.get_my_application(db, profile)
    return _to_out(app)


@router.get(
    "/applications/{application_id}",
    response_model=HtfApplicationOut,
)
async def get_application(
    application_id: uuid.UUID,
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    app = await htf_application_service.get_application(db, application_id, profile)
    return _to_out(app)


@router.patch(
    "/applications/{application_id}",
    response_model=HtfApplicationOut,
)
async def update_draft(
    application_id: uuid.UUID,
    payload: HtfApplicationUpdate,
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    app = await htf_application_service.update_draft(
        db, application_id, profile, payload.responses
    )
    return _to_out(app)


@router.post(
    "/applications/{application_id}/submit",
    response_model=HtfApplicationOut,
)
async def submit_application(
    application_id: uuid.UUID,
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    app = await htf_application_service.submit_application(db, application_id, profile)
    return _to_out(app)
