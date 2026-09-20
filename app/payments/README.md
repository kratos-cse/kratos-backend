# Payments & Refunds module

Owns the `payments` and `receipts` tables and the Razorpay integration for KRATOS'26. No
frontend/UI here — that track is owned elsewhere; this is the FastAPI backend slice.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # or: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env         # fill in Supabase + Razorpay TEST MODE credentials, if not present
```

Apply `supabase/migrations/0001_payments.sql` to your Supabase project. It creates `payments`
and `receipts` with RLS enabled; all writes in this module go through the service-role client
(`app/payments/supabase_client.py`), which bypasses RLS by design.

## Endpoints

| Route | Auth |
|---|---|
| `POST /payments/create-order` | Supabase session |
| `POST /payments/verify` | Supabase session, row ownership |
| `POST /payments/webhook` | Razorpay signature only |
| `GET /payments/{payment_id}` | owner or admin |
| `GET /admin/payments` | admin |
| `POST /admin/payments/{payment_id}/refund` | super admin |

The core logic (idempotent state transitions) lives in `app/payments/apply.py` and is shared
by `/verify` and `/webhook` — there is exactly one place payment status changes.

## Testing

```bash
pytest tests/ -v
```

`scripts/simulate_webhook.py` fires a signed `payment.captured` webhook at a running dev server
twice (proving idempotency over real HTTP) and once with a bad signature:

```bash
uvicorn app.main:app --reload
RAZORPAY_WEBHOOK_SECRET=... ORDER_ID=... python scripts/simulate_webhook.py
```

## Open dependencies — resolve before trusting this in production

Each has a `TODO(...)` comment at the exact line it matters:

1. **`app/payments/refund.py` — `REFUND_LINKED_STATES`.** The exact `registrations.status` /
   `team_members.status` / `teams.status` values after a refund need to come from whoever owns
   registrations/teams (brief §6.3). Current values are placeholders.
2. **`app/payments/apply.py` — `confirm_team_registration`.** There's no documented FK from
   `payments` to `teams` for a `TEAM_REGISTRATION` payment. Implemented against an assumed
   `registrations.team_id` / `registrations.profile_id` shape — confirm with the
   registrations/teams track.
3. **`app/payments/amounts.py` — unit of `events.fee`.** Assumed to be a rupee amount; confirm
   with whoever owns the `events` table.
4. **`app/payments/auth.py` — where admin/super-admin role lives.** Assumed `profiles.role`;
   confirm with the admin RBAC track. Never falls back to a client-supplied role claim.
5. **`receipts.pdf_url` is `NOT NULL`** but PDF rendering may be owned elsewhere (brief §7). See
   the TODO in the migration and in `app/payments/handoffs.py`.
6. **Three Razorpay secrets**, not two: `RAZORPAY_KEY_ID`/`KEY_SECRET` for the API, and a separate
   `RAZORPAY_WEBHOOK_SECRET` generated when the webhook URL is registered in the dashboard.

## Out of scope

Multi-event cart checkout, QR generation, notification delivery, receipt PDF rendering,
registration/team creation, event configuration, attendance scanning, admin RBAC.
