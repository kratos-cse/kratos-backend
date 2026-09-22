#!/usr/bin/env sh
# Railway / production entrypoint: migrate then serve.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-4}"
