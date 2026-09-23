import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import bearer_scheme, create_access_token, decode_token, get_current_profile, get_current_user, revoke_token
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import User
from app.schemas.auth import DevAuthRequest, GoogleAuthRequest, MeResponse, TokenResponse, UserOut
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

    token, expires_in = create_access_token(user.id, email=user.email, name=profile.full_name, is_admin=user.is_admin_flagged)

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
        profile=ProfileOut.model_validate(profile),
    )


@router.post("/dev-login", response_model=TokenResponse)
async def dev_login(payload: DevAuthRequest, db: AsyncSession = Depends(get_db)):
    """Dev-only fast authentication for localhost testing."""
    env = (settings.ENVIRONMENT or "development").strip().lower()
    if env not in ("development", "test", "testing"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev login is only permitted in development or test environments",
        )

    # Deterministic user ID based on email for repeatable sessions
    dev_user_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(payload.email).lower().strip())
    is_admin = bool(payload.is_admin or "admin" in str(payload.email).lower())

    user = None
    profile = None
    try:
        result = await db.execute(select(User).where(User.email == payload.email))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                id=dev_user_id,
                google_sub=f"dev-{dev_user_id.hex[:12]}",
                email=payload.email,
                is_admin_flagged=is_admin,
            )
            db.add(user)
            await db.flush()
        else:
            if is_admin and not user.is_admin_flagged:
                user.is_admin_flagged = True
                await db.flush()

        result_p = await db.execute(select(Profile).where(Profile.user_id == user.id))
        profile = result_p.scalar_one_or_none()
        if profile is None:
            profile = Profile(
                user_id=user.id,
                full_name=payload.name,
                contact_email=payload.email,
                college_name="KRATOS Institute of Technology",
            )
            db.add(profile)
            await db.flush()

        await db.commit()
        await db.refresh(user)
        await db.refresh(profile)
    except Exception:
        # Fallback when DB is not running locally
        user = User(
            id=dev_user_id,
            email=payload.email,
            google_sub=f"dev-{dev_user_id.hex[:12]}",
            is_admin_flagged=is_admin,
            created_at=datetime.now(timezone.utc),
            last_login_at=datetime.now(timezone.utc),
        )
        profile = Profile(
            id=uuid.uuid4(),
            user_id=dev_user_id,
            full_name=payload.name,
            contact_email=payload.email,
            college_name="KRATOS Institute of Technology",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    token, expires_in = create_access_token(
        user.id,
        email=user.email,
        name=profile.full_name,
        is_admin=user.is_admin_flagged,
    )

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
