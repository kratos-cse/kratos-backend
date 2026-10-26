"""Late capture on an older attempt uses payments.registration_id (not current pointer)."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.payments.apply import apply_payment_success
from app.payments.razorpay_verify import VerifiedCapture

from tests.conftest import _make_event, _make_payment, _make_solo_registration, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_old_failed_payment_confirms_via_registration_id_after_retry(db):
    profile = await _make_user_profile(db, email=f"late-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Late {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)

    old_payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.FAILED,
    )
    old_payment.registration_id = registration.id

    new_payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=None,
        status=PaymentStatus.CREATED,
    )
    new_payment.registration_id = registration.id
    registration.payment_id = new_payment.id
    await db.flush()

    rzp_id = f"pay_{uuid.uuid4().hex}"
    with patch(
        "app.payments.razorpay_verify.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=VerifiedCapture(
            razorpay_payment_id=rzp_id,
            amount_paise=old_payment.amount_paise,
            currency="INR",
            status="captured",
        ),
    ):
        from app.payments.reconciliation import reconcile_local_payment_state

        result = await reconcile_local_payment_state(db, old_payment, razorpay_payment_id=None)

    await db.refresh(old_payment)
    await db.refresh(registration)
    assert old_payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED
    assert result.applied is True
