"""Admin RBAC dependencies — real JWT user -> admin_users -> roles."""
from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.admin import SUPER_ADMIN_ROLE_NAME, AdminUser, Role
from app.models.user import User


async def get_current_active_admin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role).selectinload(Role.permissions))
        .where(AdminUser.user_id == current_user.id)
    )
    admin = result.scalar_one_or_none()
    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have active administrative privileges.",
        )
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
