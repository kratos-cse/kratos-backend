# KRATOS'26 — Master Backend Plan

**Payment rule (locked):** Only the solo participant or the team leader pays.
**Team members never pay.** There is no `PER_MEMBER`, `TEAM_MEMBER_TOPUP`,
or member Razorpay flow.

## Architecture

```text
Frontend → FastAPI /api/v1 → async SQLAlchemy → PostgreSQL (Alembic)
Integrations: Google OAuth, Razorpay, SMTP
```

No Supabase client. No multi-event cart. No WebSocket teams (deferred).

## Lifecycles

### 1. Solo
Google → profile → create registration (`profile_id`) → Razorpay
`SOLO_REGISTRATION` → verify/webhook → CONFIRMED → receipt PDF → QR
(`registration_id`) → confirmation email → attendance via QR.

### 2. Team leader
Google → profile → create team + leader `TEAM_MEMBERS` + registration
(`team_id`) → Razorpay `TEAM_REGISTRATION` → PAID + CONFIRMED → leader
QR (`team_member_id`) → reusable invite → receipt/email.

### 3. Team member (no payment)
Invite → Google → profile → join (atomic capacity) → `ACTIVE` → member
QR → confirmation email + WhatsApp link. **No payment step.**

Team can be PAID at 1/N and COMPLETE when active count hits max.

## Registration mode (events)

`INDIVIDUAL_ONLY` | `TEAM_ONLY` | `TEAM_OR_INDIVIDUAL`

Fee is a single event fee paid once.

## Alembic

`0001` core → `0002` event slot → `0003` invitations+RBAC →
`0004` registration_mode (drops fee_charge_model) →
`0005` notifications + attendance

## Environment / Railway

See **[ENV_AND_RAILWAY.md](ENV_AND_RAILWAY.md)** for Postgres on Railway and
where to obtain Google, JWT, Razorpay, and SMTP credentials.
Template: [`.env.example`](../.env.example).

## Definition of Ready checklist

See status in `docs/BACKEND_STATUS.md`. Deferred: WebSocket, cart.
