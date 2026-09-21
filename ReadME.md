# KRATOS'26 Backend

FastAPI `/api/v1` — async SQLAlchemy + Alembic + Razorpay + SMTP.

**Payment rule:** Only solo participants and team leaders pay. Members never pay.

## Environment (Railway Postgres)

Full guide with **where to get every key**:

→ **[docs/ENV_AND_RAILWAY.md](docs/ENV_AND_RAILWAY.md)**

```powershell
copy .env.example .env
# Fill DATABASE_URL from Railway → Postgres → DATABASE_PUBLIC_URL
# Fill Google, JWT, Razorpay test keys, SMTP (see the doc)
```

Also see `docs/BACKEND_PLAN.md` and `docs/BACKEND_STATUS.md`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env
# edit .env
alembic upgrade head
uvicorn app.main:app --reload
```

Swagger: http://localhost:8000/docs

After one Google login:

```powershell
# SUPER_ADMIN_EMAIL=you@gmail.com in .env
python scripts/bootstrap_super_admin.py
```

## Tests

```powershell
pytest
```
