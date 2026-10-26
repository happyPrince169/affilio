# M4 — Analytics

- **Status:** Planned
- **Depends on:** M0, M2, M3 accepted (content and product identity for attribution)
- **Unlocks:** M5 (performance-grounded optimization)

## Scope

Track affiliate performance and commissions and attribute them to published content and
products. Data arrives through official reporting APIs where available and through
file-based import (CSV/XLSX exports from the Shopee Affiliate portal) otherwise. Historical
data is minimal, so the design optimizes for incremental daily imports.

## Deliverables

- `analytics` module: `performance_imports` (source, file hash, period, status),
  `performance_metrics` (date, account, content/product, clicks, orders, revenue,
  commission), `commission_records`.
- Import pipeline with validation, deduplication by file hash, and a mapping step to link
  rows to content items / products (manual override UI for unmatched rows).
- API: import upload/status, time-series and breakdown queries, CSV export.
- Web: Analytics page with time range, per-account and per-product views, import dialog;
  Dashboard tiles switch from placeholders to real numbers.

## Acceptance criteria

- An exported Shopee report imports without data loss; re-import is a no-op.
- Dashboard and Analytics show only imported data, with the import date visible.
- Attribution coverage (matched vs. unmatched rows) is displayed, never hidden.

## Known risks

- Report formats change without notice; keep parsers versioned and tested on fixtures.
- Attribution is inherently partial; the UI must communicate confidence honestly.
