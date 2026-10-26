# Architecture overview

## Vision in one paragraph

AFFILIO manages affiliate content for many social and marketplace accounts from one place:
media comes from a Synology NAS, products come from affiliate programs (Shopee first),
Claude assists with planning and captions, content is scheduled and published through
official platform APIs, and performance data flows back to close the loop. The MVP proves
that loop for a single Shopee Affiliate account.

## Shape: modular monolith

One deployable API, one web app, one database. Modules are separated by import rules and
ownership, not by processes. See `docs/decisions/0001-modular-monolith.md`.

```
┌──────────────────────────┐      HTTP/JSON       ┌────────────────────────────────────┐
│ apps/web (Next.js)       │ ───────────────────▶ │ apps/api (FastAPI)                 │
│  App Router, RSC shell   │  /api/v1/*           │  core/      config, logging,       │
│  TanStack Query (client) │                      │             errors, middleware     │
│  shadcn/ui + Tailwind    │ ◀─────────────────── │  api/v1/    health, system, router │
└──────────────────────────┘  X-Request-ID, CORS  │  db/        base, session, registry│
                                                  │  modules/   identity ▸ accounts ▸  │
packages/shared-types ◀── mirrors schemas ──────▶ │             media ▸ products ▸     │
                                                  │             content ▸ publishing ▸ │
                                                  │             analytics ▸ agents     │
                                                  └───────────────┬────────────────────┘
                                                                  │ SQLAlchemy 2 (sync, psycopg 3)
                                                                  ▼
                                                  ┌────────────────────────────────────┐
                                                  │ PostgreSQL 17 (docker compose)     │
                                                  │ Alembic migrations                 │
                                                  └────────────────────────────────────┘
```

## Processes

| Process | Entry point | Responsibility |
| --- | --- | --- |
| web | `apps/web` (Next.js standalone) | UI; in production also proxies `/api/*` to the API, forwarding the session cookie (identity-neutral) |
| api | `uvicorn affilio.main:app` | HTTP API; enqueues work, never runs ffmpeg |
| worker | `python -m affilio.worker` | Claims scan jobs and pending thumbnails from PostgreSQL (`SKIP LOCKED`), runs ffprobe/ffmpeg |
| postgres | container | Only state besides regenerable thumbnails |

Media files are addressed as logical root + relative path (ADR 0005); roots are bound to
container/host paths from settings. Originals and proxies are read-only.

## Request lifecycle (API)

1. `CORSMiddleware` (outermost) — allows configured web origins, exposes `X-Request-ID`.
2. `RequestContextMiddleware` — reads or generates a request id, binds it to structlog
   context, adds the response header, logs one `http.request` line with duration.
3. Router — `/health` (unversioned) and everything under `/api/v1`.
4. Exception handlers — `AppError`, `HTTPException`, validation errors, and unhandled
   exceptions all produce the same envelope (`docs/api/README.md`).

## Frontend principles

- Server components render the shell; client components handle interactivity and data
  fetching with TanStack Query. The API base URL is `NEXT_PUBLIC_API_URL`.
- Cache Components (Next 16) is enabled; pages are static shells with client-side data.
- Design tokens live in `src/app/globals.css` (OKLCH, light + dark). The brand accent is
  orange (`--brand`). shadcn/ui components are vendored into `src/components/ui`.
- Placeholder pages use `PlaceholderPage` and must state that nothing is implemented.

## Multi-account, multi-tenant readiness

- `organizations` is the tenant boundary. Every business table carries `organization_id`.
- `social_accounts` is platform-agnostic (`platform` column), so adding TikTok, Facebook,
  Instagram, or YouTube is data, not schema.
- Content will be modeled as platform-agnostic items with per-platform variants (M3).

## Organization isolation and RBAC (implemented in M3A)

1. **Authentication** — local email + password (argon2id) and server-side sessions delivered as
   HttpOnly `SameSite=Lax` cookies (`affilio.core.security`, `identity/service.py`). A service
   token (`MEDIA_ADMIN_TOKEN`) authenticates CLI/cron calls only.
2. **Organization context** — `affilio/api/deps.py` resolves an `Identity` for every request:
   the session's user plus the organization named by `X-Organization-Slug` *if the user is a
   member*, else the first membership. Missing identity → 401; non-member slug → 403.
3. **Repository scoping** — unchanged: service functions take `organization_id` and apply it
   to every query, so a valid identity still gets 404 for another tenant's rows.
4. **Roles** — `viewer < editor < admin < owner`. `RequireEditor` guards ordinary writes,
   `RequireAdmin` guards approvals, reconciliation, account registry, members, audit, handoff
   review, purchase decisions and scan triggers. `GET /auth/me` returns the permission set
   the UI uses to hide actions; the API remains the enforcement point.
5. **Audit** — `audit_events` records the acting user id for every security-relevant and
   publishing action. Details and threat notes: `docs/architecture/security.md`.
6. **Defense in depth** — PostgreSQL row-level security remains an option once the access
   model stabilizes; it is not planned for the MVP.

## Observability

- structlog with console (dev) or JSON (prod/CI) output; `request_id` on every line.
- `/api/v1/system/info` reports DB readiness and latency for dashboards and probes.
- Error responses carry the request id so a user-visible failure maps to a log line.

## Out of scope by design

Microservices, Kafka, Kubernetes, Elasticsearch, unofficial platform APIs, browser
automation, cookie-based publishing. Redis only with a documented need.
