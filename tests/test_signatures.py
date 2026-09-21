import hashlib
import hmac
import json

from app.payments.signatures import verify_checkout_signature, verify_webhook_signature


def _sign_checkout(order_id: str, payment_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _sign_bytes(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifyCheckoutSignature:
    key_secret = "test_key_secret"
    order_id = "order_ABC123"
    payment_id = "pay_XYZ789"

    def test_accepts_a_genuine_signature(self):
        signature = _sign_checkout(self.order_id, self.payment_id, self.key_secret)
        assert verify_checkout_signature(self.order_id, self.payment_id, signature, self.key_secret) is True

    def test_rejects_wrong_secret(self):
        signature = _sign_checkout(self.order_id, self.payment_id, "wrong_secret")
        assert verify_checkout_signature(self.order_id, self.payment_id, signature, self.key_secret) is False

    def test_rejects_tampered_payment_id(self):
        signature = _sign_checkout(self.order_id, "pay_TAMPERED", self.key_secret)
        assert verify_checkout_signature(self.order_id, self.payment_id, signature, self.key_secret) is False


class TestVerifyWebhookSignature:
    webhook_secret = "test_webhook_secret"
    raw_body = json.dumps(
        {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_1"}}}}
    ).encode()

    def test_accepts_signature_over_exact_raw_body(self):
        signature = _sign_bytes(self.raw_body, self.webhook_secret)
        assert verify_webhook_signature(self.raw_body, signature, self.webhook_secret) is True

    def test_rejects_signature_over_reparsed_reserialized_body(self):
        reparsed = json.loads(self.raw_body)
        reserialized = json.dumps({"payload": reparsed["payload"], "event": reparsed["event"]}).encode()
        signature = _sign_bytes(reserialized, self.webhook_secret)
        assert verify_webhook_signature(self.raw_body, signature, self.webhook_secret) is False
