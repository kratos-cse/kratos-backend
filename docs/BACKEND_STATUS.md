# KRATOS'26 — Backend Implementation Status

Updated after Full Backend Phases execution (solo/leader-only payments).

| Area | Status |
|------|--------|
| Auth / JWT / Google | Done |
| Profile | Done |
| Events + registration_mode | Done |
| Solo registration + payment | Done |
| Team create + leader payment | Done |
| Member join (ACTIVE, no pay) | Done |
| Leave / remove | Done |
| Leadership transfer | Done |
| Team COMPLETE detection | Done |
| Razorpay order/verify/webhook | Done |
| Idempotent payment transitions | Done |
| Refund (Super Admin) | Done |
| Receipt PDF + re-download | Done |
| QR solo/leader/member | Done |
| Notifications + SMTP | Done (needs real SMTP env) |
| Attendance checkpoints/scans | Done |
| Admin dashboard/events/participants/teams/regs/payments/exports | Done |
| RBAC + Super Admin bootstrap | Done |
| Error `{error:{code,message}}` | Done |
| PER_MEMBER / TEAM_MEMBER_TOPUP | **Removed from app** (intentionally not a gap) |
| WebSocket teams | Deferred |
| Multi-event cart | Deferred |
| Live Postgres + Razorpay + SMTP E2E | Pending credentials |

## Migrations

`alembic upgrade head` → through `0005`.

## Bootstrap Super Admin

1. User signs in via Google once  
2. Set `SUPER_ADMIN_EMAIL`  
3. `python scripts/bootstrap_super_admin.py`
