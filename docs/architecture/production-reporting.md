# Production reporting (upload track U6)

Read-only reporting over final videos for the editing team: what was submitted, reviewed and
approved, per production owner, attributed editor and production period. It is **not a
payroll calculation**: no rates, no pay rules, and an "approved work unit" is a counting
rule, not a payable amount. Code: `media/reporting.py` (rows, lineage, aggregation, CSV),
`analytics/router.py` (routes), `components/reports/production-report.tsx` (page
`/reports/production`). Admin only; organization scoped; never writes.

## Sources of truth (nothing new is stored)

| Fact                           | From                                                                                                            |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| Final video, period, status    | `media_assets` (`asset_kind = final`, `production_period`, `handoff_*`, `status`)                               |
| Uploader, attributed editor    | `media_uploads` (`uploaded_by_user_id`, `submitted_for_user_id`) joined on the asset                            |
| Production owner               | the upload's owner id and name snapshot; otherwise the asset's path assignment under the production-owner facet |
| Product category, content type | the asset's facet assignments (manual, then path, then folder); upload values as fallback                       |
| Reviewer, time, reason         | `handoff_reviewed_by` (user label), `handoff_reviewed_at`, `handoff_note` on the asset                          |
| Revision lineage               | `media_assets.revision_of_asset_id` (set only by "submit corrected version")                                    |

Historical finals indexed from the NAS have `ingest_source = scan`: the owner may come from
the folder path, the uploader and attributed editor are **unknown** and are shown as such,
never inferred.

## Metric definitions

| Metric                        | Rule                                                                                             |
| ----------------------------- | ------------------------------------------------------------------------------------------------ |
| Files                         | final asset rows in the selection (missing files included and flagged)                           |
| Submitted                     | files with `ingest_source = upload`                                                              |
| Historical                    | files with `ingest_source = scan`                                                                |
| Pending / Approved / Rejected | current `handoff_status`                                                                         |
| Corrected submissions         | files with a `revision_of_asset_id`                                                              |
| Unique approved work units    | work chains with at least one approved asset, counted once on the most recently approved version |

## Revision counting

- A **work chain** is an original plus every correction pointing back to it, directly or
  through earlier corrections. Only explicit links form chains; two unrelated videos never
  become a chain because they share an owner, a product category or a name.
- Every version keeps its own row, status, reviewer and note. Nothing is deleted or merged.
- The chain's **approved unit** is the approved version with the latest review time (ties:
  the newer asset). Its period, owner and editor carry the unit in summaries.
- Cases: rejected original + pending correction → 2 files, 1 rejected, 1 pending, 0 units.
  Rejected original + approved correction → 1 rejected, 1 approved, 1 unit. Several
  approved versions → 1 unit. Several corrections → the chain still counts at most 1 unit.
- Rows expose `work_root_id`, `revision_index`, `chain_length`, `is_latest_in_chain` and
  `approved_unit`, and the UI badges "corrected version", "has corrections" and "approved
  unit".

## Production period caveats

- `production_period` is the UTC month of **first ingestion** (ADR 0011). For uploads that
  is the submission month. For historical finals it is the month the first scan saw the
  file, which can be long after the video was edited: the 92 legacy finals all carry the
  month of their first index, not their `T9` folder.
- Legacy folder names are never read as periods, files are never moved, and historical
  periods are never rewritten. Every response carries `period_source: "ingestion"` and the
  page shows a data-quality notice when historical files or missing files are in the
  selection.

## API (admin, organization scoped)

| Route                                | Returns                                                                                                                    |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `GET /analytics/production/summary`  | totals, breakdown by owner and by attributed editor (unknown bucket last), by status, data-quality counts, periods present |
| `GET /analytics/production/rows`     | per-video rows with lineage, newest first, `page` / `page_size` (≤ 200)                                                    |
| `GET /analytics/production/rows.csv` | the same rows as RFC 4180 CSV with a UTF-8 BOM; formula-leading cells are neutralised                                      |
| `GET /analytics/production/periods`  | periods present with file and submission counts                                                                            |

Filters on summary, rows and CSV: `period` (YYYY-MM), `owner_id`, `editor_id`, `status`,
`attribution` (`upload` | `historical`), `product_category_id`, `content_type_id`,
`unknown_editor`. Three queries serve a request regardless of row count; lineage is resolved
in memory over the organization's finals so chains that span periods stay intact.

## UI

`/reports/production`: period and filter selectors, KPIs (submitted, pending, approved,
rejected, unique approved work units), breakdown tables by owner and by editor, a paginated
detail table with links to the library asset, badges for historical records and unknown
editors, revision indicators, reviewer and note, and a CSV export of the current filters.
Editors do not see the page's data (admin permission `approve`); their own work is on
"My uploads".

## Before this can feed payroll

1. Decide which unit is payable (approved unit, approved file, or something else) and how
   corrections are compensated.
2. Set an editing-date source for historical finals, or exclude pre-upload periods.
3. Enforce reviewer accountability on the review action itself (two-person rule, reasons
   required on rejection are already enforced).
4. Freeze periods: approvals after a cut-off should not move units between months.
5. Resume identity is verified over the whole committed prefix since U7 Phase A (chunk
   manifest); keep it that way.
