"""Auth dependencies must not reuse detached ORM instances across requests."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps_admin import get_current_active_admin
from app.core.config import settings
from app.core.security import create_access_token, revoke_token
from app.main import app
from app.models.admin import ADMIN_ROLE_NAME

from tests.conftest import _make_event, _make_team_registration, _make_user_profile, requires_db
from tests.test_event_scoped_rbac import _admin_with_role

pytestmark = requires_db

client = TestClient(app)


def test_auth_me_without_token_returns_401():
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_auth_me_invalid_token_returns_401():
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_auth_me_expired_token_returns_401():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": past,
        "exp": past + timedelta(minutes=5),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sequential_auth_me_same_token(db):
    profile = await _make_user_profile(db, email=f"seq-{uuid.uuid4().hex}@auth.test")
    await db.commit()

    token, _ = create_access_token(profile.user_id)
    headers = {"Authorization": f"Bearer {token}"}

    r1 = client.get("/api/v1/auth/me", headers=headers)
    r2 = client.get("/api/v1/auth/me", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["user"]["id"] == r2.json()["user"]["id"]


@pytest.mark.asyncio
async def test_create_order_reaches_handler_with_valid_auth(db):
    leader = await _make_user_profile(db, email=f"pay-{uuid.uuid4().hex}@auth.test")
    event = await _make_event(db, name=f"Pay Auth {uuid.uuid4().hex[:6]}", team=True)
    _team, registration = await _make_team_registration(db, event=event, leader=leader)
    await db.commit()

    token, _ = create_access_token(leader.user_id)
    headers = {"Authorization": f"Bearer {token}"}

    async def _noop_sync(_db, payment):
        return payment

    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock(
            return_value={"id": "order_test", "amount": 25000, "currency": "INR"}
        )
        with patch("app.api.v1.endpoints.payments.sync_payment_from_razorpay", _noop_sync):
            resp = client.post(
                "/api/v1/payments/create-order",
                json={
                    "event_id": str(event.id),
                    "payment_type": "TEAM_REGISTRATION",
                    "registration_id": str(registration.id),
                },
                headers=headers,
            )

    assert resp.status_code == 200, resp.text
    assert "orderId" in resp.json() or "paymentId" in resp.json()


@pytest.mark.asyncio
async def test_revoked_token_returns_401(db):
    profile = await _make_user_profile(db, email=f"rev-{uuid.uuid4().hex}@auth.test")
    await db.commit()

    token, _ = create_access_token(profile.user_id)
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    revoke_token(payload["jti"])

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_admin_me_with_dependency_override():
    app.dependency_overrides[get_current_active_admin] = lambda: _admin_with_role(
        ADMIN_ROLE_NAME, ["event-read", "dashboard-read"]
    )
    try:
        resp = client.get("/api/v1/admin/me", headers={"Authorization": "Bearer override"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "success"
    finally:
        app.dependency_overrides.clear()
