"""Payment recovery edge cases (mocked Razorpay; no live API)."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import PaymentStatus, RegistrationStatus
from app.models.payment import Payment
from app.payments.razorpay_verify import VerifiedCapture, payment_item_matches_local
from app.payments.reconciliation import ReconcileOutcome, reconcile_local_payment_state
from app.payments.webhook import handle_webhook_event


def test_payment_item_rejects_amount_mismatch():
    payment = SimpleNamespace(razorpay_order_id="order_1", amount_paise=25000, currency="INR")
    item = {
        "id": "pay_1",
        "order_id": "order_1",
        "status": "captured",
        "amount": 999,
        "currency": "INR",
    }
    assert payment_item_matches_local(item, payment) is False


@pytest.mark.asyncio
async def test_duplicate_payment_captured_webhook_idempotent(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    payment.status = PaymentStatus.PAID
    await db.flush()
    rzp_id = f"pay_{uuid.uuid4().hex}"
    event = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": rzp_id,
                    "order_id": payment.razorpay_order_id,
                    "status": "captured",
                    "amount": payment.amount_paise,
                    "currency": "INR",
                }
            }
        },
    }
    with patch(
        "app.payments.webhook.verify_capture_by_payment_id",
        new_callable=AsyncMock,
    ) as mock_verify:
        mock_verify.return_value = VerifiedCapture(
            razorpay_payment_id=rzp_id,
            amount_paise=payment.amount_paise,
            currency="INR",
            status="captured",
        )
        with patch("app.payments.webhook.apply_payment_success", new_callable=AsyncMock) as mock_apply:
            from app.payments.apply import ApplyResult, PaymentRow

            mock_apply.return_value = ApplyResult(
                applied=False,
                payment=PaymentRow(
                    id=payment.id,
                    payment_type=payment.payment_type,
                    team_member_id=None,
                    payer_profile_id=payment.payer_profile_id,
                    amount_paise=payment.amount_paise,
                ),
            )
            result = await handle_webhook_event(db, event)
    assert result["applied"] is False


@pytest.mark.asyncio
async def test_order_paid_requires_verified_capture(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    event = {
        "event": "order.paid",
        "payload": {"order": {"entity": {"id": payment.razorpay_order_id}}},
    }
    with patch(
        "app.payments.webhook.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await handle_webhook_event(db, event)
    assert result["applied"] is False
    assert result.get("reason") == "no_verified_capture"


@pytest.mark.asyncio
async def test_reconcile_cancelled_registration(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    registration.status = RegistrationStatus.CANCELLED
    await db.flush()

    result = await reconcile_local_payment_state(db, payment, razorpay_payment_id=None)
    assert result.outcome == ReconcileOutcome.REGISTRATION_CANCELLED


@pytest.mark.asyncio
async def test_reconcile_duplicate_capture_outcome(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    other = Payment(
        payer_profile_id=payment.payer_profile_id,
        payment_type=payment.payment_type,
        registration_id=registration.id,
        razorpay_order_id=f"order_{uuid.uuid4().hex}",
        amount_paise=payment.amount_paise,
        currency="INR",
        status=PaymentStatus.PAID,
    )
    db.add(other)
    await db.flush()

    verified = VerifiedCapture(
        razorpay_payment_id=f"pay_{uuid.uuid4().hex}",
        amount_paise=payment.amount_paise,
        currency="INR",
        status="captured",
    )
    with patch(
        "app.payments.razorpay_verify.find_verified_capture_for_payment",
        new_callable=AsyncMock,
        return_value=verified,
    ):
        result = await reconcile_local_payment_state(db, payment, razorpay_payment_id=None)
    assert result.outcome == ReconcileOutcome.DUPLICATE_CAPTURE
