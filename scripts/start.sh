#!/usr/bin/env sh
# Railway / production entrypoint: migrate then serve (LF line endings).
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
