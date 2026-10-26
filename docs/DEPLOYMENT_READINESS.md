# Deployment readiness — payment recovery (local audit)

**No commit / push / deploy / production `--apply` / production migrations executed.**

## Migration head

```
alembic heads → 0019 (head)
```

| Revision | File | Parent |
|----------|------|--------|
| `0019` | `0019_payment_registration_and_provider_metadata.py` | `0018` |

Validated by `tests/test_alembic_revision_chain.py`.

### `payments.registration_id` backfill

At upgrade, SQL sets `registration_id` only where `registrations.payment_id = payments.id`. That is the **current** checkout pointer at migration time. New orders set `registration_id` at create-order. Older attempts after retry keep their own `registration_id` for late-capture reconcile. **Never** infer from payer alone. See `tests/test_migration_backfill_documentation.py` and `docs/PAYMENT_RECOVERY.md`.

## Files changed (this pass)

| File | Why |
|------|-----|
| `app/payments/fulfillment.py` | Retryable QR/receipt/notification; email failure does not roll back payment |
| `app/payments/admin_offline.py` | Offline payment + confirm + audit; block fake Razorpay recovery |
| `app/payments/apply.py` | Uses fulfillment; safe post-commit notification |
| `app/api/v1/endpoints/admin_ops.py` | Block PATCH→CONFIRMED; offline endpoint; `mark_paid` uses offline flow |
| `app/schemas/admin_ops.py` | `AdminOfflinePaymentBody` |
| `app/payments/admin_recover.py` | Reject offline order ids for Razorpay recovery |
| `tests/test_*` | Guards, scenarios, alembic, fulfillment, late capture |
| `docs/PAYMENT_RECOVERY.md` | Dry-run format + admin flows |

(Previous pass: `razorpay_verify`, `webhook`, `sync`, `reconciliation`, `0019`, `recover_mismatched_payments.py`, frontend reconcile.)

## Admin confirmation coverage

| Flow | Behavior |
|------|----------|
| `PATCH /admin/registrations/{id}` → CONFIRMED | **400** — must use offline or recover-captured |
| `PATCH` CONFIRMED → PENDING | `revoke_registration_confirmation_admin` (unchanged) |
| `POST /admin/participants` + `mark_paid` | Creates PENDING reg → `confirm_offline_payment_for_registration` |
| `POST .../confirm-offline-payment` | Manual method/reference/amount + fulfillment + audit |
| `POST .../recover-captured-payment` | Verified Razorpay capture → `apply_payment_success` |

## Late capture & duplicate capture tests

| Test module | Scenario |
|-------------|----------|
| `test_payment_sync_policy.py` | FAILED + sync = no-op |
| `test_payment_reconcile_late_capture.py` | FAILED + reconcile when captured |
| `test_late_capture_registration_link.py` | Old attempt + `registration_id` after retry pointer moved |
| `test_payment_recovery_scenarios.py` | Amount mismatch, duplicate webhook, order.paid, cancelled, duplicate PAID sibling |
| `test_fulfillment_resilience.py` | Notification failure isolated |
| `test_admin_confirmation_guards.py` | Offline orders blocked from Razorpay recovery |

DB-backed rows in the table above require `DATABASE_URL`; not verified in this environment if asyncpg fails.

## Test commands & results

```powershell
cd kratos-backend
python -m alembic heads
python -m pytest tests/test_alembic_revision_chain.py tests/test_admin_confirmation_guards.py tests/test_migration_backfill_documentation.py tests/test_razorpay_verify_unit.py tests/test_http_code_from_detail.py tests/test_roster.py tests/test_log_sanitize.py tests/test_payment_recovery_scenarios.py::test_payment_item_rejects_amount_mismatch -q
```

**Result:** `0019 (head)`, **32 passed** (unit / no-DB).

```powershell
python -m pytest tests/test_payment_sync_policy.py tests/test_payment_reconcile_late_capture.py tests/test_late_capture_registration_link.py tests/test_payment_recovery_scenarios.py tests/test_fulfillment_resilience.py tests/test_payment_webhooks.py -q
```

**Result:** run when DB available (previously `ConnectionDoesNotExistError`).

```powershell
cd kratos-frontend
npm run test:matrix
```

**Result:** ok.

## Dry-run recovery procedure

1. `python scripts/recover_mismatched_payments.py` (default dry-run).
2. Review JSON: `eligible`, `manual_review`, per-row `outcome` / `reason`.
3. Cross-check Razorpay dashboard (capture, amount, order id) for each eligible row.
4. Only then: `python scripts/recover_mismatched_payments.py --apply --operator you@example.com`.

## Deployment order

1. Apply migration `0019` on backend (`alembic upgrade head`).
2. Deploy **backend** (webhooks, reconcile, admin endpoints, fulfillment).
3. Deploy **frontend** (FAILED → reconcile, CREATED → sync).
4. Run recovery **dry-run** in production; manual review before any `--apply`.

## Remaining risks

- Legacy payments with null `registration_id` and broken pointer → manual review.
- Dual captured Razorpay payments on one registration → `DUPLICATE_CAPTURE`; no auto-refund.
- Cancelled registration + capture → no auto-reactivate; admin/orphan flow.
- Email/QR partial failure → use `retry_post_payment_fulfillment` / admin tools; payment row stays PAID.
- JWT multi-worker revoke set still in-memory (pre-existing).
