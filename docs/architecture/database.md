# Database design

PostgreSQL 17. Schema is owned by Alembic migrations in `apps/api/alembic/versions/`;
ORM models live in each module's `models.py` and are registered in `affilio/db/registry.py`.

## Conventions

| Concern            | Rule                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------- |
| Primary keys       | `UUID`, generated client-side (`uuid4`) with `gen_random_uuid()` server default        |
| Timestamps         | `TIMESTAMPTZ`, `created_at` / `updated_at` with `now()` defaults; `updated_at` is set on ORM update |
| Tenant scoping     | Every business table has `organization_id` → `organizations.id` (`ON DELETE CASCADE`) |
| Enum-like columns  | `VARCHAR(32)` + `CHECK (col IN (...))`; Python `StrEnum` in `affilio/core/enums.py`    |
| Constraint names   | Naming convention in `affilio/db/base.py` (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`)           |
| Access pattern     | Synchronous SQLAlchemy 2 + psycopg 3 (`docs/decisions/0003-sync-database-access.md`)    |

Native PostgreSQL enums are deliberately avoided: adding a value to a CHECK constraint is a
drop-and-recreate that downgrades cleanly, whereas `ALTER TYPE ... ADD VALUE` cannot be rolled
back inside a transaction.

## Foundation tables (migration `0001_foundation`)

```
organizations                     users
─────────────                     ─────
id            uuid PK             id            uuid PK
name          varchar(200)        email         varchar(320)  UNIQUE
slug          varchar(100) UNIQUE password_hash varchar(255)  NULL (auth design pending)
created_at    timestamptz         status        varchar(32)   CHECK invited|active|suspended
updated_at    timestamptz         created_at    timestamptz
                                  updated_at    timestamptz
        │                                   │
        │ 1                                 │ 1
        ▼ n                                 ▼ n
organization_memberships
────────────────────────
id              uuid PK
organization_id uuid FK → organizations.id  ON DELETE CASCADE
user_id         uuid FK → users.id          ON DELETE CASCADE   (ix_organization_memberships_user_id)
role            varchar(32)  CHECK owner|admin|editor|viewer
created_at      timestamptz
UNIQUE (organization_id, user_id)

social_accounts
───────────────
id                  uuid PK
organization_id     uuid FK → organizations.id  ON DELETE CASCADE
platform            varchar(32)  CHECK shopee|facebook|tiktok|instagram|youtube
display_name        varchar(200)
external_account_id varchar(255) NULL (unknown until an official connection completes)
status              varchar(32)  CHECK pending|connected|disconnected|disabled
created_at / updated_at timestamptz
UNIQUE (organization_id, platform, external_account_id)   -- uq_social_accounts_org_platform_external_id
INDEX  (organization_id, platform)                        -- ix_social_accounts_organization_id_platform
```

Why `social_accounts` carries no credentials: tokens and secrets will live in a separate,
encrypted credential table introduced with the first official integration (M3), so that
listing accounts never touches secret material and rotation/revocation is isolated.

## Seed data (development only)

`uv run python -m affilio.scripts.seed_dev` inserts, idempotently:

- organization `demo-org` — "Demo Organization (DEV ONLY)"
- user `dev@affilio.local` (no password hash, status `active`)
- membership `owner`
- social account `shopee`, status `pending`, no external id — explicitly **not connected**

The script refuses to run unless `APP_ENV` is `development` or `test`.

## Migration workflow

```bash
cd apps/api
uv run alembic revision --autogenerate -m "describe change"   # generates from model diff
# review the file: naming, downgrade(), data migrations
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head       # prove reversibility
TEST_DATABASE_URL=... uv run pytest tests/test_migrations.py     # drift check
```

`tests/test_migrations.py::test_models_match_migrations` runs Alembic's autogenerate
comparison and fails on any difference, so models and migrations cannot silently diverge.

## Media library tables (migration `0002_media_library`)

```
media_roots                                  media_scan_jobs
───────────                                  ───────────────
id              uuid PK                      id              uuid PK
organization_id FK organizations CASCADE     organization_id FK organizations CASCADE
key             varchar(64)   UNIQUE(org,key)media_root_id   FK media_roots CASCADE
kind            original|proxy|thumbnail|export  status      queued|running|succeeded|failed|cancelled
access          read_only|read_write         trigger         manual|cli|scheduled
name, scan_enabled, last_scan_at             attempt, max_attempts, next_attempt_at
created_at / updated_at                      started_at, finished_at, heartbeat_at, worker_id
                                             requested_by, stats jsonb, errors jsonb, error text
                                             full_rescan bool, created_at / updated_at
        │ 1
        ▼ n
media_file_variants                          media_assets
───────────────────                          ────────────
id              uuid PK                      id              uuid PK
asset_id        FK media_assets CASCADE      organization_id FK organizations CASCADE
media_root_id   FK media_roots  CASCADE      title           varchar(255)
kind            original|proxy|thumbnail|export  status      active|missing|unsupported
status          present|missing              duplicate_of_id FK media_assets SET NULL
relative_path   varchar(1024) UNIQUE(root,path)  duration_seconds, width, height, frame_rate,
file_name, extension, file_size, modified_at     video_codec, audio_codec, pixel_format,
fingerprint     sha256 of size+3 samples         bit_depth, bit_rate, container_format, rotation
content_hash    full sha256 (duplicates only)    probe_error text
duration_seconds … rotation (per file)       thumbnail_status pending|ready|failed|skipped
probe jsonb (trimmed ffprobe), probed_at,    thumbnail_attempts, thumbnail_error
probe_error                                  first_seen_at, last_seen_at, missing_since
last_seen_at, missing_since                  created_at / updated_at
```

Rules that follow from this design:

- **No host paths.** A file is `(media_root_id, relative_path)`; the root's path is bound from
  `MEDIA_*_PATH` at runtime (ADR 0005). The same database works on the workstation and the NAS.
- **Assets are keyed by the original.** Proxy, thumbnail, and export rows are variants of it.
- **Fast identity first.** Unchanged files are recognised by size + mtime (no reads). A changed
  or new file gets a 3 MiB sampled `fingerprint`; `content_hash` is computed only when two
  files share a fingerprint, to confirm `duplicate_of_id`.
- **Jobs are the queue.** `media_scan_jobs` rows with `status = queued` are claimed by the
  worker with `FOR UPDATE SKIP LOCKED` (ADR 0004); `errors` keeps up to 200 entries per scan.
- Indexes: `(organization_id, status)`, `(organization_id, created_at)`, `thumbnail_status`
  on assets; `asset_id`, `(media_root_id, fingerprint)`, `content_hash` on variants;
  `(status, next_attempt_at)` on jobs.

## Product catalog tables (migration `0003_product_catalog`)

```
products                                        product_status_events
────────                                        ─────────────────────
id              uuid PK                         id          uuid PK
organization_id FK organizations CASCADE        product_id  FK products CASCADE
name            varchar(255)                    from_status varchar(32) NULL  (CHECK)
platform        shopee|facebook|tiktok|…        to_status   varchar(32)       (CHECK)
product_url, product_url_normalized varchar(2048) NULL   note text, actor varchar(255)
affiliate_url   varchar(2048) NULL              confirmed   bool   (true only for WINNER)
platform_product_id varchar(128) NULL           created_at  timestamptz
seller_name, category                           INDEX (product_id, created_at)
images          jsonb [] (reference URLs)
currency char(3), observed_price numeric(14,2), product_media_links
price_observed_at                               ───────────────────
estimated_commission_rate   numeric(6,4)        id, organization_id FK, product_id FK CASCADE,
estimated_commission_amount numeric(14,2)       media_asset_id FK media_assets CASCADE
status  discovered|shortlisted|testing|          is_primary bool, notes text, created_at
        winner|paused|archived                  UNIQUE (product_id, media_asset_id)
status_changed_at, research_notes text          UNIQUE (media_asset_id) WHERE is_primary
created_at / updated_at                         INDEX media_asset_id, organization_id
UNIQUE (organization_id, platform, platform_product_id) WHERE platform_product_id IS NOT NULL
UNIQUE (organization_id, product_url_normalized)        WHERE product_url_normalized IS NOT NULL
INDEX (organization_id, status), (organization_id, created_at)
```

- **Unknown identifiers are allowed.** Shopee product ids are not assumed stable or present, so
  uniqueness is enforced only when a value is known (partial unique indexes).
- **Estimated vs. actual commission.** `estimated_*` columns are operator estimates. Actual
  earned commission is recorded by the analytics module (M4) in its own tables and joined to
  `products.id`; it never overwrites these columns.
- **WINNER is human-confirmed.** The lifecycle is enforced in `products/lifecycle.py` and every
  change is written to `product_status_events` (`confirmed=true` only for a winner decision).

## Procurement, production, imports and handoff (migration `0004_procurement_production`)

```
product_imports (MeoScout CSV runs)        purchase_requests          purchase_orders
  id, organization_id, source, status        id, organization_id,       id, organization_id,
  file_name, file_sha256 (idempotency)       product_id FK CASCADE,     order_number UNIQUE(org) PO-YYYY-NNNN,
  mapping jsonb, on_duplicate, row_count,    status requested|approved| status draft|placed|shipped|delivered|
  created/updated/skipped/failed counts,       rejected|ordered|cancelled        partially_received|received|cancelled
  errors jsonb, duplicate_of_import_id,      quantity, reason,          platform, supplier_name, platform_order_ref,
  actor, created_at                          requested_by, decided_by,  currency, shipping_cost, total_cost (derived),
products += source manual|meoscout,          decided_at, decision_note  tracking_number, tracking_url, ordered_at,
            source_ref, import_id FK                                    expected_delivery_at, delivered_at, notes

purchase_order_items                        production_jobs
  id, order_id FK CASCADE, product_id FK      id, organization_id, job_number UNIQUE(org) PJ-YYYY-NNNN,
  RESTRICT, purchase_request_id FK SET NULL   title, description, status planned|scheduled|filming|filmed|cancelled,
  UNIQUE (one request → one order line),      editing_status not_started|in_progress|review|done,
  quantity_ordered, quantity_received,        planned_filming_date, deadline, staff_labels jsonb, notes
  unit_cost, received_at, notes             production_job_products: job_id, product_id, purchase_order_item_id?,
  UNIQUE (order_id, product_id)               quantity_used, notes   UNIQUE (job, product)
                                            production_job_media:    job_id, media_asset_id, role footage|final|
media_assets += asset_kind footage|final,     reference, notes      UNIQUE (job, asset)
  handoff_status pending_review|approved|rejected (NULL for footage),
  handoff_note, handoff_reviewed_at, handoff_reviewed_by
media_roots.kind / media_file_variants.kind += final
```

## Growth plan (not implemented)

| Milestone | Tables (indicative)                                                              |
| --------- | -------------------------------------------------------------------------------- |
| M1 (later)| `media_tags`, `media_asset_tags` once tagging is prioritised                     |
| M2 (later)| `product_price_history` when analytics needs price trends                        | `products`, `product_links`, `product_media`                                     |
| M3        | `content_items`, `content_variants`, `publish_jobs`, `account_credentials` (encrypted) |
| M4        | `performance_imports`, `performance_metrics`, `commission_records`               |
| M5        | `agent_runs`, `agent_tool_calls`, `approvals`                                    |

Each milestone adds only the tables it needs and documents them here.
