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

## Phase B — probe (needs approval)

1. Create `FINAL/.affilio-incoming` (owner 1026, group users, mode 0770) and grant the ACL
   below. Nothing else changes.
2. Run `scripts/nas-upload-probe.sh --staging /volume1/ContentFactory/fashion/FINAL/.affilio-incoming`
   as the service identity. First from the host with `sudo -u hoangtuhanhphuc`, which still
   carries the administrators group, then the meaningful run **inside a container** once the
   api mount exists: `sudo docker exec affilio-api bash /app/scripts/nas-upload-probe.sh …`
   (the worker container for the owner-dir step). Dry run first, then `--apply`.
3. Optional step 5 with `--owner-dir "<FINAL>/Hang/Mens-Fashion/Review"` links one
   dot-prefixed probe file into a real folder and removes it (scanner ignores dot files).
   Approve separately.
4. Failure handling: the script cleans up on exit; if a step fails, the only possible
   leftovers are `probe-<ts>/` under staging or `.affilio-probe-<ts>.tmp` in the owner
   folder, both listed by the script, both safe to delete. A failed step means the ACL or
   filesystem assumption is wrong: stop, do not deploy.

## ACL grant (NAS admin, DSM File Station → Permissions or `synoacltool`)

| Path                      | Entry for `hoangtuhanhphuc` (uid 1026)                                                                                                 |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `FINAL/.affilio-incoming` | allow read, write, execute, append, delete, delete subfolders/files, read/write attributes, inherit to files and folders               |
| `FINAL` (tree)            | allow read, write (create files), execute, append, read/write attributes, read permissions; **no delete**; inherit to **folders only** |

Verify with `synoacltool -get` on FINAL, an owner folder, a category folder and a legacy file:
the legacy files must show no new write entry.

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
