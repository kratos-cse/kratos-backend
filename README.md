# KRATOS'26 Backend — `Authentication` branch

FastAPI service implementing **Authentication, User Profile, Events, and
Registration** — the four endpoint groups assigned to this branch, per the
project's API reference and finalized DB schema.

## Stack

- **FastAPI** — Swagger UI ships free at `/docs`, ReDoc at `/redoc`.
- **SQLAlchemy 2.0 (async)** + **asyncpg** — talks to Postgres on Railway.
- **Alembic** — schema migrations (async-aware `env.py`).
- **PyJWT** — our own session tokens, issued after Google verification.
- **google-auth** — verifies the Google `id_token` the frontend sends us.

## What's implemented

| Section | Endpoints |
|---|---|
| 1. Authentication | `POST /auth/google`, `GET /auth/me`, `POST /auth/logout` |
| 2. User Profile | `GET /users/me/profile`, `PATCH /users/me/profile` |
| 3. Events | `GET /events`, `GET /events/{event_id}` |
| 4. Registration | `POST /events/{event_id}/registrations`, `GET /registrations/{id}`, `GET /users/me/registrations`, `GET /registrations/{id}/receipt`, `GET /registrations/{id}/qr` |

All routes are mounted under `/api/v1` (e.g. `/api/v1/auth/google`).

## What's deliberately NOT here

Sections 5–21 of the API reference (Teams CRUD/invitations/join-leave,
Payments/Razorpay, QR generation, Attendance, Admin RBAC & dashboards,
Notifications, Exports) belong to other feature branches. To keep this
branch mergeable without fighting over the same tables/migrations:

- `Team` / `TeamMember` models exist here **only** because registration
  creation has to spin up a team + leader row. The dedicated Teams
  endpoints (join, leave, invite, remove) are not implemented.
- `Payment`, `Receipt`, `QRCode` models are **minimal, read-only mirrors**
  — enough for the registration/receipt/qr endpoints to look things up.
  This branch never writes to `payments`, `receipts`, or `qr_codes`.
  Registration creation leaves `payment_id` null; the Payments branch is
  expected to create the Razorpay order against the registration/team id
  this branch returns.
- `team_invitations`, `attendance_checkpoints`, `attendance_scans`,
  `notifications`, `admin_users`, `roles`, `permissions` have no models or
  migrations here at all — that's for whoever owns those sections.
- Real admin RBAC isn't built yet, so "authorized admin" access checks
  fall back to `users.is_admin_flagged` (the schema explicitly calls this
  out as a legacy/helper flag) as a stand-in until the Admin branch lands.

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

## Tests

```bash
pytest
```

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
