import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import get_current_active_admin
from app.core.errors import FORBIDDEN, NOT_FOUND, AppError
from app.core.security import get_current_profile, get_current_user
from app.db.session import get_db
from app.models.admin import AdminUser
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.models.user import User
from app.schemas.registration import QRCodeOut
from app.services import qr_service

router = APIRouter(prefix="/qr", tags=["QR"])


class QRGenerateRequest(BaseModel):
    registration_id: uuid.UUID | None = None
    team_member_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def exactly_one_owner(self) -> "QRGenerateRequest":
        has_reg = self.registration_id is not None
        has_member = self.team_member_id is not None
        if has_reg == has_member:
            raise ValueError("Provide exactly one of registration_id or team_member_id")
        return self


async def _try_active_admin(
    current_user: User,
    db: AsyncSession,
) -> Optional[AdminUser]:
    try:
        return await get_current_active_admin(current_user=current_user, db=db)
    except HTTPException:
        return None


async def _assert_can_manage_registration(
    db: AsyncSession,
    registration_id: uuid.UUID,
    profile: Profile,
    admin: Optional[AdminUser],
) -> None:
    if admin is not None:
        return
    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    registration = result.scalar_one_or_none()
    if registration is None:
        raise AppError(NOT_FOUND, "Registration not found", status_code=404)
    if registration.profile_id == profile.id:
        return
    if registration.team_id is not None:
        team_result = await db.execute(select(Team).where(Team.id == registration.team_id))
        team = team_result.scalar_one_or_none()
        if team and team.leader_profile_id == profile.id:
            return
    raise AppError(FORBIDDEN, "You do not have permission to generate this QR", status_code=403)


async def _assert_can_manage_team_member(
    db: AsyncSession,
    team_member_id: uuid.UUID,
    profile: Profile,
    admin: Optional[AdminUser],
) -> None:
    if admin is not None:
        return
    result = await db.execute(select(TeamMember).where(TeamMember.id == team_member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise AppError(NOT_FOUND, "Team member not found", status_code=404)
    if member.profile_id == profile.id:
        return
    team_result = await db.execute(select(Team).where(Team.id == member.team_id))
    team = team_result.scalar_one_or_none()
    if team and team.leader_profile_id == profile.id:
        return
    raise AppError(FORBIDDEN, "You do not have permission to generate this QR", status_code=403)


async def _assert_can_view_qr(
    db: AsyncSession,
    qr: QRCode,
    profile: Profile,
    admin: Optional[AdminUser],
) -> None:
    if admin is not None:
        return
    if qr.registration_id is not None:
        result = await db.execute(select(Registration).where(Registration.id == qr.registration_id))
        registration = result.scalar_one_or_none()
        if registration is None:
            raise AppError(NOT_FOUND, "Registration not found", status_code=404)
        if registration.profile_id == profile.id:
            return
        if registration.team_id is not None:
            member_result = await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == registration.team_id,
                    TeamMember.profile_id == profile.id,
                )
            )
            if member_result.scalar_one_or_none() is not None:
                return
    elif qr.team_member_id is not None:
        member_result = await db.execute(select(TeamMember).where(TeamMember.id == qr.team_member_id))
        member = member_result.scalar_one_or_none()
        if member and member.profile_id == profile.id:
            return
        if member:
            team_result = await db.execute(select(Team).where(Team.id == member.team_id))
            team = team_result.scalar_one_or_none()
            if team and team.leader_profile_id == profile.id:
                return
    raise AppError(FORBIDDEN, "You do not have access to this QR code", status_code=403)


@router.post("/generate", response_model=QRCodeOut)
async def generate_qr(
    body: QRGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    admin = await _try_active_admin(current_user, db)
    if body.registration_id is not None:
        await _assert_can_manage_registration(db, body.registration_id, profile, admin)
        qr = await qr_service.generate_for_registration(db, body.registration_id)
    else:
        assert body.team_member_id is not None
        await _assert_can_manage_team_member(db, body.team_member_id, profile, admin)
        qr = await qr_service.generate_for_team_member(db, body.team_member_id)
    await db.commit()
    await db.refresh(qr)
    return qr


@router.get("/{qr_id}", response_model=QRCodeOut)
async def get_qr(
    qr_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    admin = await _try_active_admin(current_user, db)
    qr = await qr_service.get_by_id_or_404(db, qr_id)
    await _assert_can_view_qr(db, qr, profile, admin)
    return qr
