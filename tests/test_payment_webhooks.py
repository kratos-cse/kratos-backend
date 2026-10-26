"""Webhook event handling and idempotency."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import PaymentStatus, PaymentType
from app.payments.apply import ApplyResult, PaymentRow
from app.payments.webhook import handle_webhook_event

from tests.conftest import _make_event, _make_payment, _make_solo_registration, _make_user_profile, requires_db

pytestmark = requires_db


def _captured_event(order_id: str, payment_id: str, amount: int = 25000):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "status": "captured",
                    "amount": amount,
                    "currency": "INR",
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_payment_failed_does_not_downgrade_paid(db):
    profile = await _make_user_profile(db, email=f"wh-fail-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"WH {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.PAID,
    )

    body = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_fail",
                    "order_id": payment.razorpay_order_id,
                    "error_code": "BAD",
                    "error_description": "failed",
                }
            }
        },
    }
    result = await handle_webhook_event(db, body)
    assert result["applied"] is False
    await db.refresh(payment)
    assert payment.status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_payment_captured_webhook_applies(db):
    profile = await _make_user_profile(db, email=f"wh-cap-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"WHCap {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.CREATED,
    )
    rzp_id = f"pay_{uuid.uuid4().hex}"

    with patch(
        "app.payments.webhook.verify_capture_by_payment_id",
        new_callable=AsyncMock,
    ) as mock_verify:
        from app.payments.razorpay_verify import VerifiedCapture

        mock_verify.return_value = VerifiedCapture(
            razorpay_payment_id=rzp_id,
            amount_paise=payment.amount_paise,
            currency="INR",
            status="captured",
        )
        with patch(
            "app.payments.webhook.apply_payment_success",
            new_callable=AsyncMock,
            return_value=ApplyResult(
                applied=True,
                payment=PaymentRow(
                    id=payment.id,
                    payment_type=payment.payment_type,
                    team_member_id=None,
                    payer_profile_id=payment.payer_profile_id,
                    amount_paise=payment.amount_paise,
                ),
            ),
        ) as mock_apply:
            result = await handle_webhook_event(
                db, _captured_event(payment.razorpay_order_id, rzp_id, payment.amount_paise)
            )

    assert result["applied"] is True
    mock_apply.assert_awaited_once()
