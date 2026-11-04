# ADR 0012 — Editor video uploads: split write path, staged finalization, direct registration

- **Status:** Proposed (U1, 2026-10-29); implemented locally U2–U7 Phase A; amended
  2026-10-30 after the U7 Phase B2B probe; **amended 2026-11-04 (amendment 2, fast track):
  placement into FINAL by hard link is abandoned in favour of a dedicated, AFFILIO-managed
  upload root.** Decisions 1 and 3 and the 2026-10-30 amendment are superseded where they
  describe writing into FINAL; everything else stands. Not deployed.
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
  verification steps are listed in the design document. Amendment 2026-10-30: the grant is
  one create-only entry per type folder, not inherited; see below.
- The worker's scan code keeps running in a process that can write FINAL. The scanner has no
  write path and tests assert that; the finalizer's write calls are confined to one module.
- Uploads add one `uploaded` → `completed` hop of up to one worker poll interval (5 s) plus
  verification time (hashing at disk speed, one ffprobe). The UI shows this as "verifying".
- Staging consumes disk until finalization or expiry; quotas and cleanup are part of U4.
- The 92 legacy finals and the existing scan path are unchanged. Files still copied onto the
  share by hand remain supported and are still indexed by scans.

## Amendment 2026-10-30 — U7 Phase B2B findings on the production NAS

Source: `docs/deployment/upload-rollout.md`, "Phase B2B". One directory
(`FINAL/Hang/Mens-Fashion/Try-On`), one disposable placed file, one disposable protected
file, everything removed and the ACL restored to its baseline afterwards; the 92 legacy
finals were never opened and are byte-for-byte and ACL-for-ACL unchanged.

**Observed on the tested NAS** (DS923+, DSM 7.3.2, btrfs volume with `synoacl`):

- With a single create-only entry (`-w-----------`, no inheritance) on the type folder, the
  service identity (uid 1026, gid 100, no supplementary groups, no capabilities) created a
  staging file, hard-linked it into the folder (same inode), was refused `EEXIST` on the
  existing name, and was refused every write, utime, link, rename, unlink and mkdir attempt
  against a file carrying the legacy ACL. After placement the staging name could be
  unlinked and the placed name could not.
- The placed file carried the destination folder's inherited entries (service user read and
  execute only; administrators and the three editors full rights), not the staging
  directory's entry. On this configuration a hard link re-inherits the destination's ACL
  onto the inode.
- The probe exited 1 only because its T6 expectation (`allowed`) encoded the pre-probe
  hypothesis; the probe now requires the contract to be stated explicitly and `denied` is
  the verified value for this NAS.

**Assumptions not yet independently verified:** whether the service user, as the owner of
a placed file, can still open it for writing, change its times or mode, or rewrite its
ACL (owner-implicit rights; the negatives ran against a file owned by another user); whether
the behaviour holds on other DSM releases, other volume types or after the worker creates a
folder itself; whether the inode's stored ACL changes at link time or permissions are
evaluated by path (irrelevant to the worker, which never writes after linking).

**Security properties required for production:** the service identity must be unable to
modify, rename, link or delete any legacy final (verified); unable to overwrite an existing
name (verified); able to create files only in authorized type folders (verified for one
folder); unable to delete a placed final (verified on this NAS); unable to modify a placed
final (**not yet verified**, owner-rights probe required); the worker container must run
without capabilities (to confirm after deployment).

**Decisions amended:**

1. The ACL grant in "Consequences" becomes: one explicit, non-inherited create-only entry on
   each of the 72 bootstrapped type folders; nothing on FINAL, owner roots, category folders
   or T9; the staging directory keeps its single full-rights entry. The worker does not
   receive the create-subfolder right; new categories are bootstrapped by an administrator
   and uploads to a missing folder fail with `destination_unavailable`.
2. The staging ACL split (directory entry plus a file entry without delete, plus human
   entries) is **modified**: it is no longer a prerequisite, because the placed file does
   not keep the staging entry on this NAS and inherits the editors' entries from the
   destination. It stays documented as the mandatory response if any future probe observes
   that the service identity can unlink a placed file (`T6 observed: allowed`), for example
   after a DSM update or a volume migration. The human-entries part is dropped.
3. Phase C is blocked until Phase B3 (owner-rights probe on the placed file, then the
   72-folder ACL rollout with full-tree verification) passes. The worker implementation is
   unchanged by this amendment.

## Amendment 2 — 2026-11-04 — Two storage roots, no hard links, no ACL work (fast track)

**Context.** Phases B1–B3.1 proved that the hard-link placement into `FINAL` works on this
NAS but only behind a chain of Synology ACL assumptions (create-only entries on 72 folders,
re-inheritance of the destination ACL at link time). The B3.1B owner-rights probe then
failed: the service identity could `chmod` the file it had placed, which dropped its
Synology ACL, and could create further hard links to it (`docs/deployment/upload-rollout.md`,
"B3.1B probe result"). Property P6 does not hold, so the design's remaining protection would
have been code discipline anyway. Every one of those is filesystem behaviour AFFILIO does not control
and would have to re-establish after any DSM update or volume change. The business needs a
working upload, library and approval flow now.

**Decision.**

1. **Two storage roots.** `finals` (LEGACY FINAL, `MEDIA_FINALS_PATH`) stays exactly what it
   is: a read-only root, mounted read-only in every container, scanned as today, holding the
   92 existing records at their existing paths. A new root `uploads` (AFFILIO_UPLOADS,
   `MEDIA_UPLOADS_PATH`, `MediaRootKind.UPLOAD`, access read-write) is dedicated to files
   delivered through AFFILIO. It lives outside `FINAL` on the same volume, is never scanned,
   and is **application-managed storage**: its layout and contents are owned by the code,
   not by people copying files onto a share.
2. **Layout and finalization.** `<uploads>/staging/<organization>/<token>.part` receives
   chunks (API); `<uploads>/ready/<organization>/<YYYY>/<MM>/<upload id>.<ext>` holds the
   finalized file (worker). Finalization is one atomic `rename_noreplace` on the same
   filesystem (`renameat2(RENAME_NOREPLACE)`, with an exclusive-reservation fallback), so
   the staging name disappears as the final name appears and an existing name is never
   replaced. There are no hard links anywhere in the upload path.
3. **UUID physical names, classification in the database.** The physical filename is the
   upload id plus the sanitized extension; the asset title keeps the editor's filename.
   Owner, product category and content type are stored as `media_content_categories` rows
   with `assignment_source = upload`, taken from the taxonomy ids the editor chose (never
   from folder names). Reclassification (`PUT /media/assets/{id}/facets`) rewrites those rows
   in place (legacy finals get an `override` row that wins over their folders on rescans)
   and never moves a file; the upload session keeps its original attribution.
4. **Unified resolver.** Every reader of media bytes (preview stream, thumbnails, exports,
   publishing checks and uploads, duplicate confirmation) resolves a stored variant through
   `MediaRootRegistry.resolve_variant`, which binds the variant's own root (`finals`,
   `uploads`, `originals`, `exports`) and applies the traversal guard. Both roots are
   therefore served by one code path; `publication_uploads.file_root_key` may be `uploads`.
5. **Unchanged.** Chunk protocol, resume identity, SHA-256 verification, idempotency, quotas,
   the session state machine, duplicate rejection by content hash across both roots, the
   worker lease/retry model, owner mappings, handoff review, revisions and reporting.
6. **Not in this milestone.** Permanent deletion of uploaded files (no code path exists; the
   `ready/` tree is only ever appended to), migration of legacy finals into the new root.

**Tradeoff, stated explicitly.** The legacy tree was protected by the filesystem (read-only
mounts plus Synology ACLs); the new root is protected by the application: the API container
needs `staging/` writable and the worker the whole root writable, so a bug or compromise in
those processes could damage uploaded finals, where it could not damage legacy finals.
Mitigations: the API mounts only `staging/` read-write (the root itself read-only for
previews); the worker is the only writer of `ready/` and has exactly one write operation on
it (the no-replace rename); nothing opens a finalized file for writing, renames or deletes
one; every directory walk is `O_NOFOLLOW`; the staging file must be a singly-linked regular
file owned by the service uid; and the audit trail records every placement with its SHA-256,
so integrity can be re-verified from the database. The legacy root is untouched by all of it
and stays immutable by mount.

**Consequences.** Phase B3.2 (72-folder ACL rollout) and Phase C as planned are cancelled;
the B3.1B probe leftovers are removed and the one ACL entry restored (Phase F0,
`docs/deployment/upload-rollout.md`). Production needs one new directory tree (created by an
administrator, owned by the service uid), two compose binds and two environment variables;
no ACL changes. Migration 0018 widens two CHECK constraints and is lossless on downgrade.
