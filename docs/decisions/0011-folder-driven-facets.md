# ADR 0011 — Folder-driven multi-facet classification of finals

- **Status:** Accepted (implemented locally 2026-11-01, not deployed)
- **Context:** M3A.1 derives one category per final from folder rules. The library holds 92
  finals, all "Uncategorized", laid out as `<Creator>/T9/`. A Shopee affiliate workflow needs
  three separate dimensions per video: who produced it, what product category it sells and
  what kind of content it is. Folder names are the cheapest reliable signal, but new folders
  must not silently create taxonomy entries, and existing assets, approvals, thumbnails,
  exports and product links must not change identity.

## Decision

1. **Facets live in the existing category tree.** `content_categories.facet` (nullable
   VARCHAR + CHECK: `production_owner`, `product_category`, `content_type`) marks one facet
   root per organization and facet (partial unique index on `(organization_id, facet)` where
   `parent_id IS NULL`); descendants inherit the facet on create and on move. No new category
   table, no change to routing, filters or badges beyond showing the facet.
2. **Structured path convention** `{Owner}/{ProductCategory}/{ContentType}/video.mp4`.
   Segments are matched to active facet values by normalized name or slug
   (`taxonomy.service.normalize_name`: NFKC, case-fold, apostrophes dropped, non-alphanumerics
   collapsed), so `Mens-Fashion`, `mens_fashion` and "Men's Fashion" are one value. Deeper
   folders are ignored. Two-level paths whose second segment is a period token (`T9`,
   `2026-09`) are legacy; one- and two-level paths are incomplete. Both keep M3A.1 behaviour
   and at most derive the owner.
3. **Derived assignments** get `assignment_source = path` (one row per facet value) next to
   the folder-rule row. Derived primacy: folder rule → path product category → Uncategorized
   fallback, which is only written when neither of the first two applies. A manual primary
   still wins; manual rows are never touched by scans.
4. **Unknown values become suggestions, never categories.** The scanner records a pending
   `category_suggestions` row per (organization, facet, normalized name) only when a probed,
   supported final sits under the folder. Empty folders, unsupported files, legacy and
   incomplete paths never suggest. Rejected rows stay rejected across rescans; approved rows
   whose category was archived reopen. Approval creates the category under the facet root
   (or adopts a matching one), validates that the chosen name or slug still normalizes to the
   folder name, and re-runs the path classification on the finals already indexed under that
   name from database rows alone.
5. **Production period** (`media_assets.production_period`, "YYYY-MM" in UTC) is stamped from
   `first_seen_at` when the asset row is created. Rescans and moves never rewrite it; legacy
   period folders are not parsed into it.
6. **Facets are opt-in per organization**: classification and suggestions run only once all
   three facet roots exist (`POST /content-categories/ensure-facets`). Organizations without
   roots behave exactly as before.
7. **Folder creation is an operator step** outside the application
   (`affilio.scripts.media_folder_bootstrap`, standard library only, dry run by default,
   creates only missing directories, never touches files). AFFILIO's mounts stay read-only.

## Consequences

- One incremental finals scan applies new rules, facet values and suggestions to existing
  assets without fingerprinting or probing them. Asset ids, handoff decisions, thumbnails,
  export variants and product links are untouched; `classification_changed_at` is set when a
  derived classification changes, which publications already surface as `classification_stale`.
- "Uncategorized" now means "no folder rule and no product category from the path". Legacy
  finals keep that fallback until they are moved into the structured tree or a rule maps them.
- The existing 92 finals receive `production_period` from their first ingestion month
  (2026-10), not from the `T9` folder; that is the rule the user chose and is documented in
  the UI as "month of first ingestion".
- Rollback: `alembic downgrade 0012_upload_file_identity` deletes path-derived rows, the
  suggestions table, the period column and the facet column; folder rules and manual rows
  survive.
