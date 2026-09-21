"""
*** TEMPORARY ***
No auth module exists on this branch — real endpoints need a
`get_current_profile` dependency that returns the authenticated caller's
PROFILES row. Until the Authentication branch's real one merges, this stub
fakes it by reading a profile id straight from a request header — same
approach teams/app/core/deps_stub.py already uses for the identical
problem on that branch.

SECURITY:
This stub is strictly gated to local/test environments (ENVIRONMENT=development,
test, or local). Outside these environments, it fails closed to prevent
unauthorized privilege escalation.

DELETE THIS FILE once the real auth/session module merges, and swap
`from app.core.deps_stub import get_current_profile`
for whatever the real module exposes.

Usage while testing locally:
    curl -H "X-Debug-Profile-Id: <uuid>" http://localhost:8000/...
    curl -H "X-Debug-Profile-Id: <uuid>" -H "X-Debug-Is-Admin: true" http://localhost:8000/...
    curl -H "X-Debug-Profile-Id: <uuid>" -H "X-Debug-Is-Super-Admin: true" http://localhost:8000/...
"""
import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from app.core.config import settings


@dataclass
class StubProfile:
    id: uuid.UUID
    user_id: uuid.UUID
    is_admin: bool = False
    is_super_admin: bool = False


def get_current_profile(
    x_debug_profile_id: str | None = Header(default=None),
    x_debug_is_admin: str | None = Header(default=None),
    x_debug_is_super_admin: str | None = Header(default=None),
) -> StubProfile:
    if not settings.is_local_or_test_env:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Debug authentication headers are disabled outside development/test environments",
        )

    if not x_debug_profile_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (stub: pass X-Debug-Profile-Id header)",
        )
    try:
        pid = uuid.UUID(x_debug_profile_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid debug profile id")

    is_super = (x_debug_is_super_admin or "").strip().lower() == "true"
    is_admin = is_super or ((x_debug_is_admin or "").strip().lower() == "true")

    return StubProfile(id=pid, user_id=pid, is_admin=is_admin, is_super_admin=is_super)
