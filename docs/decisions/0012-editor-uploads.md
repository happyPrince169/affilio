# ADR 0012 — Editor video uploads: split write path, staged finalization, direct registration

- **Status:** Proposed (U1, 2026-10-29; documentation only, nothing implemented or deployed)
- **Context:** Finished videos reach AFFILIO today by editors copying files onto the NAS share
  under `FINAL/{Owner}/{ProductCategory}/{ContentType}/` and an admin running a finals scan.
  Editors must know the folder convention, the scan is whole-root and admin-only, and nothing
  records who delivered a file. The product needs a browser upload page (many files at once,
  resumable, with progress), attribution of every delivered video to a production owner and
  to the person who uploaded it, immediate appearance in the library, the existing handoff
  review, and later production reporting. Constraints: every container mounts FINAL
  read-only (ADR 0005 keeps originals and proxies read-only by construction), the public
  hostname runs through Cloudflare Tunnel (request bodies above 100 MB are refused), the
  Next.js proxy buffers each request body in memory, the NAS has two cores and limited RAM,
  and the API process is the only internet-facing component. Design detail lives in
  `docs/architecture/editor-uploads.md`; milestones in `docs/milestones/U-editor-uploads.md`.

## Decision

1. **Split write path: the API stages, the worker finalizes.** The API container gets exactly
   one new writable bind: the hidden staging directory `FINAL/.affilio-incoming`, mounted at
   `/data/incoming`. It validates sessions and appends chunks there and never sees FINAL
   writable. The existing worker container (scans, thumbnails, maintenance; not reachable
   from the network) gets FINAL mounted read-write and performs verification, the atomic
   placement into `{Owner}/{ProductCategory}/{ContentType}/` and the asset registration as a
   queued job. Publisher and exporter keep FINAL read-only; originals and proxies stay
   read-only everywhere. The staging directory is dot-prefixed, so the scanner never indexes
   it, and it sits inside FINAL so the worker's rename never crosses a mount boundary
   (`rename(2)` across bind mounts fails with `EXDEV` even on one volume).
2. **Resumable, server-authoritative chunk protocol** (tus-like, no library): one upload
   session per file, fixed 8 MiB chunks sent strictly in order with the committed offset as
   precondition and a per-chunk SHA-256, idempotent retries, a per-session row lock so two
   writers cannot interleave, progress read back from the server after any interruption or
   reload, two files in flight per browser by default. Chunk bodies are streamed to the
   staging file in the API; the proxy forwards them without buffering where the runtime
   allows, and buffers at most one chunk otherwise.
3. **Staged finalization with an intent record.** Completion only moves the session to
   `uploaded`; the worker then verifies size, full SHA-256, content fingerprint and ffprobe,
   checks for duplicate content, resolves the destination directory from the authorized
   taxonomy values (never from client strings), records the intended final path in the
   session row, places the file by `link` + `unlink` (exclusive, never overwriting), registers
   the asset through the shared ingest service in the same transaction as the session's
   `completed` transition, and only then removes the staging copy. Every boundary is
   re-entrant: a crashed step is redone from the recorded state, and a file the scanner
   indexed first is adopted rather than duplicated.
4. **Content-level duplicate rejection.** Identity is the full SHA-256 confirmed by the same
   quick fingerprint the scanner uses. A second upload of existing content (whatever its
   name) ends as `duplicate`, creates no asset and no file, and points at the existing asset.
   A partial unique index on `(organization_id, verified_sha256)` over finalizing and
   completed sessions makes two simultaneous identical uploads converge on one asset.
5. **Attribution is written once and kept in the media module.** `media_owner_mappings` links
   a user to one or more production-owner categories (admin-managed, revocable, audited).
   Each session names exactly one owner, chosen from the uploader's active mappings, or any
   active owner when an admin uploads on behalf of someone and names that editor. The session
   stores the authenticated uploader, the attributed owner category id and the owner's name
   at submission time; the asset stores `ingest_source`, `uploaded_by_user_id` and
   `revision_of_asset_id`. None of these columns has an update path. Payroll reporting reads
   these ids, not folder names or current category names. Identity keeps its position in the
   module order: the mapping table is a media table that references identity and taxonomy.
6. **Review and corrections reuse the handoff gate.** Every registered upload starts
   `pending_review`. A corrected submission is a new session and a new asset that carries
   `revision_of_asset_id`; the rejected original, its file and its review history stay.
7. **Direct registration.** The scanner's asset creation, probing, classification, thumbnail
   scheduling and handoff initialisation move into `media/ingest.py` and are used by both the
   scanner and the finalizer, so an uploaded final is classified exactly as a scanned one and
   appears in the library without a root scan.

## Alternatives considered

- **API container with FINAL mounted read-write (one process does everything).** Simplest
  compose change and no job hop, but the only internet-facing process would hold write
  access to every legacy final. Code discipline (exclusive create, no open-for-write of
  existing paths) would be the sole barrier. Rejected: the split costs one queued job and no
  new container, and removes the whole class of "API bug or compromise rewrites a final".
- **New dedicated finalizer container.** Same code as the chosen design with the finalizer
  isolated from the scanner. It adds roughly 100–130 MB of RAM on a machine already running
  six containers, another health check, another log stream and another thing to roll back,
  for isolation between two non-network processes that already share an image and a database
  identity. Deferred: the job runs under the worker entrypoint, and moving it to its own
  container is a compose-only change (`command: ["finalizer"]`) if the need appears.
- **Copy into FINAL instead of rename.** Avoids the same-filesystem requirement but a copy is
  not atomic: a crash leaves a truncated video that the scanner may index. Rejected.
- **Whole-file or large multipart uploads.** Refused by the tunnel above 100 MB and buffered
  by the proxy. Rejected.
- **tus server library, object storage, Redis-backed queues.** New infrastructure or
  dependencies for a protocol subset that fits in a few endpoints over the existing
  database-backed worker (ADR 0004). Rejected.
- **Client-computed whole-file hash as the duplicate key.** WebCrypto cannot stream, so a
  2 GB file would need a JavaScript hashing library and a full read in the browser. Rejected
  as a requirement; the server computes the hash once at verification. A client-supplied hash
  stays an optional early check.

## Consequences

- Two production changes need separate approval before U7: a Synology ACL grant for the
  AFFILIO service user on FINAL (create and traverse on folders, full rights on the staging
  directory only, no delete on the tree) and the compose mount changes above. The
  verification steps are listed in the design document.
- The worker's scan code keeps running in a process that can write FINAL. The scanner has no
  write path and tests assert that; the finalizer's write calls are confined to one module.
- Uploads add one `uploaded` → `completed` hop of up to one worker poll interval (5 s) plus
  verification time (hashing at disk speed, one ffprobe). The UI shows this as "verifying".
- Staging consumes disk until finalization or expiry; quotas and cleanup are part of U4.
- The 92 legacy finals and the existing scan path are unchanged. Files still copied onto the
  share by hand remain supported and are still indexed by scans.
