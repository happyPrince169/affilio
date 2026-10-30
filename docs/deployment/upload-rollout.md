# Editor uploads — production rollout (U7)

Phase A (readiness audit, read-only) is done; Phase B (probe) and Phase C (deployment) each
need explicit approval. Design: `docs/architecture/editor-uploads.md`, ADR 0012.

## Preconditions found in Phase A

- Production runs b16e8e2 at migration 0013; local chain d98b08a → 563d5a7 (+ the U7
  hardening commit) adds migrations 0014–0017, linear after 0013.
- All five application containers run as uid 1026, gid 100, **no supplementary groups**.
  The FINAL tree's ACL gives that user read and execute only; the Step 4 folders are owned
  by the user but carry the same read-only entry. Writes from containers are therefore
  refused today. The SSH session that created the folders had the administrators group,
  which containers do not have.
- FINAL lives on btrfs (`synoacl` mount); no hard links exist there yet. `fs.protected_*`
  sysctls are root-only to read; the probe answers the question empirically.
- Compose mounts FINAL read-only for api, worker, publisher and exporter; there is no staging
  bind and no upload environment variable yet. Code defaults keep uploads disabled:
  `MEDIA_INCOMING_PATH` unset → upload routes answer 503; `MEDIA_FINALS_WRITABLE=false` →
  finalizer and cleanup inert.
- 11 TB free; 166 assets, 92 finals, 407 variants; publishing flag false; health and tunnel
  green.

## Phase B1 — staging probe (done, passed)

Isolated container (`affilio-api:latest`, uid 1026 gid 100 without groups, no network, no
capabilities, read-only root, staging bind only) proved exclusive create and write, hard link
with the same inode (nlink 2), EEXIST on an existing name, unlink persistence and clean
cleanup. `FINAL/.affilio-incoming` exists (0770, owner 1026, one full-rights entry for the
service user).

## Phase B2B — finalization probe on ONE destination directory (executed 2026-10-30, PASS)

Scope: `FINAL/Hang/Mens-Fashion/Try-On` only. No entry was added to FINAL, owner roots,
category folders, other type folders or T9 folders; the service user's existing read and
traverse entries cover the ancestors, and inside the container the bind mount starts at
FINAL anyway. Scripts: `scripts/nas-b2b-host.sh` (host side, administrator identity) and
`scripts/nas_finalize_probe.py` (container side, service identity). Baseline:
`backups/b2b-20261029T161154Z/`; probe reports: `/tmp/affilio-b2b/probe-report.txt` and
`probe-report-attempt1-identity-guard.txt` on the NAS. Both are kept.

ACL entry under test, Synology syntax, create-file only, this folder only (no inheritance):

```
user:hoangtuhanhphuc:allow:-w-----------:----
```

`w` is "write data / create file" on a directory; `p` (create subfolder), `d`, `D`, `A`,
`C` and `o` are deliberately absent. The worker needs `p` only to create a missing folder,
which the single-directory probe does not exercise.

### B2B results

Environment: DS923+, DSM 7.3.2 (build 86009), volume 1 btrfs mounted with `synoacl`, image
`affilio-api:latest` (95aecb93f1ae, the deployed b16e8e2 image), container run with
`--user 1026:100 --cap-drop ALL --security-opt no-new-privileges --read-only --network none`
and one read-write bind of FINAL. Identity inside the container: uid 1026, gid 100,
`os.getgroups() == [100]` (the primary gid only; attempt 1 was refused by the identity guard
for exactly that listing and the guard was corrected in d1c6d67 before the retry).

| Test | What                                                                          | Result                                       |
| ---- | ----------------------------------------------------------------------------- | -------------------------------------------- |
| T1   | exclusive create, write, fsync under `.affilio-incoming/probe-<token>/`       | ok                                           |
| T2   | hard link into the type dir                                                   | ok: same device and inode 412051, nlink 2    |
| T3   | link onto the existing name                                                   | ok: `EEXIST`, placed file untouched          |
| T4a  | protected file: open for write                                                | refused `EACCES`                             |
| T4b  | protected file: open read-write                                               | refused `EACCES`                             |
| T4c  | protected file: utime                                                         | refused `EPERM`                              |
| T4d  | protected file: hard link to a new name                                       | refused `EPERM`                              |
| T4e  | type dir: mkdir                                                               | refused `EACCES` (no `p` granted)            |
| T4f  | protected file: rename                                                        | refused `EACCES`                             |
| T4g  | protected file: unlink                                                        | refused `EACCES`                             |
| T4h  | protected file inode, size, mtime, ctime unchanged                            | ok                                           |
| T5   | unlink of the staging name                                                    | ok: placed file readable, nlink 1            |
| T6   | unlink of the placed name, contract `--expect-placed-unlink allowed`          | **observed denied** (`EACCES`) → reported FAIL |
| T7   | cleanup by the service identity                                               | probe dir removed; placed file left for host |

The probe exited 1. The only failed line is T6, and it failed because the stated expectation
(`allowed`, the pre-B2B hypothesis) was wrong, not because a security property was violated:
every negative test was refused with `EACCES` or `EPERM`, and the service identity could not
remove the file it had just placed. The protected file was owned by `dangtung` (set with
`synoacltool -set-owner` under sudo in step 5A after the administrator identity was refused)
and carried the legacy reference ACL, entry for entry.

Host side afterwards (steps 7–9, all exit 0): `cleanup <token>` removed exactly the
protected file and the placed probe file; `restore-acl` found the create-only entry at a
unique index, removed it and proved the directory ACL equal to the five-entry baseline;
`verify` printed PASS; all 92 legacy files have identical ACLs (460 lines) and identical
inode, link count, size, mtime and ctime; staging is empty with its single service-user
entry unchanged; no `.affilio-*` name exists under FINAL; six containers up;
`META_LIVE_PUBLISHING_ENABLED=false`.

### Observed ACL behaviour (this NAS only)

**Observed.** Listed with `synoacltool -get` after the probe, when the staging name was
already gone, the placed file (inode 412051, owner `hoangtuhanhphuc`) carried exactly the
entries of the destination directory, inherited (levels 5 and 6, one deeper than the type
directory's levels 4 and 5):

```
[0] user:hoangtuhanhphuc:allow:r-x---a-R-c--:---- (level:5)
[1] group:administrators:allow:rwxpdDaARWc--:---- (level:6)
[2] user:phuongnhung:allow:rwxpdDaARWc--:---- (level:6)
[3] user:dangtung:allow:rwxpdDaARWc--:---- (level:6)
[4] user:duckhang:allow:rwxpdDaARWc--:---- (level:6)
```

No entry derived from the staging directory (`rwxpdDaARWc--:fd--`, where the inode was
created) survived, and the level-0 create-only entry did not propagate (it has no inherit
flags, by design). The listing is identical to the administrator-created protected file in
the same directory and to the legacy reference file. This explains T5 and T6 together: the
staging name could be unlinked through the staging directory's delete-child right (`D`),
the placed name could not, because the service user has neither `d` on the inode nor `D`
on the type directory.

**Classification.** Synology stores one ACL per inode as an extended attribute (the listing
header says `Archive: is_inherit`, and `level:N` marks an entry inherited from N levels up).
The placed file's entries sit one level below the type directory's, so the inode's ACL was
recomputed from the destination parent when the second name was created. Synology's public
documentation describes inheritance on creation and explicit propagation; we found no
statement about `link(2)`. Treat this as implementation behaviour of the `synoacl` btrfs
module on DSM 7.3.2, not as a documented contract and not as a property of hard links in
general: on a plain POSIX filesystem the inode keeps whatever it had, and on any other
volume type, DSM release or share the observation must be re-established before it is
relied upon. The probe's T6 contract is therefore stated by the operator per configuration
(`--expect-placed-unlink`, no default), and `denied` is the verified value for this NAS.

**Not independently verified (assumptions).**

1. Whether the inode's stored ACL was replaced at link time (so the staging name was also
   read-only for the service user during the nlink-2 window) or Synology evaluates by path
   and the listing shows an inherited view. The data cannot distinguish them. The worker
   never writes through either name after hashing, so the difference has no effect on the
   production path; it matters only for a process that would append to a staging file after
   linking it, which nothing does.
2. Owner-implicit rights on the placed file. The T4 negatives ran against a file owned by
   `dangtung`; the placed file is owned by the service user. Linux lets a file's owner
   `chmod`, `utime` and set extended attributes without `CAP_FOWNER`, and Synology may give
   the owner an implicit permission-change right. Whether the service user can open the
   placed file for writing, change its times or mode, or rewrite `system.synoacl` on it is
   **unknown** and must be probed (T8 in Phase B3) before Phase C.
3. The behaviour when the worker itself creates a missing type folder (needs `p`). Not
   tested; T4e only proved that `mkdir` is refused without `p`.
4. Stability across DSM updates and other volumes. One directory, one file, one release.
5. Entries added by the worker's hard link never appear as explicit (level 0) entries, so
   later ACL edits by an administrator on the type folder propagate to uploaded finals
   exactly as to legacy files. Consistent with the listing, not separately exercised.

**Security properties required for production, and their status.**

| Property                                                                   | Status                                                                |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| P1 service identity cannot open a legacy-ACL file for writing              | verified (T4a, T4b) on a disposable file with the legacy ACL          |
| P2 cannot delete, rename or hard-link a legacy-ACL file                    | verified (T4d, T4f, T4g)                                              |
| P3 cannot overwrite an existing name                                       | verified (T3)                                                         |
| P4 can create a file in an authorized type folder with the create-only entry | verified (T2), one folder                                           |
| P5 cannot delete a placed final                                            | verified (T6) on this NAS; contract now explicit in the probe         |
| P6 cannot modify a placed final (write, utime, mode, ACL)                  | **not verified** (owner-implicit rights, assumption 2)                |
| P7 cannot create subfolders unless `p` is granted                          | verified denied (T4e); production decision on `p` still open          |
| P8 placed finals carry the same human entries as legacy files              | observed in the listing; to be exercised as an editor in the smoke test |
| P9 identity has no supplementary groups and no capabilities                | verified in the probe container; production worker container to confirm (`CapEff` 0) |
| P10 staging cleanup by the service identity                                | verified (T5, T7, B1)                                                 |

### Staging ACL split: reassessment → MODIFY (conditional hardening, not a prerequisite)

The split was planned to replace the staging directory's single entry
(`rwxpdDaARWc--:fd--`) with a directory entry (`rwxpdDaARWc--:d---`) and a file entry
without delete (`rw----aARWc--:fi--`), plus the four human entries, for two purposes:

1. Remove the service user's delete right from placed finals, on the assumption that the
   inode keeps its staging ACL after the link. Under the verified behaviour the inode does
   not keep it: T6 was denied with the unsplit staging ACL. For this NAS the split adds no
   delete protection.
2. Make uploaded finals visible to editors through staging-inherited human entries. Also
   moot: the placed file carries the destination's human entries.

Where the split would still provide value: only on a configuration where the B2B
observation does not hold (another DSM release, a volume without `synoacl`, a migration to
ext4 or another NAS), because there the staging-inherited `d` right would travel with the
inode into the type folder. It provides no value against assumption 2 (owner-implicit
rights are independent of ACL entries) and no value inside staging itself, where the API
needs write and the delete-child right anyway.

Decision: keep the recipe in this document as the documented response to a future probe
that observes `T6 allowed`; do not apply it now; drop the human-entries part entirely
(visibility comes from destination inheritance). It is not a Phase C prerequisite. The
condition that re-activates it is mechanical: any run of the container probe with
`--expect-placed-unlink denied` that reports `observed: allowed`.

### Worker versus probe

The probe stands in for `media/upload_finalize.py` and `media/upload_storage.py`. Differences
that matter, and what covers them:

| Aspect                   | Probe (B2B)                                                                                 | Worker                                                                                                                         | Gap and coverage                                                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Staging path             | `.affilio-incoming/probe-<token>/src.part`, created by the probe                            | `.affilio-incoming/<organization uuid>/<32 hex>.part`, directory and file created by the **API** container, same uid           | Same depth, same inheritance; both containers are uid 1026 gid 100. No gap.                                                                               |
| Link call                | `os.link(src, placed)` with full paths                                                      | `os.link(staging_path, filename, dst_dir_fd=dir_fd)` after an `O_DIRECTORY\|O_NOFOLLOW` walk                                   | Both are `linkat(2)`; the ACL check is on the destination directory in both. No gap.                                                                      |
| Destination name         | dot-prefixed (scanner-invisible)                                                            | visible final name                                                                                                             | ACL-independent. A crash between link and commit leaves a visible file; the scanner may index it and the finalizer adopts it (recovery matrix). Unit-tested. |
| Destination folders      | one existing folder with a manually added entry                                             | any of the 72 bootstrapped type folders; a missing folder is created with `os.mkdir` (needs `p`)                               | Without `p`, `mkdir` fails `EACCES` → `failed(destination_unavailable)`, staging kept until TTL. Decision in Phase B3: no `p`, folders are bootstrapped by an admin. |
| Hash before placement    | content written and read back only                                                          | full SHA-256, fingerprint and ffprobe on the staging file **before** the intent and the link; after the link only reads        | The worker never writes after linking, so assumption 1 cannot affect it. Covered by tests that assert no open-for-write of placed paths.                  |
| Atomicity and retries    | single link                                                                                 | intent committed, then link; `EEXIST` → compare size and SHA-256 of the occupant: own earlier link continues, else `destination_conflict` | Under the verified ACL the worker cannot remove a wrong occupant, which is the desired outcome. Crash windows are re-entrant from the row state. Unit-tested. |
| Staging unlink           | T5 via the staging directory's `D` right                                                    | `StagingStore.remove` after the registration commit; a failure is logged and retried by maintenance                            | Same right, same directory. Verified by T5.                                                                                                               |
| Reads after placement    | read-back of the placed name                                                                | SHA-256 on resume, ffprobe, thumbnail generation (written to the thumbnails root, never FINAL)                                 | Read via the inherited `r-x` entry. Verified by T2/T5 read-back.                                                                                          |
| Owner of the placed file | service user                                                                                | service user                                                                                                                   | Same, which is why assumption 2 applies to production and must be probed (T8).                                                                            |
| Container hardening      | `--cap-drop ALL`, `no-new-privileges`, read-only root, no network                           | compose sets no `cap_drop` or `security_opt`; the process runs as uid 1026 so it has no effective capabilities by default      | Confirm `CapEff` is all zeros in the running worker after deployment; add `cap_drop: [ALL]` to the worker in the Phase C compose change.                  |
| Database and lease       | none                                                                                        | claim with lease, intent row, partial unique index on `verified_sha256`                                                        | Not a filesystem property; unit-tested with a local database.                                                                                             |

The worker implementation is unchanged in this phase.

### Commands, in order (recipe used; step E contract corrected)

The host script prints `PASS <step>` or `BLOCKED`/`FAIL` and exits non-zero on anything
unexpected; it never deletes an ACL entry it cannot identify uniquely and prints manual
recovery instructions instead.

| Step                    | Where                     | Command                                                                                                                                                             | sudo?                                                                          |
| ----------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| A preflight (read-only) | NAS, admin                | `bash scripts/nas-b2b-host.sh preflight`                                                                                                                            | no                                                                             |
| B baseline              | NAS, admin                | `bash scripts/nas-b2b-host.sh capture` then `export B2B_DIR=…` as printed                                                                                           | no                                                                             |
| C ACL change            | NAS, admin                | `bash scripts/nas-b2b-host.sh apply-acl` (PASS only when the ACL equals baseline + exactly our entry)                                                               | no, unless `synoacltool -add` is refused for a folder the account does not own |
| D protected file        | NAS, admin                | `bash scripts/nas-b2b-host.sh prepare-protected` (dot-prefixed file in the type dir, ACL copied from one legacy file, which is only read) then `export PROTECTED=…` | no                                                                             |
| E probe                 | NAS, root for Docker only | see below; note the `"token"` in the summary line                                                                                                                   | yes: the Docker socket is root-only                                            |
| F cleanup               | NAS, admin                | `bash scripts/nas-b2b-host.sh cleanup <token>` (exact names only, directories only when empty, never recursive)                                                     | no                                                                             |
| G restore               | NAS, admin                | `bash scripts/nas-b2b-host.sh restore-acl` (unique fixed-string match; PASS only when the full ACL equals the baseline again)                                       | no                                                                             |
| H verify                | NAS, admin                | `bash scripts/nas-b2b-host.sh verify` (PASS or BLOCKED)                                                                                                             | no                                                                             |

Step E, after copying `scripts/nas_finalize_probe.py` to `/tmp/affilio-b2b/`:

```
sudo docker run --rm --name affilio-b2b-probe-$(date -u +%H%M%S) --network none --user 1026:100 \
  --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp:rw,size=16m \
  -v /volume1/ContentFactory/fashion/FINAL:/final:rw \
  -v /tmp/affilio-b2b/nas_finalize_probe.py:/probe.py:ro \
  --entrypoint python3 affilio-api:latest \
  -I /probe.py --final /final --type-dir Hang/Mens-Fashion/Try-On --protected "$PROTECTED" \
  --expect-placed-unlink denied --apply
echo "probe exit=$?"
```

`--expect-placed-unlink` has no default. `denied` is the contract verified on this NAS on
2026-10-30; the B2B run itself used `allowed` (the pre-probe hypothesis) and exited 1 on the
mismatch. The probe also checks the placed file after T6 against the same contract (T6b:
present, nlink 1 and readable when denied; gone when allowed), so an unexpected outcome in
either direction is a FAIL.

The container exits with the probe's code (0 only when every expectation holds, including
the negative ones; an unexpectedly successful destructive test is a FAIL even when the probe
reverses it). It prints the identity, each test with the errno observed, the host-side
cleanup list and a JSON summary with the run token. The one read-write mount is FINAL as a
whole, because hard links cannot cross mounts; the ACL is the only guard, exactly as it will
be for the worker. The probe derives every path from its arguments plus the token, never
opens legacy media, and leaves the placed probe file for host cleanup when the identity may
not delete it.

Negative tests on the protected file: open for write, open read-write, utime, hard link to a
new name, rename, unlink, plus mkdir in the type dir; each must be refused with EACCES or
EPERM (any other errno is also a failure), and the file's inode, size, mtime and ctime must be
unchanged afterwards.

Expected versus unexpected changes during B2B:

- Expected: the type directory's own listing line changes (mtime and ctime move when the ACL
  entry is added and when the protected or probe files are created and removed); while the
  entry is applied, the type directory's ACL block differs from the baseline by exactly that
  entry. `verify` accepts these and nothing else.
- Unexpected, and `verify` reports BLOCKED: any other directory or file whose inode, link
  count, size, mtime or ctime changed, and any ACL line added or removed anywhere else,
  including on legacy files.

What B2B established: the service identity can place a file by hard link into one folder
with a create-only entry, cannot overwrite an existing name, cannot modify, link, rename or
delete a file carrying the legacy ACL, and cannot delete the file it placed. It does **not**
establish production readiness: P6 (no modification of a placed final by its owner) is
unverified, the create-only entry exists on none of the 72 type folders (it was removed in
step G), and a full probe run with the correct contract (exit 0) has not been observed on
the NAS. Those are Phase B3.

## Phase B3 — proposed next milestone (needs approval; nothing below is applied)

Smallest safe step between B2B and deployment. Two parts, the second only after the first.

**B3.1 Owner-rights probe, same single directory.** Extend the container probe with T8,
negatives against the file the probe itself placed (owned by the service user): open for
write, open read-write, `utime`, `chmod`, `os.link` to a new name, `rename`,
`os.setxattr("system.synoacl")`, each expected `EACCES` or `EPERM`, then the inode, size,
mtime, ctime and content re-checked. Rehearse locally first (POSIX lets the owner do most of
these, so the local rehearsal asserts the FAIL reporting). Then on the NAS, exactly the B2B
recipe A–H on `Hang/Mens-Fashion/Try-On` with `--expect-placed-unlink denied`: capture a fresh
baseline, apply the create-only entry, prepare the protected file (owner `dangtung` under
sudo as in step 5A), run the probe, clean up by token, restore, verify. Success means exit 0
from the probe and PASS from cleanup, restore and verify. If any T8 negative succeeds, stop:
P6 then needs a different mechanism (for example placement by a separate identity that does
not own the file, or an administrator-side `chown` step) and Phase C is not reachable from
this design without a new ADR amendment.

**B3.2 ACL rollout to the 72 type folders.** Only after B3.1 passes. A new host-script
subcommand pair, `apply-acl-list <file>` and `restore-acl-list <file>`, driven by an
explicit list of the 72 bootstrapped type folders (generated from the Step 4 listing,
reviewed before use; no FINAL root, owner roots, category folders or T9 folders), the same
entry `user:hoangtuhanhphuc:allow:-w-----------:----`, no `p`. Before: full-tree baseline
(listing and ACL dump) under `backups/`. Per folder: PASS only when the ACL equals that
folder's baseline plus exactly the entry; stop at the first non-PASS and print the manual
recovery instructions. The scripts are rehearsed against the mocked `synoacltool` first.

Validation after B3.2: full-tree `verify` showing exactly 72 added ACL lines and no other
change; all 92 legacy files identical in ACL and metadata; staging unchanged; a second
container probe run (`denied`, with T8) against a different type folder under a different
owner, preferably one that holds no files yet, exit 0; cleanup, no `.affilio-*` left.

Rollback: `restore-acl-list` with the same unique-match rule per folder (never by index),
then `verify` against the baseline; probe artefacts by token only; baselines retained.

Decision recorded for B3: missing type folders are not created by the worker in production
(`p` is not granted). A new category needs the admin bootstrap (Step 4) before uploads to it
succeed; until then the session fails with `destination_unavailable` and the staging copy is
kept until its TTL.

Exact prerequisites for Phase C, all of which must hold at the same time:

1. B3.1 probe exit 0 on the NAS with `--expect-placed-unlink denied` and T8; its report kept.
2. B3.2 applied and verified: 72 entries, legacy 92 unchanged, second probe exit 0.
3. Local code at the approved commit: `pnpm lint`, `pnpm typecheck`, `pnpm build`,
   `uv run pytest` with `TEST_DATABASE_URL` all green in the session that reports it.
4. Compose diff reviewed: api gains only the `.affilio-incoming` bind (rw) and
   `MEDIA_INCOMING_PATH`; worker finals `read_only: false`, `MEDIA_FINALS_WRITABLE=true`,
   `cap_drop: [ALL]`; publisher, exporter, originals and proxies unchanged.
5. `META_LIVE_PUBLISHING_ENABLED=false` confirmed in the NAS `.env`.
6. Database backup taken by the deploy script; the previous two kept.
7. Smoke-asset decision (keep or remove) recorded before the test.
8. Post-deploy checks defined and ready: `/proc/<worker pid>/status` `CapEff` all zeros,
   finals still `ro` in publisher and exporter, worker log `worker.started`, migration
   `0017_upload_chunk_manifest`.

## Phase C — deployment sequence (needs approval; BLOCKED until Phase B3 passes)

1. `pnpm nas:sync` (code only), then `pnpm nas:deploy check`.
2. Backup: the deploy script takes `backups/pre-deploy-<ts>.dump`; keep the previous two.
3. Compose (`infrastructure/docker/synology/docker-compose.yml`, on the NAS via sync):
   - api: add bind `${AFFILIO_FINALS_HOST_PATH}/.affilio-incoming` → `/data/incoming` (rw)
     and env `MEDIA_INCOMING_PATH=/data/incoming`;
   - worker: `/media/finals` `read_only: false`, env `MEDIA_FINALS_WRITABLE=true`, and
     `cap_drop: [ALL]` (the B2B probe ran that way; the compose file currently drops nothing);
   - publisher, exporter, originals, proxies: unchanged (read-only).
4. `.env`: `UPLOAD_*` only if the defaults need changing; `META_LIVE_PUBLISHING_ENABLED=false`
   stays.
5. `pnpm nas:deploy deploy`: builds images, runs migrations 0014–0017 on api start, restarts
   api, worker, publisher, exporter, web. Expected downtime: the usual image swap.
6. Health: `/api/v1/health` and `/system/info`; `alembic_version = 0017_upload_chunk_manifest`;
   `GET /media/uploads/options` answers 200 for an editor; finals still `ro` in publisher and
   exporter (`/proc/<pid>/mountinfo`); worker log shows `worker.started`.
7. Smoke test: one short synthetic clip (ffmpeg, host) uploaded by a mapped editor through
   `/uploads` on the public hostname; expect `processing` → `completed` within one poll
   interval plus hashing; asset in the library with three path assignments, pending review;
   `upload.completed` audit; staging copy removed; `/reports/production` shows it. Then the
   same file again → `duplicate` with no asset and no file. Reviewer rejects the first,
   editor submits a corrected version → revision link visible in the report.
8. Keep or remove the smoke asset: decide before the test (removal is a scan-visible
   deletion by the operator plus a `missing` asset row).

## What happens if placement succeeds but the database transaction fails

The file is already linked under `FINAL/<Owner>/<Category>/<Type>/` and the staging copy
still exists; the session row stays `finalizing` with its intent (`final_relative_path`,
`verified_sha256`). Database rollback does **not** remove the file. Recovery: when the lease
expires (10 min) the next worker claim resumes from the intent, verifies the placed file by
size and SHA-256, registers it (or adopts a scan-registered asset after the same proof),
commits, then removes the staging copy. If the database stays unavailable, a later scan may
index the file first; the finalizer then adopts it rather than creating a second asset. In no
case is the placed file deleted or overwritten by recovery.

## Rollback

| Layer      | Action                                                                                                                                                                                                                       |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Code       | `pnpm nas:deploy rollback` to the previous images (b16e8e2).                                                                                                                                                                 |
| Database   | `alembic downgrade 0013_facet_classification` on the previous image, or restore `pre-deploy-<ts>.dump`. Downgrading drops upload sessions and owner mappings and the three asset attribution columns; assets and files stay. |
| Mounts     | revert the compose changes and `up -d` api and worker; uploads then answer 503 and the finalizer is inert even before the revert takes effect.                                                                               |
| Filesystem | files placed by uploads are ordinary finals: leave them. `FINAL/.affilio-incoming` may stay (hidden, ignored by scans) or be removed after confirming it holds no `.part` files. The ACL grant can be removed independently. |
| Reports    | the report page needs 0015+; after a downgrade it answers with empty uploads but still lists historical finals.                                                                                                              |

## Resume identity after Phase A

`media_uploads.chunk_manifest` (migration 0017) records the SHA-256 of every committed
chunk; the client verifies a reselected file over the whole committed prefix before sending
more bytes. Interrupted sessions created before this change have an empty manifest and are
refused on resume (`manifest_incomplete`); the editor cancels and uploads again.
