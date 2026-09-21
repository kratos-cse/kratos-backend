"""
*** TEMPORARY ***
No auth module exists on this branch — real endpoints need a
`get_current_profile` dependency that returns the authenticated caller's
PROFILES row. Until the Authentication branch's real one merges, this stub
fakes it by reading a profile id straight from a request header — same
approach teams/app/core/deps_stub.py already uses for the identical
problem on that branch.

DELETE THIS FILE once the real auth/session module merges, and swap
`from app.core.deps_stub import get_current_profile`
for whatever the real module exposes.

Usage while testing locally:
    curl -H "X-Debug-Profile-Id: <uuid>" http://localhost:8000/...

Admin/super-admin gating (GET /admin/payments, refund) has no real RBAC to
depend on either (the Admin branch's get_current_user is itself an
unimplemented stub) — extended here with a second debug header rather than
guessing at admin_users/roles. TODO(admin-rbac-owner): swap both for the
real dependencies once Authentication and Admin land.
"""
import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, status


@dataclass
class StubProfile:
    id: uuid.UUID
    user_id: uuid.UUID
    is_admin: bool = False


def get_current_profile(
    x_debug_profile_id: str | None = Header(default=None),
    x_debug_is_admin: str | None = Header(default=None),
) -> StubProfile:
    if not x_debug_profile_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (stub: pass X-Debug-Profile-Id header)",
        )
    try:
        pid = uuid.UUID(x_debug_profile_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid debug profile id")
    return StubProfile(id=pid, user_id=pid, is_admin=(x_debug_is_admin or "").lower() == "true")
