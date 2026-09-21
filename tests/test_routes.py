import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.enums import PaymentStatus

client = TestClient(app)


def test_debug_auth_disabled_outside_local_env(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    resp = client.post(
        "/payments/create-order",
        json={"registration_id": str(uuid.uuid4())},
        headers={"X-Debug-Profile-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 501
    assert "disabled outside development/test" in resp.json()["detail"]


def test_verify_fails_with_503_when_key_secret_missing(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "")
    resp = client.post(
        "/payments/verify",
        json={
            "razorpay_order_id": "order_1",
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": "sig",
        },
        headers={"X-Debug-Profile-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 503
    assert "missing key secret" in resp.json()["detail"]


def test_webhook_fails_with_503_when_webhook_secret_missing(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
    resp = client.post(
        "/payments/webhook",
        content=b'{"event":"test"}',
        headers={"X-Razorpay-Signature": "sig"},
    )
    assert resp.status_code == 503
    assert "missing webhook secret" in resp.json()["detail"]


def test_refund_rejects_regular_admin_without_super_admin_header():
    resp = client.post(
        f"/admin/payments/{uuid.uuid4()}/refund",
        json={"reason": "test"},
        headers={"X-Debug-Profile-Id": str(uuid.uuid4()), "X-Debug-Is-Admin": "true"},
    )
    # is_admin is true, but is_super_admin is false -> 403 Super admin access required
    assert resp.status_code == 403
    assert "Super admin access required" in resp.json()["detail"]
