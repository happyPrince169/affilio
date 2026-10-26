"""Background worker: claims queued scan jobs and renders pending thumbnails.

The database is the queue (`media_scan_jobs.status = queued`, `media_assets.thumbnail_status =
pending`) and claims use `SELECT ... FOR UPDATE SKIP LOCKED`, so several workers are safe and no
broker is needed. See docs/decisions/0004-database-backed-worker.md.
"""

from affilio.worker.runner import run_forever, run_once

__all__ = ["run_forever", "run_once"]
