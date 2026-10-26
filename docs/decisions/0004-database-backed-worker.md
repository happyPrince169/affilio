# ADR 0004 — Separate worker process with a database-backed queue (no Redis)

- **Status:** Accepted (2026-10-26)
- **Milestone:** M1

## Context

Media indexing and thumbnail generation are long-running and CPU/I/O heavy (ffprobe per new
file, ffmpeg per thumbnail, sampled reads over SMB/NFS). Running them inside the API process
would make request latency unpredictable and tie ffmpeg's CPU use to the API's limits. The
deployment target is a NAS with few cores, where per-container resource limits matter.
ADR 0001 allows Redis only when a concrete background-processing need is documented.

## Decision

- Run a **separate worker container** from the same image (`docker-entrypoint.sh worker`,
  `python -m affilio.worker`).
- Use **PostgreSQL as the queue**: `media_scan_jobs.status = queued` and
  `media_assets.thumbnail_status = pending` are claimed with
  `SELECT … FOR UPDATE SKIP LOCKED`, so multiple workers are safe and no broker is required.
- Retries with exponential backoff live on the job row (`attempt`, `max_attempts`,
  `next_attempt_at`); the worker heartbeat file backs the container healthcheck.
- The API only enqueues; it never runs ffprobe/ffmpeg.

## Consequences

- One fewer service to run and back up; the job history is queryable SQL and visible in the UI.
- Poll latency (default 5 s) is acceptable for scans and thumbnails.
- If a future milestone needs sub-second dispatch or fan-out (e.g. many concurrent platform
  API calls), introduce Redis then, keeping the same job tables as the source of truth.
