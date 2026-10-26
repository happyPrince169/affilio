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

## Media (M1)

All media routes are organization-scoped: send `X-Organization-Slug: <slug>` or rely on
`DEFAULT_ORGANIZATION_SLUG`. Unknown slug → 404 `not_found`.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/media/roots` | `[{key, kind, access, name, configured, exists, readable, writable, scan_enabled, last_scan_at}]`. Never includes paths. |
| GET | `/api/v1/media/stats` | Counters: total/active/missing/unsupported/duplicates/with_proxy/thumbnails_*/total_bytes/total_duration_seconds |
| GET | `/api/v1/media/assets` | Query: `q`, `status`, `orientation` (vertical\|horizontal\|square), `has_proxy`, `video_codec`, `duplicates_only`, `sort` (newest\|oldest\|title\|duration\|size\|last_seen), `page`, `page_size` (≤100). Returns `{items, page, page_size, total, total_pages}`. |
| GET | `/api/v1/media/assets/{id}` | Summary + `variants[]` (original/proxy/thumbnail/export with per-file probe data) + `duplicates[]` |
| GET | `/api/v1/media/assets/{id}/thumbnail` | `image/jpeg`, `Cache-Control: private, max-age=3600`; 404 until the worker renders it |
| GET | `/api/v1/media/scans/status` | `{current, last_completed, roots, worker_hint}` |
| GET | `/api/v1/media/scans?limit=` | History, newest first |
| GET | `/api/v1/media/scans/{id}` | One job incl. `stats` and up to 200 `errors[{kind,path,message}]` |
| POST | `/api/v1/media/scans` | Body `{root_key?: "originals", full_rescan?: false}` → 202 queued job. Requires `X-Affilio-Admin-Token` when `MEDIA_ADMIN_TOKEN` is set (always in production). 409 `scan_already_active`, 409 `media_root_not_configured`, 503 `media_root_unavailable`. |

Error codes specific to media: `invalid_media_path` (400), `media_root_not_configured` (409),
`scan_already_active` (409), `media_root_unavailable` (503), `admin_token_required` (401),
`admin_endpoints_disabled` (403).

## Products (M2)

Organization-scoped like media. Writes (`POST`/`PATCH`/`DELETE`) require the admin token when
`MEDIA_ADMIN_TOKEN` is set. Decimal fields are strings (`"199000.00"`, rate `"0.0800"`).

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/products` | Query: `q` (name/seller/category/platform id), `status`, `platform`, `category`, `has_media`, `sort` (newest\|oldest\|name\|updated\|price\|commission), `page`, `page_size` ≤ 100 |
| GET | `/api/v1/products/stats` | `{total, by_status, with_media, with_affiliate_url, with_estimated_commission}` |
| POST | `/api/v1/products` | Body: name (required) + optional fields; `status` may not be `winner`. 201 → detail. 409 `duplicate_product`, 422 `invalid_product_url` / `validation_error` |
| GET | `/api/v1/products/{id}` | Detail incl. `research_notes`, `status_events[]`, `allowed_transitions[]` |
| PATCH | `/api/v1/products/{id}` | Partial update; `null` clears a field; status is not editable here |
| POST | `/api/v1/products/{id}/status` | `{status, note?, confirmed?}`. 409 `invalid_status_transition` (details list `allowed`), 422 `winner_confirmation_required` |
| GET | `/api/v1/products/{id}/status-history` | Audit trail, newest first |
| GET | `/api/v1/products/{id}/media` | Links with embedded asset summaries, primary first |
| POST | `/api/v1/products/{id}/media` | `{media_asset_id, is_primary?, notes?}` → 201. 409 `duplicate_media_link`; 404 when the asset is unknown or belongs to another organization |
| PATCH | `/api/v1/products/{id}/media/{asset_id}` | `{is_primary?, notes?}`; setting primary clears it on other products for that asset |
| DELETE | `/api/v1/products/{id}/media/{asset_id}` | 204 |
| GET | `/api/v1/products/media/{asset_id}` | Products linked to an asset (reverse lookup) |

## MeoScout import, procurement, production, handoff (M2B)

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/products/imports/preview` | multipart `file` (CSV ≤ 5 MB, ≤ 5000 rows), optional `mapping` (JSON field→column), `on_duplicate` skip\|update. Returns headers, suggested mapping, per-row actions, counts, `already_imported` |
| POST | `/api/v1/products/imports` | Same form + `force`. 201 → import record. Same file hash → `status: skipped` unless `force=true` |
| GET | `/api/v1/products/imports` | Import history |
| GET/POST | `/api/v1/procurement/requests` | Purchase requests; `POST …/{id}/decision` `{status: approved\|rejected\|cancelled\|requested, note?}` |
| GET/POST | `/api/v1/procurement/orders` | Orders (paginated, `status`, `q`); create with `items[]` of approved request ids or free product lines. 409 `duplicate_order`, `request_not_approved` |
| GET/PATCH | `/api/v1/procurement/orders/{id}` | Detail / edit fields |
| POST | `/api/v1/procurement/orders/{id}/status` | Transition map; 409 `invalid_procurement_transition` |
| POST/DELETE | `/api/v1/procurement/orders/{id}/items[/{item_id}]` | Add a line / remove a draft line |
| POST | `/api/v1/procurement/orders/{id}/receive` | `{items:[{item_id, quantity}], note?}` cumulative; status becomes partially_received/received |
| GET | `/api/v1/procurement/stats`, `/products/{product_id}` | Counters, per-product procurement summary |
| GET/POST | `/api/v1/production/jobs` | Jobs (`q`, `status`, `editing_status`, `product_id`, `media_asset_id`, `sort`) |
| GET/PATCH | `/api/v1/production/jobs/{id}` | Detail / edit |
| POST | `/api/v1/production/jobs/{id}/status` | `{status?, editing_status?, note?}`; editing requires `filmed` |
| POST/DELETE | `/api/v1/production/jobs/{id}/products[/{product_id}]`, `…/media[/{asset_id}]` | Optional associations; 409 `duplicate_job_association` |
| GET | `/api/v1/production/stats` | Counters incl. upcoming (7 days) and overdue |
| POST | `/api/v1/media/assets/{id}/handoff` | `{status: approved\|rejected\|pending_review, note?}` for `asset_kind = final` only; approve/reject need a note |
| GET | `/api/v1/media/assets?asset_kind=final&handoff_status=pending_review` | Handoff queue |

### Other module routers

`identity`, `accounts`, `content`, `publishing`, `analytics`, `agents` mount **empty** routers
at `/api/v1/<module>` until their milestone.
