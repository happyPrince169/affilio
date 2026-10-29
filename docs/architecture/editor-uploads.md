# Editor video uploads — technical design (U1)

Decision record: `docs/decisions/0012-editor-uploads.md`. Milestones:
`docs/milestones/U-editor-uploads.md`. Status: design only; no code, schema or production
change exists yet. Names below are proposals for U2–U6 and may be refined there.

## 1. Architecture comparison

Two candidates were evaluated for who writes into FINAL.

| Criterion                    | A. Dedicated restricted writer (worker process)                                                                             | B. API container with FINAL writable                                             |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Security isolation           | API can write only `FINAL/.affilio-incoming` (its own bind); legacy finals unreachable for writing from the network process | Internet-facing process can open any legacy final for writing; code is the guard |
| Blast radius of an API bug   | Garbage in staging, cleaned by expiry                                                                                       | Corrupted or replaced finals                                                     |
| DSM ACL / uid behaviour      | Both run as uid 1026 gid 100; the writer needs create rights on the tree, the API only on staging                           | Same uid needs create rights on the tree from the API                            |
| Mount design                 | API: `+1` rw bind of the staging subdirectory; worker: FINAL rw instead of ro                                               | API: FINAL rw; nothing else                                                      |
| Operational complexity       | One queued job type on the existing worker; no new container                                                                | None beyond the mount                                                            |
| CPU / RAM                    | Hashing and ffprobe run in the worker (already does scans and thumbnails); API stays light                                  | Hashing and ffprobe inside request handlers of the API                           |
| Failure recovery             | Job table gives claims, leases and retries for free (ADR 0004)                                                              | Needs the same machinery inside the API anyway                                   |
| Deployment / rollback        | Two compose edits, both reversible                                                                                          | One compose edit                                                                 |
| Maintenance for a small team | Same image, same code paths as scans                                                                                        | Slightly less code (no job hop)                                                  |

Chosen: **A, on the existing worker container.** It is the simplest design that keeps the
network-facing process unable to touch existing finals. A separate finalizer container is
not needed for that property and is left as a compose-only option.

Deployment shape (U7, not applied):

```
api        /data/incoming   <- ${FINALS}/.affilio-incoming   rw   (new)
           /media/finals    <- ${FINALS}                      ro   (unchanged)
worker     /media/finals    <- ${FINALS}                      rw   (was ro)
publisher  /media/finals    ro, exporter /media/finals ro, originals/proxies ro everywhere
```

Inside the worker, `/media/finals/.affilio-incoming/<file>` and the destination share one
mount, so `link`/`rename` stay on one filesystem. The API never renames anything.

## 2. Resumable upload protocol

### 2.1 Shape

- One **session per file**. A browser-generated `batch_id` (UUID) may group sessions for
  reporting; it has no server-side lifecycle.
- **Chunk size** is server-defined (`UPLOAD_CHUNK_BYTES`, default 8 MiB) and returned at
  session creation. Each request carries one chunk, well under the tunnel's 100 MB limit.
- **Strict order**: a chunk is accepted only when its `Upload-Offset` equals the session's
  committed offset. The client keeps at most one chunk in flight per session and uploads two
  sessions concurrently by default (configurable in the UI, hard-capped by the server per
  user).
- **Server-authoritative progress**: the committed offset lives in the database and moves
  only after the bytes are durably written and the chunk digest matched. The client never
  reports completion; `complete` is refused unless `offset == expected_size`.
- **Idempotent retries**: a retry of an already committed chunk (its range lies below the
  committed offset and the digest matches the recorded one for that index) returns the
  current offset without writing. A chunk whose offset is ahead of the committed offset is
  refused with the current offset so the client re-syncs.
- **Exclusive writer**: the chunk handler takes `SELECT … FOR UPDATE NOWAIT` on the session
  row; a concurrent writer gets `423` and retries after reading the offset.
- **Pause** is purely client-side (stop sending). **Resume** reads the offset and continues.
  **Cancel** calls abort. **Retry** re-sends the failed chunk or re-reads the offset.

### 2.2 Endpoints (all under `/api/v1/media`, cookie session + CSRF as today)

| Method | Path                     | Role                       | Purpose                                                                                                                                                                                                       |
| ------ | ------------------------ | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/uploads/options`       | editor+                    | Owners the caller may submit for, active product categories and content types (from the facet roots, dynamic), limits (`chunk_bytes`, `max_file_bytes`, concurrency cap)                                      |
| POST   | `/uploads`               | editor+                    | Create a session: `filename`, `size`, `production_owner_category_id`, `product_category_id`, `content_type_id`, optional `batch_id`, `replaces_asset_id`, `client_sha256`, admin-only `submitted_for_user_id` |
| GET    | `/uploads/{id}`          | uploader or admin          | State, committed offset, expiry, result (`media_asset_id` or `duplicate_of_asset_id`), failure code                                                                                                           |
| PATCH  | `/uploads/{id}/chunk`    | uploader or admin          | Body: raw bytes (`application/offset+octet-stream`). Headers: `Upload-Offset`, `Content-Length`, `X-Upload-Chunk-Sha256`. Returns the new offset                                                              |
| POST   | `/uploads/{id}/complete` | uploader or admin          | Requires full offset; transitions to `uploaded`, queues finalization, returns `202` with the state. Idempotent: later calls return the current state                                                          |
| DELETE | `/uploads/{id}`          | uploader or admin          | Abort; staging file removed; terminal                                                                                                                                                                         |
| GET    | `/uploads`               | editor (own) / admin (all) | List with filters: status, period, owner, uploader, batch                                                                                                                                                     |
| GET    | `/owner-mappings`        | admin                      | Mappings per user                                                                                                                                                                                             |
| POST   | `/owner-mappings`        | admin                      | Create (`user_id`, `production_owner_category_id`); audited                                                                                                                                                   |
| DELETE | `/owner-mappings/{id}`   | admin                      | Revoke (status `revoked`, row kept); audited                                                                                                                                                                  |

Error envelope and codes follow `AppError`: `409 upload_offset_mismatch` (with the current
offset in `details`), `423 upload_locked`, `410 upload_gone` (expired/aborted), `413
upload_chunk_too_large`, `422 upload_chunk_digest_mismatch`, `409 upload_incomplete`,
`403 upload_forbidden`, `409 upload_duplicate` (early check), `422 upload_invalid_taxonomy`.

Authorization is evaluated on every endpoint: organization from the session, then
`uploaded_by_user_id == identity.user.id` or admin.

### 2.3 Streaming and memory

- The chunk endpoint is the one `async def` route in the API: it reads `request.stream()` in
  64 KiB pieces, hashes as it goes and writes with `pwrite` at the committed offset into the
  staging file opened `O_WRONLY | O_NOFOLLOW`. The database work runs in the threadpool as
  elsewhere (ADR 0003). Memory per in-flight chunk is bounded by the read buffer.
- On digest mismatch or disconnect the file is truncated back to the committed offset;
  nothing is committed.
- The Next.js proxy currently reads the whole body (`arrayBuffer`). U5 passes
  `request.body` through with `duplex: "half"` for the chunk path when the runtime supports
  it, and otherwise buffers one chunk (8 MiB). Two concurrent uploads therefore cost at most
  16 MiB in the web container (limit 384 MB).
- Backpressure: `UPLOAD_MAX_ACTIVE_SESSIONS_PER_USER` (default 20), per-user concurrent chunk
  limit enforced by the row lock, `UPLOAD_STAGING_MAX_BYTES` and a free-space headroom check
  at session creation, hashing in the worker with a heartbeat as the scanner does.

### 2.4 Browser side

- Selection: `<input type="file" multiple>`, drag-and-drop (`DataTransferItem
.webkitGetAsEntry` walks dropped folders), and `webkitdirectory` folder picking where the
  browser offers it. Files are read with `File.slice`, so no file is loaded whole; per-chunk
  SHA-256 uses `crypto.subtle.digest` on the 8 MiB slice.
- Queue: lightweight client-side list with states `queued → uploading → verifying →
completed | duplicate | failed | cancelled`, two active sessions, per-file pause, resume,
  cancel and retry, batch defaults with per-file overrides, partial success never rolls back
  finished files.
- Reload recovery: `localStorage` keeps per upload `{upload_id, batch_id, filename, size,
lastModified, taxonomy}` only; the session cookie is HttpOnly and nothing secret is stored.
  On load the page lists the server's active sessions and asks the user to re-select the
  files; matches by name, size and last-modified resume from the server offset. On Chromium,
  `FileSystemFileHandle`s from `showOpenFilePicker` are stored in IndexedDB and reused after a
  permission prompt, so re-selection is skipped there. Firefox and Safari cannot keep file
  handles across reloads: the user re-selects. A cancelled or expired server session is
  shown as such and dropped from the local list.

### 2.5 Duplicate detection

- Identity is the full-file SHA-256 computed by the worker at verification plus the scanner's
  `quick_fingerprint` (sampled bytes, 3 MiB) and size. Lookup: variants of the organization's
  finals with the same `content_hash`, or the same `(file_size, fingerprint)` confirmed by
  hashing the existing file (the scanner's own rule). A match ends the session as
  `duplicate` with `duplicate_of_asset_id`; nothing is created and staging is removed.
- Same name, different content: allowed; the new file gets a collision-safe name. Same
  content, different name: duplicate. The UI states which case applies.
- Optional early check: when the client supplies `client_sha256`, creation refuses a known
  hash with `409 upload_duplicate` before any byte is sent. Not required.
- Concurrent identical uploads: the finalizer claims a session with the partial unique index
  on `(organization_id, verified_sha256)` for `finalizing`/`completed`; the loser becomes
  `duplicate` of the winner's asset.
- Registered uploads write `content_hash` on the variant, so later scans and later uploads
  see the same identity.

## 3. Storage layout and finalization

```
FINAL/
├── .affilio-incoming/                 staging, dot-prefixed (scanner skips), mode 0770
│   └── <organization_id>/<upload_id>.part
├── Hang/Mens-Fashion/Try-On/<file>.mp4   destination, built from authorized taxonomy ids
└── Hang/T9/…                          legacy, never written
```

Rules enforced in code (one module, `media/upload_storage.py`):

- Every path component is resolved with `openat`-style walks using `O_DIRECTORY |
O_NOFOLLOW`; a symlink or non-directory anywhere on the way aborts. Staging files are
  opened `O_NOFOLLOW` and must be regular files owned by the service uid.
- Destination directories come from the three category ids: each segment is matched against
  existing entries of the parent by `normalize_name` (so `Mens-Fashion` and "Men's Fashion"
  resolve to the same folder); a missing folder is created with the category's NFKC name
  stripped of path separators and control characters, mode 0750, exclusive. Client strings
  never reach the filesystem.
- Filename: NFKC, control and separator characters removed, leading dots stripped, trimmed
  to 120 characters, extension must be in `MEDIA_VIDEO_EXTENSIONS`. Collision: append
  `-<first 8 hex of upload id>` before the extension. Placement is `os.link(staging, dest)`
  (fails with `EEXIST` instead of overwriting) followed by `unlink(staging)`; if hard links
  are refused on the share, `open(dest, O_CREAT|O_EXCL)` reserves the name and `rename`
  replaces only that reservation. Never `os.replace` onto an unknown path, never any write to
  a path outside staging or the resolved destination directory, never a delete outside
  staging.
- Validation before placement: size equals `expected_size`; SHA-256 equals
  `client_sha256` when supplied; ffprobe succeeds and reports a video stream; extension
  matches the container reported by ffprobe family (mp4/mov for `.mp4`/`.mov` …).
- Quotas: `UPLOAD_MAX_FILE_BYTES` (default 8 GiB), `UPLOAD_SESSION_TTL_HOURS` (48, measured
  from the last committed chunk), `UPLOAD_MAX_ACTIVE_SESSIONS_PER_USER` (20),
  `UPLOAD_STAGING_MAX_BYTES` (sum of `expected_size` of active sessions, default 100 GiB) and a
  free-space headroom of `expected_size + 1 GiB` at creation.
- Cleanup (maintenance run, hourly): sessions past TTL → `expired`, staging removed; aborted
  sessions' staging removed; `*.part` files in staging with no session row and an mtime older
  than the TTL removed. Nothing outside `.affilio-incoming` is ever deleted.

## 4. State machine

| From                     | Event                                         | To           | Side effects                                                                |
| ------------------------ | --------------------------------------------- | ------------ | --------------------------------------------------------------------------- |
| —                        | POST /uploads (authorized, within quota)      | `created`    | Row written, staging file created empty (exclusive), audit `upload.created` |
| `created`                | first chunk committed                         | `uploading`  | offset advanced                                                             |
| `uploading`              | chunk committed                               | `uploading`  | offset advanced, `last_chunk_at`                                            |
| `created`/`uploading`    | POST complete with offset == size             | `uploaded`   | finalization job visible to the worker                                      |
| `uploaded`               | worker claims (FOR UPDATE SKIP LOCKED, lease) | `verifying`  | hash, fingerprint, probe, duplicate lookup                                  |
| `verifying`              | invalid media / size or hash mismatch         | `failed`     | `failure_code`, staging removed, audit `upload.failed`                      |
| `verifying`              | content already in library                    | `duplicate`  | `duplicate_of_asset_id`, staging removed, audit `upload.duplicate`          |
| `verifying`              | checks pass                                   | `finalizing` | intent recorded: `final_relative_path`, `verified_sha256`, fingerprint      |
| `finalizing`             | link + register asset (one transaction)       | `completed`  | `media_asset_id`, audit `upload.completed`; staging unlinked afterwards     |
| `finalizing`             | unrecoverable error (destination unwritable)  | `failed`     | `failure_code`, staging kept for operator inspection until TTL              |
| any non-terminal         | DELETE /uploads/{id}                          | `aborted`    | staging removed, audit `upload.aborted`                                     |
| `created`/`uploading`    | TTL elapsed (maintenance)                     | `expired`    | staging removed                                                             |
| `verifying`/`finalizing` | lease expired (worker died)                   | re-claimed   | recovery procedure below, attempt counter incremented                       |

Terminal: `completed`, `duplicate`, `failed`, `aborted`, `expired`. Attempts of
verification/finalization are capped (`UPLOAD_FINALIZE_MAX_ATTEMPTS`, 3); exhaustion is
`failed` with the last error and is listed on the admin page.

## 5. Failure recovery matrix

| Boundary                                                | Observable state                                            | Recovery (idempotent)                                                                                                                                                |
| ------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Connection drops mid-chunk                              | file may hold partial bytes past the committed offset       | handler truncates to the committed offset on exit; client re-reads the offset and resends                                                                            |
| Duplicate chunk retry                                   | same range, same digest                                     | returns current offset, no write; different digest for a committed range → `409`                                                                                     |
| Chunk digest mismatch                                   | bytes written past offset                                   | truncate, `422`, offset unchanged                                                                                                                                    |
| API restart during chunk                                | row lock released by the database                           | same as a drop                                                                                                                                                       |
| Invalid video                                           | `verifying`                                                 | `failed(invalid_media)`, staging removed, visible to uploader and admin                                                                                              |
| Size or hash mismatch                                   | `verifying`                                                 | `failed(size_mismatch                                                                                                                                                | hash_mismatch)` |
| Session expires                                         | no chunk for TTL                                            | `expired`, staging removed; UI shows "expired, upload again"                                                                                                         |
| Worker restart before rename                            | `verifying`/`finalizing`, staging present, no destination   | lease expiry → re-claim, redo verification from the file; same results by construction                                                                               |
| Crash after `link` before `unlink(staging)`             | destination and staging both exist, `finalizing`            | destination size+hash match the intent → continue with registration, then unlink staging                                                                             |
| Crash after placement before DB commit                  | `finalizing`, destination exists, no asset row              | re-claim: verify destination against the intent, register the asset; if a scan registered the path meanwhile, adopt that asset (set attribution) instead of creating |
| Crash after DB commit before response                   | `completed`                                                 | client's `complete`/status call returns the result                                                                                                                   |
| Concurrent completion requests                          | two callers                                                 | `UPDATE … WHERE status='uploaded'` claims once; the other reads the state                                                                                            |
| Two sessions, identical content, finalized concurrently | partial unique index conflict on `verified_sha256`          | loser → `duplicate` of the winner's asset                                                                                                                            |
| Content already in library                              | `verifying`                                                 | `duplicate(duplicate_of_asset_id)`                                                                                                                                   |
| Destination folder missing                              | `finalizing`                                                | created exclusively from the category name; `EEXIST` with a non-directory → `failed(destination_conflict)`                                                           |
| Filename collision                                      | `EEXIST` on link                                            | new suffixed name, intent updated, retried once                                                                                                                      |
| Scanner runs while a file is staged                     | dot-folder skipped                                          | nothing indexed                                                                                                                                                      |
| Scanner indexes a placed file before registration       | asset with `ingest_source=scan` at `final_relative_path`    | finalizer adopts it: sets `uploaded_by_user_id`, `ingest_source=upload`, links the session; no second asset                                                          |
| Maintenance deletes staging of a live session           | impossible by rule (only expired/aborted or orphan `.part`) | —                                                                                                                                                                    |

Monitoring hooks (U6/U7): counts per state, sessions in `failed`, finalization attempts,
staging bytes, oldest active session; surfaced on the admin upload page and in structured
logs (`media.upload.*` events with `upload_id`, never with paths outside the root).

## 6. Authorization and attribution

- Roles: viewer cannot create sessions; editor and above can; admin and owner manage
  mappings and may upload for any active owner. `RequireEditor`/`RequireAdmin` as in ADR
  0006, plus per-row ownership checks on session endpoints and organization scoping on every
  query.
- Mappings: `media_owner_mappings` (user ↔ production-owner category, several per user,
  `active`/`revoked`). Creation validates that the category is an active value under the
  organization's production-owner root. Revocation keeps the row for history. The taxonomy
  reference registry refuses deleting a mapped category.
- Session creation: the owner must be one of the caller's active mappings, or the caller is
  admin. `submitted_for_user_id` (admin only) records the editor the work is attributed to;
  `uploaded_by_user_id` is always the authenticated user. Taxonomy ids must be active values
  under the matching facet roots; archived or wrong-facet ids are refused.
- Immutability: uploader, owner id, owner name snapshot, taxonomy ids and timestamps are
  written at creation/finalization and have no update endpoint; the audit trail is
  append-only as today.
- Revision: `replaces_asset_id` on the session requires an asset in the same organization
  with `handoff_status = rejected` that the caller uploaded (or admin). The new asset gets
  `revision_of_asset_id`; the original is untouched. Reports count a completed revision
  separately from first submissions.
- Isolation: all lookups filter by `organization_id` from the session; cross-organization ids
  return `404`, never `403` with detail.

## 7. Integration boundaries

- **Ingest service (U3)**: `media/ingest.py` exposes `register_file(session, registry,
root, relative_path, *, prober, now, attribution)` returning the asset; the scanner calls it
  for new and moved files; the finalizer calls it with attribution. Both produce identical
  identity (fingerprint, probe columns), classification (`FacetContext.classify` +
  `apply_folder_classification`), `handoff_status = pending_review`, `production_period`
  from first ingestion, and `thumbnail_status = pending`.
- **Scanner**: unchanged behaviour; it keeps indexing hand-copied files. The dot-prefixed
  staging folder is skipped by the existing name rule, and `.part` is already a temp marker.
- **Thumbnails**: the worker picks up `pending` as today; uploads add no new path.
- **Handoff review**: unchanged endpoints and UI; the asset page shows uploader, owner and
  revision link.
- **Product-media links**: untouched; links keep pointing at the original asset of a revision
  unless an operator re-links.
- **Reporting (U6)**: reads `media_uploads`, `media_assets` attribution columns and handoff
  fields only.

## 8. Minimal schema (proposed; migrations in U2 and U4)

```
media_owner_mappings
  id uuid pk, organization_id fk, user_id fk users, production_owner_category_id fk content_categories (RESTRICT),
  status varchar check (active|revoked), created_by_user_id fk users, created_at, updated_at, revoked_at,
  unique (organization_id, user_id, production_owner_category_id)

media_uploads
  id uuid pk, organization_id fk, uploaded_by_user_id fk users, submitted_for_user_id fk users null,
  production_owner_category_id fk content_categories (RESTRICT), production_owner_name varchar(120),
  product_category_id fk, content_type_id fk, batch_id uuid null, replaces_asset_id fk media_assets null,
  original_filename varchar(255), expected_size bigint, client_sha256 char(64) null,
  chunk_bytes int, committed_offset bigint default 0, last_chunk_sha256 char(64) null, last_chunk_at,
  staging_name varchar(80) unique, status varchar check (created|uploading|uploaded|verifying|finalizing|completed|duplicate|failed|aborted|expired),
  verified_sha256 char(64) null, fingerprint varchar(64) null, final_relative_path varchar(1024) null,
  media_asset_id fk media_assets null, duplicate_of_asset_id fk media_assets null,
  failure_code varchar(64) null, failure_detail text null, finalize_attempts int default 0, lease_until timestamptz null,
  created_at, updated_at, uploaded_at null, completed_at null, expires_at
  index (organization_id, uploaded_by_user_id, status), index (status, updated_at),
  unique partial (organization_id, verified_sha256) where status in (finalizing, completed)

media_assets (+)
  ingest_source varchar check (scan|upload) default scan, uploaded_by_user_id fk users null,
  revision_of_asset_id fk media_assets null
```

Conventions: UUID keys, timezone-aware timestamps, named constraints, VARCHAR + CHECK
enums, `organization_id` everywhere, working downgrades.

## 9. Security threat checklist

- [ ] Viewer cannot create, read, patch, complete or abort sessions.
- [ ] Editor limited to mapped owners; revoked mapping refused immediately.
- [ ] Admin on-behalf upload records both users.
- [ ] Session endpoints deny other users' sessions and other organizations' ids (404).
- [ ] Taxonomy ids validated for facet, activity and organization; archived refused.
- [ ] Filenames sanitized; traversal (`..`, separators, NUL), leading dots refused.
- [ ] Symlinks and special files refused on every path component and on staging files.
- [ ] Destination never overwritten (`link`/`O_EXCL`); legacy files never opened for write.
- [ ] No delete outside staging; staging deletes only for expired, aborted or orphan files.
- [ ] Chunk bodies bounded (`Content-Length` ≤ chunk size, hard cap on bytes read).
- [ ] Per-session lock prevents interleaved writers; offset precondition prevents gaps.
- [ ] Quotas per user and global; free-space headroom.
- [ ] Hash verified server-side; client hash optional and never trusted for placement.
- [ ] ffprobe time-limited (existing `MEDIA_PROBE_TIMEOUT_SECONDS`); invalid media rejected.
- [ ] Duplicate response reveals only asset id and title visible to the caller's role.
- [ ] Audit events for create, complete, fail, duplicate, abort, mapping changes.
- [ ] Logs redact paths (existing redactor) and never include chunk data.
- [ ] CSRF: same-origin rule applies to all unsafe methods including PATCH chunks.
- [ ] Rate limits: existing login limiter unchanged; chunk endpoint bounded by the lock and
      quotas, not by a new limiter.

## 10. Production changes required before U7 (not applied)

| Area        | Change                                                                                                                                                                                                                                                                                                                                           |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| NAS dirs    | Create `FINAL/.affilio-incoming` (owner uid 1026, group users, mode 0770). Nothing else.                                                                                                                                                                                                                                                         |
| NAS ACL     | On `FINAL`: allow `hoangtuhanhphuc` (uid 1026) read, write (create), execute, append, read/write attributes, read permissions, **no delete**, inherit to folders only (so legacy and future files gain no write entry). On `.affilio-incoming`: full rights for the same user, inherited. Validate with a probe directory before the real grant. |
| Compose     | api: add bind `${AFFILIO_FINALS_HOST_PATH}/.affilio-incoming` → `/data/incoming` rw. worker: `/media/finals` `read_only: false`. No change to publisher, exporter, originals, proxies.                                                                                                                                                           |
| Environment | api: `MEDIA_INCOMING_PATH=/data/incoming`, `UPLOAD_CHUNK_BYTES`, `UPLOAD_MAX_FILE_BYTES`, `UPLOAD_SESSION_TTL_HOURS`, `UPLOAD_MAX_ACTIVE_SESSIONS_PER_USER`, `UPLOAD_STAGING_MAX_BYTES`. worker: `MEDIA_FINALS_WRITABLE=true`, `UPLOAD_FINALIZE_MAX_ATTEMPTS`. `META_LIVE_PUBLISHING_ENABLED` stays `false`.                                     |
| Database    | 0014 owner mappings (U2); 0015 upload sessions + asset attribution columns (U4). Backup before each deployment as in the runbook; downgrades tested by `test_migrations.py`.                                                                                                                                                                     |
| Monitoring  | Admin uploads page (states, failures, staging bytes), structured log events, `nas:status` extended with staging size, alert on sessions `failed` or stuck in `verifying` past the lease.                                                                                                                                                         |
| Rollback    | Revert compose mounts and env, downgrade migrations, leave `.affilio-incoming` in place (harmless, hidden). Uploaded finals remain valid library assets.                                                                                                                                                                                         |

## 11. Unresolved decisions

1. Admin uploads on behalf of an editor: attribute to the named editor via
   `submitted_for_user_id` (recommended) or always to the admin.
2. Filename collision: suffix with the upload id (recommended) or refuse and ask for a new
   name.
3. Staging TTL 48 h and the 8 GiB per-file limit are proposals.
4. Whether the finalizer should run in its own container at U7 (default: the existing
   worker).
5. What a duplicate response may show to an editor who did not upload the original
   (default: title and submission date, no reviewer notes).
6. Whether to keep the optional `client_sha256` early check in U5 (default: skip in MVP).
