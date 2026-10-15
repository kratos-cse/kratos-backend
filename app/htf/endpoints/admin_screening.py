import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import require_permission
from app.db.session import get_db
from app.htf.schemas.screening import ScreeningDecisionRequest, ScreeningResultOut
from app.htf.services import screening_service
from app.models.admin import AdminUser

router = APIRouter(tags=["HTF Admin Screening"])


@router.post(
    "/admin/htf/applications/{application_id}/screening",
    status_code=status.HTTP_200_OK,
)
async def post_screening_decision(
    application_id: uuid.UUID,
    payload: ScreeningDecisionRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission("htf-screening")),
) -> dict:
    data = await screening_service.record_screening_decision(
        db,
        application_id=application_id,
        result=payload.result,
        notes=payload.notes,
        admin=admin,
    )
    return {
        "status": "success",
        "data": data.model_dump(),
    }
