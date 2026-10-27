# M3C.1 — Media foundation (make the NAS library visible and keep it that way)

- **Status:** In progress. Step 1 implemented 2026-10-27 (verified locally; not deployed).
  Steps 2–5 approved in principle, each started only on explicit approval.
- **Depends on:** M1 (scanner, roots, worker), M2B (finals root), M3A (auth/RBAC).
- **Not in scope:** `media_asset_relations` (deferred until `media_file_variants` +
  `production_job_media` are shown insufficient), MeoScout sync (M3C.2), analytics (M3C.3),
  scanning other ContentFactory channels (one root per kind per deployment for now).

## Why

Production reports zero media assets because no scan was ever enqueued: scans are manual,
admin-only, and the first admin user was created on 2026-10-27. Before the first 72 GB scan
runs on the NAS the scanner must be able to survive a worker crash and be stoppable.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 1 | Scan watchdog + safe cancellation (this document) | implemented, tested locally |
| 2 | Finals scanning from the UI and the CLI (`--root`) | approved, not started |
| 3 | Path-safe error strings (no container paths in stored errors) | approved, not started |
| 4 | Identity across scans: missing file reappearing elsewhere keeps its asset | approved, not started |
| 5 | Scheduled incremental rescan (`MEDIA_SCAN_INTERVAL_MINUTES`, default off) | approved, not started |

## Step 1 — Scan watchdog and safe cancellation

**Problem.** `media_scan_jobs.heartbeat_at` was written but never read: a worker crash left
the job `running` forever, which blocked every later scan of that root (`enqueue_scan`
refuses while a queued/running job exists). There was no way to stop a scan.

**Design (no migration).**

- *Heartbeat.* A running scan commits progress and refreshes `heartbeat_at` every
  `MEDIA_SCAN_COMMIT_EVERY` units of work **or** every `MEDIA_SCAN_HEARTBEAT_SECONDS`
  (default 30), whichever comes first, including during the slow loops (full hashes for
  duplicate candidates, proxy probes).
- *Watchdog.* Before each claim the worker runs `recover_stale_scan_jobs`: running jobs whose
  last sign of life (`heartbeat_at`, else `started_at`, else `created_at`) is older than
  `MEDIA_SCAN_STALE_AFTER_SECONDS` (default 600) are re-queued with the normal backoff while
  attempts remain, otherwise marked `failed`. Rows are taken `FOR UPDATE SKIP LOCKED` with the
  status predicate, so concurrent watchdogs never recover the same job twice and a second pass
  is a no-op. Settings validation requires stale ≥ 3 × heartbeat.
- *Job fence.* Every write a scan run makes to its job row (checkpoints and the final
  succeeded/failed outcome) is conditioned on the row still holding the status, worker id and
  attempt captured when the run started (`scanner.JobFence`). Cancellation, recovery and a
  re-claim all change one of them, so a stalled-but-alive worker that wakes up gets
  `rowcount == 0`, rolls back everything it had not committed and stops. Already-committed
  incremental progress is kept: it is idempotent and the next scan continues from it.
- *Cancellation.* `POST /api/v1/media/scans/{id}/cancel` (admin role or the service token):
  queued → cancelled immediately; running → cancelled immediately, the worker notices at its
  next fenced checkpoint (≤ heartbeat interval or one long file operation). Finished jobs
  answer 409 `scan_not_cancellable`. An audit event `scan.cancelled` is recorded. The scan
  card offers "Cancel current scan".

**Files.** `core/config.py`, `modules/media/{errors,scanner,service,router}.py`,
`api/deps.py` (service-token allow-list), `worker/runner.py`, web `lib/api/media.ts` and
`components/media/scan-status-card.tsx`, both `.env.example` files, Synology compose,
`docs/api/README.md`. Tests: `tests/test_media_watchdog.py` (recovery, retries, lock skipping,
zombie fence, cancellation paths, API lifecycle, RBAC, service token) and `test_config.py`.

**Partial-commit consistency (follow-up review, 2026-10-27).** Checkpoints commit whole
units of work (one file: asset + variant + fingerprint + probe; one hash; one proxy link), so
every committed state is one the next scan can continue from: unchanged files are skipped by
size + mtime, files that were in flight when the run was fenced out are rolled back and
re-indexed as new, duplicate detection re-examines every present variant of the root, and
`_link_proxies` now also covers assets of the root that have no present proxy yet (not only
assets touched in this scan). That last point also fixes a pre-existing gap: proxies rendered
by the ContentFactory worker minutes after the original landed were never linked until a
full rescan. The walk heartbeats per directory so deep trees with few videos cannot look
dead. Progress counters in `stats` reflect the last checkpoint; a resumed attempt starts its
own counters. `media_roots.last_scan_at` is only written by a successful run.

**Test isolation.** `tests/conftest.py` disables pydantic's `.env` sources for the test
process (`Settings.model_config["env_file"] = None`): every value a test needs is pinned
explicitly, so real credentials in a developer `.env` can no longer leak into assertions.

**Known limits.** A cancelled running job's worker keeps reading files until its next
checkpoint (bounded by the heartbeat interval plus one file operation); with the single
production worker a new scan of the same root therefore starts only after the abort. The
watchdog cannot distinguish a dead worker from one blocked for longer than the stale window on
a single file (ffprobe is capped at 60 s, full hashing of a multi-GB original on the NAS is
well under 10 minutes); if that ever happens the job is simply retried once the worker is
free, and the fence prevents double writes.

## Acceptance criteria (step 1)

- [x] A running job whose heartbeat is older than the stale window is re-queued (attempt kept,
      backoff applied) or failed when attempts are spent; fresh jobs are never touched.
- [x] Recovery is idempotent and skips rows locked by another watchdog.
- [x] A worker whose job was recovered or cancelled cannot commit results afterwards (checked
      at checkpoints and at the final write, on both the success and the failure path).
- [x] Admin cancel for queued and running jobs; 409 once finished; editors get 403; the
      service token may cancel; the root is free for a new scan immediately after.
- [x] Existing scan behaviour and API responses unchanged (existing media suites pass).
