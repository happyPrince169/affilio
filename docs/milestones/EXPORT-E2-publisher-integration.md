# Export pipeline E2 — Publisher integration and operator UI

- **Status:** Implemented 2026-10-30; verified locally (full backend suite, web tests); not
  deployed. ADR 0010. Architecture: `docs/architecture/media-exports.md`.
- **Depends on:** E1 (migration 0011, exporter, CLI).
- **Not in scope (E3):** approval and publication triggers, backfill of the existing finals,
  tone-mapping policy for HDR sources, garbage collection of superseded exports.

## Delivered

| Area | Files |
| --- | --- |
| Selection | `publishing/media_checks.py`: a current, valid export is preferred (same asset, derived from the present final, identity match, supported recipe, file present with recorded size/mtime, stream facts pass); otherwise the final must pass on its own; otherwise both reasons are reported and the error names the fix |
| Migration 0012 | `publication_uploads.file_root_key`, `file_fingerprint`, `file_variant_id` (FK, SET NULL); existing rows backfilled to `finals` |
| Engine | `_step_validate` pins root, path, size, mtime, fingerprint, variant and records `media_source` / `export_state`; `_resolve_pinned_file` accepts only `finals` / `exports`, resolves through the traversal guard, re-checks size and mtime on every chunk step and the sampled fingerprint before the first byte (`media_changed`, never a silent upload) |
| Compose | publisher mounts the exports root read-only next to finals |
| API | `GET /media/assets/{id}` carries `export` (state, detail, latest job, export file, `can_export`); `GET /media/assets/{id}/export`; `POST /media/assets/{id}/export` (admin, audited `export.queued`); `POST /media/exports/{id}/cancel` (admin, audited); publication summaries carry `media_readiness` from indexed facts; upload summaries carry `file_root_key` / `file_relative_path`; new `manage_exports` permission |
| UI | Export status card on the final's page (Not needed, Not generated, Queued, Processing, Ready, Failed, Blocked, Stale) with Create / Re-export / Cancel for admins; readiness chip on queue rows; "Streams" row in the upload panel |
| E1 review | orphan temp-file cleanup in the exporter loop; `--check` encoder verification at start-up and on demand; crash analysis documented |
| Tests | `tests/test_export_selection.py` (selection cases, traversal and foreign roots, readiness and state, API lifecycle, RBAC, isolation, audit), `tests/test_publishing_exports.py` (export streamed, final streamed, incompatible final refused with the fix named, source changed after scheduling, export replaced or removed before upload, authorization unchanged), web `export-status-card.test.tsx` |

## Acceptance criteria

- [x] A current export is streamed from the exports root; a compatible final still streams
      from the finals root; an incompatible final without a current export fails validation
      with "Queue a new publish-ready export".
- [x] Stale, missing, replaced, invalid or unsupported-recipe exports are never used.
- [x] The upload row records root, path, size, mtime, fingerprint and variant; the file is
      revalidated immediately before streaming (content, not only size).
- [x] Path traversal and roots other than finals/exports are refused.
- [x] Export state and actions are organization-scoped and admin-gated; every action audited.
- [x] Live publishing authorization is unchanged: `META_LIVE_PUBLISHING_ENABLED=false` keeps
      every real Page ineligible, export or not.

## Known limits

- Queue-row readiness is computed from indexed facts without a stat call; the engine's
  filesystem check at validation remains authoritative.
- `media_checks` still carries its own copy of the codec constants; E3 can collapse them onto
  `media/export_profile.py`.
