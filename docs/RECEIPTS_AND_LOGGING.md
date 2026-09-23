# Receipts, Logging, and Caching

## Receipt architecture

Receipts are **generated on demand** by the backend. The `receipts` table stores metadata only:

- `receipt_number` (stable once issued)
- `payment_id` (unique)
- `pdf_url` / API links (authenticated endpoints)
- `issued_at`

No generated HTML or PDF files are written to disk.

### Receipt access (browser tabs)

Session JWTs are **not** passed in receipt URLs. Flow:

1. Client calls `POST /api/v1/registrations/{id}/receipt/access-token` (or payments equivalent) with `Authorization: Bearer`.
2. Server returns a short-lived **receipt-scoped** JWT (`purpose=receipt`, `aud=receipt`, `payment_id`, `sub=profile_id`, ~20 min).
3. Browser opens `html_url` / `pdf_url` with `?receipt_token=...` only.

Receipt HTML/PDF endpoints accept either Bearer auth or `receipt_token` query param. Receipt tokens cannot authenticate other API routes.

Endpoints:

- `POST /api/v1/payments/{id}/receipt/access-token` — issue scoped token + URLs
- `POST /api/v1/registrations/{id}/receipt/access-token` — same via registration
- `GET /api/v1/payments/{id}/receipt/html` — in-memory HTML
- `GET /api/v1/payments/{id}/receipt/pdf` — in-memory PDF

Branding assets live under `app/assets/kratos/` and are shared with transactional email templates via `app/branding/`.

## Logging

- Every request receives `X-Request-ID` (client-supplied if safe, otherwise generated UUID).
- Structured logs: `request_completed request_id=... method=... path=... status=... duration_ms=...`
- Business logs use module loggers (`payments.apply`, `receipt_service`, `notification_service`, etc.).
- **Audit logs** (`audit_logs` table) remain the authoritative security/admin trail; application logs are for operations only.
- Never log JWTs, Razorpay secrets, webhook signatures, or full payment payloads.

## Caching (process-local)

| Cache | TTL | Invalidation |
|-------|-----|--------------|
| User / profile | 20s | `invalidate_user_cache()` on profile update |
| Admin RBAC | 30s | `invalidate_admin_cache()` on role/admin changes |
| Event list | 20s | `invalidate_events_list_cache()` on event mutations |
| Spots remaining | 5s | `invalidate_spots_cache(event_id)` on registration/team/payment changes |
| Admin dashboard | 10s | `invalidate_dashboard_cache()` on relevant mutations |

Caches are **in-process** (single uvicorn worker). Use Redis or similar if scaling to multiple workers.
