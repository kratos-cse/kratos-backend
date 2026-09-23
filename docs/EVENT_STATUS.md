# Event Visibility & Registration

## Source of truth

Website visibility and registration are **two independent admin controls** stored on the event:

| Field | Values | Meaning |
|-------|--------|---------|
| `visibility` | `PUBLISHED`, `UNPUBLISHED` | Whether the event appears on the public website |
| `registration_status` | `OPEN`, `CLOSED` | Whether users may register |

Effective participant UX state is computed server-side in `resolve_registration_availability()`:

| Field | Meaning |
|-------|---------|
| `registration_availability` | `OPEN`, `CLOSED`, or `FULL` (capacity-derived) |
| `registration_open` | Legacy boolean — `true` only when `registration_availability == OPEN` |
| `spots_remaining` | `null` when no capacity limit |

## Rules

1. **UNPUBLISHED + OPEN registration is invalid** — unpublishing always sets `registration_status = CLOSED`.
2. **Publishing does not open registration** — admin must explicitly open registration after publish.
3. **Capacity is separate** — `registration_status = OPEN` with full capacity yields `registration_availability = FULL`.
4. **Registration window dates are not used** — `registration_opens_at` / `registration_closes_at` are deprecated and ignored.

## Precedence for `registration_availability`

1. `visibility != PUBLISHED` → `CLOSED`
2. `registration_status != OPEN` → `CLOSED`
3. `spots_remaining == 0` → `FULL`
4. Otherwise → `OPEN`

## Public API

- `GET /api/v1/events` — **PUBLISHED events only**
- `GET /api/v1/events/{id}` — **404 for UNPUBLISHED**
- Published events with `registration_status = CLOSED` remain visible; registration CTAs show closed state.

## Admin API

- `GET /api/v1/admin/events` — all events (published + unpublished)
- `POST /api/v1/admin/events/{id}/publish`
- `POST /api/v1/admin/events/{id}/unpublish` (also closes registration)
- `POST /api/v1/admin/events/{id}/open-registration` (requires `PUBLISHED`)
- `POST /api/v1/admin/events/{id}/close-registration`

## Registration endpoint checks

`POST /api/v1/events/{id}/registrations` verifies:

1. Event exists
2. `visibility == PUBLISHED`
3. `registration_status == OPEN`
4. Capacity available
5. Existing eligibility rules

## Caching

- Backend event list cache: 20s (invalidated on admin mutations)
- Backend spots cache: 5s per event
- Frontends: `fetch` uses `cache: 'no-store'` on API GET
