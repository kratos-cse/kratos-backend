# KRATOS'26 — Money / Cancel / Authz Implementation Plan

Doctrine: grilling Q1–Q23 (closed).  
Scope: single-fest registration + payment machine. DB constraints are business logic.

## Current-state snapshot (repo today)

| Doctrine item | Status |
| --- | --- |
| Participant unpaid cancel + unlink `payment_id` | Done (`cancel_unpaid_registration`) |
| Confirm skips `CANCELLED` | Done (`confirm_solo` / `confirm_team`) |
| Webhook + GET sync call `apply_payment_success` | Done |
| Team-member active partial unique | Done (migration `0003`) |
| Solo / team **registration** active partial uniques | **Missing** (app-level `_already_registered` only) |
| `FAILED→PAID` only if live non-cancelled reg owns payment | **Partial** (confirm guarded; transition still allows any `FAILED→PAID`) |
| PAID orphan admin filter `?orphan=1` | **Missing** |
| Audit log (DB) for cancel/refund | **Missing** |
| `is_admin_flagged` removal (compat → delete) | **Not started** (still used on registration view/cancel + `/auth/me` + bootstrap) |
| Auto-refund / multi-fest / Redis revoke / audit UI | Out of scope (do not build) |

---

## Sequenced plan

### 1. Current-state verification (read-only)

- Inventory every `is_admin_flagged` read/write.
- Trace payment apply paths: webhook, verify, GET sync, `POST …/sync`.
- List uniqueness constraints on `registrations`, `team_members`, `teams`.
- Confirm admin cancel vs participant cancel vs refund entry points.
- **Exit:** short gap list matching the snapshot table (no code yet).

### 2. `is_admin_flagged` compatibility migration (Q2, Q6)

**Deploy A (compat):**

- Helper: `actor_is_admin(user) := active AdminUser OR is_admin_flagged` (temporary).
- Replace registration view/cancel `is_admin` checks with that helper.
- Stop **writing** the flag in `bootstrap_super_admin.py` (still ensure `AdminUser` row).
- Keep column + `/auth/me` field for one cycle (additive; Q22).

**Deploy B (delete) — only after verifying all admins have `admin_users` rows:**

- Remove helper; require `AdminUser` only.
- Drop column (Alembic), remove schema field, stop exposing on `/auth/me` (coordinate frontends if they read `is_admin`).

### 3. Audit infrastructure (Q7, Q15, Q19)

- Table (name may remain `admin_audit_log`):  
  `id`, `created_at`, `actor_admin_user_id` (nullable FK), `action`, `entity_type`, `entity_id`, `metadata` JSONB.
- Write on: participant unpaid cancel, admin unpaid cancel (if any), refund, orphan refund.
- Participant rows: `actor_admin_user_id = NULL`, metadata includes `actor_user_id`, `profile_id`.
- No audit UI (Q17).

### 4. Payment / orphan semantics (Q8, Q9, Q20)

In `apply_payment_success` / transition path:

- Before confirming: resolve registration by `payment_id` where status ≠ `CANCELLED`.
- If none: allow payment → `PAID` (orphan), **do not** confirm registration/team, **do not** issue QR/receipt handoffs that assume a live reg; audit `payment.orphaned_paid` (or equivalent).
- `FAILED→PAID` only through this same apply path with the live-reg rule above.

Refund orphan:

- Money-only Razorpay refund; no registration/team mutation; audit.

### 5. Cancellation guards (Q13, Q14)

- Single unpaid-cancel service path; refuse `CONFIRMED` / `PAID`.
- Paid unwind = refund only (existing Super Admin refund).
- Allow cancel while order `CREATED` (unlink + mark FAILED as today).
- Emit audit row on cancel.

### 6. Active-only uniqueness verification (Q18, Q21)

Add Alembic partial uniques (history preserved):

1. Solo: `(event_id, profile_id)` WHERE `profile_id IS NOT NULL AND status <> 'CANCELLED'`
2. Team reg: `(event_id, team_id)` WHERE `team_id IS NOT NULL AND status <> 'CANCELLED'`
3. Membership: already present — verify cancel sets `REMOVED`/`LEFT` correctly

Pre-migration: query for duplicates that would block the index; fix or cancel orphans manually.

App `_already_registered` stays as UX 409; DB is the real guarantee.

### 7. Admin orphan filter (Q12, Q16)

- Extend `GET /admin/payments` with `orphan=1` (additive).
- Orphan := `status = PAID` AND no registration with that `payment_id` and `status ≠ CANCELLED` (unlinked cancelled regs count as orphan).
- Gated by existing `payment-read`; refund still Super Admin.

### 8. Webhook + GET reconciliation convergence (Q4, Q11)

- Ensure GET sync / `POST …/sync` / webhook / verify all call **one** apply function (no duplicate confirm logic).
- GET must never resurrect CANCELLED (covered by §4).
- Log sync failures (already partially done).

### 9. Tests for race / failure cases

Minimum suite:

- Cancel unpaid solo → re-register same event (no 409).
- Cancel unpaid team → members can join/register again.
- Cancel then late capture → payment PAID orphan, registration stays CANCELLED, no QR.
- `FAILED→PAID` with live PENDING reg → CONFIRMED.
- Refund orphan → payment refunded, registration untouched.
- Compat: admin without flag but with `AdminUser` can view; after Deploy B, flag ignored/gone.
- Audit rows written for cancel + refund.

### 10. Frontend contract compatibility (Q22)

- Additive APIs only (`orphan`, audit internal).
- Participant cancel response shape unchanged unless both frontends migrate together.
- Before Deploy B: confirm admin + participant clients do not depend on `is_admin` / `is_admin_flagged` for gates (admin panel already uses `/admin/me`).

### 11. Migration / deployment order

```text
1. Ship code: compat admin helper + audit table + apply orphan rules + cancel audit
2. Ship migration: audit_log (+ optional registration partial uniques after duplicate check)
3. Backfill/verify: every is_admin_flagged user has admin_users row
4. Ship: orphan=1 filter + tests green
5. Deploy B: remove is_admin_flagged reads/writes/column (frontend OK)
```

Never: drop flag before AdminUser backfill.  
Never: add partial unique before clearing duplicate active rows.

### 12. Pre-fest verification checklist

- [ ] Webhook + verify + GET sync all confirm the same way
- [ ] Cancel unpaid → immediate re-register works (solo + team)
- [ ] Forced late capture after cancel → orphan list shows payment; reg CANCELLED
- [ ] Super Admin refund orphan succeeds; audit row present
- [ ] No `is_admin_flagged` reads in runtime paths (post Deploy B)
- [ ] `/admin/payments?orphan=1` returns only true orphans
- [ ] RBAC: non–Super Admin cannot refund
- [ ] Document: JWT revoke is in-process only (known limitation)

---

## Explicit non-goals (do not schedule)

Multi-fest, auto-orphan-refund, user-ban CMS, notification CMS, audit UI, Redis JWT revoke, separate admin service, new permission keys per action, QR/attendance redesign (separate grill).

---

## Suggested PR slices (small, reviewable)

1. **Compat auth helper** — stop writing flag; dual-read  
2. **Audit table + writers** on cancel/refund  
3. **Apply orphan semantics** + tests  
4. **Registration partial uniques** migration + cancel/re-register tests  
5. **`orphan=1` admin filter**  
6. **Deploy B** drop `is_admin_flagged`

---

## Done when

Shared understanding Q1–Q23 is implemented in code + migrations + tests, frontends still compatible, and the pre-fest checklist is checked off — without expanding into deferred scope.
