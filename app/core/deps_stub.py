"""
*** TEMPORARY ***
No auth module exists in the repo yet. Real endpoints need a
`get_current_profile` dependency that returns the authenticated user's
PROFILES row. Until that PR merges, this stub fakes it by reading a
profile ID straight from a request header - good enough to build and
Postman-test your own endpoints in isolation.

DELETE THIS FILE once the real auth/session module merges, and swap
`from app.core.deps_stub import get_current_profile`
for whatever the real module exposes (likely
`from app.core.deps import get_current_profile`).

Usage while testing locally:
    curl -H "X-Debug-Profile-Id: <uuid>" http://localhost:8000/...
"""
import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, status


@dataclass
class StubProfile:
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str = "Stub User"


def get_current_profile(x_debug_profile_id: str | None = Header(default=None)) -> StubProfile:
    if not x_debug_profile_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (stub: pass X-Debug-Profile-Id header)",
        )
    try:
        pid = uuid.UUID(x_debug_profile_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid debug profile id")
    return StubProfile(id=pid, user_id=pid)