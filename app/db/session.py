from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# A placeholder URL is used only so the engine object can be constructed
# (and the app/Swagger UI can start) before DATABASE_URL is set. Nothing
# actually connects until a request touches the DB.
_FALLBACK_URL = "postgresql+asyncpg://user:pass@localhost:5432/kratos"

engine = create_async_engine(
    settings.async_database_url or _FALLBACK_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
