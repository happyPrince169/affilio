# M2B — Product Procurement & Production Pipeline

- **Status:** Implemented 2026-10-26, pending acceptance review
- **Depends on:** M0, M1, M2A
- **Unlocks:** M3 Publishing (approved finals are the only publishable inputs; publication
  records will carry affiliate links), M4 Analytics (cost of goods per product/job)

## Business workflow covered

MeoScout research → CSV import → approve for purchase → order → receive → schedule filming →
footage on NAS (existing proxy worker untouched) → editors upload finals → AFFILIO indexes
finals independently → human approval → (M3) publishing.

## Deliverables

| Area | Delivered |
| --- | --- |
| MeoScout import | CSV upload (≤ 5 MB / 5000 rows, delimiter sniffing, UTF-8/Win-1252), header-based mapping suggestions, operator-adjustable mapping, preview with per-row validation and duplicate detection (catalog by platform id / normalized URL, and within the file), `skip` or `update` on duplicates, import record with file SHA-256 so the same file is a reported no-op, import history |
| Procurement | `purchase_requests` (requested → approved/rejected/cancelled → ordered), `purchase_orders` (`PO-YYYY-NNNN`, draft → placed → shipped → delivered → partially_received/received, cancel releases requests), `purchase_order_items` (one approved request → one order line; unique product per order), costs with derived totals, tracking fields, cumulative receiving, per-product procurement summary, stats. Product research status is never touched. |
| Production | `production_jobs` (`PJ-YYYY-NNNN`, planned → scheduled → filming → filmed, editing not_started → in_progress → review → done gated on filmed), staff labels, dates, deadlines, notes; optional `production_job_products` (which purchased unit) and `production_job_media` (footage/final/reference). Assets never carry a job id. |
| Final handoff | Fifth read-only root `finals`; `asset_kind = final` indexed independently (own duplicate scope, no proxy linking); settle window + temp-name markers skip in-flight uploads; `handoff_status` pending_review → approved/rejected with mandatory note, actor and timestamp; footage cannot be approved |
| API | Import preview/run/history; 12 procurement routes; 10 production routes; handoff decision; media list filters `asset_kind`, `handoff_status`; stats counters |
| Web | MeoScout import dialog (upload → mapping → preview → import → result), Purchases page (requests + orders tabs, approve/reject, new order from approved requests, order detail with status, tracking and receiving), Production board by status with job detail (status/editing controls, products, videos), Content Library finals tab with handoff badges and approve/reject on the asset page, dashboard tiles |
| Tests | 26 new tests: parsing/mapping/partial import/idempotent re-import/update mode, request lifecycle, duplicate orders (request reuse, product twice), transitions, receiving bounds, cancel releasing requests, cross-org on requests/orders/jobs/products/assets, optional associations, editing gate, stats, finals independence, settle window, handoff rules; migration drift |

## Acceptance criteria

- [x] A MeoScout CSV can be previewed, mapped and imported; invalid rows are reported and valid rows imported; re-importing the same file creates nothing
- [x] Approved requests become order lines exactly once; receiving updates quantities and status
- [x] Jobs track production and editing status with audited notes and optional products/videos
- [x] Finals are indexed from their own root, never merged with footage, and require human approval
- [x] Cross-organization reads/writes return 404 everywhere
- [x] Existing NAS pipeline untouched: all media roots except thumbnails/exports stay read-only
- [x] Migration `0004` round-trips and matches the models
- [x] Browser verification: import preview with mapping and row actions, purchases requests/orders, order detail with receiving inputs, production board and job detail with footage + final, finals tab with handoff badges, approved final asset with review card, product page with purchases and jobs cards (zero console errors)

## Known limitations

- Order and job numbers are allocated by counting per year; concurrent creation from two
  operators in the same second could collide (unique constraint rejects it; retry).
- Receiving is per order line (cumulative totals), not per shipment event.
- Actor labels are free text until authentication (M3).
- Final-video approval is a flag and note; a version history per final (v1, v2…) is a
  candidate for M3 when publishing records exist.

## Verification record (2026-10-26, Ubuntu workstation)

- Backend: 125 tests passed with `TEST_DATABASE_URL` (migration `0004` round-trip and drift
  included); Ruff clean. `pnpm check` (lint, typecheck, test, build) passed; Prettier clean;
  Synology compose file validates with the new finals mount.
- End to end on the dev database via the API: originals scan (6 files) and finals scan (2
  indexed, 1 skipped as still uploading), MeoScout CSV import (3 created, 2 invalid reported,
  re-import reported as already imported), request → approve → order `PO-2026-0001` (total
  605,000 incl. shipping) → placed → shipped → partial receipt, job `PJ-2026-0001` through
  filmed with editing in progress, footage + final attached, final approved with a note.
- Not done: NAS deployment (out of scope), GitHub CI run (nothing pushed).
