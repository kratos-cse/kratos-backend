"""FAILED local payment reconciled via provider-verified capture (not sync)."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.payments.reconciliation import ReconcileOutcome, reconcile_local_payment_state
from app.payments.razorpay_verify import VerifiedCapture

from tests.conftest import _make_event, _make_payment, _make_solo_registration, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_reconcile_failed_payment_when_provider_captured(db):
    profile = await _make_user_profile(db, email=f"recon-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Recon {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.FAILED,
    )

    verified = VerifiedCapture(
        razorpay_payment_id=f"pay_{uuid.uuid4().hex}",
        amount_paise=payment.amount_paise,
        currency="INR",
        status="captured",
    )
    with patch(
        "app.payments.razorpay_verify.verify_capture_by_payment_id",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.payments.razorpay_verify.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=verified,
    ):
        result = await reconcile_local_payment_state(db, payment, razorpay_payment_id=None)

    assert result.outcome in (ReconcileOutcome.APPLIED, ReconcileOutcome.OK)
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_reconcile_stays_failed_when_provider_not_captured(db):
    profile = await _make_user_profile(db, email=f"recon-no-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"ReconNo {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.FAILED,
    )

    with patch(
        "app.payments.razorpay_verify.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await reconcile_local_payment_state(db, payment, razorpay_payment_id=None)

    assert result.outcome == ReconcileOutcome.NOT_CAPTURED
    await db.refresh(payment)
    assert payment.status == PaymentStatus.FAILED
