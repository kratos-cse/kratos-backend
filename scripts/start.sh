#!/usr/bin/env sh
# Railway / Azure production entrypoint: activate venv, migrate, then serve.
set -e

if [ -f "antenv/bin/activate" ]; then
    . antenv/bin/activate
elif [ -f "/antenv/bin/activate" ]; then
    . /antenv/bin/activate
elif [ -f "/home/site/wwwroot/antenv/bin/activate" ]; then
    . /home/site/wwwroot/antenv/bin/activate
fi

python -m alembic upgrade head
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
