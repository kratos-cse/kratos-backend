"""SUPER ADMIN admin-user hard delete."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.admin import SUPER_ADMIN_ROLE_NAME, AdminUser, Role
from app.services.admin_delete_service import admin_delete_admin_user

from .conftest import _make_user_profile, requires_db


@requires_db
@pytest.mark.asyncio
async def test_admin_delete_user_blocks_self(db):
    profile = await _make_user_profile(db, email=f"self-del-{uuid.uuid4().hex}@test.local")
    role_result = await db.execute(select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME))
    role = role_result.scalar_one()
    actor = AdminUser(id=uuid.uuid4(), user_id=profile.user_id, role_id=role.id, is_active=True)
    db.add(actor)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await admin_delete_admin_user(db, actor.id, actor)
    assert exc.value.status_code == 400


@requires_db
@pytest.mark.asyncio
async def test_admin_delete_user_removes_other_admin(db):
    profile = await _make_user_profile(db, email=f"actor-{uuid.uuid4().hex}@test.local")
    other = await _make_user_profile(db, email=f"target-{uuid.uuid4().hex}@test.local")
    role_result = await db.execute(select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME))
    role = role_result.scalar_one()

    actor = AdminUser(id=uuid.uuid4(), user_id=profile.user_id, role_id=role.id, is_active=True)
    target = AdminUser(id=uuid.uuid4(), user_id=other.user_id, role_id=role.id, is_active=True)
    db.add(actor)
    db.add(target)
    await db.flush()

    result = await admin_delete_admin_user(db, target.id, actor)
    assert result["deleted"] is True

    gone = await db.execute(select(AdminUser).where(AdminUser.id == target.id))
    assert gone.scalar_one_or_none() is None
