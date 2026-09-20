"""Resolves the caller's identity from the bearer token on the request. Never
trust a profile/user id passed in the request body — this is the only
legitimate source of "who is calling" for every route in this module.
"""
from typing import Optional

from fastapi import Header, HTTPException

from .supabase_client import get_db


def get_caller_profile_id(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer "):]

    try:
        result = get_db().auth.get_user(token)
    except Exception:  # noqa: BLE001 - any auth-client failure means an invalid session
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = getattr(result, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user.id


# TODO(admin-rbac-owner): admin/super-admin roles are owned by the admin RBAC track,
# not this module. Assumed shape here: a `profiles.role` column with 'SUPER_ADMIN' /
# 'ADMIN' values. Confirm the actual table/column before trusting this in
# production — never fall back to trusting a client-supplied role claim.
def get_caller_role(profile_id: str) -> Optional[str]:
    result = get_db().table("profiles").select("role").eq("id", profile_id).single().execute()
    data = getattr(result, "data", None)
    return data.get("role") if data else None


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    profile_id = get_caller_profile_id(authorization)
    role = get_caller_role(profile_id)
    if role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return profile_id


def require_super_admin(authorization: Optional[str] = Header(None)) -> str:
    profile_id = get_caller_profile_id(authorization)
    role = get_caller_role(profile_id)
    if role != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return profile_id
