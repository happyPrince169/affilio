# Database design

PostgreSQL 17. Schema is owned by Alembic migrations in `apps/api/alembic/versions/`;
ORM models live in each module's `models.py` and are registered in `affilio/db/registry.py`.

## Conventions

| Concern           | Rule                                                                                                |
| ----------------- | --------------------------------------------------------------------------------------------------- |
| Primary keys      | `UUID`, generated client-side (`uuid4`) with `gen_random_uuid()` server default                     |
| Timestamps        | `TIMESTAMPTZ`, `created_at` / `updated_at` with `now()` defaults; `updated_at` is set on ORM update |
| Tenant scoping    | Every business table has `organization_id` → `organizations.id` (`ON DELETE CASCADE`)               |
| Enum-like columns | `VARCHAR(32)` + `CHECK (col IN (...))`; Python `StrEnum` in `affilio/core/enums.py`                 |
| Constraint names  | Naming convention in `affilio/db/base.py` (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`)                       |
| Access pattern    | Synchronous SQLAlchemy 2 + psycopg 3 (`docs/decisions/0003-sync-database-access.md`)                |

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

## Authentication, registry, links and distribution (migration `0005_distribution_core`)

```
organizations += timezone (IANA, display only; storage is UTC)
users += display_name, last_login_at            user_sessions
                                                  id, user_id FK CASCADE, token_hash UNIQUE (sha256 of the cookie),
audit_events (append-only)                        expires_at, last_seen_at, revoked_at, user_agent, ip_address
  id, organization_id FK?, actor_kind user|service|system, actor_user_id FK SET NULL, actor_label,
  action, entity_type, entity_id, data jsonb, request_id, created_at

social_accounts += handle, account_type page|profile|group|channel|shop, capabilities jsonb,
  is_verified bool (false until OAuth), source manual|oauth, profile_url, notes, registered_by

affiliate_links                                 campaigns
  id, organization_id, platform, product_id FK    id, organization_id, name, description,
  SET NULL, label, url, url_normalized, source     status draft|active|completed|archived,
  manual|import, status active|archived, notes,    starts_on, ends_on, notes, created_by_user_id
  created_by; UNIQUE(org, platform, url_normalized) campaign_media (campaign_id, media_asset_id) UNIQUE
  WHERE status = 'active'                          campaign_targets (campaign_id, social_account_id) UNIQUE

publication_jobs                                publication_snapshots (immutable)
  id, organization_id, campaign_id, media_asset_id, id, job_id FK CASCADE, content_version, title,
  social_account_id, platform, intent_id UNIQUE,     caption, cta, hashtags, links, media, target (jsonb),
  idempotency_key UNIQUE(org) WHERE NOT NULL,        approved_by_user_id, approved_by_label, approved_at
  status (10 values), version, title, caption, cta, publication_links (job_id, affiliate_link_id, position)
  hashtags jsonb, content_notes, content_version,    UNIQUE(job, link)
  scheduled_at, approved_snapshot_id FK SET NULL,  publication_attempts
  published_at, external_post_id, external_post_url, id, job_id, attempt_no UNIQUE(job), mode assisted|api,
  evidence_kind operator_reported|api_verified,      adapter, status started|succeeded|failed|ambiguous,
  confirmed_by_user_id/label/at, confirmation_note,  snapshot_id FK SET NULL, started_by_label, started_at,
  last_error, created_by_user_id, status_changed_at  finished_at, detail jsonb, error
  UNIQUE(campaign_id, media_asset_id, social_account_id)
  UNIQUE(social_account_id, external_post_id) WHERE external_post_id IS NOT NULL
```

## Content taxonomy, folder classification, channel routing (migration `0006_content_routing`)

```
content_categories                              channel_profiles (1:1 social_accounts)
  id, organization_id, parent_id FK RESTRICT,     id, organization_id, social_account_id UNIQUE,
  slug, name, description, status active|archived, primary_category_id FK SET NULL, description,
  sort_order; UNIQUE(org, coalesce(parent,0), slug) target_audience, language, country(2), routing_enabled
media_assets += classification_changed_at      channel_category_rules
media_content_categories                          id, organization_id, social_account_id, category_id
  id, organization_id, media_asset_id, category_id  FK RESTRICT, match_policy allow|prefer|block, priority
  FK RESTRICT, is_primary, assignment_source        UNIQUE(account, category)
  folder|manual|import|ai_future; UNIQUE(asset, category);
  partial UNIQUE(asset) WHERE is_primary
folder_category_rules
  id, organization_id, media_root_id FK CASCADE, relative_folder_path (normalized, '' = root),
  category_id FK RESTRICT, include_subfolders, priority, is_active; UNIQUE(root, path)
```

## Publication revalidation markers (migration `0007_publication_revalidation`)

```
publication_jobs += revalidated_at, revalidated_by_label
```

Owned by 0007, not 0006. 0007 is idempotent: it adds each column only if missing and drops
each only if present, so it is safe on databases that already carry the columns.

### Reconciling databases patched by hand

Symptom: a database is stamped `0006_content_routing` but `publication_jobs` already has
`revalidated_at` / `revalidated_by_label` (they were added with `ALTER TABLE` while the
interim 0006 contained them, or the database was built from that interim 0006).

Procedure (no data loss, Alembic history preserved):

1. Back up: `pnpm db:backup` (or `pg_dump`).
2. Check the state: `alembic current` must print `0006_content_routing`;
   `\d publication_jobs` shows whether the two columns exist. Either state is fine.
3. Run `alembic upgrade head`. 0007 skips columns that exist and creates the missing ones,
   then stamps `0007_publication_revalidation`. Never run `alembic stamp` to skip 0007.
4. Verify: `alembic current` prints `0007_publication_revalidation (head)` and
   `uv run pytest tests/test_migrations.py` reports no drift.

Databases that never saw the interim 0006 need nothing special: `upgrade head` applies 0006
(original shape) and then 0007.

## Meta connection infrastructure (migration `0008_meta_connections`)

```
platform_connections                            account_credentials (encrypted; versioned)
  id, organization_id FK, provider meta,          id, organization_id FK, connection_id FK CASCADE,
  external_user_id, external_user_name,           social_account_id FK CASCADE (null until linked),
  status active|revoked|expired|invalid,          external_subject_id (Meta user or Page id),
  granted_scopes, missing_scopes, discovered_pages kind meta_user_long_lived|meta_page, version,
  (jsonb, no tokens), token_expires_at,           status active|superseded|revoked|invalid,
  data_access_expires_at, is_mock,                ciphertext bytea, nonce bytea, key_id,
  connected_by_user_id FK SET NULL,               token_fingerprint (sha256), scopes, page_tasks,
  connected_by_label, connected_at,               issued_at, expires_at, data_access_expires_at,
  last_checked_at, last_error, created/updated    last_validated_at, last_used_at,
  UNIQUE(organization_id, provider, external_user_id) invalidated_reason, is_mock, created_by_label
                                                  partial UNIQUE(connection_id, external_subject_id,
oauth_states (one-time attempts)                     kind) WHERE status = 'active'
  id, organization_id FK, user_id FK, session_id FK SET NULL, provider, state_hash UNIQUE (sha256),
  redirect_uri, requested_scopes, mock_scenario, status pending|completed|denied|failed|expired,
  error, connection_id FK SET NULL, expires_at, consumed_at, created_at

social_accounts += connection_id FK SET NULL, connected_at, last_verified_at, disconnect_reason,
  page_tasks jsonb (nullable), is_mock bool
```

No table stores a plaintext token or a host-specific value. `is_mock` rows only exist in
development/test databases (mock mode is refused elsewhere) and are never shown as real.

## Publishing engine (migration `0009_publishing_engine`)

```
publication_uploads (one per API attempt; durable phase record)
  id, organization_id FK, job_id FK CASCADE, attempt_id FK CASCADE UNIQUE, social_account_id FK,
  media_asset_id FK, snapshot_id FK SET NULL, provider meta, is_mock,
  phase pending|validating|session_starting|uploading|upload_complete|processing|finalizing|
        verifying|retry_wait|published|failed|needs_reconciliation|abandoned,
  publish_request_status not_sent|sent|acknowledged|confirmed|rejected|unknown,
  provider_session_id, provider_video_id, external_post_id, external_post_url,
  file_relative_path, file_size, file_mtime, bytes_total, bytes_sent, processing_status,
  last_status jsonb, last_polled_at, poll_count, finish_requested_at, finish_response jsonb,
  published_at, last_error, last_error_code, last_error_transient, retry_count, max_retries,
  next_action_at, lease_owner, lease_expires_at, started_at, finished_at, created/updated
  partial UNIQUE(job_id) WHERE phase NOT IN (published, failed, abandoned)
  index(next_action_at) WHERE live; index(social_account_id); index(organization_id, phase)
publisher_workers   id, worker_id UNIQUE, hostname, pid, version, max_concurrency, in_flight,
                    started_at, last_seen_at, stopped_at
social_accounts    += publish_hold_until, publish_hold_reason (rate limit / quota holds)
```

## Overdue schedule policy (migration `0010_overdue_review`)

```
publication_jobs += overdue_review_at (set when a SCHEDULED job was missed beyond the grace
                    window and waits for an operator's release; cleared by release/reschedule)
```

## Publish-ready exports (migration `0011_media_exports`)

Export pipeline E1 (ADR 0010, `docs/architecture/media-exports.md`).

- `media_file_variants` gains provenance for derived files: `derived_from_variant_id`
  (self FK, SET NULL), `recipe_key`, `source_fingerprint`, `source_file_size`,
  `source_modified_at`, and the partial unique index `uq_media_file_variants_asset_export_recipe`
  on `(asset_id, recipe_key) WHERE kind = 'export'` (one export per asset and recipe).
- `media_export_jobs` — the export worker's queue and audit trail: `organization_id`,
  `media_asset_id`, `source_variant_id`, `output_variant_id` (SET NULL), `status`
  (queued | running | ready | skipped | blocked | failed | cancelled, CHECK), `trigger`
  (manual | cli | approval | publish, CHECK), `recipe_key`, `recipe` JSONB (ffmpeg version and
  placeholder arguments), the source identity captured at run time, `attempt`, `max_attempts`,
  `next_attempt_at`, `started_at`, `finished_at`, `heartbeat_at`, `worker_id`, `requested_by`,
  `error_code`, redacted `error`, `stats` JSONB. Indexes on `(organization_id, created_at)`,
  `(status, next_attempt_at)`, `media_asset_id`, plus the partial unique
  `uq_media_export_jobs_active_asset_recipe` on `(media_asset_id, recipe_key)` for queued or
  running jobs.
- Downgrade drops the table, the export variant rows (they would dangle without provenance)
  and the five columns.

## Upload file identity (migration `0012_upload_file_identity`)

Export pipeline E2. `publication_uploads` gains `file_root_key` (`finals` or `exports`),
`file_fingerprint` (sampled fingerprint of the validated file) and `file_variant_id` (FK to
`media_file_variants`, SET NULL). Existing rows with a relative path are backfilled to
`finals`. The downgrade drops the three columns.

## Facet classification (migration `0013_facet_classification`)

ADR 0011. `content_categories.facet` (VARCHAR + CHECK `production_owner` | `product_category` |
`content_type`, nullable) with a partial unique index `uq_content_categories_org_facet_root`
on `(organization_id, facet)` where `parent_id IS NULL`. The check constraint on
`media_content_categories.assignment_source` gains `path`. `media_assets.production_period`
VARCHAR(7) is backfilled from `first_seen_at` (UTC month). New table:

```
category_suggestions
  id, organization_id FK CASCADE, media_root_id FK CASCADE, facet, name, normalized_name,
  relative_folder_path, example_relative_path, status pending|approved|rejected, seen_count,
  first_seen_at, last_seen_at, decided_at, decided_by, note,
  category_id FK content_categories SET NULL, created_at, updated_at
  UNIQUE(organization_id, facet, normalized_name); INDEX(organization_id, status)
```

The downgrade deletes `path` assignments, drops the table, the period column, the index and
the facet column.

## Editor → production-owner mappings (migration `0014_owner_mappings`)

ADR 0012 (upload track U2). Authorizes a member to submit finished videos for a production
owner (a value under the `production_owner` facet root). Several active rows per member;
revoked rows are kept and reactivated instead of duplicated; the owner category is
protected from deletion by the taxonomy reference registry. New table:

```
media_owner_mappings
  id, organization_id FK CASCADE, user_id FK users CASCADE,
  production_owner_category_id FK content_categories RESTRICT, status active|revoked,
  created_by_user_id FK users SET NULL, revoked_at, revoked_by_user_id FK users SET NULL,
  created_at, updated_at
  UNIQUE(organization_id, user_id, production_owner_category_id); INDEX(organization_id, user_id)
```

The downgrade drops the index and the table; mappings only authorize future submissions, so
no media attribution is lost (U4 records the owner on each upload session).

## Editor upload sessions and asset attribution (migration `0015_media_uploads`)

ADR 0012 (upload track U4). `media_assets` gains `ingest_source` (VARCHAR + CHECK `scan` |
`upload`, default `scan`), `uploaded_by_user_id` (FK users RESTRICT) and
`revision_of_asset_id` (FK media_assets SET NULL); all three are written once at registration
and have no update path. New table:

```
media_uploads
  id, organization_id FK CASCADE, uploaded_by_user_id FK users RESTRICT,
  submitted_for_user_id FK users RESTRICT null, production_owner_category_id FK RESTRICT,
  production_owner_name (snapshot), product_category_id FK RESTRICT, content_type_id FK RESTRICT,
  batch_id null, replaces_asset_id FK media_assets RESTRICT null,
  original_filename, safe_filename, expected_size, client_sha256 null, chunk_bytes,
  committed_offset, last_chunk_offset, last_chunk_sha256, last_chunk_at,
  staging_name UNIQUE (server-generated token), status created|uploading|uploaded|verifying|
  finalizing|completed|duplicate|failed|aborted|expired,
  verified_sha256, fingerprint, final_relative_path (intent record), media_asset_id FK SET NULL,
  duplicate_of_asset_id FK SET NULL, failure_code, failure_detail, finalize_attempts,
  lease_until, worker_id, uploaded_at, completed_at, expires_at, created_at, updated_at
  INDEX(organization_id, uploaded_by_user_id, status); INDEX(status, updated_at);
  UNIQUE(organization_id, verified_sha256) WHERE status IN ('finalizing','completed')
```

The partial unique index makes two simultaneous uploads of identical content converge on one
asset. The downgrade drops the table (upload history) and the three asset columns; assets
and files stay.

## Production reporting indexes (migration `0016_reporting_indexes`)

Upload track U6. Two indexes on `media_assets`: `(organization_id, asset_kind,
production_period)` for period reports and `(revision_of_asset_id)` for revision chains. No
new table: reporting reads finals, completed uploads and users
(`docs/architecture/production-reporting.md`). The downgrade drops both indexes.

## Upload chunk manifest (migration `0017_upload_chunk_manifest`)

U7 Phase A hardening. `media_uploads.chunk_manifest` (JSONB, default `[]`) holds the SHA-256
of every committed chunk, indexed by `offset // chunk_bytes`, so a reselected file can be
verified over the whole committed prefix before an interrupted upload resumes. At most 1024
entries for an 8 GiB file. The downgrade drops the column.

## Dedicated upload root (migration `0018_upload_root`)

ADR 0012 amendment 2 (fast track F1). Two CHECK constraints are widened: `media_roots.kind`
gains `upload` (the AFFILIO_UPLOADS root, read-write, never scanned) and
`media_content_categories.assignment_source` gains `upload` (owner / product category /
content type copied from the upload session instead of folder names) and `override` (a
person's facet value on a legacy final, kept across rescans). No table or column
changes: uploaded finals are ordinary `media_assets` (`asset_kind = final`,
`ingest_source = upload`) whose `final` variant points at the `uploads` root with
`relative_path = ready/<organization>/<YYYY>/<MM>/<upload id>.<ext>`. The downgrade folds the
new values into the legacy ones (`upload` kind → `final`, `upload` source → `path`,
`override` → `manual`) and
restores the previous constraints, so no asset, file row or assignment is lost.

## Integration keys and MeoScout ingestion (migration `0019_product_ingest`)

ADR 0013 / M3.2-B.2 P1. A scoped machine credential and the idempotent record of each
server-to-server product handoff.

```
integration_api_keys (identity)                 product_ingest_batches (products)
  id, organization_id FK CASCADE                  id, organization_id FK CASCADE
  name, prefix (first 12 chars of the secret)     batch_id; UNIQUE (organization_id, batch_id)
  key_hash UNIQUE (sha256 of the secret)          contract, contract_version, payload_sha256
  scopes jsonb ["products:ingest"]                source jsonb, on_conflict keep|replace
  created_by_user_id FK SET NULL                  status completed|failed, error (redacted)
  expires_at, last_used_at                        item_count, created/updated/unchanged/
  revoked_at, revoked_by_user_id FK SET NULL        conflict/rejected counts
  rotated_from_key_id FK self SET NULL            api_key_id FK integration_api_keys SET NULL
  created_at                                      actor, request_id, sent_at, completed_at,
  ix (organization_id, created_at)                  created_at; ix (organization_id, created_at)

product_ingest_items                            products += ingest_batch_id FK
  id, ingest_batch_id FK CASCADE, organization_id   product_ingest_batches SET NULL
  position, client_ref; UNIQUE (batch, client_ref)  (the batch that created the row; parallel
  status created|updated|unchanged|conflict|rejected   to import_id)
  code, reason, product_id FK products SET NULL
  changed_fields jsonb, conflicts jsonb, evidence jsonb, created_at; ix (product_id)
```

The downgrade drops the three tables and the column; ingested products stay in the catalog
(`source=meoscout`, `source_ref=client_ref`) and every integration key stops working.

## Growth plan (not implemented)

| Milestone  | Tables (indicative)                                                                                            |
| ---------- | -------------------------------------------------------------------------------------------------------------- |
| M1 (later) | `media_tags`, `media_asset_tags` once tagging is prioritised                                                   |
| M2 (later) | `product_price_history` when analytics needs price trends                                                      | `products`, `product_links`, `product_media` |
| M3B        | `account_credentials` (encrypted, separate from `social_accounts`), OAuth state, API-verified evidence columns |
| M4         | `performance_imports`, `performance_metrics`, `commission_records`                                             |
| M5         | `agent_runs`, `agent_tool_calls`, `approvals`                                                                  |

Each milestone adds only the tables it needs and documents them here.
