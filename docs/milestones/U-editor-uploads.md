# U — Editor video uploads and production submission

- **Status:** U0 implemented locally (commit `d98b08a`); U1 design accepted (`ada5462`,
  ADR 0012, `docs/architecture/editor-uploads.md`); U2 implemented locally (migration 0014,
  owner mappings API and members UI); U3 implemented locally (`media/ingest.py`);
  U4 implemented locally (migration 0015, upload API, staging store, worker finalizer);
  U5 implemented locally (`/uploads` page, queue, resume, My uploads, streaming proxy);
  U6 implemented locally (production reporting API, `/reports/production`, migration 0016);
  U7 planned. Nothing deployed.
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

### U7 — Production rollout (not approved)

Requires separate explicit approval and covers: ACL review and grant with a probe directory,
compose mount and environment changes, backup, migrations and deployment, one real editor
pilot upload, verification of classification, thumbnail, review, audit and reporting, and a
rollback rehearsal. Nothing in U2–U6 writes to production.

## Acceptance criteria (whole track)

- [ ] Editors upload many files at once with resumable progress and recover after a reload.
- [ ] Every uploaded final is attributed to an owner and an uploader, classified like a
      scanned file, visible immediately, and enters handoff review.
- [ ] Duplicate content never creates a second asset or file; legacy files are never touched.
- [ ] Crashes at any finalization boundary are recovered without loss or duplication.
- [ ] Reports distinguish submitted, approved and corrected work per owner and uploader.
- [ ] Only the API's staging bind and the worker's finals mount are writable; every other
      mount stays read-only.

## Operational notes for U5 and U7

- The API needs `MEDIA_INCOMING_PATH` (its own writable bind of `FINAL/.affilio-incoming`);
  the worker needs `MEDIA_FINALS_WRITABLE=true`. Without them the upload routes answer 503
  and the finalizer stays inert, so the code is safe to deploy before the mounts exist.
- Chunk requests carry `Upload-Offset`, `Content-Length` and `X-Upload-Chunk-Sha256`; the
  proxy must forward PATCH bodies. Buffering one 8 MiB chunk per request is acceptable.
- Finalization runs one session per worker poll between scan claims and thumbnail work; a
  queue of N uploads drains in roughly N × (hash time + ffprobe) with the 5 s poll interval.
- `failed` sessions keep their staging copy until the TTL for inspection; `duplicate`,
  `aborted`, `expired` and `completed` sessions lose it immediately or at the next hourly
  maintenance run.
- Hard links on the production share (btrfs) have not been exercised yet; U7 verifies them
  with a probe file before the first real upload.

## Known limits

- U5 resume identity verifies file metadata plus the last committed chunk only; a
  whole-file identity check (full hash before completion, or server-side comparison of the
  reselected file's chunk digests) is required before production rollout (U7 prerequisite).

- Every browser requires re-selecting files after a reload in U5; the Chromium persistent
  file-handle optimisation is a follow-up. Reselected files are verified against the
  uploaded bytes before resuming.
- The proxy streams chunk bodies; the one-chunk buffer limit no longer applies.
- No bulk re-assignment of categories after upload beyond the existing manual override.
