# Event status contract

## Source of truth

All registration UX state is computed **server-side** in `app/services/event_service.py` via `resolve_registration_availability()`.

Clients (Admin + Public) must consume the API fields below — never infer registration state from dates, local clocks, or `status` alone.

## Fields

| Field | Type | Meaning |
|-------|------|---------|
| `status` | `EventStatus` | Admin lifecycle: `OPEN`, `CLOSED`, `COMPLETED`, `CANCELLED` |
| `registration_availability` | `RegistrationAvailability` | Authoritative registration UX state (see below) |
| `registration_open` | `boolean` | `true` only when `registration_availability == OPEN` (legacy convenience) |
| `spots_remaining` | `int \| null` | `null` when event has no capacity limit |
| `registration_opens_at` | `datetime \| null` | Optional registration window start (UTC) |
| `registration_closes_at` | `datetime \| null` | Optional registration window end (UTC) |

## `registration_availability` values

| Value | When | Public UI |
|-------|------|-----------|
| `OPEN` | Event `OPEN`, inside window, capacity available | Registration open / Register CTA |
| `NOT_YET_OPEN` | Event `OPEN`, before `registration_opens_at` | Coming soon |
| `WINDOW_CLOSED` | Event `OPEN`, after `registration_closes_at` | Registration closed |
| `FULL` | Event `OPEN`, `spots_remaining == 0` | Event full |
| `EVENT_CLOSED` | Event `CLOSED` / `CANCELLED` / `COMPLETED` | Event closed |

**Precedence:** event lifecycle → registration window → capacity.

## Endpoints

- `GET /api/v1/events` — list; includes all fields above
- `GET /api/v1/events/{id}` — detail; same derived fields
- `GET /api/v1/admin/events/{id}` — admin detail + nested `rules` + same derived fields

Admin open/close (`POST …/open`, `POST …/close`) returns the full admin event payload with updated `registration_availability`.

## Caching

- Backend event list cache: 20s (invalidated on admin mutations)
- Backend spots cache: 5s per event (invalidated on registration/team/payment changes and capacity edits)
- Frontends: `fetch` uses `cache: 'no-store'` for API calls; no SWR/React Query layer

## Display-only (frontend)

Fee and roster strings are formatted in each frontend using the same backend numeric fields (`fee`, `required_member_count`, `substitute_count`). Event **names** are shown as stored — edit in admin if casing should change.
