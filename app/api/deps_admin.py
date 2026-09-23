"""Admin RBAC dependencies — real JWT user -> admin_users -> roles."""
import time
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.admin import SUPER_ADMIN_ROLE_NAME, AdminUser, Role
from app.models.user import User

_ADMIN_CACHE_TTL_SEC = 30.0
_admin_cache: dict[UUID, tuple[float, AdminUser]] = {}


def invalidate_admin_cache(user_id: UUID | None = None) -> None:
    if user_id is not None:
        _admin_cache.pop(user_id, None)
    else:
        _admin_cache.clear()


async def get_current_active_admin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    now = time.monotonic()
    cached = _admin_cache.get(current_user.id)
    if cached is not None:
        exp, admin_obj = cached
        if now < exp:
            return admin_obj

    admin = None
    try:
        result = await db.execute(
            select(AdminUser)
            .options(selectinload(AdminUser.role).selectinload(Role.permissions))
            .where(AdminUser.user_id == current_user.id)
        )
        admin = result.scalar_one_or_none()
    except Exception:
        admin = None

    env = (settings.ENVIRONMENT or "development").strip().lower()
    is_dev = env in ("development", "test", "testing")

    if (not admin or not admin.is_active) and is_dev and (current_user.is_admin_flagged or "admin" in str(current_user.email).lower()):
        # Dev-only fallback Super Admin
        dev_role = Role(id=current_user.id, name=SUPER_ADMIN_ROLE_NAME, description="Super Administrator", permissions=[])
        admin = AdminUser(
            id=current_user.id,
            user_id=current_user.id,
            role_id=current_user.id,
            role=dev_role,
            is_active=True,
        )

    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have active administrative privileges.",
        )
    _admin_cache[current_user.id] = (now + _ADMIN_CACHE_TTL_SEC, admin)
    return admin


async def require_super_admin(
    current_admin: AdminUser = Depends(get_current_active_admin),
) -> AdminUser:
    if not current_admin.role or current_admin.role.name != SUPER_ADMIN_ROLE_NAME:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires SUPER ADMIN privileges.",
        )
    return current_admin


async def require_active_admin_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Any active admin (used by payment lookup / team admin access)."""
    return await get_current_active_admin(current_user=current_user, db=db)


def admin_permission_keys(admin: AdminUser) -> set[str]:
    if not admin.role:
        return set()
    return {p.permission_key for p in admin.role.permissions}


def is_super_admin(admin: AdminUser) -> bool:
    return bool(admin.role and admin.role.name == SUPER_ADMIN_ROLE_NAME)


def admin_has_permission(admin: AdminUser, *permission_keys: str) -> bool:
    if is_super_admin(admin):
        return True
    keys = admin_permission_keys(admin)
    return any(k in keys for k in permission_keys)


def require_permission(*permission_keys: str):
    """Super Admin or any listed permission_key on the admin role."""

    async def _dependency(
        current_admin: AdminUser = Depends(get_current_active_admin),
    ) -> AdminUser:
        if admin_has_permission(current_admin, *permission_keys):
            return current_admin
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )

    return _dependency
