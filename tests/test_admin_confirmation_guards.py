"""Admin must not treat PATCH CONFIRMED as payment success."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.payments.admin_offline import OFFLINE_ORDER_PREFIX, assert_recoverable_razorpay_payment


def test_offline_order_rejected_for_razorpay_recovery():
    payment = SimpleNamespace(razorpay_order_id=f"{OFFLINE_ORDER_PREFIX}abc123")
    with pytest.raises(HTTPException) as exc:
        assert_recoverable_razorpay_payment(payment)
    assert exc.value.status_code == 400


def test_razorpay_order_allowed_for_recovery():
    payment = SimpleNamespace(razorpay_order_id="order_TestCapture123")
    assert_recoverable_razorpay_payment(payment)
