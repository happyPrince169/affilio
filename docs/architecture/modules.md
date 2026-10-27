# Domain modules

All modules live in `apps/api/src/affilio/modules/`. Each is a Python package with, as it
matures, the following files:

```
modules/<name>/
├── __init__.py     docstring: purpose + milestone
├── models.py       SQLAlchemy models (tables owned by this module)
├── schemas.py      Pydantic request/response models
├── service.py      business logic; the ONLY entry point for other modules
├── repository.py   (optional) query helpers
└── router.py       FastAPI router, prefix /<name>, mounted under /api/v1
```

Every module exposes `router` (possibly empty). `affilio.modules.iter_module_routers()`
mounts them in order; `DOMAIN_MODULES` is also what `/api/v1/system/info` reports.

## Dependency direction

Modules are ordered. A module may depend on modules **to its left**, never to its right.

```
identity → accounts → media → products → procurement → production → content → publishing → analytics → agents
```

| Module       | Owns                                                        | May depend on                     | Milestone |
| ------------ | ----------------------------------------------------------- | --------------------------------- | --------- |
| `identity`   | organizations, users, memberships, auth, RBAC               | core, db                          | M0 tables; auth later |
| `accounts`   | social/marketplace accounts, (later) encrypted credentials  | identity                          | M0 table; connect flows M3 |
| `media`      | logical roots, scanner, ffprobe, thumbnails, scan jobs      | identity                          | **M1 ✓** (tags later) |
| `products`   | product catalog, research lifecycle, product↔media mapping  | identity, media                   | **M2 ✓** |
| `procurement`| purchase requests, orders, items, receiving                 | identity, products                | **M2B ✓** |
| `production` | filming/editing jobs, optional product + media associations | identity, media, products, procurement | **M2B ✓** |
| `content`    | platform-agnostic content items, variants, calendar states  | identity, accounts, media, products, production | M3 |
| `publishing` | official platform clients, publish jobs, manual checklists  | identity, accounts, content       | M3        |
| `analytics`  | performance imports, attribution, commission reporting      | identity, accounts, content, products | M4    |
| `agents`     | Claude tool-based assistant, approvals, run audit trail     | everything before it              | M5        |

Cross-cutting code (`affilio/core`, `affilio/db`, `affilio/api`) is not a module and may be
used by any module. Modules never import `affilio.api`. `affilio/worker` is the background
process entry point; it composes module services (`media.scanner`, `media.thumbnails`) and owns
no tables.

### media module layout (M1)

```
modules/media/
├── models.py       MediaRoot, MediaAsset, MediaFileVariant, MediaScanJob
├── schemas.py      API responses (mirrored in packages/shared-types/src/media.ts)
├── roots.py        RootBinding / MediaRootRegistry: settings → paths, safe resolve()
├── fingerprint.py  quick_fingerprint (3 samples) / full_hash (streamed)
├── probe.py        FFprobe wrapper + parser (rotation, bit depth, frame rate)
├── scanner.py      incremental scan: unchanged / touched / changed / moved / new / missing
├── thumbnails.py   ffmpeg thumbnail generation, proxy-first, atomic writes
├── service.py      organization-scoped queries + enqueue_scan
├── errors.py       AppError subclasses + ScanConfigurationError
└── router.py       /api/v1/media/*
```

### products module layout (M2)

```
modules/products/
├── models.py     Product, ProductStatusEvent, ProductMediaLink
├── schemas.py    create/update/status/link schemas + responses (mirrored in shared-types)
├── urls.py       http(s)-only validation + normalization for duplicate detection
├── lifecycle.py  ALLOWED_TRANSITIONS, HUMAN_CONFIRMATION_REQUIRED (WINNER)
├── service.py    organization-scoped CRUD, transitions, linking (uses media.service for assets)
├── errors.py     duplicate_product, invalid_status_transition, winner_confirmation_required, …
└── router.py     /api/v1/products/*
```

`products` reaches media assets only through `media.service.get_asset` / `asset_summary`
(never by querying media tables directly), which is what makes cross-organization linking
impossible: an asset from another organization is simply "not found".

### Independence rules (M2B)

- **Products and videos are independent.** `media_assets` has no product or job column. The
  only product↔video relation is `product_media_links` (owned by `products`), and the only
  job↔video relation is `production_job_media` (owned by `production`); both are optional and
  many-to-many.
- **Filming ≠ promoting.** `production_job_products` records what was used on set;
  `product_media_links` records what a video promotes; publication records (M3) will carry
  the affiliate links. None of these are derived from each other.
- **Research ≠ procurement.** `products.status` (research lifecycle) is never changed by
  procurement; a WINNER can be unpurchased and a received product can be ARCHIVED.
- **Finals are not footage.** The finals root produces `asset_kind = final` assets with their
  own duplicate scope, no proxy linking, and a human handoff gate (`handoff_status`).

### publishing engine and publisher worker (M3B.2A)

```
publishing/  += engine.py (dispatch, claim/lease, phase steps, retries, reconciliation),
             eligibility.py (API vs assisted), media_checks.py (pre-upload validation),
             models.PublicationUpload / PublisherWorker, routes /publishing/engine/status and
             /publishing/publications/{id}/reconcile-provider
worker/      += publisher.py (`python -m affilio.worker.publisher`, separate container)
accounts/    providers gain the Reels calls (mock: faults + registry; live: gated)
```

`publishing.engine` imports `accounts.vault`, `accounts.connections` and the provider factory
(service functions only). The publisher reads the finals root through `RootBinding.resolve`
and never writes anywhere. See `docs/architecture/publishing-engine.md`.

### accounts: connections, vault and providers (M3B.1A)

```
accounts/    += crypto.py (AES-256-GCM key ring), vault.py (encrypted credential rows; the only
             code that decrypts), connections.py (OAuth attempts, grants, Page linking, health),
             connect_router.py (/accounts/connect/facebook/*, /accounts/connections/*,
             /accounts/{id}/disconnect|verify|credentials), providers/{base,live,mock}.py
worker/      += maintenance.py (session + OAuth-attempt purge), called hourly by runner.py
scripts/     += maintenance.py, rotate_credential_keys.py
```

Dependency direction is unchanged: `accounts` imports `identity` only; `publishing` will read
Page credentials through `accounts.vault`/`accounts.connections` service functions in M3B.1B
and never through the tables. Provider adapters are the only code that may talk to Meta.

### accounts, content and publishing (M3A)

```
accounts/    models (social_accounts registry fields), schemas, service (manual registration,
             PLATFORM_CAPABILITIES, publishable_url), router (admin writes)
content/     models (affiliate_links), schemas, service (validation, normalized duplicates), router
publishing/  models (campaigns, campaign_media, campaign_targets, publication_jobs, publication_links,
             publication_snapshots, publication_attempts), lifecycle (ACTIONS table), errors, schemas,
             service (state machine, snapshots, evidence, scheduling, queue, calendar), router,
             adapters/ (base contract, assisted, facebook placeholder, registry)
identity/    += schemas, service (credentials, sessions, memberships), router (/auth)
core/        += security (argon2id, session tokens), audit (record_audit)
api/deps.py  Identity resolution, CurrentIdentity / RequireEditor / RequireAdmin
```

Dependency direction is respected: `publishing` imports `accounts`, `content`, `media` and
`products` services; `content` imports `products`; nothing imports `publishing`. Adapters live
inside `publishing` and perform no network I/O in M3A.

### taxonomy and routing (M3A.1)

```
taxonomy/    models (content_categories), schemas, errors, service (tree snapshot, cycle and slug
             guards, reference registry for safe archiving), router (/content-categories)
accounts/    += channel_profiles, channel_category_rules, PUT /accounts/{id}/channel-profile
media/       += media_content_categories, folder_category_rules, classification.py (path
             normalization, rule resolution, idempotent assignment), scanner hooks, filters
routing/     engine.py (pure scoring, inheritance, BLOCK precedence), service, router
             (/distribution/recommendations)
```

Order in `DOMAIN_MODULES`: identity, **taxonomy**, accounts, media, …, publishing, **routing**.
`taxonomy` imports nothing domain-specific; `media` and `accounts` import `taxonomy`;
`routing` reads `accounts`, `media`, `publishing` and `taxonomy` services and writes nothing.

## Rules

1. **Tables belong to one module.** Another module reads them only through that module's
   service functions (or a read-model it exposes), never by importing its models into queries.
   Importing a model class for a foreign key or relationship type is fine.
2. **Services take `organization_id`.** Every service function on tenant data requires it
   and applies it in the query. This is where organization isolation is enforced.
3. **Routers are thin.** Validate with Pydantic, call a service, return a schema.
4. **No platform calls outside `publishing`/`analytics`.** And only official APIs there.
   In M3A no adapter performs network publishing at all; see
   `docs/architecture/distribution.md#adapter-interface`.
5. **Routers declare roles.** `CurrentIdentity` for reads, `RequireEditor` for ordinary
   writes, `RequireAdmin` for approvals, registry, members and reconciliation; services keep
   `organization_id` scoping (`docs/architecture/security.md`).
6. **Agents act through tools that call services.** Any tool that mutates or publishes
   returns a proposal that requires human approval before execution.

## Frontend mirror

```
apps/web/src/
├── app/(dashboard)/<area>/page.tsx   route per navigation item
├── components/<area>/                feature components
├── components/layout/                shell: sidebar, top nav, page header
├── components/ui/                    shadcn/ui primitives (generated, formatted by Prettier)
├── lib/api/<area>.ts                 fetchers + TanStack `queryOptions`
└── config/                           navigation, milestones, site metadata
```

`packages/shared-types` mirrors the API's Pydantic schemas and enums. Change both sides in
the same commit.
