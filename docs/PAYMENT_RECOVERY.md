# Payment recovery

## Participant flows

- **`POST /payments/{id}/sync`** — only for **`CREATED`** checkout attempts. Does **not** change `FAILED` payments.
- **`POST /payments/reconcile`** — fetches Razorpay and applies a **verified capture** (including late capture after a local `FAILED`).

## Operator bulk recovery

Dry-run (default):

```bash
python scripts/recover_mismatched_payments.py
```

Apply (after reviewing JSON output):

```bash
python scripts/recover_mismatched_payments.py --apply --operator you@example.com
```

Per-registration admin preview/apply remains under `/api/v1/admin/registrations/{id}/recover-captured-payment`.

## Admin confirmation (not PATCH)

- **`PATCH /admin/registrations/{id}`** cannot set `CONFIRMED` (use explicit flows below).
- **`POST /admin/registrations/{id}/confirm-offline-payment`** — manual/offline payment + fulfillment.
- **`POST /admin/registrations/{id}/recover-captured-payment`** — verified Razorpay capture only.

## Migration `0019` backfill

```sql
UPDATE payments p SET registration_id = r.id
FROM registrations r
WHERE r.payment_id = p.id AND p.registration_id IS NULL;
```

This links each payment row to the registration that **currently** pointed at it. After a retry, only the latest attempt has `registrations.payment_id`; older attempts rely on `payments.registration_id` set at create-order (post-deploy). Ambiguous legacy rows → manual review.

## Dry-run report format

`scripts/recover_mismatched_payments.py` prints JSON:

```json
{
  "inspected": 12,
  "eligible": 2,
  "already_correct": 5,
  "manual_review": 5,
  "corrected": 0,
  "dry_run": true,
  "rows": [
    {
      "payment_id": "...",
      "registration_id": "...",
      "outcome": "eligible",
      "proposed": "apply_payment_success via reconcile"
    }
  ]
}
```

**Review procedure:** run dry-run → inspect `manual_review` and `eligible` → verify Razorpay dashboard for each `payment_id` → only then run with `--apply --operator you@example.com`.
