"""Fires a signed payment.captured webhook at a locally running dev server
twice, to prove idempotency end-to-end over real HTTP (not just the unit-level
stub in tests/test_apply_idempotency.py). Also fires one with a mangled
signature.

Usage:
    RAZORPAY_WEBHOOK_SECRET=whatever_you_set_locally ORDER_ID=order_xxx \
        python scripts/simulate_webhook.py

ORDER_ID must already exist in your local `payments` table (create it via
POST /payments/create-order first) or the webhook will 200 with
"unknown order" and applied:false both times -- that is a correctly-handled
no-op, not a failure of this script.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

base = os.environ.get("BASE_URL", "http://localhost:8000")
webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
order_id = os.environ.get("ORDER_ID")

if not webhook_secret or not order_id:
    print("Set RAZORPAY_WEBHOOK_SECRET and ORDER_ID env vars first.", file=sys.stderr)
    sys.exit(1)

body = json.dumps(
    {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_sim_{int(time.time() * 1000)}",
                    "order_id": order_id,
                    "status": "captured",
                }
            }
        },
    }
).encode()


def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def post(payload: bytes, signature: str) -> None:
    req = urllib.request.Request(
        f"{base}/payments/webhook",
        data=payload,
        method="POST",
        headers={"content-type": "application/json", "x-razorpay-signature": signature},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(resp.status, resp.read().decode())
    except urllib.error.HTTPError as err:
        print(err.code, err.read().decode())


valid_signature = sign(body, webhook_secret)

print("--- 1st delivery (expect 200, applied:true) ---")
post(body, valid_signature)

print("--- 2nd delivery, same payload/signature (expect 200, applied:false) ---")
post(body, valid_signature)

print("--- 3rd delivery, tampered signature (expect 400) ---")
post(body, "deadbeef" * 8)
