# AFFILIO

**Create Once. Distribute Everywhere. Optimize Everything.**

AFFILIO is an AI-powered Affiliate Content Operating System: a platform for managing,
distributing, analyzing, and optimizing affiliate marketing content across social and
e-commerce platforms. The long-term goal is to run 100+ authorized social accounts with
AI-assisted content operations, centralized publishing, and performance intelligence.

The MVP targets **one Shopee Affiliate account** and validates a repeatable
content-to-commission workflow before any other platform is added.

> **Status: M2B — Procurement & Production** (M0, M1, M2A delivered). The Content Library
> indexes footage and finished videos from the NAS, Products tracks Shopee affiliate products
> (manual or MeoScout CSV import) through a research lifecycle, Purchases tracks approval,
> orders and receiving, and Production tracks filming/editing jobs. No platform integration,
> publishing, AI feature, or analytics is implemented; those pages are explicit placeholders.

---

## Architecture at a glance

Modular monolith, one repository, three runtimes:

| Area      | Path                    | Stack                                                                      |
| --------- | ----------------------- | -------------------------------------------------------------------------- |
| Web       | `apps/web`              | Next.js 16 (App Router), React 19, TypeScript, Tailwind 4, shadcn/ui, TanStack Query |
| API       | `apps/api`              | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, structlog, pytest |
| Worker    | `apps/api` (`affilio.worker`) | Same image as the API; scans media and renders thumbnails (ffmpeg)    |
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
| Docker + Compose v2 | any recent        | PostgreSQL locally; the whole stack on the NAS               |
| ffmpeg          | ≥ 4.2                | `ffprobe`/`ffmpeg` on PATH for the media library and its tests (`apt install ffmpeg`) |

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
pnpm dev:worker             # background scans + thumbnails (needs MEDIA_* paths, see below)
pnpm dev:web                # http://localhost:3000
```

### Media library on the workstation

Point the four logical roots at local folders or a mounted NAS share in `.env`, then scan:

```bash
MEDIA_ORIGINALS_PATH=/mnt/nas/video/originals    # read-only
MEDIA_PROXIES_PATH=/mnt/nas/video/proxies        # read-only
MEDIA_FINALS_PATH=/mnt/nas/video/finals          # read-only, finished edits (handoff review)
MEDIA_THUMBNAILS_PATH=$PWD/var/thumbnails        # read-write (create it)
MEDIA_EXPORTS_PATH=$PWD/var/exports              # read-write (create it)

pnpm media:scan -- --thumbnails   # one-off synchronous scan + thumbnails (no worker needed)
pnpm dev:worker                   # or keep the worker running and press "Scan now" in the UI
```

AFFILIO never writes under the originals or proxies roots. Proxy files are linked to originals
by filename pattern (`MEDIA_PROXY_PATTERNS`); confirm the pattern matches your proxy naming.

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
| `pnpm dev:worker`         | Background worker: scan jobs + thumbnails                      |
| `pnpm media:scan`         | Synchronous CLI scan (`-- --full`, `-- --thumbnails`)          |
| `pnpm db:backup`          | `pg_dump` from the postgres container to `./backups`           |

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
`social_accounts`; `0002_media_library` adds `media_roots`, `media_assets`,
`media_file_variants`, `media_scan_jobs`. Design notes: `docs/architecture/database.md`.

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

## API endpoints

| Method | Path                                   | Purpose                                                   |
| ------ | -------------------------------------- | --------------------------------------------------------- |
| GET    | `/health`                              | Liveness (unversioned, hidden from OpenAPI)               |
| GET    | `/api/v1/health`                       | Liveness, versioned                                       |
| GET    | `/api/v1/system/info`                  | Version, environment, Python, DB readiness, modules       |
| GET    | `/api/v1/media/roots`                  | Logical roots: configured / readable / writable           |
| GET    | `/api/v1/media/stats`                  | Library counters                                          |
| GET    | `/api/v1/media/assets`                 | List + search + filter + paginate                         |
| GET    | `/api/v1/media/assets/{id}`            | Detail with every file variant                            |
| GET    | `/api/v1/media/assets/{id}/thumbnail`  | JPEG thumbnail                                            |
| GET    | `/api/v1/media/scans/status`           | Current + last scan, root availability                    |
| GET    | `/api/v1/media/scans`, `/scans/{id}`   | Scan history and detail                                   |
| POST   | `/api/v1/media/scans`                  | Queue a scan — **admin token** outside development        |
| GET/POST | `/api/v1/products`                   | Product catalog list/search and creation (**admin** for writes) |
| GET/PATCH | `/api/v1/products/{id}`             | Product detail and edit                                   |
| POST   | `/api/v1/products/{id}/status`         | Audited lifecycle change; WINNER needs confirmation        |
| GET/POST/PATCH/DELETE | `/api/v1/products/{id}/media[/{asset_id}]` | Product ↔ video links                  |
| POST   | `/api/v1/products/imports/preview`, `/imports` | MeoScout CSV preview and import (**admin**)    |
| GET/POST | `/api/v1/procurement/requests`, `/orders` | Purchase requests, orders, receiving (**admin** writes) |
| GET/POST | `/api/v1/production/jobs`              | Production jobs with optional product/video links (**admin** writes) |
| POST   | `/api/v1/media/assets/{id}/handoff`    | Approve / reject a final video (**admin**)                |
| GET    | `/docs`                                | Swagger UI (`DOCS_ENABLED=false` to disable)              |

Organization scoping uses the `X-Organization-Slug` header (default `DEFAULT_ORGANIZATION_SLUG`)
until authentication lands in M3. Mutating media endpoints require `X-Affilio-Admin-Token`
when `MEDIA_ADMIN_TOKEN` is set, and are refused in production when it is not.

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
- **Scan button says "admin token required"** — in production the web container needs
  `MEDIA_ADMIN_TOKEN` (same value as the API); in development leave it unset on both.
- **Thumbnails stay pending** — the worker is not running (`pnpm dev:worker`) or
  `MEDIA_THUMBNAILS_PATH` is not writable.
- **No proxies linked** — adjust `MEDIA_PROXY_PATTERNS` to the real proxy naming.

## Documentation

- `CLAUDE.md` — engineering rules for AI-assisted sessions
- `docs/architecture/` — overview, database, modules
- `docs/decisions/` — architecture decision records
- `docs/api/` — endpoint and error contract reference
- `docs/deployment/synology.md` — production deployment on the NAS (mounts, permissions, backups, upgrades)
- `docs/milestones/` — M0 → M5 scope, acceptance criteria, risks
