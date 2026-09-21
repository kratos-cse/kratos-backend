"""Admin and super-admin access dependencies for GET /admin/payments and refund,
built on top of the local auth stub (app/core/deps_stub.py).

TODO(admin-rbac-owner): swap for the real require_admin / require_super_admin
once Authentication + Admin are merged and usable.
"""
from fastapi import Depends, HTTPException, status

from app.core.deps_stub import StubProfile, get_current_profile


def require_admin_profile(profile: StubProfile = Depends(get_current_profile)) -> StubProfile:
    if not profile.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return profile


def require_super_admin_profile(profile: StubProfile = Depends(get_current_profile)) -> StubProfile:
    if not profile.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    return profile
