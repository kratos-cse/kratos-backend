"""Interim admin-access dependency for GET /admin/payments and the refund
endpoint.

Real admin RBAC (roles/permissions, ADMIN vs SUPER_ADMIN) is being built on
the Admin branch and is not usable yet — its app/api/dependencies.py has
get_current_user() as an unimplemented stub, so nothing built on top of it
resolves.

Per the Authentication branch's own documented convention (its README.md,
"What's deliberately NOT here"): "Real admin RBAC isn't built yet, so
'authorized admin' access checks fall back to users.is_admin_flagged... as
a stand-in until the Admin branch lands." This follows that same fallback
rather than inventing a different one.

TODO(admin-rbac-owner): swap for the real require_admin / require_super_admin
from the Admin branch once its get_current_user is implemented and merged.
There is no admin/super-admin distinction available anywhere yet — the
brief requires refund to be super-admin-only, but the only signal that
exists today is the single is_admin_flagged bit, so both checks below are
currently identical. Tighten this the moment real roles exist.
"""
from fastapi import Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.user import User


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin_flagged:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


# No separate super-admin signal exists yet — see module TODO above.
require_super_admin_user = require_admin_user
