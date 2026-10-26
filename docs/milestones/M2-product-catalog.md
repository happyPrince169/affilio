# M2 — Product Catalog & Media-Product Mapping

- **Status:** Implemented 2026-10-26, pending acceptance review
- **Depends on:** M0, M1 (assets to link)
- **Unlocks:** M3 (content variants reference products and their affiliate URLs), M4 (actual
  commission attribution per product), M5 (agent grounding on catalog + performance)

## Scope

A manually curated, organization-scoped catalog of affiliate products with a research
lifecycle and many-to-many links to indexed videos. No scraping, no unofficial Shopee APIs:
every value is entered by an operator and unknown values stay empty.

## Deliverables

| Area | Delivered |
| --- | --- |
| Data model | `products`, `product_status_events` (audit trail), `product_media_links` (migration `0003_product_catalog`) |
| Integrity | Partial unique indexes per organization on `(platform, platform_product_id)` and on the normalized product URL, so unknown identifiers never collide; unique `(product, asset)` link; at most one primary product per asset; cascading FKs; CHECK-constrained statuses |
| Validation | http(s)-only URLs (no `javascript:`/`data:`/`file:`, credentials, whitespace); ISO currency; commission rate 0–1; max 10 image URLs, de-duplicated |
| Lifecycle | `discovered → shortlisted → testing → winner/paused → archived` with an explicit transition map; WINNER requires `confirmed=true` **and** a note; every change is audited with actor and timestamp |
| API | `GET/POST /api/v1/products`, `GET /products/stats`, `GET/PATCH /products/{id}`, `POST /products/{id}/status`, `GET /products/{id}/status-history`, `GET/POST /products/{id}/media`, `PATCH/DELETE /products/{id}/media/{asset_id}`, `GET /products/media/{asset_id}` |
| Web | Products table with search/status/video filters, sort, pagination, status chips; creation and edit dialog (React Hook Form + Zod); detail page with status control and winner confirmation, images, research notes, linked videos with thumbnails, link-video picker, status timeline; linked-products card on the asset page; live products tile on the dashboard |
| Tests | 22 new tests: URL safety, normalization, CRUD, duplicates (id/URL/cross-org), partial update, isolation, transition rules and audit, listing/filters/stats, media linking (primary handover, counts, reverse lookup, update, unlink), invalid/foreign asset ids, cascade on asset delete, API schema/envelopes/admin gate |

## Acceptance criteria

- [x] Products can be created, edited, listed, searched, filtered and paginated per organization
- [x] Unsafe URL schemes are rejected with `invalid_product_url`
- [x] Duplicates by platform id or normalized URL return `duplicate_product` with the existing id
- [x] Status transitions follow the map; WINNER cannot be set without confirmation and a note
- [x] A product links to many assets and an asset to many products; duplicates are refused; one primary per asset
- [x] Cross-organization products or assets are invisible (404)
- [x] Migration drift test passes; `pnpm check` passes
- [x] Verified in the browser: table, creation dialog validation (blank name, javascript: URL), status filter, detail with linked videos and timeline, winner confirmation gating, linked-products card on the asset page, dark mobile layout

## Integration with later milestones

- **M3 Publishing:** a content item will reference `products` (and read `affiliate_url`) to build
  captions and link stickers; `product_media_links` tells the planner which videos already
  feature a product. Publishing records will point back to the product for attribution.
- **M4 Analytics:** imported performance rows attach to `products.id` (and, where a platform
  provides it, `platform_product_id`). Actual commission lives in analytics tables; the
  `estimated_*` columns here are never overwritten by imports, so estimate vs. actual can be
  compared and the lifecycle (e.g. confirming a WINNER) stays a human decision backed by data.
- **M5 Agent:** research notes, lifecycle history and linked videos are the grounding context
  for product research tools; the agent may *propose* status changes but cannot bypass the
  confirmation rule.

## Known limitations

- Manual entry only; no CSV import/export yet (candidate for a follow-up once the field set
  settles).
- Price history is a single observed value plus timestamp; a history table can be added when
  analytics needs it.
- Actor is a free-text label (`api`) until authentication provides user identities (M3).
- Product images are reference URLs rendered directly from Shopee's CDN; nothing is downloaded.

## Verification record (2026-10-26, Ubuntu workstation)

- `pnpm check` (ESLint, tsc, Ruff, pytest with `TEST_DATABASE_URL`, Next build): passed;
  full backend suite 101 tests; Prettier clean.
- Dev database migrated `0002 → 0003`; demo products, transitions and video links created via
  the API: duplicate link → 409, winner without confirmation → 422, stats consistent.
- Browser (system Chrome via Playwright, zero console errors): creation dialog blocked a blank
  name and a `javascript:` URL client-side, product created, status filter, detail page with
  three linked-video entries and status timeline, winner dialog disabled until evidence +
  confirmation, "Confirmed winner" banner after submit, asset page lists two linked products,
  dark mobile layout.
- Not done: NAS deployment (out of scope), CI run on GitHub (nothing pushed).
