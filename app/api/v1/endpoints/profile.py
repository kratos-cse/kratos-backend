from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile, invalidate_user_cache
from app.db.session import get_db, is_db_configured
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
    if is_db_configured():
        try:
            result = await db.execute(select(Profile).where(Profile.id == profile.id))
            db_profile = result.scalar_one_or_none()
            if db_profile is not None:
                for field, value in payload.model_dump(exclude_unset=True).items():
                    setattr(db_profile, field, value)

                await db.commit()
                await db.refresh(db_profile)
                invalidate_user_cache(db_profile.user_id)
                return db_profile
        except Exception:
            pass

    # In-memory dev fallback when live DB is unavailable
    for field, value in payload.model_dump(exclude_unset=True).items():
        if hasattr(profile, field):
            setattr(profile, field, value)
    invalidate_user_cache(profile.user_id)
    return profile
