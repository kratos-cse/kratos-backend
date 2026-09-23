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
import time
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

# Process-local TTL caches (single-worker deployment). Swap for Redis if multi-worker.
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


def create_access_token(user_id: uuid.UUID) -> Tuple[str, int]:
    expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    raw_token = credentials.credentials

    payload = decode_token(raw_token)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    now = time.monotonic()
    cached = _user_cache.get(user_id)
    if cached is not None and now < cached[0]:
        return cached[1]

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    _user_cache[user_id] = (now + _USER_CACHE_TTL_SEC, user)
    return user


async def get_current_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    now = time.monotonic()
    cached = _profile_cache.get(current_user.id)
    if cached is not None and now < cached[0]:
        return cached[1]

    result = await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found for this user")
    _profile_cache[current_user.id] = (now + _USER_CACHE_TTL_SEC, profile)
    return profile
