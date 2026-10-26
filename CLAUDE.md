# CLAUDE.md — engineering rules for AFFILIO

These rules apply to every Claude Code session (and every human) working in this repository.
They exist to keep the modular monolith coherent while the product grows milestone by milestone.

## 1. Orientation before action

- Read `docs/architecture/overview.md`, `modules.md`, and `database.md` before changing
  structure, data models, or cross-module behavior.
- Read the milestone document for the work at hand (`docs/milestones/M*-*.md`). It defines
  scope, acceptance criteria, and known risks.
- Check `docs/decisions/` for prior ADRs before re-deciding something.

## 2. One milestone at a time

- Implement only the requested milestone. Do not start the next one, even partially.
- Stop after the milestone's acceptance criteria are met and report. Do not "keep going".
- If a requirement is ambiguous, state the assumption in the report; if it would change the
  architecture, ask first.

## 3. Preserve modular boundaries

- Domain modules live in `apps/api/src/affilio/modules/<name>/` and own their models,
  schemas, services, and router.
- A module may import from modules listed **before** it in `DOMAIN_MODULES`
  (`affilio/modules/__init__.py`), never after. Cross-module access goes through the owning
  module's service functions, not raw table access.
- Frontend features mirror backend modules under `apps/web/src/components/<area>/`
  and `apps/web/src/lib/api/<area>.ts`.
- Do not introduce microservices, message brokers, Kubernetes, Elasticsearch, or any
  distributed infrastructure. Redis only with a concrete, documented background-processing need.

## 4. Dependencies

- Add a dependency only when it removes meaningful work. Prefer the stack already in use.
- Verify a package or API exists and is stable before using it; do not assume from memory.
- Python deps: `uv add` in `apps/api` (never pip, never pnpm). JS deps: `pnpm add --filter`.
- Keep lockfiles (`pnpm-lock.yaml`, `apps/api/uv.lock`) committed and in sync.

## 5. Secrets and credentials

- Never hardcode secrets, tokens, API keys, passwords, or hostnames of real systems.
- Only `.env.example` is committed; it holds placeholders and local-dev defaults.
- Configuration goes through `affilio.core.config.Settings`. Secrets are `SecretStr`.
- Never return configuration values from an HTTP endpoint. `/api/v1/system/info` is the
  model: facts about the build, nothing about the environment.

## 6. Platform integrations

- Official APIs only. Never implement unofficial endpoints, browser automation, cookie
  replay, or scraping for publishing or analytics.
- Never invent platform capabilities. If API availability or permissions are unconfirmed
  (Shopee Video publishing is), say so in code comments, docs, and UI.
- The UI must never show fabricated data or imply an account is connected when it is not.

## 7. Database

- Every schema change ships with an Alembic migration that has a working `downgrade`.
- Models and migrations must agree: `tests/test_migrations.py` compares them.
- UUID primary keys, timezone-aware timestamps, named constraints (naming convention in
  `affilio/db/base.py`), `organization_id` on every tenant-scoped table.
- Enum-like columns are VARCHAR + CHECK constraint, not native PostgreSQL enums.

## 8. Tests and verification

- Add tests for meaningful behavior: endpoints, error paths, migrations, services.
- Before reporting completion run the relevant checks: `pnpm lint`, `pnpm typecheck`,
  `pnpm build`, and `uv run pytest` (with `TEST_DATABASE_URL` when touching the DB).
- Never claim a test, build, or check passed unless it was executed in this session.
  Distinguish "verified" from "not run" in every report.
- Do not weaken or delete a failing test to make it pass; fix the cause or explain.

## 9. Code quality

- TypeScript strict mode, ESLint, Prettier for the web; Ruff (lint + format) for Python.
- Keep types honest: Pydantic schemas in `apps/api` and the mirrored types in
  `packages/shared-types` change together.
- Structured logging via structlog; include `request_id` context. No `print` outside scripts.
- Errors use `AppError` subclasses and the `{"error": {...}}` envelope.

## 10. Documentation

- Record architectural decisions as ADRs in `docs/decisions/NNNN-title.md`.
- Update `docs/architecture/*.md` when module boundaries or tables change.
- Update `README.md` when commands, ports, or setup steps change.
- Mark a milestone document "Accepted" only after its acceptance criteria are verified.

## 11. Reporting

Every completion report must include: what was implemented, commands executed with
results, what was verified vs. not, remaining limitations, and the recommended next step.
