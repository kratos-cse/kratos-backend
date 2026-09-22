# KRATOS'26 Backend — Hardening Implementation Report

Date: 2026-09-22  
Scope: Event catalogue, category lock, tagline, concurrency, WhatsApp, performance.

## Changed files

- `app/models/enums.py` — `EventCategory`
- `app/models/event.py` — tagline, category enum, `ix_events_starts_at`
- `app/models/registration.py` — active solo/team partial unique indexes
- `app/models/team.py` — `TeamMember.profile` relationship
- `app/schemas/event.py` — list/detail/whatsapp contracts
- `app/schemas/admin_ops.py` — category enum + tagline
- `app/api/v1/endpoints/events.py` — optimized list, WhatsApp gate
- `app/api/v1/endpoints/admin_ops.py` — tagline; cache invalidation
- `app/services/event_service.py` — list cache helpers; capacity comments
- `app/services/team_service.py` — selectinload profiles; join capacity recheck; IntegrityError
- `app/services/registration_service.py` — IntegrityError → `ALREADY_REGISTERED`
- `app/services/admin_ops_service.py` — tagline / whatsapp_available in admin dict
- `app/core/errors.py` — expanded codes + HTTP detail mapping
- `app/main.py` — timing middleware; smarter HTTPException codes
- `alembic/versions/0006_event_category_tagline_uniques.py`
- `tests/test_event_hardening.py`
- `tests/test_capacity_semantics.py`

## Migrations

**0006** (`0006_event_category_tagline_uniques.py`):

1. Create PG enum `event_category` (TECHNICAL, PLAYGROUND, SPARK, ONLINE, CULTURAL)
2. Normalize existing category strings (SPORTS→PLAYGROUND; unknown → NULL — not invented)
3. Add `events.tagline` (nullable)
4. Cast `events.category` to enum
5. Index `ix_events_starts_at`
6. Partial uniques:
   - `uq_registrations_event_profile_active`
   - `uq_registrations_event_team_active`

## API changes

| Endpoint | Change |
| --- | --- |
| `GET /events` | Adds `tagline`; join+projection; **no** capacity calc; ~20s in-memory TTL cache |
| `GET /events/{id}` | Adds `tagline`, `whatsapp_group_available`; **removes** public `whatsapp_group_link` |
| `GET /events/{id}/whatsapp` | **New** — auth required; confirmed solo or paid/complete team member |
| Admin event create/patch | `tagline`, `category: EventCategory` |

## Business rules

- **Categories:** five values only; PLAYGROUND covers sports; no free-form strings
- **Solo duplicate:** app check + DB partial unique + IntegrityError → 409 `ALREADY_REGISTERED`
- **Team capacity:** `SELECT … FOR UPDATE` on team; recount after insert; IntegrityError on unique
- **WhatsApp:** link not on public detail; entitled participants only via dedicated endpoint
- **Team rules:** still `team_min/max`, registration_mode, etc. from rules table (unchanged product)

## Performance

- `/events`: outer join Event↔rules, ordered by `starts_at` (indexed); no N+1; no spots_remaining
- Short TTL cache (20s), invalidated on admin event mutations
- Team detail: `selectinload(TeamMember.profile)` — no per-member profile query
- Request timing: `X-Response-Time-Ms` + structured log for `/api/v1/*`

Query-count before/after was not measured against a live production DB in this pass (no production credentials in CI).

## Tests

- `tests/test_event_hardening.py` — categories, registration window, cache, schema, whatsapp route, error mapping
- `tests/test_capacity_semantics.py` — documented capacity semantics + unique index names
- Existing suite still expected to run

**Not covered live:** concurrent join race against real Postgres, Razorpay E2E, SMTP E2E.

## Remaining limitations

- Live concurrent DB tests require Postgres
- Category values that were unrecognized become NULL (not deleted rows; values not invented)
- In-process events cache is per-worker (fine for fest scale; not Redis)
- JWT revoke remains in-process
- Full payment/QR/attendance integration suites still thin (pre-existing)

## Acceptance checklist

- [x] Architecture preserved (FastAPI/SQLAlchemy/Razorpay)
- [x] Five categories enforced
- [x] PLAYGROUND single category
- [x] Tagline supported
- [x] `/events` lightweight / no capacity per event
- [x] `starts_at` indexed
- [x] Solo duplicate DB protection
- [x] Team capacity lock + recheck
- [x] Team detail N+1 removed
- [x] WhatsApp not public URL
- [x] Payment model unchanged (solo/leader pay)
- [x] Migrations clean from 0005
- [ ] Production timed before/after (needs deploy)
- [ ] Concurrent join under real load (needs Postgres)
