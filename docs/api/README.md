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

Organization-scoped like media. Writes (`POST`/`PATCH`/`DELETE`) require the `editor` role
(session cookie) or the service token. Decimal fields are strings (`"199000.00"`, rate `"0.0800"`).

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

## Authentication and identity (M3A)

All routes below `/api/v1` except `/health`, `/system/info`, `/auth/login` and `/auth/logout`
require an identity. Errors: 401 `authentication_required` (no/invalid session), 403
`forbidden` (role too low or non-member organization; details carry `required`/`role` or
`slug`), 403 `csrf_rejected` (cookie-authenticated write from a foreign origin), 403
`admin_endpoints_disabled` (service token sent but not configured).

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | `{email, password, organization_slug?}` → current user + `Set-Cookie: affilio_session` (HttpOnly, Lax, Secure outside dev). 401 `invalid_credentials` |
| POST | `/api/v1/auth/logout` | Revokes the session, clears the cookie (204) |
| GET | `/api/v1/auth/me` | `{id, email, display_name, identity_kind user\|service, organization{slug,name,timezone,role}, memberships[], permissions{view,edit,approve,manage_accounts,manage_members}}` |
| POST | `/api/v1/auth/password` | `{current_password, new_password ≥ 10}`; other sessions are revoked |
| GET/POST | `/api/v1/auth/members` | Members; create `{email, password, display_name?, role editor\|admin\|viewer}` (**admin**) |
| PATCH | `/api/v1/auth/members/{user_id}` | `{role}` (**admin**; owners untouched) |
| PATCH | `/api/v1/auth/organization` | `{name?, timezone? (IANA)}` (**admin**); 422 `invalid_timezone` |
| GET | `/api/v1/auth/audit` | `entity_type`, `entity_id`, `limit` ≤ 200 (**admin**) |

Role requirements: reads need any membership; product/procurement/production/link/campaign/
publication writes need `editor`; approvals, revocations, reconciliation, account registry
changes, members, audit, handoff review, purchase-request decisions and scan triggers need
`admin`. `X-Organization-Slug` switches between organizations the user belongs to.

## Social account registry (M3A)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/accounts` | `platform?`, `include_disabled?` |
| POST | `/api/v1/accounts` | Manual registration `{platform, account_type, display_name, handle?, external_account_id?, profile_url?, notes?}` → `status: disconnected`, `is_verified: false`, `source: manual`, platform `capabilities` (**admin**). 409 on duplicate platform id |
| GET/PATCH | `/api/v1/accounts/{id}` | Detail / edit label fields (**admin** for PATCH) |
| POST | `/api/v1/accounts/{id}/status` | `{status: disabled\|disconnected, note?}` (**admin**). `connected` requires OAuth (M3B) |

No credentials are stored or accepted. `connection_label` is the honest UI string.

## Affiliate links (M3A)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/content/links` | `status` (default active; empty = all), `platform`, `product_id`, `q` |
| POST | `/api/v1/content/links` | `{platform, label, url (http(s) only), product_id?, notes?}` (**editor**). 409 when the normalized URL is already active |
| GET/PATCH | `/api/v1/content/links/{id}` | Edit / `status: archived` (**editor**) |

## Distribution (M3A)

See `docs/architecture/distribution.md` for the state machine and invariants.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/publishing/stats` | `{by_status, due_now, needs_attention, campaigns_active, accounts_registered, links_active}` |
| GET | `/api/v1/publishing/queue` | Non-terminal, non-draft jobs ordered by urgency |
| POST | `/api/v1/publishing/queue/release-due` | SCHEDULED jobs whose time has come → READY_TO_PUBLISH (**editor** or service token); never publishes |
| GET | `/api/v1/publishing/calendar` | `start`, `end` (UTC, ≤ 62 days; default −7/+35 days) → `{timezone, items[]}` |
| GET/POST | `/api/v1/publishing/campaigns` | Paginated (`status`, `q`); create `{name, description?, starts_on?, ends_on?, notes?, media_asset_ids[], social_account_ids[]}` (**editor**). 422 `media_not_approved`, `target_unavailable` |
| GET/PATCH | `/api/v1/publishing/campaigns/{id}` | Detail with media, targets, publications / edit incl. `status` |
| POST/DELETE | `/api/v1/publishing/campaigns/{id}/media[/{asset_id}]`, `…/targets[/{account_id}]` | Manage approved finals and targets |
| GET/POST | `/api/v1/publishing/publications` | Paginated (`status`, `statuses`, `campaign_id`, `social_account_id`, `media_asset_id`, `platform`, `q`, `scheduled_from/to`, `sort`); create `{campaign_id, media_asset_id, social_account_id, title?, caption?, cta?, hashtags[], link_ids[], content_notes?, scheduled_at?, idempotency_key?}` → 201, or 200 with the existing job for a repeated key. 409 `duplicate_publication` |
| POST | `/api/v1/publishing/publications/batch` | `{campaign_id, media_asset_id, items[{social_account_id, …content, idempotency_key?}]}` → `{created[], reused[]}` |
| GET | `/api/v1/publishing/publications/{id}` | Detail: live content, `links[]`, `approved_snapshot`, `snapshots[]`, `attempts[]`, evidence, `validation`, `allowed_actions[]`, platform `capabilities` |
| PATCH | `/api/v1/publishing/publications/{id}` | Content edit with `version`; approved states drop back to DRAFT. 409 `version_conflict` |
| GET | `/api/v1/publishing/publications/{id}/prepare` | Assisted checklist from the approved snapshot (409 without approval) |
| POST | `…/{id}/submit`, `/return-to-draft`, `/approve`*, `/revoke-approval`*, `/unschedule`, `/start`, `/retry`, `/revise`, `/cancel` | `{version, note?}`; `*` = **admin**. 409 `invalid_publication_transition` |
| POST | `…/{id}/schedule` | `{version, scheduled_at (tz-aware), allow_conflict?, note?}`; 422 `schedule_invalid`, 409 `schedule_conflict` (details list conflicts), 409 `target_blocked` / `revalidation_required` |
| POST | `…/{id}/confirm` | `{version, external_post_url, external_post_id?, published_at?, note?}` → PUBLISHED with `evidence_kind: operator_reported`; 422 `evidence_invalid`, 409 `duplicate_evidence` |
| POST | `…/{id}/fail` | `{version, reason, outcome: failed\|ambiguous}` → FAILED or NEEDS_RECONCILIATION |
| POST | `…/{id}/reconcile` | `{version, outcome: published\|not_published\|still_unknown, external_post_url?, note?}` (**admin**) |
| POST | `…/{id}/revalidate` | `{version, note?}` admin confirms the approved snapshot still applies after the video was reclassified; 409 `target_blocked` when the target is ineligible (**admin**) |

## Content taxonomy, folder mapping, channel routing (M3A.1)

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/api/v1/content-categories` | Tree depth-first (`include_archived`); create `{name, slug?, parent_id?, description?, sort_order?}` (**admin**). 409 `duplicate_category_slug`, 422 `category_cycle` |
| GET/PATCH/DELETE | `/api/v1/content-categories/{id}` | Edit/move/archive (**admin**); delete only unreferenced leaves, else 409 `category_in_use` |
| POST | `/api/v1/content-categories/ensure-uncategorized` | Creates the fallback if missing (**admin**) |
| GET/POST | `/api/v1/media/folder-rules` | Rules for FINAL roots: `{media_root_id, relative_folder_path, category_id, include_subfolders?, priority?, is_active?}` (**admin**). 422 `invalid_folder_path`, 409 on duplicate path, 409 for non-final roots |
| PATCH/DELETE | `/api/v1/media/folder-rules/{id}` | Edit / disable / delete (**admin**) |
| GET | `/api/v1/media/folder-rules/preview?media_root_id&relative_folder_path` | Winning rule, fallback flag, all candidates |
| GET | `/api/v1/media/folder-rules/report` | Folders holding indexed finals with their mapping (`mapped=false` = unmapped) |
| PUT | `/api/v1/media/assets/{id}/categories` | `{primary_category_id: id\|null, secondary_category_ids[]}` manual override (**editor**, finals only) |
| GET | `/api/v1/media/assets?category_id=&uncategorized=` | Category filter includes descendants |
| GET | `/api/v1/media/categories/counts` | Primary-category counts |
| GET/PUT | `/api/v1/accounts/{id}/channel-profile` (alias GET `…/category-rules`) | Routing profile + rules `{primary_category_id?, description?, target_audience?, language?, country?, routing_enabled, rules[{category_id, match_policy allow\|prefer\|block, priority?}]}` (**admin** for PUT) |
| GET | `/api/v1/distribution/recommendations?media_asset_id&campaign_id?` | `{primary_category, secondary_categories, media_approved, recommended[], low_relevance[], blocked[]}` with scores and reasons; read-only |

### Other module routers

`analytics` and `agents` mount **empty** routers until their milestone.
