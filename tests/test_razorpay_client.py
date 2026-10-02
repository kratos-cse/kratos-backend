import requests

from app.payments.razorpay_client import _is_retryable


def test_connection_error_is_retryable():
    assert _is_retryable(requests.ConnectionError("boom"))


def test_timeout_is_retryable():
    assert _is_retryable(requests.Timeout("slow"))


def test_value_error_not_retryable():
    assert not _is_retryable(ValueError("nope"))
