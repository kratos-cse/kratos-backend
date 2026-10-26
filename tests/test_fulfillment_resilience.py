"""Fulfillment failures must not imply payment rollback."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.payments.fulfillment import run_post_payment_fulfillment


@pytest.mark.asyncio
async def test_notification_failure_returns_false_not_raise(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]

    with patch(
        "app.payments.fulfillment.ensure_qr_for_registration",
        new_callable=AsyncMock,
    ), patch(
        "app.payments.fulfillment.ensure_receipt_safe",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.payments.fulfillment.notify_payment_confirmed_safe",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await run_post_payment_fulfillment(
            db, payment=payment, registration=registration, send_notification=True
        )

    assert result["notification"] is False
    assert result["receipt"] is True
