# Teams Module — Kratos26 Backend

Owns: `TEAMS`, `TEAM_MEMBERS`, `TEAM_INVITATIONS` tables and their 9 endpoints
---

## 1. Folder structure

```
app/
├── main.py                          # wires routers + (temporarily) create_all()
├── core/
│   ├── database.py                  # engine/session/Base, loads .env via python-dotenv
│   ├── deps_stub.py                 # TEMPORARY fake auth - see §3
│   └── enums.py                     # TeamStatus, TeamMemberRole, TeamMemberStatus, FeeChargeModel
├── models/
│   ├── team.py                      # Team, TeamMember, TeamInvitation (yours, permanent)
│   └── _temp_external_stubs.py      # TEMPORARY Event/Profile/Rules stand-ins - see §3
├── schemas/
│   └── team.py                      # Pydantic request/response models
├── services/
│   ├── team_service.py              # all business logic
│   └── notification_service.py      # SMTP email sending (dry-run by default)
├── api/v1/
│   ├── teams.py                     # create/get/patch team, list members, create invitation
│   ├── team_members.py              # leave, remove
│   └── team_invitations.py          # public invite lookup, join
└── db/migrations/
    └── 0001_teams_module.sql        # only the 2 partial unique indexes still matter - see §5
```

---

## 2. Endpoints

| Method | Path | Access |
|---|---|---|
| POST | `/events/{event_id}/teams` | Authenticated user |
| GET | `/teams/{team_id}` | Team member/leader |
| GET | `/teams/{team_id}/members` | Team member/leader |
| PATCH | `/teams/{team_id}` | Leader |
| POST | `/teams/{team_id}/invitations` | Leader |
| GET | `/team-invitations/{invite_code}` | Public |
| POST | `/team-invitations/{invite_code}/join` | Authenticated user |
| POST | `/teams/{team_id}/members/{member_id}/leave` | That member |
| POST | `/teams/{team_id}/members/{member_id}/remove` | Leader |

---

## 3. Temporary files — why they exist and when to delete them

Nothing else in the repo existed yet when this module was built (no DB setup,
no auth, no Event/Profile models). These three files are scaffolding so the
module runs standalone. Each is marked `TEMPORARY` in its own docstring.

| File | Why it exists | Delete when... |
|---|---|---|
| `core/deps_stub.py` | Fakes `get_current_profile` via an `X-Debug-Profile-Id` header, since no real auth exists | Real auth PR merges — swap the import in all 3 routers to the real dependency |
| `models/_temp_external_stubs.py` | Minimal read-only `Event`, `EventRegistrationRules`, `Profile` classes — just the columns this module reads | Real Events/Profile models merge — swap the import in `team_service.py`, check column names still match |

`core/database.py` is **not** temporary in the same sense — it's a real,
permanent file, just currently a minimal version. If a teammate's DB-setup
PR provides a more complete one, merge/reconcile rather than delete
outright, since other modules will also depend on it.

**Do not keep these permanently** — running two different SQLAlchemy `Base`
registries in one app causes duplicate/conflicting table definitions.

---

## 4. Setup

```bash
pip install fastapi sqlalchemy pydantic psycopg2-binary uvicorn python-dotenv
```

Create a `.env` file at the repo root (same level as `requirements.txt`,
**not** inside `app/`) — this repo's `.gitignore` should already exclude it:

```env
# Database (Supabase pooler connection - see §6 for why pooler, not direct)
user=postgres.xxxxxxxx
password=your-db-password
host=aws-0-region.pooler.supabase.com
port=5432
dbname=postgres

# Email (see §7)
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
FROM_EMAIL=no-reply@kratos26.events
EMAIL_DRY_RUN=true
```

Wire the routers into `main.py` if not already done:

```python
from app.core.database import Base, engine
from app.models import team as _team_models              # noqa: F401
from app.models import _temp_external_stubs as _stubs     # noqa: F401
from app.api.v1 import teams, team_members, team_invitations

app = FastAPI()

# --- TEMPORARY: creates tables on every startup while testing. REMOVE BEFORE MERGING. ---
Base.metadata.create_all(bind=engine)
# ------------------------------------------------------------------------------------

app.include_router(teams.router, prefix="/api/v1")
app.include_router(team_members.router, prefix="/api/v1")
app.include_router(team_invitations.router, prefix="/api/v1")
```

Run it:

```bash
uvicorn app.main:app --reload
```

---

## 5. Database notes

- Tables + Postgres ENUM types are created automatically by `create_all()`
  in `main.py` above — no separate migration script needed for those.
- The **two partial unique indexes** cannot be expressed through the ORM.
  Run these once in Supabase's SQL Editor (not the whole `.sql` file, since
  the `CREATE TABLE`/`CREATE TYPE` parts will conflict with what
  `create_all()` already made):

  ```sql
  CREATE UNIQUE INDEX uq_active_member_per_event
      ON team_members (event_id, profile_id)
      WHERE status NOT IN ('LEFT', 'REMOVED');

  CREATE UNIQUE INDEX uq_one_active_leader_per_team
      ON team_members (team_id)
      WHERE role = 'LEADER' AND status NOT IN ('LEFT', 'REMOVED');
  ```

- **Remove the `create_all()` block from `main.py`** before merging, or once
  real Event/Profile models land — it will otherwise create/manage tables
  it doesn't own every time anyone runs the server.

---

## 6. Supabase connection notes

Use the **Session Pooler** connection string (Project Settings → Database →
Connection string → "Session pooler" tab), not "Direct connection" — the
direct hostname is IPv6-only on many projects and fails to resolve on
typical home/campus networks. Pooler username format is `postgres.<ref>`,
not just `postgres`.

---

## 7. Email notifications

`services/notification_service.py` sends three emails:

- `send_member_confirmation` — to the joining member
- `notify_leader_member_joined` — to the team leader
- `send_team_completed` — to the leader, when the team hits max capacity

All three are called from `team_service.join_via_invitation()`, after the
DB transaction commits. Send failures are caught and logged — they never
roll back or fail the API call itself.

**Dry-run by default** (`EMAIL_DRY_RUN=true`): nothing is sent, everything
is printed to your terminal as `[DRY RUN EMAIL]` instead. Good for testing
the trigger logic without real SMTP credentials.

**To send real test emails**: sign up at mailtrap.io (free sandbox inbox),
copy the SMTP host/port/username/password from their dashboard into `.env`,
set `EMAIL_DRY_RUN=false`. No code changes needed.

**Known gap**: the joining member's own email comes from whatever
`get_current_profile` returns. The current auth stub (`deps_stub.py`)
doesn't carry an email, so it falls back to blank in that case — check this
once real auth replaces the stub.

---

## 8. Testing via Swagger

1. Seed one `events` row (`status='OPEN'`), one `event_registration_rules`
   row for it, and two `profiles` rows via Supabase's SQL Editor or Table
   Editor — note their UUIDs.
2. Open `http://localhost:8000/docs`.
3. Every protected endpoint needs a header: `X-Debug-Profile-Id: <uuid>` —
   add it manually per-request via "Try it out" until real auth exists.
4. Walk the flow: create team (as leader) → create invitation (as leader)
   → get invitation (public, no header) → join (as a second profile) →
   list members → confirm both show up.
5. Check edge cases: re-joining with the same code (should be idempotent,
   not duplicate), non-leader hitting PATCH (should 403), a profile that
   never joined hitting GET /teams/{id} (should 404, not 403).
6. Export the running app's OpenAPI spec (`/openapi.json`) into Postman via
   File → Import → Link if you prefer Postman over Swagger's UI.

---

## 9. Open decisions — confirm with the team before merging

1. **Who creates the `REGISTRATIONS` row?** `create_team()` here does not
   create one — whoever builds `POST /events/{event_id}/registrations`
   should call into this and create it on their side.
2. **Invitation reissue behavior** — calling create-invitation twice
   currently returns the same active code rather than rotating it.
3. **Admin override** — every access check has a spot marked for an admin
   bypass once RBAC exists; not implemented yet.
4. **Where does `email` actually live in the real schema?** — likely on a
   `USERS` table joined via `Profile.user_id`, not directly on `PROFILES`.
   Confirm before swapping out `_temp_external_stubs.py`.