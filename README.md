# KRATOS'26 Backend — `Authentication` + `payments` branches merged

FastAPI service implementing **Authentication, User Profile, Events,
Registration** (this branch's original scope) plus **Payments & Refunds**
(Section 8/23, merged in from the `payments` feature branch — see
"Payments & Refunds" below), per the project's API reference and finalized
DB schema.

## Stack

- **FastAPI** — Swagger UI ships free at `/docs`, ReDoc at `/redoc`.
- **SQLAlchemy 2.0 (async)** + **asyncpg** — talks to Postgres on Railway.
- **Alembic** — schema migrations (async-aware `env.py`).
- **PyJWT** — our own session tokens, issued after Google verification.
- **google-auth** — verifies the Google `id_token` the frontend sends us.
- **razorpay** (Payments module) — Razorpay orders/refunds, test mode first.

## What's implemented

| Section | Endpoints |
|---|---|
| 1. Authentication | `POST /auth/google`, `GET /auth/me`, `POST /auth/logout` |
| 2. User Profile | `GET /users/me/profile`, `PATCH /users/me/profile` |
| 3. Events | `GET /events`, `GET /events/{event_id}` |
| 4. Registration | `POST /events/{event_id}/registrations`, `GET /registrations/{id}`, `GET /users/me/registrations`, `GET /registrations/{id}/receipt`, `GET /registrations/{id}/qr` |
| 8. Payments | `POST /payments/create-order`, `POST /payments/verify`, `POST /payments/webhook`, `GET /payments/{id}` |
| 23. Admin Payments/Refunds | `GET /admin/payments`, `POST /admin/payments/{id}/refund` |

All routes are mounted under `/api/v1` (e.g. `/api/v1/auth/google`,
`/api/v1/payments/create-order`).

## Payments & Refunds (Section 8/23)

Owns the `payments`/`receipts` tables and the Razorpay integration.
Business logic lives in `app/services/{payment_apply,refund,amounts,
signatures,razorpay_client,handoffs}.py`; endpoints in
`app/api/v1/endpoints/{payments,admin_payments}.py`.

- `POST /payments/create-order` takes a `registration_id` (for
  SOLO_REGISTRATION/TEAM_REGISTRATION) or a `team_member_id` (for
  TEAM_MEMBER_TOPUP) — never a raw amount or payment type from the client.
  It computes the charge server-side from `events.fee` +
  `event_registration_rules.fee_charge_model`.
- `POST /payments/verify` (fast-path UI confirmation) and
  `POST /payments/webhook` (source of truth, Razorpay signature only) both
  call the same `apply_payment_success`/`apply_payment_failure` — there is
  exactly one place payment status changes, and it's idempotent against
  webhook retries (conditional `UPDATE ... WHERE status IN (...) RETURNING`,
  no separate dedup table).
- `POST /admin/payments/{id}/refund` and `GET /admin/payments` currently
  gate on `users.is_admin_flagged` (see `app/core/admin_deps.py`) — the
  same documented interim fallback this branch already uses elsewhere,
  since the `Admin` branch's real RBAC isn't wired up yet. **TODO(admin-
  rbac-owner):** swap to the real `require_admin`/`require_super_admin`
  once that branch's `get_current_user` is implemented and merged; there's
  currently no admin-vs-super-admin distinction available anywhere.
- Tests: `tests/test_signatures.py`, `tests/test_amounts.py`,
  `tests/test_payment_apply.py` (the idempotency guarantees above, driven
  by an in-memory store stub — no DB needed).

## What's deliberately NOT here

Teams CRUD/invitations/join-leave, QR generation, Attendance, Admin RBAC
(roles/permissions) & dashboards, and Notifications belong to other feature
branches (`teams`, `Admin`, etc.). To keep this
branch mergeable without fighting over the same tables/migrations:

- `Team` / `TeamMember` models exist here **only** because registration
  creation has to spin up a team + leader row. The dedicated Teams
  endpoints (join, leave, invite, remove) are not implemented.
- `QRCode` model is still a **minimal, read-only mirror** — enough for the
  `/qr` endpoint to look things up. QR generation itself is a different
  track's job. `Payment` and `Receipt` are no longer read-only: the
  Payments module above owns writing to `payments`; `receipts` is still
  read-only here too (its `pdf_url` is `NOT NULL`, and PDF rendering is a
  separate concern — see `app/services/handoffs.py`).
  Registration creation leaves `payment_id` null; `POST
  /payments/create-order` fills it in once a payment is created against
  the registration/team id this branch returns.
- `team_invitations`, `attendance_checkpoints`, `attendance_scans`,
  `notifications`, `admin_users`, `roles`, `permissions` have no models or
  migrations here at all — that's for whoever owns those sections.
- Real admin RBAC isn't built yet, so "authorized admin" access checks
  (both here and in the Payments module) fall back to
  `users.is_admin_flagged` (the schema explicitly calls this out as a
  legacy/helper flag) as a stand-in until the Admin branch lands.

## Assumptions made (flag if wrong)

- **Framework/DB**: FastAPI + Postgres via SQLAlchemy async, per your
  confirmation. `DATABASE_URL` and `GOOGLE_CLIENT_ID` are left blank in
  `.env` for your team lead to fill in.
- **`member_registration_mode`**: the schema marks this an admin-configurable
  ENUM without naming values. Modeled as `LEADER_MANAGED` / `SELF_ENTRY`
  from the shared workflow diagram (leader fills everything vs. members
  join and self-enter). Confirm the exact names before anyone else's code
  depends on them.
- **Logout**: the schema has no `SESSIONS` table, so auth is stateless
  JWT. `/auth/logout` revokes the token via an in-process set (works fine
  for local testing / a single worker) — swap for Redis or a refresh-token
  flow before running multiple workers in production.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Fill in DATABASE_URL and GOOGLE_CLIENT_ID once your lead has them.
# Generate a JWT_SECRET_KEY: python -c "import secrets; print(secrets.token_hex(32))"
```

## Migrations

```bash
alembic upgrade head
```

This creates the 10 tables this branch needs (see `alembic/versions/0001_initial_schema.py`).
It will NOT create tables owned by other branches — merge their migrations
in when those branches land.

## Run it

```bash
uvicorn app.main:app --reload
```

Open **http://localhost:8000/docs** for Swagger UI.

### Testing the full flow in Swagger

1. `GET /events` will be empty until something creates an event (Admin
   Event Management is a different branch). For local testing, seed two
   sample events:
   ```bash
   python scripts/seed_dev_data.py
   ```
2. `POST /auth/google` needs a **real, valid Google `id_token`** — it
   will hard-fail with a clear 500 until `GOOGLE_CLIENT_ID` is set, and
   after that still needs a token actually issued for that client ID
   (e.g. grab one from your frontend's login flow, or from
   [Google OAuth Playground](https://developers.google.com/oauthplayground)
   configured with the same client). There's no way around this — it's
   what "verify Google" means.
3. Copy the `access_token` from the response, click **Authorize** in
   Swagger UI, paste it in as `Bearer <token>`.
4. `GET /auth/me`, `GET/PATCH /users/me/profile`, `POST
   /events/{event_id}/registrations` (use one of the seeded event ids),
   `GET /users/me/registrations` all work from there.
5. `/receipt` and `/qr` will correctly 404 until the Payments/QR branches
   generate those rows — that's expected, not a bug.
6. Payments: `POST /payments/create-order` with a `registration_id` from
   step 4 (needs `RAZORPAY_KEY_ID`/`KEY_SECRET` test-mode keys set), then
   the Razorpay test checkout, then `POST /payments/verify`. Or skip
   `/verify` entirely and run `scripts/simulate_webhook.py` against a real
   `RAZORPAY_WEBHOOK_SECRET` and the order id — fires the webhook twice to
   prove idempotency over real HTTP.

## Tests

```bash
pytest
```

Needs `pytest-asyncio` (in `requirements-dev.txt`; `pytest.ini` sets
`asyncio_mode = auto` so async test functions just work, no per-test
markers). `test_signatures.py`, `test_amounts.py`, and
`test_payment_apply.py` need no DB — everything else does.

Only `/health` is covered here (no DB needed to run it). Endpoint tests
that need Postgres are left for whoever wires up a test database /
fixtures for the whole team, so this branch doesn't hardcode assumptions
about how that's set up.

## Project layout

```
app/
  core/        # settings, JWT + auth dependencies
  db/          # SQLAlchemy engine/session
  models/      # ORM models (see table above for scope)
  schemas/     # Pydantic request/response models
  services/    # business logic (auth, events, registrations)
  api/v1/      # route definitions
alembic/       # migrations
scripts/       # seed_dev_data.py
tests/
```
