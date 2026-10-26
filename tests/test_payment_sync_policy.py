"""Participant sync: CREATED-only; never repairs FAILED rows."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.payments.razorpay_verify import VerifiedCapture
from app.payments.sync import sync_payment_from_razorpay

from tests.conftest import _make_event, _make_payment, _make_solo_registration, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_sync_no_op_when_payment_failed_even_if_provider_captured(db):
    profile = await _make_user_profile(db, email=f"sync-fail-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"SyncFail {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.FAILED,
    )

    with patch(
        "app.payments.sync.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=VerifiedCapture(
            razorpay_payment_id="pay_captured",
            amount_paise=payment.amount_paise,
            currency="INR",
            status="captured",
        ),
    ):
        result = await sync_payment_from_razorpay(db, payment)

    assert result.status == PaymentStatus.FAILED
    await db.refresh(registration)
    assert registration.status == RegistrationStatus.PENDING


@pytest.mark.asyncio
async def test_sync_applies_when_created_and_provider_captured(db):
    profile = await _make_user_profile(db, email=f"sync-ok-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"SyncOk {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.CREATED,
    )

    with patch(
        "app.payments.sync.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=VerifiedCapture(
            razorpay_payment_id=f"pay_{uuid.uuid4().hex}",
            amount_paise=payment.amount_paise,
            currency="INR",
            status="captured",
        ),
    ):
        result = await sync_payment_from_razorpay(db, payment)

    assert result.status == PaymentStatus.PAID
    await db.refresh(registration)
    assert registration.status == RegistrationStatus.CONFIRMED
