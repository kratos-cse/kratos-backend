# Project Expo category

## Overview

`PROJECT_EXPO` is a first-class `event_category` enum value. Project Expo events use the same registration, payment, capacity, team, and admin flows as other catalogue events.

## Database

Migration `0018` adds the enum value:

```sql
ALTER TYPE event_category ADD VALUE IF NOT EXISTS 'PROJECT_EXPO';
```

Deploy backend code that includes `EventCategory.PROJECT_EXPO` before or with running `alembic upgrade head`.

## API

No new public endpoints. Events are created and listed via existing routes:

- `GET /api/v1/events` — public list (includes `category`)
- `GET /api/v1/events/{id}` — detail + registration availability
- `POST /api/v1/registrations` — same registration body as other events

Admin:

- Create/patch events with `category: "PROJECT_EXPO"` via `POST/PATCH /api/v1/admin/events`
- Registrations, metrics, and exports filter by `event_id` (category is on the event row)

## Public frontend

- Hub: `/project-expo` (live events filtered by `PROJECT_EXPO`)
- Catalogue: `/events?category=PROJECT_EXPO`
- Landing orbit arena links to `/project-expo`

## Admin

Category appears in the event editor dropdown as **Project Expo**.

## Missing business data

Fees, dates, venues, prizes, and per-event eligibility are **not** hardcoded. Organisers configure each Project Expo event in the admin dashboard.
