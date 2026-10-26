# M0 — Foundation

- **Status:** Accepted 2026-10-26 (committed as `M0`)
- **Depends on:** nothing
- **Unlocks:** M1 (and every later milestone)

## Scope

Stand up the monorepo, the API skeleton, the database with migrations, the dashboard shell,
local development tooling, CI, and documentation. No product feature is implemented.

## Deliverables

| Area        | Delivered                                                                                   |
| ----------- | ------------------------------------------------------------------------------------------- |
| Monorepo    | pnpm workspaces, Turborepo, shared Prettier/tsconfig (`packages/config`), `packages/shared-types` |
| API         | FastAPI app factory, `/health`, `/api/v1/health`, `/api/v1/system/info`, request-id middleware, structured logging, error envelope, CORS, settings, 8 empty domain modules |
| Database    | PostgreSQL 17 via Docker Compose, Alembic, migration `0001_foundation` (organizations, users, organization_memberships, social_accounts), dev-only idempotent seed |
| Web         | Next.js 16 App Router shell: sidebar, top nav, theme toggle, dashboard with live API status, 7 placeholder pages, loading/error/404 states, orange brand tokens |
| Quality     | TS strict, ESLint, Prettier, Ruff, pytest (25 tests incl. migration drift + seed idempotency), GitHub Actions CI |
| Docs        | README, CLAUDE.md, architecture (overview/database/modules), ADR 0001–0003, API reference, M0–M5 milestone docs |

## Acceptance criteria

- [x] `pnpm install` and `uv sync` succeed from a clean checkout
- [x] `pnpm db:up && pnpm db:migrate && pnpm db:seed` succeed; seed is idempotent
- [x] API answers `/health`, `/api/v1/health`, `/api/v1/system/info` with `database.status: ok`
- [x] Web production build succeeds; dashboard renders live API data in the browser
- [x] Dark/light themes, desktop and mobile layouts work
- [x] `pnpm lint`, `pnpm typecheck`, `pnpm build`, `uv run pytest` (with DB) all pass
- [x] Migration upgrade → downgrade → upgrade round-trips; autogenerate shows no drift
- [x] No secrets in the repository; `system/info` leaks no configuration
- [x] No platform integration is represented as working
- [ ] CI workflow observed green on GitHub (requires a remote; not yet pushed)

## Known risks and limitations

- Next.js 16 with Cache Components and shadcn/ui v4 are recent; upgrades may need care.
- SQLAlchemy resolved to 2.1.x (allowed range `>=2.0,<3`); pin tighter if a regression appears.
- Authentication, RBAC, and organization scoping are documented, not enforced.
- Host port 5440 for PostgreSQL avoids common conflicts; adjust via `.env` if needed.
- `TEST_DATABASE_URL` must be set to run migration and seed tests locally.
