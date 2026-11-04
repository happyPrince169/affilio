# U — Editor video uploads and production submission

- **Status:** U0 implemented locally (commit `d98b08a`); U1 design accepted (`ada5462`,
  ADR 0012, `docs/architecture/editor-uploads.md`); U2 implemented locally (migration 0014,
  owner mappings API and members UI); U3 implemented locally (`media/ingest.py`);
  U4 implemented locally (migration 0015, upload API, staging store, worker finalizer);
  U5 implemented locally (`/uploads` page, queue, resume, My uploads, streaming proxy);
  U6 implemented locally (production reporting API, `/reports/production`, migration 0016);
  U7 Phases A–B3.1 done; **fast track F0–F2 (2026-11-04) replaces the FINAL hard-link
  placement with a dedicated upload root (ADR 0012 amendment 2, migration 0018)**; Phase
  B3.2 and the old Phase C are cancelled. Nothing deployed.
- **Depends on:** M3A (sessions, roles), M3A.1 (taxonomy, folder rules), M3C.1 (finals,
  handoff review), Facets F1 (facet roots, path classification), Step 4 (75 directories).
- **Goal:** editors submit finished videos through AFFILIO in bulk, resumably, attributed to a
  production owner and to themselves; managers review them with the existing handoff gate;
  reports show production output per owner, uploader and period.

## Milestones

Each milestone ends with its checks run, a local commit and a report; nothing is deployed or
pushed until U7 is approved separately.

### U0 — Thumbnail invalidation fix (done)

- One line in the scanner: a classification-only change no longer marks the asset touched.
- Regression test in `tests/test_facets.py`. Full backend suite green (206).

### U1 — Storage and upload architecture (this milestone)

- ADR 0012 (split write path, protocol, finalization, duplicates, attribution).
- Technical design: endpoint contract, state table, recovery matrix, threat checklist,
  proposed schema, production change list, unresolved decisions.
- No code.

### U2 — Editor to owner mapping (done locally)

- Migration 0014: `media_owner_mappings` with constraints and downgrade; `register_reference`
  so a mapped owner category cannot be deleted.
- Service + admin endpoints: list, create, revoke; audit `owner_mapping.created|revoked`.
- Members settings UI: owner column and an add/revoke control per member; shared types.
- Tests: RBAC (viewer/editor 403, admin ok), facet validation (non-owner or archived category
  refused), organization isolation, revoke keeps history, delete-protection of the category.
- Acceptance: an admin maps a user to one or more owners from the UI; `GET /uploads/options`
  logic (owners for the caller) is unit-tested here even though the route ships in U4.

### U3 — Reusable media ingest service (done locally)

- `media/ingest.py`: `register_file` (asset + main variant + probe + classification +
  handoff + period + thumbnail pending) and `find_duplicate` (content hash, then
  size+fingerprint with hash confirmation), extracted from `scanner.py` without behaviour
  change.
- Scanner calls the service for new files; moved/updated paths untouched.
- Tests: existing scanner, finals and facets suites unchanged and green; new unit tests for
  `register_file` attribution fields and `find_duplicate`.
- Acceptance: `git diff` of scanner behaviour is a pure extraction; scan stats identical on
  the existing fixtures.

### U4 — Resumable bulk-upload backend (done locally)

- Migration 0015: `media_uploads`; `media_assets.ingest_source`, `uploaded_by_user_id`,
  `revision_of_asset_id`; partial unique index on verified hash; downgrade.
- Settings: `MEDIA_INCOMING_PATH`, `MEDIA_FINALS_WRITABLE`, `UPLOAD_*` (section 10 of the
  design). Registry gains the `incoming` binding (read-write kind).
- API: options, create, chunk (streaming, offset precondition, digest, row lock), status,
  complete, abort, list. Error codes per the design.
- Worker: finalization job (claim with lease, verify, duplicate check, destination resolve,
  intent record, link+unlink, register with attribution, adoption of scanner-registered
  files), recovery on start and on lease expiry, maintenance expiry and orphan cleanup.
- Storage module with the walk/`O_NOFOLLOW`/exclusive-create rules; no other module writes.
- Audit events; structured logs.
- Tests (temporary directories, fake prober, local database): the full checklist in
  section 16 of the master brief — viewer denied, owner authorization, on-behalf upload,
  cross-organization 404, foreign session denied on every endpoint, invalid and archived
  taxonomy, traversal and symlink refusal, collision suffixing, duplicate rejection,
  concurrent duplicate finalization, idempotent chunk retry, resume after interruption,
  expiry cleanup, invalid media, size and hash mismatch, crash after link and after placement
  (simulated by killing the step), completion idempotency, revision linkage, legacy assets
  untouched, scanner skipping staging.
- Acceptance: an end-to-end test uploads three files in chunks with a retry and a reload-style
  resume, finalizes them, and the assets carry the three path assignments, pending review,
  uploader and owner.

### U5 — Upload UI (done locally)

- `components/uploads/`: drop zone, file and folder pickers, queue with per-file rows and
  controls, batch defaults and per-file overrides, two concurrent sessions (configurable),
  progress, summary, duplicate and error states, reload recovery with re-selection and
  Chromium file handles.
- "My uploads" page with thumbnail, owner, categories, status, reviewer note, revision link,
  filters; "Submit corrected version" from a rejected row.
- Admin: uploads overview (states, failures); owner mapping control already in members (U2).
- Proxy: stream chunk bodies when supported.
- `lib/api/uploads.ts`, shared types; UI tests for queue state transitions, resume matching,
  override precedence and error rendering.
- Acceptance: manual run against the local API with mock-mode settings; lint, typecheck,
  build, web tests green.

### U6 — Production reporting (done locally)

- Endpoint: per period × owner × uploader × product category × content type × status ×
  batch; metrics: submitted, pending, approved, rejected, corrected submissions, approval
  rate; CSV export. Revisions counted separately from first submissions; legacy finals keep
  their scan-derived period and no uploader.
- Reports page with period and status filters.
- Tests for aggregation and CSV shape.

### U7 — Production rollout (in progress; each phase separately approved)

Phase A (readiness audit and resume hardening), B1 (staging probe) and B2B (single-folder
finalization probe, cleanup and ACL restoration) are done; B2C (this documentation and
design review) records the findings. Remaining: B3 (owner-rights probe, 72-folder ACL
rollout), then C (compose mount and environment changes, backup, migrations, deployment,
one real editor pilot upload, verification of classification, thumbnail, review, audit and
reporting, rollback rehearsal). Record: `docs/deployment/upload-rollout.md`. Nothing in
U2–U6 writes to production.

### Fast track F0–F2 (2026-11-04) — two storage roots

- F0 (closed 2026-11-04): read-only inventory, safety review, then the approved exact-token
  cleanup, ACL restoration and verification on the NAS; 92 legacy files unchanged, type dir
  ACL back to baseline, no probe artefacts left (`docs/deployment/upload-rollout.md`, "Phase
  F0"). The B3.1B probe had failed P6 (chmod and hard links allowed on a placed file).
- F1: `uploads` root (`MEDIA_UPLOADS_PATH`, kind `upload`, read-write) with `staging/` and
  `ready/`; finalization by `rename_noreplace`; UUID physical names; classification stored as
  `upload` assignments from the session's taxonomy; unified `resolve_variant`; legacy
  `finals` root untouched and read-only; migration 0018; settings `MEDIA_UPLOADS_PATH`,
  `MEDIA_UPLOADS_WRITABLE` (the old `MEDIA_INCOMING_PATH` / `MEDIA_FINALS_WRITABLE` are gone).
- F2: library filters by owner, product category, content type, source and uploader;
  provenance on assets (`ingest_source`, `uploaded_by_user_id`, `root_key`,
  `revision_of_asset_id`); streaming preview with Range support (`…/stream`, proxied);
  metadata-only reclassification (`PUT /media/assets/{id}/facets`, admin any final, editor
  own uploads within mapped owners); "Uploaded" provenance in the library and asset page.
- Tests: `tests/test_media_uploads.py` (31 tests: protocol, finalize, duplicates, same-name
  collisions, crash recovery at four boundaries, conflicts, symlinks, expiry, inert worker,
  legacy read-only guard with 0555 directories, static no-write guard, resolver, stream and
  ranges, publishing input, thumbnail from the uploads root, filters, reclassification).

- F4 (2026-11-04): Phase D deployed to the NAS (`7e25b6a`, migration 0018, uploads root
  mounted per the runbook) and the production smoke test passed through the API (40 checks).
  Browser-level acceptance (bulk queue, resume after reload, preview playback, editor
  account flows, report page) remains to be exercised by a person; see the rollout record.

## Acceptance criteria (whole track)

- [ ] Editors upload many files at once with resumable progress and recover after a reload.
- [x] Every uploaded final is attributed to an owner and an uploader, classified like a
      scanned file, visible immediately, and enters handoff review (production smoke test
      2026-11-04).
- [x] Duplicate content never creates a second asset or file; legacy files are never touched
      (production smoke test 2026-11-04; 92 legacy files verified unchanged).
- [ ] Crashes at any finalization boundary are recovered without loss or duplication.
- [ ] Reports distinguish submitted, approved and corrected work per owner and uploader.
- [x] Only the API's `uploads/staging` bind and the worker's `uploads` mount are writable;
      legacy finals, originals and proxies stay read-only in every container (verified from
      `/proc/<pid>/mountinfo` after the 2026-11-04 deploy).

## Operational notes for U5 and U7

- Every container needs `MEDIA_UPLOADS_PATH` (the AFFILIO_UPLOADS root; the API needs its
  `staging/` writable); the worker needs `MEDIA_UPLOADS_WRITABLE=true`. Without them the
  upload routes answer 503 and the finalizer stays inert, so the code is safe to deploy
  before the mounts exist.
- Chunk requests carry `Upload-Offset`, `Content-Length` and `X-Upload-Chunk-Sha256`; the
  proxy must forward PATCH bodies. Buffering one 8 MiB chunk per request is acceptable.
- Finalization runs one session per worker poll between scan claims and thumbnail work; a
  queue of N uploads drains in roughly N × (hash time + ffprobe) with the 5 s poll interval.
- `failed` sessions keep their staging copy until the TTL for inspection; `duplicate`,
  `aborted`, `expired` and `completed` sessions lose it immediately or at the next hourly
  maintenance run.
- Hard links into FINAL were exercised in U7 Phases B1–B3.1 and then abandoned (ADR 0012
  amendment 2): uploads are finalized by rename inside the dedicated `uploads` root, so no
  ACL change on FINAL is needed for deployment.

## Known limits

- Resume identity (hardened in U7 Phase A, migration 0017): a reselected file is verified
  against the SHA-256 of every committed chunk before any byte is appended. Bytes beyond the
  committed offset are verified chunk by chunk as they arrive.

- Every browser requires re-selecting files after a reload; the Chromium persistent
  file-handle optimisation is a follow-up.
- The proxy streams chunk bodies; the one-chunk buffer limit no longer applies.
- No bulk re-assignment of categories after upload beyond the existing manual override.
