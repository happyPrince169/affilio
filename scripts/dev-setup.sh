#!/usr/bin/env bash
# One-shot local bootstrap for AFFILIO. Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing prerequisite: $1 ($2)"; exit 1; }; }

step "Checking prerequisites"
need node   "https://nodejs.org (>= 22)"
need pnpm   "npm i -g pnpm@10 or corepack enable"
need uv     "https://docs.astral.sh/uv/getting-started/installation/"
need docker "https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 plugin is required"; exit 1; }

if [ ! -f .env ]; then
  step "Creating .env from .env.example"
  cp .env.example .env
else
  echo ".env already exists — leaving it untouched"
fi

step "Installing JavaScript workspace (pnpm)"
pnpm install --frozen-lockfile

step "Syncing Python environment (uv, Python 3.12)"
(cd apps/api && uv sync)

step "Starting PostgreSQL"
docker compose up -d postgres
printf 'Waiting for PostgreSQL to be healthy'
for _ in $(seq 1 30); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' affilio-postgres 2>/dev/null)" = "healthy" ]; then
    echo " — ready"; break
  fi
  printf '.'; sleep 1
done

step "Applying database migrations"
(cd apps/api && uv run alembic upgrade head)

step "Seeding development data (DEV ONLY)"
(cd apps/api && uv run python -m affilio.scripts.seed_dev)

step "Done"
cat <<'EOF'
Next:
  pnpm dev:api   # FastAPI  -> http://localhost:8000  (docs at /docs)
  pnpm dev:web   # Next.js  -> http://localhost:3000
  pnpm dev       # both, via Turborepo
  ./scripts/check-health.sh
EOF
