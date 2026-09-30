"""Event-scoped authorization for admin operations."""
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import EVENT_COORDINATOR_PERMISSIONS, admin_has_expanded_permission
from app.models.admin import (
    ADMIN_ROLE_NAME,
    EVENT_COORDINATOR_ROLE_NAME,
    SUPER_ADMIN_ROLE_NAME,
    AdminUser,
)
from app.models.event_admin_assignment import EventAdminAssignment


def admin_permission_keys(admin: AdminUser) -> set[str]:
    if not admin.role:
        return set()
    return {p.permission_key for p in admin.role.permissions}


def is_super_admin(admin: AdminUser) -> bool:
    return bool(admin.role and admin.role.name == SUPER_ADMIN_ROLE_NAME)


def is_event_coordinator(admin: AdminUser) -> bool:
    return bool(admin.role and admin.role.name == EVENT_COORDINATOR_ROLE_NAME)


def is_global_admin(admin: AdminUser) -> bool:
    return bool(admin.role and admin.role.name == ADMIN_ROLE_NAME)


def has_global_permission(admin: AdminUser, *permission_keys: str) -> bool:
    if is_super_admin(admin):
        return True
    keys = admin_permission_keys(admin)
    return admin_has_expanded_permission(keys, *permission_keys)


async def get_assigned_event_ids(db: AsyncSession, admin_user_id: uuid.UUID) -> set[uuid.UUID]:
    result = await db.execute(
        select(EventAdminAssignment.event_id).where(EventAdminAssignment.admin_user_id == admin_user_id)
    )
    return {row[0] for row in result.all()}


async def has_event_assignment(db: AsyncSession, admin_user_id: uuid.UUID, event_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(EventAdminAssignment.id).where(
            EventAdminAssignment.admin_user_id == admin_user_id,
            EventAdminAssignment.event_id == event_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def require_event_access(
    db: AsyncSession,
    admin: AdminUser,
    event_id: uuid.UUID,
    permission: str,
    *,
    write: bool = False,
) -> None:
    """Enforce event-scoped access. Raises HTTP 403 on denial."""
    if is_super_admin(admin):
        return

    if is_event_coordinator(admin):
        if write:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Event coordinators have read-only access.",
            )
        if permission not in EVENT_COORDINATOR_PERMISSIONS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        if not await has_event_assignment(db, admin.id, event_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not assigned to this event.",
            )
        return

    if not has_global_permission(admin, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action.",
        )


async def scoped_event_ids(db: AsyncSession, admin: AdminUser) -> Optional[set[uuid.UUID]]:
    """None = all events visible; set = coordinator-scoped event ids only."""
    if is_super_admin(admin) or not is_event_coordinator(admin):
        return None
    return await get_assigned_event_ids(db, admin.id)


async def assert_event_in_scope(
    db: AsyncSession,
    admin: AdminUser,
    event_id: uuid.UUID,
    permission: str,
) -> None:
    await require_event_access(db, admin, event_id, permission, write=False)


async def assert_event_write(
    db: AsyncSession,
    admin: AdminUser,
    event_id: uuid.UUID,
    permission: str,
) -> None:
    await require_event_access(db, admin, event_id, permission, write=True)
