# ADR 0002 — uv for Python, pnpm + Turborepo for JavaScript

- **Status:** Accepted (2026-10-26)
- **Milestone:** M0

## Context

The monorepo contains a TypeScript web app and a Python API. Turborepo orchestrates tasks
through `package.json` scripts. The requirement is explicit: Python dependencies must not be
forced into pnpm, and the API keeps its own virtual-environment conventions.

## Decision

- `apps/api` is a standard Python project (`pyproject.toml`, `uv.lock`, `.venv/`) managed by
  **uv**. Python 3.12 is pinned in `.python-version`; uv provisions it when absent.
- `apps/api/package.json` exists only as a **task adapter**: scripts such as `lint`, `test`,
  `migrate` call `uv run ...`. It declares no npm dependencies, so pnpm installs nothing
  for it while `pnpm turbo run lint test` still covers the API.
- CI installs Python deps with `uv sync --frozen` and JS deps with `pnpm install
  --frozen-lockfile`, in separate jobs.

## Consequences

- Developers run one command set (`pnpm lint|test|check`) across both languages.
- Python tooling (Ruff, pytest, Alembic) stays idiomatic and fast; no Node wrappers.
- Two lockfiles must be kept in sync by their own tool. `CLAUDE.md` records this.
