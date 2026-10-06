"""Event coordinator scoped read access and write denial."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.admin import EVENT_COORDINATOR_ROLE_NAME, AdminUser
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.event_admin_assignment import EventAdminAssignment
from app.models.user import User
from app.services.admin_ops_service import dashboard_payload, search_participant_profiles
from app.services.event_access_service import require_event_access, scoped_event_ids
from tests.test_event_scoped_rbac import _admin_with_role

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db


async def _coordinator_admin(db, *, event_ids: list[uuid.UUID]) -> AdminUser:
    from app.models.admin import Role
    from sqlalchemy.orm import selectinload

    role_result = await db.execute(select(Role).where(Role.name == EVENT_COORDINATOR_ROLE_NAME))
    role = role_result.scalar_one()
    user = User(google_sub=f"coord-{uuid.uuid4().hex}", email=f"coord-{uuid.uuid4().hex}@test.edu")
    db.add(user)
    await db.flush()
    admin_row = AdminUser(user_id=user.id, role_id=role.id, is_active=True)
    db.add(admin_row)
    await db.flush()
    for eid in event_ids:
        db.add(
            EventAdminAssignment(
                event_id=eid,
                admin_user_id=admin_row.id,
                assignment_type="EVENT_COORDINATOR",
            )
        )
    await db.flush()
    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role).selectinload(Role.permissions))
        .where(AdminUser.id == admin_row.id)
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_coordinator_zero_assignments_empty_dashboard(db):
    admin = await _coordinator_admin(db, event_ids=[])
    scoped = await scoped_event_ids(db, admin)
    assert scoped == set()
    payload = await dashboard_payload(db, scoped_event_ids=scoped)
    assert payload["events_total"] == 0
    assert payload["paid_revenue_paise"] == 0
    assert payload["recent_registrations"] == []
    assert payload["recent_payments"] == []


@pytest.mark.asyncio
async def test_coordinator_sees_only_assigned_event_registrations(db):
    event_a = await _make_event(db, name=f"Coord A {uuid.uuid4().hex[:6]}")
    event_b = await _make_event(db, name=f"Coord B {uuid.uuid4().hex[:6]}")
    profile_a = await _make_user_profile(db, email=f"a-{uuid.uuid4().hex}@t.l")
    profile_b = await _make_user_profile(db, email=f"b-{uuid.uuid4().hex}@t.l")
    await _make_solo_registration(db, event=event_a, profile=profile_a)
    await _make_solo_registration(db, event=event_b, profile=profile_b)

    admin = await _coordinator_admin(db, event_ids=[event_a.id])
    scoped = await scoped_event_ids(db, admin)
    profiles, total = await search_participant_profiles(
        db, q=None, event_id=None, scoped_event_ids=scoped, skip=0, limit=50
    )
    assert total == 1
    assert profiles[0].id == profile_a.id

    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event_b.id, "event-read")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_coordinator_dashboard_scoped_payments(db):
    event_a = await _make_event(db, name=f"Pay A {uuid.uuid4().hex[:6]}")
    event_b = await _make_event(db, name=f"Pay B {uuid.uuid4().hex[:6]}")
    p_a = await _make_user_profile(db, email=f"pa-{uuid.uuid4().hex}@t.l")
    p_b = await _make_user_profile(db, email=f"pb-{uuid.uuid4().hex}@t.l")
    reg_a = await _make_solo_registration(db, event=event_a, profile=p_a)
    reg_b = await _make_solo_registration(db, event=event_b, profile=p_b)
    pay_a = await _make_payment(
        db,
        payer=p_a,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=reg_a,
        status=PaymentStatus.PAID,
    )
    await _make_payment(
        db,
        payer=p_b,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=reg_b,
        status=PaymentStatus.PAID,
    )
    reg_a.status = RegistrationStatus.CONFIRMED
    await db.flush()

    admin = await _coordinator_admin(db, event_ids=[event_a.id])
    scoped = await scoped_event_ids(db, admin)
    dash = await dashboard_payload(db, scoped_event_ids=scoped)
    assert dash["paid_revenue_paise"] == pay_a.amount_paise
    assert dash["payments_by_status"].get("PAID", 0) == 1


@pytest.mark.asyncio
async def test_coordinator_write_denied(db):
    event = await _make_event(db, name=f"Write {uuid.uuid4().hex[:6]}")
    admin = await _coordinator_admin(db, event_ids=[event.id])
    with pytest.raises(HTTPException) as exc:
        await require_event_access(db, admin, event.id, "event-read", write=True)
    assert exc.value.status_code == 403
