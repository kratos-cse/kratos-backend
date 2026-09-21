from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import bearer_scheme, create_access_token, decode_token, get_current_profile, get_current_user, revoke_token
from app.core.config import settings
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
    payload = decode_token(credentials.credentials)
    revoke_token(payload["jti"])
    return None


# ── Dev / Testing helpers ─────────────────────────────────────────────────────
# These endpoints are disabled in ENVIRONMENT=production automatically.

class DevTokenRequest(BaseModel):
    user_id: UUID


def _assert_dev_mode():
    """Raise 404 if running in production — dev endpoints must not be accessible."""
    if settings.ENVIRONMENT == "production":
        raise HTTPException(status_code=404, detail="Not found")


@router.post(
    "/dev-token",
    tags=["Dev / Testing"],
    summary="[DEV ONLY] Issue a JWT for any existing user_id",
    description=(
        "**For Swagger testing only — disabled in production.** "
        "Provide any `user_id` from the `users` table "
        "and receive a valid JWT you can paste into the 🔒 Authorize button.\n\n"
        "Steps:\n"
        "1. Call `GET /api/v1/auth/users` to list existing users and grab a `user_id`.\n"
        "2. Call this endpoint with that `user_id` to get the token.\n"
        "3. Click **Authorize** at the top of /docs and enter: `Bearer <token>`."
    ),
)
async def dev_token(body: DevTokenRequest, db: AsyncSession = Depends(get_db)):
    _assert_dev_mode()
    result = await db.execute(select(User).where(User.id == body.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found. Run `python scripts/seed_admin.py` or POST /auth/google first.",
        )
    token, expires_in = create_access_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user_id": str(user.id),
        "email": user.email,
        "is_admin_flagged": user.is_admin_flagged,
        "hint": "Paste 'Bearer <token>' in the Authorize button above",
    }


@router.get(
    "/users",
    tags=["Dev / Testing"],
    summary="[DEV ONLY] List all registered users",
    description="Returns all user rows — disabled in production.",
)
async def list_users(db: AsyncSession = Depends(get_db)):
    _assert_dev_mode()
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(50))
    users = result.scalars().all()
    return {
        "total": len(users),
        "users": [
            {
                "user_id": str(u.id),
                "email": u.email,
                "is_admin_flagged": u.is_admin_flagged,
                "created_at": u.created_at,
            }
            for u in users
        ],
    }
