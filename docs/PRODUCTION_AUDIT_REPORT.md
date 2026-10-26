# KRATOS'26 Production Audit — Implementation Report

**Date:** 2026-10-26  
**Backend HEAD (local):** `219eba9` — feat: Enhance error handling and logging for roster management and authentication  
**Frontend HEAD (local):** `d731ef4` — feat(errors): enhance user messages for roster conflicts and validation scripts  
**Git operations:** none (no commit/push/deploy per instructions)

## Deployment baseline

Both repos report `main...origin/main` with no unpushed commits at audit start. Treat production as aligned with these SHAs unless Railway/Vercel show otherwise.

## Root causes addressed

| Issue | Cause | Fix |
|-------|--------|-----|
| DetachedInstanceError on create-order | ORM User/Profile cached across requests | Per-request DB load in `app/core/security.py` |
| Paid at Razorpay, PENDING registration | `registration.payment_id` moves on retry; success used pointer only | `payments.registration_id` durable FK + `resolve_registration_for_payment()` |
| Sync marked authorized as paid | `sync.py` accepted `authorized` | `razorpay_verify` — captured only |
| Verify always returned PAID | Hard-coded response | Returns persisted `status` + `registrationStatus` |
| Webhook gaps | No `order.paid`; weak failure handling | `app/payments/webhook.py` |
| FAILED row “repair” via sync | Policy: sync is CREATED-only | Reconcile + webhooks for late capture |
| Roster 409 on DB errors | Broad except → 409 | Re-raise → 500 |
| Receipt tokens in access logs | Query string in uvicorn | `app/core/log_sanitize.py` |

## Migrations

**`0019_payment_registration_and_provider_metadata.py`** (`down_revision = 0018`):

- `payments.registration_id` → `registrations.id` (backfilled from `registrations.payment_id`)
- Provider failure metadata columns on `payments`

Apply with `alembic upgrade head` before deploy.

## Payment event handling

| Event | Behavior |
|-------|----------|
| `payment.captured` | Verify capture vs local order/amount/currency → `apply_payment_success` (idempotent) |
| `payment.failed` | `CREATED` → `FAILED` + metadata; never downgrades `PAID` |
| `order.paid` | Resolve **captured** payment via API — order entity alone is not proof |

## Sync vs reconcile

- **Sync:** `CREATED` only; `FAILED`/`PAID`/`REFUNDED` no-op.
- **Reconcile:** Provider-verified capture; can promote `FAILED` → `PAID` when eligible.

## Recovery

- **Bulk:** `python scripts/recover_mismatched_payments.py` (dry-run default); `--apply` requires explicit flag.
- **Admin:** existing `/admin/registrations/{id}/recover-captured-payment` (uses captured-only fetch).

## Files changed (backend — principal)

- `alembic/versions/0019_payment_registration_and_provider_metadata.py`
- `app/models/payment.py`
- `app/payments/razorpay_verify.py`, `webhook.py`, `sync.py`, `apply.py`, `reconciliation.py`, `create_order.py`, `admin_recover.py`
- `app/api/v1/endpoints/payments.py`
- `scripts/recover_mismatched_payments.py`
- `tests/test_payment_sync_policy.py`, `test_payment_reconcile_late_capture.py`, `test_payment_webhooks.py`, `test_razorpay_verify_unit.py`
- `docs/PAYMENT_RECOVERY.md`

## Files changed (frontend)

- `lib/api/payments.js` — `reconcilePayment()`
- `app/registrations/[id]/page.js` — auto reconcile when payment `FAILED`

## Tests

```powershell
cd kratos-backend
python -m pytest tests/test_razorpay_verify_unit.py tests/test_http_code_from_detail.py tests/test_roster.py tests/test_log_sanitize.py -q
```

**Result:** 27 passed (unit / no-DB).

DB integration tests (`test_payment_sync_policy.py`, `test_payment_reconcile_late_capture.py`, `test_payment_webhooks.py`, `test_add_roster_member.py`, etc.) require a stable `DATABASE_URL`. Last run: `asyncpg.exceptions.ConnectionDoesNotExistError` — **not executed successfully**.

```powershell
cd kratos-frontend
npm run test:matrix
```

## Manual review

Cases still requiring operator review: duplicate captures on one registration, cancelled registration + captured payment, missing `registration_id` on legacy rows without pointer, amount/currency mismatch at Razorpay.
