"""Admin-access dependency for GET /admin/payments and the refund endpoint,
built on top of the local auth stub (app/core/deps_stub.py) — see that
file's module docstring for why a stub exists at all on this branch.

There is no admin/super-admin distinction available anywhere yet (the
Admin branch's real RBAC isn't usable — its get_current_user is itself an
unimplemented stub), so both checks below are currently identical.
TODO(admin-rbac-owner): swap for the real require_admin / require_super_admin
once Authentication + Admin are merged and usable.
"""
from fastapi import Depends, HTTPException, status

from app.core.deps_stub import StubProfile, get_current_profile


def require_admin_profile(profile: StubProfile = Depends(get_current_profile)) -> StubProfile:
    if not profile.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return profile


# No separate super-admin signal exists yet — see module TODO above.
require_super_admin_profile = require_admin_profile
