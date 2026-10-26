# ADR 0003 — Synchronous SQLAlchemy for the MVP

- **Status:** Accepted (2026-10-26)
- **Milestone:** M0

## Context

FastAPI supports both async and sync endpoints. SQLAlchemy 2 offers an async engine
(`asyncpg`/`psycopg` async) and a sync engine. The MVP serves one organization, a handful of
users, and low request volume; the heavy work (media scanning, imports) will be batch jobs.

## Decision

Use the **synchronous** SQLAlchemy 2 engine with **psycopg 3**, `pool_pre_ping`, and a
request-scoped `Session` dependency (`affilio.db.session.get_db`). Endpoints that touch the
database are plain `def` functions, which FastAPI runs in its thread pool. Alembic, the seed
script, and tests use the same engine code path.

## Consequences

- Simpler code: no `await` on every query, no lazy-loading pitfalls, one way to open a
  session everywhere (requests, scripts, migrations, tests).
- Throughput is bounded by the thread pool, which is more than adequate for the MVP.
- If a later milestone needs async I/O (many concurrent platform API calls), the
  module/service boundary allows migrating one module at a time to an async session; the
  URL scheme `postgresql+psycopg://` already supports the async driver.
