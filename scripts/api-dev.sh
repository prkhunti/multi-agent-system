#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")

cd "$PROJECT_ROOT"
test -f .env || cp .env.example .env
exec docker compose \
    -f infra/docker-compose.yaml \
    -f infra/docker-compose.dev.yaml \
    up --build api
