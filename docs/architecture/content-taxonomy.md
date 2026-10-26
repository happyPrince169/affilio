# Content taxonomy and folder classification (M3A.1)

## Why

Editors finish videos and upload them into category folders on the NAS. The folder is the
cheapest, most reliable signal of what a video is about, so AFFILIO derives the primary
category from it. Everything else (manual overrides, future AI tagging) layers on top with
explicit provenance.

## Categories (`taxonomy` module)

`content_categories` is an organization-scoped tree: `parent_id`, `slug` (unique among
siblings, enforced by a partial-expression unique index that treats `NULL` parents as one
group), `name`, `description`, `status` (`active` | `archived`), `sort_order`. Depth is
capped at 6. Writes are admin-only; reads need any membership.

Protections in `taxonomy/service.py`:

- **Cycles** — a category cannot become its own ancestor (`category_cycle`, 422).
- **Sibling slugs** — duplicate slugs under the same parent are refused
  (`duplicate_category_slug`, 409); derived slugs come from the name.
- **Archive instead of delete** — a category referenced by media assignments, folder rules or
  channel rules (`category_in_use`, 409) can only be archived; the same for parents with
  active children. `DELETE` succeeds only for unreferenced leaves. Modules that reference
  categories register their table with `register_reference()` so the check stays in the
  taxonomy module without importing later modules.
- **Uncategorized** — `ensure_uncategorized()` creates the fallback (`slug = uncategorized`,
  top level) on demand; it cannot be archived.

The API returns the tree depth-first (`GET /content-categories`) with `depth`, `path`
("Men's Sports / Pickleball") and `path_slugs`.

## Assignments (`media_content_categories`)

One row per (asset, category) with `is_primary` (partial unique index: one primary per asset)
and `assignment_source`:

| Source | Written by | Notes |
| --- | --- | --- |
| `folder` | the scanner | exactly one row per final asset; rewritten only when the resolved category changes |
| `manual` | an editor via `PUT /media/assets/{id}/categories` | survives rescans; a manual primary beats the folder primary |
| `import` | reserved | bulk imports (not implemented) |
| `ai_future` | reserved | no AI classification exists in M3A.1 |

Only `asset_kind = final` assets are classified. Footage is never categorized.

## Folder rules (`folder_category_rules`)

Scoped to one FINAL media root. `relative_folder_path` is normalized (POSIX separators, no
leading/trailing slash, no `..`, `""` = the root itself) by
`classification.normalize_folder_path()`; unsafe paths are `invalid_folder_path` (422).
Each rule has `include_subfolders`, `priority` and `is_active`; `(root, path)` is unique.

Resolution for a file in folder `F` (`classification.resolve_rule`):

1. Active rules of the same root whose path equals `F` (exact) or, with
   `include_subfolders`, is an ancestor of `F` (`""` matches everything).
2. The **most specific** path wins (deepest match).
3. Ties at equal depth → higher `priority`, then older rule (`created_at`, then id).
4. No match → Uncategorized.

`GET /media/folder-rules/preview` shows the winning rule and all candidates;
`GET /media/folder-rules/report` lists every folder that holds indexed finals with its
mapping, flagging unmapped folders. The UI (Settings → Folder mapping) warns when a rule
overlaps a broader parent rule, which is expected but worth seeing.

## Scanner integration

`MediaScanner` loads the root's rules once per scan and calls
`apply_folder_classification()`:

- after a new final asset is created,
- after a known file is reconciled as **unchanged** (so rule edits apply on the next scan
  without touching files),
- after a **move** is detected (size + fingerprint identity, same mechanism as M1).

The function is idempotent: when the resolved category equals the existing folder row,
nothing is written. When it differs, the folder row is replaced, manual rows are preserved,
`media_assets.classification_changed_at` is set, and the scan counter `reclassified`
increments. The asset keeps its handoff state: classification never approves anything.

Moves between category folders therefore reclassify automatically while media identity is
preserved. If a move cannot be matched (fingerprint mismatch), the existing lifecycle applies:
old row `missing`, new row indexed as a new asset with its own classification. Nothing is
guessed; the missing asset stays visible for manual reconciliation.

Publications compare `classification_changed_at` with their approved snapshot's
`approved_at` and expose `classification_stale`; snapshots themselves are never modified
(see `docs/architecture/distribution.md`).

## API

| Method | Path | Role |
| --- | --- | --- |
| GET/POST | `/api/v1/content-categories` | viewer / admin |
| GET/PATCH/DELETE | `/api/v1/content-categories/{id}` | viewer / admin |
| POST | `/api/v1/content-categories/ensure-uncategorized` | admin |
| GET/POST | `/api/v1/media/folder-rules`, GET `…/report`, GET `…/preview` | viewer / admin |
| PATCH/DELETE | `/api/v1/media/folder-rules/{id}` | admin |
| PUT | `/api/v1/media/assets/{id}/categories` | editor |
| GET | `/api/v1/media/assets?category_id=…` (includes descendants), `?uncategorized=true` | viewer |
| GET | `/api/v1/media/categories/counts` | viewer |

Media summaries now carry `folder_path`, `categories[]` (with `is_primary` and
`assignment_source`) and `classification_changed_at`.
