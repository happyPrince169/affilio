# M3A.1 — Folder-Based Content Classification & Channel Routing

- **Status:** Implemented, verified locally (2026-10-27); awaiting acceptance
- **Depends on:** M3A (uncommitted at the time of implementation; both ship together)
- **Design docs:** `docs/architecture/content-taxonomy.md`,
  `docs/architecture/channel-routing.md`, `docs/operations/nas-final-folder-convention.md`

## Delivered

- **Taxonomy** (`taxonomy` module): hierarchical, organization-scoped categories with
  sibling-unique slugs, cycle prevention, archive-instead-of-delete protection, Uncategorized
  fallback. Admin CRUD at `/content-categories`; Settings → Categories UI.
- **Folder classification** (`media` module): `folder_category_rules` for FINAL roots
  (normalized paths, exact or descendant matching, most-specific-wins, priority tie-break,
  fallback), `media_content_categories` with provenance (`folder` / `manual`), one primary per
  asset, manual override endpoint. Scanner hooks on create / unchanged / move; idempotent;
  files untouched; proxies untouched. Library filters, badges, folder path, override card,
  Settings → Folder mapping with preview, overlap warnings and unmapped-folder report.
- **Channel profiles** (`accounts` module): `channel_profiles` + `channel_category_rules`
  (ALLOW / PREFER / BLOCK, inheritance, BLOCK precedence, routing toggle). Accounts page editor.
- **Routing engine** (`routing` module): deterministic scoring (documented), explanations,
  eligibility and blocking reasons, `GET /distribution/recommendations`. Composer shows
  recommended / low-relevance / excluded Pages and creates drafts through the existing
  publishing batch endpoint.
- **Safeguards**: classification never approves media; recommendations never create or
  publish; drafts require the normal approval; approved snapshots are immutable and
  publications expose `classification_stale` when the video's category changes afterwards.
- **Hardening**: routing eligibility is re-checked before approve / schedule / start / retry
  and in release-due; blocked targets and stale classifications require an explicit admin
  decision (`revalidate` or `revoke_approval`); `publication_jobs.revalidated_at/by`.
- Migrations `0006_content_routing` and `0007_publication_revalidation` (revalidation
  markers; idempotent) with downgrades.

## Acceptance criteria → verification

| Criterion | Evidence |
| --- | --- |
| Video in a configured folder is indexed and auto-classified; stays pending review | `tests/test_content_routing.py::test_scanner_classifies_moves_and_is_idempotent`, smoke steps |
| Nested / exact vs descendant / overlapping rules / fallback | `test_folder_rule_resolution_and_validation` |
| Duplicate scans, moves, manual overrides, missing files | `test_scanner_classifies_moves_and_is_idempotent`, `test_manual_override_survives_rescans` |
| Hierarchy cycles, duplicate slugs, archive protection | `test_category_tree_rules` |
| Inheritance, BLOCK precedence, disabled channels, cross-org isolation | `test_channel_rules_inheritance_block_and_disabled`, `test_taxonomy_rbac_and_isolation` |
| Multiple eligible Pages, low-relevance inclusion, duplicate prevention, snapshot preservation, no auto publishing | `test_recommendations_to_drafts_preserve_safeguards` |
| Concurrent draft creation, BLOCK after approval, revalidation after reclassification, disabled vs disconnected Pages, cross-org isolation, precedence edge cases | `tests/test_hardening.py` |
| Migration upgrade / downgrade / drift | `tests/test_migrations.py` |
| Desktop + mobile UI | Playwright smoke (see completion report) |

## Not done (by design)

- No AI classification (`ai_future` source reserved), no M4 analytics ranking.
- No Meta OAuth, no live publishing, no NAS deployment, no proxy worker changes.
- Bulk `import` assignment source reserved; no CSV import of categories.
