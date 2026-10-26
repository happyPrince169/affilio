# M2 — Product Catalog

- **Status:** Planned
- **Depends on:** M0 accepted; M1 for linking assets to products (soft dependency)
- **Unlocks:** M3 (content references products), M4 (commission attribution), M5 (agent grounding)

## Scope

A curated, organization-scoped catalog of affiliate products for the active Shopee account:
what is being promoted, with which affiliate link, at what commission, and which media exists
for it. Manual entry first; AI-assisted research is M5.

## Deliverables

- `products` module: `products` (name, external id, shop, price, commission rate, status
  researching|approved|retired, notes), `product_links` (affiliate URL, label, created by),
  `product_media` (link to media assets).
- API: CRUD, search/filter, CSV import/export, link management.
- Web: Products page with table, detail/edit form (React Hook Form + Zod), CSV import dialog.
- Tests: validation, import edge cases, organization scoping.

## Acceptance criteria

- Operators can add, edit, retire products and attach affiliate links and media.
- CSV round-trip preserves all fields.
- Every query is scoped by organization; cross-org access returns 404/403.

## Known risks

- Shopee affiliate link formats and tracking parameters may change.
- No official product-data API is assumed; the catalog is manual until one is confirmed.
