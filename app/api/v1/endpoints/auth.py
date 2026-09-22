from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import bearer_scheme, create_access_token, decode_token, get_current_profile, get_current_user, revoke_token
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import User
from app.schemas.auth import GoogleAuthRequest, MeResponse, TokenResponse, UserOut
from app.schemas.profile import ProfileOut
from app.services.auth_service import verify_google_id_token

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/google", response_model=TokenResponse)
async def google_login(payload: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticate with a Google id_token (obtained by the frontend via
    Google Sign-In). Creates the USERS row on first login and the reusable
    PROFILES row alongside it, then issues our own JWT for subsequent
    requests.
    """
    google_payload = verify_google_id_token(payload.id_token)

    result = await db.execute(select(User).where(User.google_sub == google_payload.sub))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(google_sub=google_payload.sub, email=google_payload.email)
        db.add(user)
        await db.flush()

    user.last_login_at = datetime.now(timezone.utc)

    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = Profile(user_id=user.id, full_name=google_payload.name or google_payload.email)
        db.add(profile)
        await db.flush()

    from app.services import audit_service

    await audit_service.log_activity(
        db,
        action="AUTH_LOGIN",
        resource_type="USER",
        resource_id=user.id,
        actor_user_id=user.id,
        actor_profile_id=profile.id,
        actor_role="PARTICIPANT",
        status="SUCCESS",
        details={"email": user.email},
    )

    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    token, expires_in = create_access_token(user.id)

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
        profile=ProfileOut.model_validate(profile),
    )


@router.get("/me", response_model=MeResponse)
async def read_me(
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    return MeResponse(
        user=UserOut.model_validate(current_user),
        profile=ProfileOut.model_validate(profile),
        is_admin=current_user.is_admin_flagged,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),  # ensures the token is valid before we bother revoking it
):
    from app.services import audit_service

    payload = decode_token(credentials.credentials)
    revoke_token(payload["jti"])
    audit_service.log_activity_bg(
        action="AUTH_LOGOUT",
        resource_type="USER",
        resource_id=current_user.id,
        actor_user_id=current_user.id,
        status="SUCCESS",
    )
    return None
