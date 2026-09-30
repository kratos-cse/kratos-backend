"""Event-scoped RBAC and permission alias tests."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.permissions import admin_has_expanded_permission
from app.models.admin import (
    ADMIN_ROLE_NAME,
    EVENT_COORDINATOR_ROLE_NAME,
    SUPER_ADMIN_ROLE_NAME,
    AdminUser,
    Permission,
    Role,
)
from app.services.event_access_service import (
    has_global_permission,
    is_event_coordinator,
    is_super_admin,
    require_event_access,
)
from tests.conftest import _make_event, requires_db

pytestmark = requires_db


def _admin_with_role(role_name: str, permission_keys: list[str]) -> AdminUser:
    role = Role(id=uuid.uuid4(), name=role_name)
    role.permissions = [Permission(id=uuid.uuid4(), role_id=role.id, permission_key=k) for k in permission_keys]
    admin = AdminUser(id=uuid.uuid4(), user_id=uuid.uuid4(), role_id=role.id, is_active=True)
    admin.role = role
    return admin


def test_permission_aliases_event_management_grants_edit():
    keys = {"event-management"}
    assert admin_has_expanded_permission(keys, "event-edit")
    assert admin_has_expanded_permission(keys, "event-read")
    assert admin_has_expanded_permission(keys, "event-control")


def test_admin_role_without_event_control():
    admin = _admin_with_role(ADMIN_ROLE_NAME, ["event-read", "event-edit"])
    assert has_global_permission(admin, "event-edit")
    assert not has_global_permission(admin, "event-control")


def test_super_admin_bypass():
    admin = _admin_with_role(SUPER_ADMIN_ROLE_NAME, [])
    assert is_super_admin(admin)
    assert has_global_permission(admin, "anything")


@pytest.mark.asyncio
async def test_coordinator_denied_write(db):
    event = await _make_event(db, name="RBAC Scope Test")
    admin = _admin_with_role(EVENT_COORDINATOR_ROLE_NAME, ["event-read", "registration-read"])
    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event.id, "event-read", write=True)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_coordinator_denied_unassigned_event(db):
    from sqlalchemy.orm import selectinload

    from app.models.event_admin_assignment import EventAdminAssignment
    from app.models.user import User

    event = await _make_event(db, name="RBAC Unassigned")
    role_result = await db.execute(select(Role).where(Role.name == EVENT_COORDINATOR_ROLE_NAME))
    role = role_result.scalar_one()
    user = User(google_sub=f"admin-{uuid.uuid4().hex}", email=f"coord-{uuid.uuid4().hex}@test.edu")
    db.add(user)
    await db.flush()
    admin_row = AdminUser(user_id=user.id, role_id=role.id, is_active=True)
    db.add(admin_row)
    await db.flush()
    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role).selectinload(Role.permissions))
        .where(AdminUser.id == admin_row.id)
    )
    admin = result.scalar_one()

    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event.id, "event-read")
    assert exc.value.status_code == 403

    db.add(
        EventAdminAssignment(
            event_id=event.id,
            admin_user_id=admin.id,
            assignment_type="EVENT_COORDINATOR",
        )
    )
    await db.flush()
    await require_event_access(db, admin, event.id, "event-read")


@pytest.mark.asyncio
async def test_global_admin_sees_all_events_without_assignment(db):
    from app.services.event_access_service import scoped_event_ids

    admin = _admin_with_role(ADMIN_ROLE_NAME, ["event-read"])
    assert await scoped_event_ids(db, admin) is None


def test_is_event_coordinator_role():
    admin = _admin_with_role(EVENT_COORDINATOR_ROLE_NAME, ["event-read"])
    assert is_event_coordinator(admin)
