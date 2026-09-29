import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app import models  # noqa: F401
from app.core.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_FALLBACK_URL = "postgresql+asyncpg://user:pass@localhost:5432/kratos"

# Check settings, then os.environ, then Azure connection string aliases
_raw_url = (
    settings.DATABASE_URL
    or os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRESQLCONNSTR_DATABASE_URL")
    or os.environ.get("CUSTOMCONNSTR_DATABASE_URL")
    or ""
).strip()

if _raw_url:
    if _raw_url.startswith("postgres://"):
        _raw_url = _raw_url.replace("postgres://", "postgresql://", 1)
    if _raw_url.startswith("postgresql://") and "+asyncpg" not in _raw_url:
        _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "sslmode=" in _raw_url:
        _raw_url = _raw_url.replace("sslmode=", "ssl=")
    _database_url = _raw_url
else:
    _database_url = _FALLBACK_URL

try:
    _u = make_url(_database_url)
    print(f"--> [Alembic] Target Database: user='{_u.username}', host='{_u.host}', port={_u.port}, db='{_u.database}', query={dict(_u.query)}", flush=True)
except Exception as _ex:
    print(f"--> [Alembic] Error parsing database URL: {_ex}", flush=True)

config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    except Exception as err:
        print(f"--> [Alembic ERROR] DB Connection Failed: {type(err).__name__}: {err}", flush=True)
        raise
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())