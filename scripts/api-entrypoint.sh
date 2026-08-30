#!/usr/bin/env sh
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python -m alembic -c pyproject.toml upgrade head
fi
exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 "$@"
