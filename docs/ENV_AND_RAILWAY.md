# KRATOS'26 — Environment & Railway Postgres setup

This guide explains **where every `.env` value comes from** and how to run
the API against **Railway PostgreSQL** from your laptop.

Template file: [`.env.example`](../.env.example)  
Copy it first:

```powershell
cd "D:\ACE\Main Kratos 26\kratos-backend"
copy .env.example .env
```

---

## Quick start (local API + Railway DB)

```text
Your PC (uvicorn)
      │
      │  DATABASE_PUBLIC_URL
      ▼
Railway PostgreSQL
```

1. Create Railway Postgres (section below) → paste URL into `DATABASE_URL`
2. Fill Google, JWT, Razorpay test keys, SMTP (sections below)
3. Install & migrate:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

4. Open http://localhost:8000/docs  
5. After one Google login, set `SUPER_ADMIN_EMAIL` and run:

```powershell
python scripts/bootstrap_super_admin.py
```

---

## 1. Railway PostgreSQL

### Create the database

1. Go to [railway.app](https://railway.app) and sign in (GitHub is fine).
2. **New Project** → **Provision PostgreSQL** (or Add Service → Database → PostgreSQL).
3. Open the **Postgres** service.

### Copy the connection URL

| Variable in Railway | Use when |
|---------------------|----------|
| `DATABASE_PUBLIC_URL` | API runs on your **laptop** (recommended for now) |
| `DATABASE_URL` | API is also deployed **on Railway** in the same project |

In the Railway UI:

- **Variables** tab, or  
- **Connect** / **Credentials** panel  

Copy the URL. It looks like:

```text
postgresql://postgres:XXXX@HOST.railway.app:PORT/railway
```

or `postgres://...` (also fine).

Paste into local `.env`:

```env
DATABASE_URL=postgresql://postgres:XXXX@HOST.railway.app:PORT/railway
```

### Notes

- The app converts `postgres://` → `postgresql://` and uses **asyncpg**
  (`postgresql+asyncpg://...`) automatically. Paste the **plain** Railway URL.
- Allow public networking if you use `DATABASE_PUBLIC_URL` from home Wi‑Fi.
- Run migrations from your PC against Railway:

```powershell
alembic upgrade head
```

- To confirm tables exist, use Railway’s **Data** tab or any Postgres client
  with the same URL.

### Optional: deploy API on Railway later

When you add a Web Service for this repo, set the same variables in Railway’s
service **Variables** (you can reference the Postgres plugin’s `DATABASE_URL`
with private networking). Keep `APP_PUBLIC_BASE_URL` as the public Railway HTTPS URL.

**Start command:** the [Procfile](../Procfile) runs `scripts/start.sh`, which
executes `alembic upgrade head` on **every deploy**, then starts uvicorn.
Ensure `DATABASE_URL` is set on the web service before the first deploy.

---

## 2. Google OAuth — `GOOGLE_CLIENT_ID`

**Where:** [Google Cloud Console](https://console.cloud.google.com/)

1. Create or select a project.
2. **APIs & Services** → **Credentials**.
3. Configure **OAuth consent screen** (External or Internal).
4. **Create Credentials** → **OAuth client ID** → type **Web application**.
5. **Authorized JavaScript origins** (examples):
   - `http://localhost:3000`
   - your deployed frontend origin
6. Copy **Client ID** → `GOOGLE_CLIENT_ID`.
7. Copy **Client secret** → `GOOGLE_CLIENT_SECRET`  
   (Credentials → click the OAuth Web client → Client secret / `GOCSPX-...`).

**Important:** Use the **same** OAuth client as the frontend Google Sign-In.
For the current `POST /api/v1/auth/google` flow the backend verifies the
`id_token` with **Client ID** only. Keep `GOOGLE_CLIENT_SECRET` in `.env`
anyway (same console page) for any future server-side code exchange.

---

## 3. JWT — `JWT_SECRET_KEY`

**Where:** generate on your machine (not from Google/Railway).

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste into `JWT_SECRET_KEY`.  
Keep `JWT_ALGORITHM=HS256` unless you know you need otherwise.  
`JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440` = 24 hours.

Use a **new** secret in production; never commit it.

---

## 4. Razorpay (test mode)

**Where:** [Razorpay Dashboard](https://dashboard.razorpay.com/)

1. Turn **Test Mode** ON.
2. **Account & Settings** → **API Keys** → Generate **Test** Key ID + Secret:
   - `RAZORPAY_KEY_ID` = `rzp_test_...`
   - `RAZORPAY_KEY_SECRET` = secret (shown once)
3. **Webhooks** → Add endpoint:
   - Local: tunnel to `https://<tunnel>/api/v1/payments/webhook` (e.g. ngrok)
   - Railway API: `https://<your-api>.up.railway.app/api/v1/payments/webhook`
4. Subscribe at least to `payment.captured` and `payment.failed`.
5. Copy **Webhook secret** → `RAZORPAY_WEBHOOK_SECRET`.

Amounts come from `events.fee` on the server — the client cannot set the price.

---

## 5. SMTP — email notifications

Pick one provider and fill:

| Variable | Meaning |
|----------|---------|
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | usually `587` |
| `SMTP_USER` | login / API user |
| `SMTP_PASSWORD` | password or API key |
| `SMTP_FROM` | From: address (must be allowed by provider) |
| `SMTP_TLS` | `true` for STARTTLS on 587 |

### Gmail (dev)

1. Enable 2-Step Verification on the Google Account.  
2. Create an [App Password](https://myaccount.google.com/apppasswords).  
3. Use that as `SMTP_PASSWORD` (not your normal Gmail password).

### Production-friendly options

Brevo, SendGrid, Mailgun, Amazon SES — copy SMTP credentials from their dashboards
and use a **verified** from-address.

If SMTP is empty or wrong, notifications stay `FAILED` in the DB; **payments are not undone**.

---

## 6. App URLs & CORS

| Variable | Local example | Meaning |
|----------|---------------|---------|
| `CORS_ORIGINS` | `http://localhost:3000` | Frontend origin(s), comma-separated |
| `APP_PUBLIC_BASE_URL` | `http://localhost:8000` | Public base URL of this API (receipt links) |
| _(removed)_ | — | Receipts are generated on demand; no disk storage directory |

---

## 7. Super Admin

1. Sign in once via the frontend / `POST /api/v1/auth/google` so your user exists.  
2. Set `SUPER_ADMIN_EMAIL` to that Google email.  
3. Run:

```powershell
python scripts/bootstrap_super_admin.py
```

This attaches the seeded `SUPER ADMIN` role (from migration `0003`).

---

## Checklist before first run

- [ ] Railway Postgres created  
- [ ] `DATABASE_URL` = public URL (laptop) or private URL (API on Railway)  
- [ ] `alembic upgrade head` succeeded  
- [ ] `GOOGLE_CLIENT_ID` matches frontend  
- [ ] `JWT_SECRET_KEY` generated  
- [ ] Razorpay **test** key id + secret (+ webhook secret if testing webhooks)  
- [ ] SMTP filled if you want real emails  
- [ ] `CORS_ORIGINS` includes the frontend  
- [ ] Super Admin bootstrapped after first login  

---

## Local PostgreSQL for faster integration tests

If you have `psql` locally, create a test database instead of hitting Railway over the public proxy (much faster):

```powershell
psql -U postgres -c "CREATE DATABASE kratos_test;"
```

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/kratos_test
```

```powershell
alembic upgrade head
pytest tests/test_receipt_access_token.py tests/test_team_concurrency.py -v
```

For Railway public proxy (`*.proxy.rlwy.net`), asyncpg needs plain TCP — tests set `ssl=False` automatically in `tests/conftest.py`.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `pytest` very slow / hangs | Railway public proxy adds ~25s per connection; use local Postgres or clear `DATABASE_URL` for unit-only runs |
| DB connection timeout from laptop | Use `DATABASE_PUBLIC_URL`; check Railway public networking |
| `alembic` fails SSL / auth | Wrong password / URL truncated; re-copy from Railway |
| Google login 500 / invalid token | Wrong `GOOGLE_CLIENT_ID` or token from another OAuth client |
| Payments fail creating order | Missing `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` or Test Mode off |
| Webhook never hits localhost | Need a public tunnel URL in Razorpay webhook settings |
| Emails never arrive | Check SMTP + spam; look at `notifications.status` = `FAILED` |
