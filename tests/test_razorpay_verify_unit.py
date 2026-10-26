"""Unit tests for Razorpay capture matching (no database)."""
from types import SimpleNamespace

from app.payments.razorpay_verify import payment_item_matches_local, razorpay_payment_is_captured


def test_captured_status_only():
    assert razorpay_payment_is_captured({"status": "captured"}) is True
    assert razorpay_payment_is_captured({"status": "authorized"}) is False


def test_payment_item_matches_local_order_amount_currency():
    payment = SimpleNamespace(razorpay_order_id="order_abc", amount_paise=25000, currency="INR")
    item = {
        "id": "pay_1",
        "order_id": "order_abc",
        "status": "captured",
        "amount": 25000,
        "currency": "INR",
    }
    assert payment_item_matches_local(item, payment) is True

    bad_amount = dict(item, amount=999)
    assert payment_item_matches_local(bad_amount, payment) is False
