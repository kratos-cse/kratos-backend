"""Razorpay signature verification — two different formulas, easy to confuse.

Checkout callback (/payments/verify): HMAC-SHA256(order_id + "|" + payment_id, key_secret).
Webhook (/payments/webhook): HMAC-SHA256(raw_request_body, webhook_secret) — over the RAW,
unparsed body. Hashing after JSON parsing/re-serializing will never match.

Fails closed if keys, secrets, or signatures are empty or missing to prevent
empty-HMAC forgery.
"""
import hashlib
import hmac


def verify_checkout_signature(order_id: str, payment_id: str, signature: str, key_secret: str) -> bool:
    if not key_secret or not key_secret.strip():
        return False
    if not signature or not signature.strip():
        return False
    if not order_id or not payment_id:
        return False

    expected = hmac.new(
        key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    if not webhook_secret or not webhook_secret.strip():
        return False
    if not signature or not signature.strip():
        return False
    if not raw_body:
        return False

    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
