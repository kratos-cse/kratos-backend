from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile
from app.db.session import get_db
from app.models.profile import Profile
from app.schemas.profile import ProfileOut, ProfileUpdateRequest

router = APIRouter(prefix="/users/me/profile", tags=["User Profile"])


@router.get("", response_model=ProfileOut)
async def read_my_profile(profile: Profile = Depends(get_current_profile)):
    return profile


@router.patch("", response_model=ProfileOut)
async def update_my_profile(
    payload: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    """
    Only the fields present in the request body are updated.
    Note: this never touches USERS.email — that stays the Google auth
    identity. contact_email here is the separate, editable field.
    """
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    return profile
