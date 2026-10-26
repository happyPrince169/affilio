# AFFILIO

**Create Once. Distribute Everywhere. Optimize Everything.**

AFFILIO is an AI-powered Affiliate Content Operating System: a platform for managing,
distributing, analyzing, and optimizing affiliate marketing content across social and
e-commerce platforms. The long-term goal is to run 100+ authorized social accounts with
AI-assisted content operations, centralized publishing, and performance intelligence.

The MVP targets **one Shopee Affiliate account** and validates a repeatable
content-to-commission workflow before any other platform is added.

> **Status: M0 — Foundation.** The monorepo, API skeleton, database migrations, and dashboard
> shell exist. No platform integration, media sync, AI feature, or analytics is implemented.
> Placeholder pages say so explicitly. See `docs/milestones/`.

---

## Architecture at a glance

Modular monolith, one repository, three runtimes:

| Area      | Path                    | Stack                                                                      |
| --------- | ----------------------- | -------------------------------------------------------------------------- |
| Web       | `apps/web`              | Next.js 16 (App Router), React 19, TypeScript, Tailwind 4, shadcn/ui, TanStack Query |
| API       | `apps/api`              | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, structlog, pytest |
| Database  | `docker-compose.yml`    | PostgreSQL 17 (container)                                                  |
| Shared    | `packages/shared-types` | TypeScript contracts mirroring API schemas                                 |
| Tooling   | `packages/config`       | Shared Prettier and tsconfig bases                                         |

- `pnpm` workspaces + Turborepo orchestrate JavaScript packages and task running.
- The Python API manages its own dependencies with **uv** (`apps/api/pyproject.toml`,
  `uv.lock`). Its `package.json` only adapts Turborepo tasks to uv commands.
- Domain modules live under `apps/api/src/affilio/modules/` (identity, accounts, media,
  products, content, publishing, analytics, agents). Read `docs/architecture/` before changing
  boundaries.

## Prerequisites

| Tool            | Version              | Notes                                                        |
| --------------- | -------------------- | ------------------------------------------------------------ |
| Node.js         | ≥ 22                 | tested with 24                                               |
| pnpm            | 10.x                 | `corepack enable` or `npm i -g pnpm@10`                      |
| uv              | ≥ 0.8                | installs Python 3.12 automatically if missing                |
| Docker + Compose v2 | any recent        | only used for PostgreSQL in M0                               |

## Quick start

```bash
./scripts/dev-setup.sh      # copies .env, installs JS + Python deps, starts DB, migrates, seeds
pnpm dev                    # web on :3000 and API on :8000 via Turborepo
./scripts/check-health.sh   # probes API + web + DB container
```

Manual equivalent:

```bash
cp .env.example .env
pnpm install
(cd apps/api && uv sync)
pnpm db:up                  # docker compose up -d postgres  (host port 5440)
pnpm db:migrate             # alembic upgrade head
pnpm db:seed                # DEV-ONLY demo org + placeholder Shopee account
pnpm dev:api                # http://localhost:8000  (OpenAPI docs at /docs)
pnpm dev:web                # http://localhost:3000
```

### Ports

| Service    | Port | Why                                                      |
| ---------- | ---- | -------------------------------------------------------- |
| Web        | 3000 | Next.js default                                          |
| API        | 8000 | uvicorn default                                          |
| PostgreSQL | 5440 | host port; 5432 is frequently taken by a system Postgres |

Change them in `.env` (`POSTGRES_PORT`, `DATABASE_URL`, `NEXT_PUBLIC_API_URL`).

## Environment variables

Copy `.env.example` to `.env` at the repository root. The API reads the root `.env`, then
`apps/api/.env` (optional overrides), then process environment variables. The web app only
sees `NEXT_PUBLIC_*` variables, inlined at build time.

Never commit `.env` or any real credential. `ANTHROPIC_API_KEY`, NAS paths, and platform
tokens are reserved for later milestones and must stay out of the repository.

## Everyday commands

| Command                   | What it does                                                   |
| ------------------------- | -------------------------------------------------------------- |
| `pnpm dev`                | Run web + API with hot reload                                  |
| `pnpm lint`               | ESLint (web, shared-types) + Ruff (api)                        |
| `pnpm typecheck`          | `tsc --noEmit` for TS packages, import smoke for api           |
| `pnpm test`               | pytest (api). Set `TEST_DATABASE_URL` for DB-backed tests      |
| `pnpm build`              | Next.js production build                                       |
| `pnpm check`              | lint + typecheck + test + build, all packages                  |
| `pnpm format`             | Prettier (JS/TS/JSON/CSS). Python: `pnpm --filter @affilio/api format` |
| `pnpm db:up` / `db:down`  | Start / stop PostgreSQL                                        |
| `pnpm db:reset`           | Destroy the volume and start fresh (re-runs init scripts)      |
| `pnpm db:migrate`         | `alembic upgrade head`                                         |
| `pnpm db:seed`            | Idempotent DEV-ONLY seed (refuses outside development/test)    |

## Database migrations

Alembic lives in `apps/api/alembic/`. All commands run from `apps/api`:

```bash
uv run alembic upgrade head                      # apply
uv run alembic downgrade -1                      # roll back one
uv run alembic revision --autogenerate -m "add x" # new migration from model changes
uv run alembic current                           # show applied revision
```

Rules: every schema change ships with a migration, migrations must be reversible, and
`tests/test_migrations.py` fails if the ORM models and migrations drift. The migration
`0001_foundation` creates `organizations`, `users`, `organization_memberships`,
`social_accounts`. Design notes: `docs/architecture/database.md`.

## Testing

```bash
# API — unit tests only (no DB needed; DB tests are skipped)
cd apps/api && uv run pytest

# API — full suite including migration + seed smoke tests
TEST_DATABASE_URL=postgresql+psycopg://affilio:affilio@localhost:5440/affilio_test uv run pytest

# Web
pnpm --filter @affilio/web lint typecheck build
```

The test-suite never touches the development database: without `TEST_DATABASE_URL` it points
at an unreachable URL by construction. The Docker init script creates `affilio_test` on first
start.

CI (`.github/workflows/ci.yml`) runs Prettier, ESLint, TypeScript, the Next build, Ruff, the
migration upgrade/downgrade/upgrade cycle, the seed twice, pytest, and a live health probe.

## API endpoints (M0)

| Method | Path                  | Purpose                                                        |
| ------ | --------------------- | -------------------------------------------------------------- |
| GET    | `/health`             | Liveness (unversioned, hidden from OpenAPI)                    |
| GET    | `/api/v1/health`      | Liveness, versioned                                            |
| GET    | `/api/v1/system/info` | Version, environment, Python, DB readiness + latency, modules  |
| GET    | `/docs`               | Swagger UI (`DOCS_ENABLED=false` to disable)                   |

Errors always use `{"error": {"code", "message", "details?", "request_id"}}` and every
response carries `X-Request-ID` (echoed if supplied). See `docs/api/README.md`.

## Troubleshooting

- **Dashboard shows "API unreachable"** — start the API (`pnpm dev:api`) and confirm
  `NEXT_PUBLIC_API_URL` matches. The web app must be rebuilt after changing that variable.
- **`database: unavailable` in system info** — `pnpm db:up`, then check
  `docker inspect -f '{{.State.Health.Status}}' affilio-postgres`.
- **Port 5440 already in use** — set `POSTGRES_PORT` and update `DATABASE_URL` /
  `TEST_DATABASE_URL` in `.env`.
- **`affilio_test` database missing** — it is created only on first container start. Run
  `pnpm db:reset` (destroys local data) or create it manually:
  `docker exec affilio-postgres psql -U affilio -c 'CREATE DATABASE affilio_test'`.
- **`ModuleNotFoundError: affilio`** — run `uv sync` inside `apps/api`.
- **pnpm refuses to install** — Node ≥ 22 and pnpm 10 are required (`engine-strict`).
- **Playwright/Chromium** is not part of M0; UI verification was done manually with the
  system browser.

## Documentation

- `CLAUDE.md` — engineering rules for AI-assisted sessions
- `docs/architecture/` — overview, database, modules
- `docs/decisions/` — architecture decision records
- `docs/api/` — endpoint and error contract reference
- `docs/milestones/` — M0 → M5 scope, acceptance criteria, risks
