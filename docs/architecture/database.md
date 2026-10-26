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

## Growth plan (not implemented)

| Milestone | Tables (indicative)                                                              |
| --------- | -------------------------------------------------------------------------------- |
| M1        | `media_assets`, `media_tags`, `media_asset_tags`                                 |
| M2        | `products`, `product_links`, `product_media`                                     |
| M3        | `content_items`, `content_variants`, `publish_jobs`, `account_credentials` (encrypted) |
| M4        | `performance_imports`, `performance_metrics`, `commission_records`               |
| M5        | `agent_runs`, `agent_tool_calls`, `approvals`                                    |

Each milestone adds only the tables it needs and documents them here.
