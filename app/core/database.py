"""Own DB engine/session for this branch. Every feature branch in this repo
bootstraps its own (see teams/app/core/database.py) rather than importing
another branch's — there's no shared infra merged to main yet, and each
branch needs to run standalone until integration. DELETE this duplication
in favor of one shared app/db/ module once branches actually merge.

Sync SQLAlchemy + psycopg2, matching the teams branch's choice (simpler to
stand up locally than async + asyncpg, and money-path logic here doesn't
need async I/O — the one Razorpay-facing route offloads the SDK's blocking
calls via asyncio.to_thread instead).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# A placeholder URL is used only so the engine object can be constructed
# (and the app/Swagger UI can start) before DATABASE_URL is set. Nothing
# actually connects until a request touches the DB.
_FALLBACK_URL = "postgresql+psycopg2://user:pass@localhost:5432/kratos"

engine = create_engine(settings.DATABASE_URL or _FALLBACK_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Shared declarative base for this branch's models only."""


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
