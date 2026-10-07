"""EVENT COORDINATOR assignment via admin-user create/update + scoped access."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.admin import (
    ADMIN_ROLE_NAME,
    EVENT_COORDINATOR_ROLE_NAME,
    SUPER_ADMIN_ROLE_NAME,
    AdminUser,
    Role,
)
from app.models.event_admin_assignment import EventAdminAssignment
from app.models.user import User
from app.services import event_assignment_service as assignment_svc
from app.services.event_access_service import (
    get_assigned_event_ids,
    require_event_access,
    scoped_event_ids,
)
from app.services.admin_ops_service import search_participant_profiles
from tests.conftest import _make_event, _make_solo_registration, _make_user_profile, requires_db
from tests.test_event_scoped_rbac import _admin_with_role

pytestmark = requires_db


async def _role(db, name: str) -> Role:
    result = await db.execute(select(Role).where(Role.name == name))
    return result.scalar_one()


async def _make_admin(db, *, role_name: str, email: str | None = None) -> AdminUser:
    role = await _role(db, role_name)
    user = User(
        google_sub=f"admin-{uuid.uuid4().hex}",
        email=email or f"admin-{uuid.uuid4().hex}@test.edu",
    )
    db.add(user)
    await db.flush()
    admin = AdminUser(user_id=user.id, role_id=role.id, is_active=True)
    db.add(admin)
    await db.flush()
    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role).selectinload(Role.permissions))
        .where(AdminUser.id == admin.id)
    )
    return result.scalar_one()


async def _reload_admin(db, admin_id: uuid.UUID) -> AdminUser:
    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role).selectinload(Role.permissions))
        .where(AdminUser.id == admin_id)
    )
    return result.scalar_one()


async def _assignment_count(db, admin_user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(EventAdminAssignment)
        .where(EventAdminAssignment.admin_user_id == admin_user_id)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_coordinator_no_assignments_empty_scope(db):
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    assert await scoped_event_ids(db, admin) == set()
    assert await get_assigned_event_ids(db, admin.id) == set()


@pytest.mark.asyncio
async def test_reconcile_assign_single_event(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event_a = await _make_event(db, name=f"A {uuid.uuid4().hex[:6]}")
    event_b = await _make_event(db, name=f"B {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)

    assigned = await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event_a.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    assert {e["id"] for e in assigned} == {event_a.id}
    admin = await _reload_admin(db, admin.id)
    scoped = await scoped_event_ids(db, admin)
    assert scoped == {event_a.id}
    await require_event_access(db, admin, event_a.id, "event-read")
    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event_b.id, "event-read")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reconcile_assign_two_events(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event_a = await _make_event(db, name=f"A {uuid.uuid4().hex[:6]}")
    event_b = await _make_event(db, name=f"B {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)

    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event_a.id, event_b.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    admin = await _reload_admin(db, admin.id)
    assert await scoped_event_ids(db, admin) == {event_a.id, event_b.id}


@pytest.mark.asyncio
async def test_coordinator_registrations_scoped(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event_a = await _make_event(db, name=f"Reg A {uuid.uuid4().hex[:6]}")
    event_b = await _make_event(db, name=f"Reg B {uuid.uuid4().hex[:6]}")
    p_a = await _make_user_profile(db, email=f"a-{uuid.uuid4().hex}@t.l")
    p_b = await _make_user_profile(db, email=f"b-{uuid.uuid4().hex}@t.l")
    await _make_solo_registration(db, event=event_a, profile=p_a)
    await _make_solo_registration(db, event=event_b, profile=p_b)

    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event_a.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    admin = await _reload_admin(db, admin.id)
    scoped = await scoped_event_ids(db, admin)
    profiles, total = await search_participant_profiles(
        db, q=None, event_id=None, scoped_event_ids=scoped, skip=0, limit=50
    )
    assert total == 1
    assert profiles[0].id == p_a.id


@pytest.mark.asyncio
async def test_coordinator_cannot_mutate_assigned_event(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event = await _make_event(db, name=f"Mut {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    admin = await _reload_admin(db, admin.id)
    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event.id, "event-edit", write=True)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc2:
        await require_event_access(db, admin, event.id, "registration-edit", write=True)
    assert exc2.value.status_code == 403


@pytest.mark.asyncio
async def test_global_admin_and_super_admin_unscoped(db):
    admin = _admin_with_role(ADMIN_ROLE_NAME, ["event-read"])
    assert await scoped_event_ids(db, admin) is None
    super_admin = _admin_with_role(SUPER_ADMIN_ROLE_NAME, [])
    assert await scoped_event_ids(db, super_admin) is None


@pytest.mark.asyncio
async def test_reconcile_ab_to_ac(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event_a = await _make_event(db, name=f"A {uuid.uuid4().hex[:6]}")
    event_b = await _make_event(db, name=f"B {uuid.uuid4().hex[:6]}")
    event_c = await _make_event(db, name=f"C {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)

    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event_a.id, event_b.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event_a.id, event_c.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    ids = await get_assigned_event_ids(db, admin.id)
    assert ids == {event_a.id, event_c.id}
    assert await _assignment_count(db, admin.id) == 2


@pytest.mark.asyncio
async def test_leave_coordinator_clears_assignments(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event = await _make_event(db, name=f"Leave {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    assert await _assignment_count(db, admin.id) == 1

    admin_role = await _role(db, ADMIN_ROLE_NAME)
    admin.role_id = admin_role.id
    await db.flush()
    admin.role = admin_role

    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        None,
        actor.id,
        role_name=ADMIN_ROLE_NAME,
        apply_event_ids=True,
    )
    assert await _assignment_count(db, admin.id) == 0
    admin = await _reload_admin(db, admin.id)
    assert await scoped_event_ids(db, admin) is None


@pytest.mark.asyncio
async def test_no_duplicate_assignments(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event = await _make_event(db, name=f"Dup {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    for _ in range(2):
        await assignment_svc.reconcile_coordinator_assignments(
            db,
            admin.id,
            [event.id],
            actor.id,
            role_name=EVENT_COORDINATOR_ROLE_NAME,
            apply_event_ids=True,
        )
    assert await _assignment_count(db, admin.id) == 1


@pytest.mark.asyncio
async def test_delete_assignment_removes_access(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event = await _make_event(db, name=f"Del {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    rows = await assignment_svc.list_assignments_for_admin(db, admin.id)
    await assignment_svc.delete_event_assignment(db, event.id, rows[0].id)
    admin = await _reload_admin(db, admin.id)
    assert await scoped_event_ids(db, admin) == set()
    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event.id, "event-read")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_assigned_event_summaries_include_names(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event = await _make_event(db, name="Box Cricket '26")
    admin = await _make_admin(db, role_name=EVENT_COORDINATOR_ROLE_NAME)
    await assignment_svc.reconcile_coordinator_assignments(
        db,
        admin.id,
        [event.id],
        actor.id,
        role_name=EVENT_COORDINATOR_ROLE_NAME,
        apply_event_ids=True,
    )
    summaries = await assignment_svc.get_assigned_event_summaries(db, [admin.id])
    assert summaries[admin.id] == [{"id": event.id, "name": "Box Cricket '26"}]


@pytest.mark.asyncio
async def test_event_ids_rejected_for_non_coordinator(db):
    actor = await _make_admin(db, role_name=SUPER_ADMIN_ROLE_NAME)
    event = await _make_event(db, name=f"No {uuid.uuid4().hex[:6]}")
    admin = await _make_admin(db, role_name=ADMIN_ROLE_NAME)
    with pytest.raises(HTTPException) as exc:
        await assignment_svc.reconcile_coordinator_assignments(
            db,
            admin.id,
            [event.id],
            actor.id,
            role_name=ADMIN_ROLE_NAME,
            apply_event_ids=True,
        )
    assert exc.value.status_code == 400
