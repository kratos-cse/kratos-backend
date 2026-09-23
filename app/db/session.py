from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# A placeholder URL is used only so the engine object can be constructed
# (and the app/Swagger UI can start) before DATABASE_URL is set. Nothing
# actually connects until a request touches the DB.
_FALLBACK_URL = "postgresql+asyncpg://user:pass@localhost:5432/kratos"

_has_db = bool(settings.DATABASE_URL and settings.DATABASE_URL.strip())

engine = create_async_engine(
    settings.async_database_url or _FALLBACK_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=25,
    max_overflow=25,
    pool_timeout=1 if not _has_db else 10,
    pool_recycle=1800,
    connect_args={
        "timeout": 1 if not _has_db else 10,
        "command_timeout": 1 if not _has_db else 10,
    },
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def is_db_configured() -> bool:
    """Returns True only when a real non-empty DATABASE_URL is set in environment."""
    return bool(settings.DATABASE_URL and settings.DATABASE_URL.strip())


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
