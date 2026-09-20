"""Razorpay signature verification — two different formulas, easy to confuse.

Checkout callback (/payments/verify): HMAC-SHA256(order_id + "|" + payment_id, key_secret).
Webhook (/payments/webhook): HMAC-SHA256(raw_request_body, webhook_secret) — over the RAW,
unparsed body. Hashing after JSON parsing/re-serializing will never match.
"""
import hashlib
import hmac


def verify_checkout_signature(order_id: str, payment_id: str, signature: str, key_secret: str) -> bool:
    expected = hmac.new(
        key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
