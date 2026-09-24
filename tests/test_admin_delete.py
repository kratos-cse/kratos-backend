"""SUPER ADMIN hard-delete policies."""
import uuid

import pytest
from fastapi import HTTPException

from app.models.admin import AdminUser
from app.models.enums import PaymentStatus, PaymentType
from app.services.admin_delete_service import admin_delete_registration

from .conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)


@requires_db
@pytest.mark.asyncio
async def test_delete_registration_blocks_paid_payment(db):
    profile = await _make_user_profile(db, email=f"reg-del-paid-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Reg Del {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.PAID,
    )

    admin = AdminUser(id=uuid.uuid4(), user_id=profile.user_id, role_id=uuid.uuid4(), is_active=True)
    with pytest.raises(HTTPException) as exc:
        await admin_delete_registration(db, registration.id, admin)
    assert exc.value.status_code == 409


@requires_db
@pytest.mark.asyncio
async def test_delete_registration_removes_created_payment(db):
    profile = await _make_user_profile(db, email=f"reg-del-ok-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Reg Del OK {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.CREATED,
    )

    admin = AdminUser(id=uuid.uuid4(), user_id=profile.user_id, role_id=uuid.uuid4(), is_active=True)
    result = await admin_delete_registration(db, registration.id, admin)
    assert result["deleted"] is True

    from sqlalchemy import select

    from app.models.payment import Payment
    from app.models.registration import Registration

    gone_reg = await db.execute(select(Registration).where(Registration.id == registration.id))
    assert gone_reg.scalar_one_or_none() is None
    gone_pay = await db.execute(select(Payment).where(Payment.id == payment.id))
    assert gone_pay.scalar_one_or_none() is None


@requires_db
@pytest.mark.asyncio
async def test_delete_team_registration_cascades_team(db):
    from sqlalchemy import select

    from app.models.registration import Registration
    from app.models.team import Team

    leader = await _make_user_profile(db, email=f"team-del-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Team Del {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)

    admin = AdminUser(id=uuid.uuid4(), user_id=leader.user_id, role_id=uuid.uuid4(), is_active=True)
    result = await admin_delete_registration(db, registration.id, admin)
    assert result["deleted"] is True

    gone_reg = await db.execute(select(Registration).where(Registration.id == registration.id))
    assert gone_reg.scalar_one_or_none() is None
    gone_team = await db.execute(select(Team).where(Team.id == team.id))
    assert gone_team.scalar_one_or_none() is None
