# Payments & Refunds Module (KRATOS'26)

FastAPI module for Payments & Refunds for KRATOS'26, integrated with Razorpay (test mode) and PostgreSQL (SQLAlchemy).

## Architecture Highlights
- **Fully Decoupled**: Zero hard dependencies on unmerged branches. Uses minimal external model mirrors (`app/models/external_mirrors.py`) for events, registrations, and teams.
- **Server-Side Pricing**: Prices are calculated from database records (`app/services/amounts.py`). Client-supplied amounts are never accepted.
- **Strict HMAC SHA-256 Signature Verification**: Validates Razorpay signatures for checkout callback and raw-byte webhook payloads (`app/services/signatures.py`) using constant-time comparison (`hmac.compare_digest`).
- **Single Idempotent State Machine**: Shared by `/verify` and `/webhook` (`app/services/payment_apply.py`) to prevent double-processing and prevent resurrection of refunded payments.
- **Narrow Retry with Exponential Backoff**: `POST /payments/create-order`'s Razorpay call retries only on responses that prove Razorpay rejected the request (5xx, or a 429 rate limit) — never on a timeout or other ambiguous failure, to avoid risking a duplicate order (`app/services/razorpay_client.py`). Refunds are never retried automatically (a duplicate refund is worse than a failed one).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows (or source .venv/bin/activate on Unix)
pip install -r requirements-dev.txt
cp .env.example .env
```

Apply database migration:
```bash
psql -d kratos -f db/migrations/0001_payments_module.sql
```

## Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/payments/create-order` | Profile Header (`X-Debug-Profile-Id`) | Create Razorpay order with server-calculated amounts |
| `POST` | `/payments/verify` | Profile Header (Owner) | Verify checkout signature & apply payment |
| `POST` | `/payments/webhook` | `X-Razorpay-Signature` (Raw HMAC) | Razorpay webhook handler |
| `GET` | `/payments/{payment_id}` | Profile Header (Owner or Admin) | Fetch payment record |
| `GET` | `/admin/payments` | Admin Header (`X-Debug-Is-Admin: true`) | Search/list payments |
| `POST` | `/admin/payments/{payment_id}/refund` | Super Admin Header (`X-Debug-Is-Super-Admin: true`) | Initiate refund via Razorpay |

All three debug headers (`X-Debug-Profile-Id`, `X-Debug-Is-Admin`, `X-Debug-Is-Super-Admin`)
only work when `ENVIRONMENT` is `development`/`test`/`local` — see
`app/core/deps_stub.py`. **This whole auth layer is a stand-in** for the
Authentication branch's real JWT/session auth and the Admin branch's real
RBAC, neither of which is usable yet; swap it out once they land.

## Testing

Run unit tests:
```bash
pytest tests/ -v
```

Simulate webhooks:
```bash
RAZORPAY_WEBHOOK_SECRET=your_secret ORDER_ID=order_xxx python scripts/simulate_webhook.py
```
