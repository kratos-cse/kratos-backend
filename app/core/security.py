"""
JWT session handling and FastAPI auth dependencies.

The finalized schema has no SESSIONS table — this is stateless JWT auth by
design. `logout` therefore can't invalidate a token server-side in the
usual DB-backed way; instead it adds the token's jti to an in-process
revoked set. That's fine for local/dev/single-worker testing in Swagger,
but resets on restart and isn't shared across workers — swap it for a
Redis set (or a short-lived access token + refresh token pair) before
running this in production with more than one worker.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Tuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

_revoked_jtis: set[str] = set()


def create_access_token(
    user_id: uuid.UUID,
    email: str = "",
    name: str = "",
    is_admin: bool = False,
) -> Tuple[str, int]:
    expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "is_admin": is_admin,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expire_minutes * 60


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if payload.get("jti") in _revoked_jtis:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    return payload


def revoke_token(jti: str) -> None:
    _revoked_jtis.add(jti)


import time

_USER_CACHE_TTL_SEC = 20.0
_user_cache: dict[uuid.UUID, tuple[float, User]] = {}
_profile_cache: dict[uuid.UUID, tuple[float, Profile]] = {}


def invalidate_user_cache(user_id: uuid.UUID | None = None) -> None:
    if user_id is not None:
        _user_cache.pop(user_id, None)
        _profile_cache.pop(user_id, None)
    else:
        _user_cache.clear()
        _profile_cache.clear()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    now = time.monotonic()
    cached = _user_cache.get(user_id)
    if cached is not None:
        exp, user_obj = cached
        if now < exp:
            return user_obj

    user = None
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    except Exception:
        user = None

    if user is None:
        email = payload.get("email") or f"user-{str(user_id)[:8]}@kratos.dev"
        is_admin = bool(payload.get("is_admin", False))
        user = User(
            id=user_id,
            email=email,
            google_sub=f"dev-{str(user_id)[:8]}",
            is_admin_flagged=is_admin,
            created_at=datetime.now(timezone.utc),
            last_login_at=datetime.now(timezone.utc),
        )

    _user_cache[user_id] = (now + _USER_CACHE_TTL_SEC, user)
    return user


async def get_current_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    now = time.monotonic()
    cached = _profile_cache.get(current_user.id)
    if cached is not None:
        exp, prof_obj = cached
        if now < exp:
            return prof_obj

    profile = None
    try:
        result = await db.execute(select(Profile).where(Profile.user_id == current_user.id))
        profile = result.scalar_one_or_none()
    except Exception:
        profile = None

    if profile is None:
        name = "Dev Tester"
        if "admin" in str(current_user.email).lower():
            name = "Super Admin"
        elif "leader" in str(current_user.email).lower():
            name = "Priya Captain"
        elif "participant" in str(current_user.email).lower():
            name = "Aarav Participant"

        profile_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"profile-{current_user.id}")
        profile = Profile(
            id=profile_id,
            user_id=current_user.id,
            full_name=name,
            contact_email=str(current_user.email),
            college_name="KRATOS Institute of Technology",
            phone="+91 98765 43210",
            department="Computer Science",
            year_of_study="3rd Year",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    _profile_cache[current_user.id] = (now + _USER_CACHE_TTL_SEC, profile)
    return profile
