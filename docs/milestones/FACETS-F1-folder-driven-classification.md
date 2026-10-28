# Facets F1 — Folder-driven multi-facet classification

- **Status:** Implemented 2026-11-01; verified locally (backend suite, web tests, lint,
  typecheck, build); **not deployed**. ADR 0011. Architecture:
  `docs/architecture/content-taxonomy.md` (facets section), operations:
  `docs/operations/nas-final-folder-convention.md`.
- **Depends on:** M3A.1 (taxonomy, folder rules, assignments), M3C.1 (scan watchdog, finals UI).
- **Phase B (production operations)** is a separate, explicitly approved step: create the
  missing folders on the NAS with the bootstrap script, verify, then one incremental scan.

## Delivered

| Area | Files |
| --- | --- |
| Migration 0013 | `content_categories.facet` + unique facet root index; `media_content_categories.assignment_source` accepts `path`; `media_assets.production_period` (backfilled from `first_seen_at`); `category_suggestions` table; downgrade restores everything |
| Taxonomy | `CategoryFacet`, `facet` on create/update/response, facet inheritance on create and move, facet roots cannot be archived, `ensure_facet_roots`, `normalize_name`, `CategoryTree.find_facet_value`, `POST /content-categories/ensure-facets` (admin, audited) |
| Media | `media/facets.py` (path parser, `FacetContext`, suggestion collector and upsert, approve / reject, `reclassify_facet_value`, `production_period_for`); `classification.apply_folder_classification` reconciles folder, path and fallback rows with the documented primacy; `set_manual_categories` preserves path rows; scanner loads the facet context once per scan, collects suggestions from valid finals only, stamps the period on create; `GET /media/category-suggestions`, `POST …/{id}/approve`, `POST …/{id}/reject` (admin, audited); `production_period` on summaries and as a list filter; `facet` on assignment entries |
| Script | `python -m affilio.scripts.media_folder_bootstrap --root <FINAL> [--apply] [--json]` (stdlib only, dry run by default, creates only missing directories, refuses conflicts and a missing root) |
| UI | Settings → Categories: "Enable facets" + facet badges; Settings → Folder mapping: "New folders found by the scanner" card (approve with optional name / reject, show decided); badges show source "from path" and the facet; asset page shows the production period; library filter by production period; scan card shows new suggestion counts |
| Tests | `tests/test_facets.py` (parsing incl. invalid paths, normalization and dedup, facet roots and inheritance, scanner classification + suggestions + rescan idempotency, rule precedence, legacy behaviour with facets disabled, approval / rejection / adoption / name mismatch / audit, manual overrides, moves keep identity and period, organization isolation and RBAC), `tests/test_media_folder_bootstrap.py`, web `category-suggestions.test.tsx` |

## Acceptance criteria

- [x] `Owner/ProductCategory/ContentType/x.mp4` yields three `path` assignments with the
      product category as primary and no Uncategorized row; legacy `Hang/T9/x.mp4` behaves
      exactly as before plus an owner assignment when the owner exists.
- [x] Unknown values of valid finals become pending suggestions once per facet value; empty
      folders, unsupported files and legacy / incomplete paths never suggest; rescans do not
      duplicate or change anything.
- [x] Approval creates the category under the facet root (adopting an existing match),
      refuses names that stop matching the folder, and classifies existing finals without
      changing asset ids or handoff state; rejection sticks across rescans.
- [x] Manual assignments and the manual primary survive scans and approvals.
- [x] `production_period` comes from the first ingestion and survives moves and rescans.
- [x] Suggestions and facet values are organization-scoped; decisions are admin-only.
- [x] Bootstrap script: dry run creates nothing; apply creates only missing folders.

## Known limits (next steps)

- No bulk manual assignment yet; the structured folders make it unnecessary for new finals,
  while the 92 legacy finals keep their current classification until moved or mapped.
- Suggestion names default to the folder name with dashes replaced by spaces; a nicer display
  name can be typed at approval time as long as it still normalizes to the folder name.
- The library filter combines one category with one period; a per-facet AND filter is a
  follow-up.
