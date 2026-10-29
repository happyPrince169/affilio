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

## Phase B2B — finalization probe on ONE destination directory (prepared, not executed)

Scope: `FINAL/Hang/Mens-Fashion/Try-On` only. No entry is added to FINAL, owner roots,
category folders, other type folders or T9 folders; the service user's existing read and
traverse entries cover the ancestors, and inside the container the bind mount starts at
FINAL anyway. Scripts: `scripts/nas-b2b-host.sh` (host side, administrator identity) and
`scripts/nas_finalize_probe.py` (container side, service identity).

ACL entry under test, Synology syntax, create-file only, this folder only (no inheritance):

```
user:hoangtuhanhphuc:allow:-w-----------:----
```

`w` is "write data / create file" on a directory; `p` (create subfolder), `d`, `D`, `A`,
`C` and `o` are deliberately absent. The worker needs `p` only to create a missing folder,
which the single-directory probe does not exercise.

Why the staging ACL matters and why it is not changed yet: a hard link shares the inode and
the inode carries the ACL, so a placed final keeps the staging file's ACL. Today staging
files inherit the service user's full entry including delete, so the service identity could
unlink a placed name from the type folder through the file's own `d` right. The probe's T6
measures exactly that with the current ACL (`--expect-placed-unlink allowed`). The intended
fix, applied only after this measurement and a second approval, splits the staging ACL into a
directory entry (`rwxpdDaARWc--:d---`, so the API can create and unlink staging names) and a
file entry without delete (`rw----aARWc--:fi--`), plus the four human entries so uploaded
finals are visible to editors like legacy files. A second probe run then expects
`--expect-placed-unlink denied`.

Immutability of a placed final for the service user: not achievable without more privilege.
The service user needs `w` on staging files to write chunks, the placed name is the same
inode, Synology ACLs cannot be edited from the worker (no `C` right, no `synoacltool` in
the image) and `chattr +i` needs CAP_LINUX_IMMUTABLE. The guarantee is therefore: no
delete right on placed files once the staging split is in, no write or delete right ever on
legacy files, and code that never opens a placed file for writing (tests assert this).

Commands, in order; every step is reviewed before the next. The host script prints
`PASS <step>` or `BLOCKED`/`FAIL` and exits non-zero on anything unexpected; it never deletes
an ACL entry it cannot identify uniquely and prints manual recovery instructions instead.

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
  --expect-placed-unlink allowed --apply
echo "probe exit=$?"
```

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

What B2B does and does not establish: a PASS proves the service identity can place a file by
hard link into one folder with a create-only entry, cannot overwrite an existing name, and
cannot modify, link, rename or delete a file carrying the legacy ACL. It does **not**
establish production readiness. With the current staging ACL the placed file still carries a
delete right for the service identity (T6 measures this). The staging ACL split and a second
probe expecting `--expect-placed-unlink denied`, the ACL entries on the other 75 folders, and
the compose and environment changes are separate, separately approved steps before any
deployment.

## Phase C — deployment sequence (needs approval)

1. `pnpm nas:sync` (code only), then `pnpm nas:deploy check`.
2. Backup: the deploy script takes `backups/pre-deploy-<ts>.dump`; keep the previous two.
3. Compose (`infrastructure/docker/synology/docker-compose.yml`, on the NAS via sync):
   - api: add bind `${AFFILIO_FINALS_HOST_PATH}/.affilio-incoming` → `/data/incoming` (rw)
     and env `MEDIA_INCOMING_PATH=/data/incoming`;
   - worker: `/media/finals` `read_only: false` and env `MEDIA_FINALS_WRITABLE=true`;
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
