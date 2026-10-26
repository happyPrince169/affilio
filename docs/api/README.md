# API reference (M0)

Base URL (local): `http://localhost:8000`. Interactive docs: `/docs` (Swagger UI), `/redoc`.
Versioned routes live under `/api/v1`. TypeScript mirrors of every response type are in
`packages/shared-types/src/api.ts`.

## Conventions

- **Request ID** — every response carries `X-Request-ID`. Send your own (≤128 printable
  chars) and it is echoed; otherwise a UUID hex is generated. The same id appears in
  structured logs and in error bodies.
- **CORS** — origins from `CORS_ORIGINS`; credentials allowed; `X-Request-ID` exposed.
- **Timestamps** — ISO 8601, UTC, e.g. `2026-10-26T03:52:54.014580Z`.
- **Errors** — always the envelope below, never FastAPI's default `{"detail": ...}`.

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [{ "loc": ["query", "count"], "msg": "...", "type": "int_parsing" }],
    "request_id": "demo-123"
  }
}
```

| HTTP | `code`               | When                                              |
| ---- | -------------------- | ------------------------------------------------- |
| 400  | `bad_request`        | generic client error                              |
| 401  | `unauthorized`       | reserved for authentication (later milestone)     |
| 403  | `forbidden` / `permission_denied` | RBAC (later milestone)              |
| 404  | `not_found`          | unknown route or `NotFoundError`                  |
| 409  | `conflict`           | `ConflictError`                                   |
| 422  | `validation_error`   | request body/query/path validation                |
| 500  | `internal_error`     | unhandled exception; message is generic on purpose |

Domain code raises `affilio.core.errors.AppError` (or a subclass) with a `code`,
`status_code`, and optional `details`; the handler renders the envelope.

## Endpoints

### `GET /health` and `GET /api/v1/health`

Liveness. Does not touch the database. The unversioned path is for infrastructure probes
and is hidden from the OpenAPI schema.

```json
{ "status": "ok", "service": "AFFILIO API", "version": "0.1.0", "environment": "development", "timestamp": "..." }
```

### `GET /api/v1/system/info`

Readiness-style diagnostics. Runs `SELECT 1` against the database and reports latency.
Never includes connection strings, hostnames, or environment variable values.

```json
{
  "service": "AFFILIO API",
  "version": "0.1.0",
  "environment": "development",
  "api_prefix": "/api/v1",
  "python_version": "3.12.11",
  "timestamp": "...",
  "database": { "status": "ok", "dialect": "postgresql", "latency_ms": 2.1 },
  "modules": ["identity", "accounts", "media", "products", "content", "publishing", "analytics", "agents"]
}
```

`database.status` is `"unavailable"` (with `latency_ms: null`) when the probe fails; the
endpoint still returns 200 so dashboards can display the degraded state.

### Module routers

Each domain module mounts an **empty** router at `/api/v1/<module>` in M0. They exist so
the wiring is in place; they expose no routes until their milestone.
