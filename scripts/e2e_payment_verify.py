"""Razorpay TEST MODE E2E against deployed API (verify + webhook paths)."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid

import httpx

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.models.profile import Profile
from app.models.user import User

API_BASE = os.getenv("E2E_API_BASE", "https://api.kratoseec.com/api/v1")
EVENT_ID = os.getenv("E2E_EVENT_ID", "c67f2382-7acd-4b84-bc74-b79f6c1c553a")


def _sign_checkout(order_id: str, payment_id: str) -> str:
    secret = settings.RAZORPAY_KEY_SECRET
    if not secret:
        raise RuntimeError("RAZORPAY_KEY_SECRET not set")
    return hmac.new(
        secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def _sign_webhook(body: bytes) -> str:
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret:
        raise RuntimeError("RAZORPAY_WEBHOOK_SECRET not set")
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _create_user_token(db) -> tuple[Profile, str]:
    suffix = uuid.uuid4().hex[:10]
    user = User(google_sub=f"e2e-{suffix}", email=f"e2e-{suffix}@kratos-test.local")
    db.add(user)
    await db.flush()
    profile = Profile(
        user_id=user.id,
        full_name=f"E2E Tester {suffix}",
        contact_email=user.email,
        phone="9999999999",
        college_name="E2E College",
    )
    db.add(profile)
    await db.commit()
    token, _ = create_access_token(user.id)
    return profile, token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_api_payment_state(
    client: httpx.Client,
    token: str,
    payment_id: str,
    registration_id: str,
    *,
    team: bool,
) -> None:
    payment = client.get(f"{API_BASE}/payments/{payment_id}", headers=_auth_headers(token))
    payment.raise_for_status()
    assert payment.json()["status"] == "PAID"

    registration = client.get(f"{API_BASE}/registrations/{registration_id}", headers=_auth_headers(token))
    registration.raise_for_status()
    rdata = registration.json()
    assert rdata["status"] == "CONFIRMED"
    if team:
        assert rdata["team"]["status"] == "PAID"

    receipt = client.get(f"{API_BASE}/payments/{payment_id}/receipt", headers=_auth_headers(token))
    receipt.raise_for_status()

    qr = client.get(f"{API_BASE}/registrations/{registration_id}/qr", headers=_auth_headers(token))
    qr.raise_for_status()
    assert qr.json()["is_active"] is True


async def run_solo_flow(client: httpx.Client, profile: Profile, token: str) -> dict:
    print("\n=== A. Solo registration ===")
    reg = client.post(
        f"{API_BASE}/events/{EVENT_ID}/registrations",
        headers=_auth_headers(token),
        json={"registration_type": "SOLO"},
    )
    reg.raise_for_status()
    registration_id = reg.json()["id"]
    print(f"registration_id={registration_id}")

    order = client.post(
        f"{API_BASE}/payments/create-order",
        headers=_auth_headers(token),
        json={"event_id": EVENT_ID, "payment_type": "SOLO_REGISTRATION", "registration_id": registration_id},
    )
    order.raise_for_status()
    order_data = order.json()
    payment_id = order_data["paymentId"]
    order_id = order_data["razorpayOrderId"]
    print(f"payment_id={payment_id} order_id={order_id}")

    razorpay_payment_id = f"pay_e2e_solo_{uuid.uuid4().hex[:12]}"
    signature = _sign_checkout(order_id, razorpay_payment_id)
    verify = client.post(
        f"{API_BASE}/payments/verify",
        headers=_auth_headers(token),
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": signature,
        },
    )
    verify.raise_for_status()
    print("verify:", verify.json())

    payment = client.get(f"{API_BASE}/payments/{payment_id}", headers=_auth_headers(token))
    payment.raise_for_status()
    pdata = payment.json()
    assert pdata["status"] == "PAID", pdata

    registration = client.get(f"{API_BASE}/registrations/{registration_id}", headers=_auth_headers(token))
    registration.raise_for_status()
    rdata = registration.json()
    assert rdata["status"] == "CONFIRMED", rdata

    receipt = client.get(f"{API_BASE}/payments/{payment_id}/receipt", headers=_auth_headers(token))
    receipt.raise_for_status()
    print("receipt:", receipt.json()["receipt_number"])

    sync = client.post(f"{API_BASE}/payments/{payment_id}/sync", headers=_auth_headers(token))
    sync.raise_for_status()
    assert sync.json()["status"] == "PAID"

    _assert_api_payment_state(client, token, payment_id, registration_id, team=False)
    print("SOLO OK")
    return {"payment_id": payment_id, "order_id": order_id, "razorpay_payment_id": razorpay_payment_id, "token": token}


async def run_team_flow(client: httpx.Client, token: str) -> dict:
    print("\n=== B. Team registration ===")
    reg = client.post(
        f"{API_BASE}/events/{EVENT_ID}/registrations",
        headers=_auth_headers(token),
        json={"registration_type": "TEAM", "team_name": f"E2E Team {uuid.uuid4().hex[:6]}"},
    )
    reg.raise_for_status()
    reg_data = reg.json()
    registration_id = reg_data["id"]
    team_id = reg_data["team"]["id"]
    print(f"registration_id={registration_id} team_id={team_id}")

    roster = client.post(
        f"{API_BASE}/teams/{team_id}/roster",
        headers=_auth_headers(token),
        json={"role": "MEMBER", "full_name": "E2E Member", "phone": "8888888888"},
    )
    roster.raise_for_status()
    print("roster members:", roster.json()["active_member_count"])

    order = client.post(
        f"{API_BASE}/payments/create-order",
        headers=_auth_headers(token),
        json={"event_id": EVENT_ID, "payment_type": "TEAM_REGISTRATION", "registration_id": registration_id},
    )
    order.raise_for_status()
    order_data = order.json()
    payment_id = order_data["paymentId"]
    order_id = order_data["razorpayOrderId"]
    print(f"payment_id={payment_id} order_id={order_id}")

    razorpay_payment_id = f"pay_e2e_team_{uuid.uuid4().hex[:12]}"
    signature = _sign_checkout(order_id, razorpay_payment_id)
    verify = client.post(
        f"{API_BASE}/payments/verify",
        headers=_auth_headers(token),
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": signature,
        },
    )
    verify.raise_for_status()
    print("verify:", verify.json())

    registration = client.get(f"{API_BASE}/registrations/{registration_id}", headers=_auth_headers(token))
    registration.raise_for_status()
    rdata = registration.json()
    assert rdata["status"] == "CONFIRMED", rdata
    assert rdata["team"]["status"] == "PAID", rdata["team"]

    receipt = client.get(f"{API_BASE}/payments/{payment_id}/receipt", headers=_auth_headers(token))
    receipt.raise_for_status()
    print("receipt:", receipt.json()["receipt_number"])

    _assert_api_payment_state(client, token, payment_id, registration_id, team=True)
    print("TEAM OK")
    return {"payment_id": payment_id, "order_id": order_id, "razorpay_payment_id": razorpay_payment_id}


async def run_duplicate_webhook(client: httpx.Client, solo: dict) -> None:
    print("\n=== C. Duplicate webhook ===")
    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": solo["razorpay_payment_id"],
                        "order_id": solo["order_id"],
                        "status": "captured",
                    }
                }
            },
        }
    ).encode()
    sig = _sign_webhook(body)
    first = client.post(
        f"{API_BASE}/payments/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
    )
    first.raise_for_status()
    second = client.post(
        f"{API_BASE}/payments/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
    )
    second.raise_for_status()
    print("webhook first:", first.json())
    print("webhook second:", second.json())
    assert second.json().get("applied") is False
    token = solo["token"]
    receipt = client.get(
        f"{API_BASE}/payments/{solo['payment_id']}/receipt",
        headers=_auth_headers(token),
    )
    receipt.raise_for_status()
    qr = client.get(
        f"{API_BASE}/registrations",
        headers=_auth_headers(token),
    )
    qr.raise_for_status()
    print("DUPLICATE WEBHOOK OK")


async def main() -> int:
    if not settings.DATABASE_URL:
        print("DATABASE_URL required (use Railway tunnel on 127.0.0.1:15432)", file=sys.stderr)
        return 1
    if not settings.JWT_SECRET_KEY:
        print("JWT_SECRET_KEY required", file=sys.stderr)
        return 1

    health = httpx.get("https://api.kratoseec.com/health", timeout=30)
    health.raise_for_status()
    print("health:", health.json())

    async with AsyncSessionLocal() as db:
        solo_profile, solo_token = await _create_user_token(db)
        async with AsyncSessionLocal() as db2:
            _, team_token = await _create_user_token(db2)

    with httpx.Client(timeout=60) as client:
        solo = await run_solo_flow(client, solo_profile, solo_token)
        await run_team_flow(client, team_token)
        await run_duplicate_webhook(client, solo)

    print("\n=== ALL E2E CHECKS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
