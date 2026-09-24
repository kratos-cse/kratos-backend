"""SUPER ADMIN payment hard-delete policy."""
import uuid

import pytest
from fastapi import HTTPException

from app.models.admin import AdminUser
from app.models.enums import PaymentStatus, PaymentType
from app.models.payment import Payment
from app.payments.admin_delete import admin_delete_payment

from .conftest import _make_event, _make_payment, _make_solo_registration, _make_user_profile, requires_db


@requires_db
@pytest.mark.asyncio
async def test_admin_delete_payment_blocks_paid(db):
    profile = await _make_user_profile(db, email=f"paid-del-{uuid.uuid4().hex}@test.local")
    payment = Payment(
        id=uuid.uuid4(),
        payer_profile_id=profile.id,
        payment_type=PaymentType.SOLO_REGISTRATION,
        razorpay_order_id=f"order_{uuid.uuid4().hex}",
        amount_paise=50000,
        status=PaymentStatus.PAID,
    )
    db.add(payment)
    await db.flush()

    admin = AdminUser(id=uuid.uuid4(), user_id=uuid.uuid4(), role_id=uuid.uuid4(), is_active=True)

    with pytest.raises(HTTPException) as exc:
        await admin_delete_payment(db, payment.id, admin)
    assert exc.value.status_code == 409


@requires_db
@pytest.mark.asyncio
async def test_admin_delete_payment_clears_registration_link(db):
    profile = await _make_user_profile(db, email=f"created-del-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Delete Pay {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.CREATED,
    )

    admin = AdminUser(id=uuid.uuid4(), user_id=profile.user_id, role_id=uuid.uuid4(), is_active=True)
    result = await admin_delete_payment(db, payment.id, admin)
    assert result["deleted"] is True

    await db.refresh(registration)
    assert registration.payment_id is None
