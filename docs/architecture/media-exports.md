# Publish-ready exports (Export pipeline)

Why: the Reels validator (`publishing/media_checks.py`) accepts only 8-bit 4:2:0 MP4; most
finals arrive as HEVC 10-bit. An export is the publish-ready rendition of a final, produced on
the NAS by a dedicated worker, stored as a **variant of the same asset** with full provenance.
ADR 0010 records the decision; this page is the operating description.

## Data

| Table / columns | Role |
| --- | --- |
| `media_file_variants` kind `export` | the rendered file under the `exports` root (`<aa>/<asset hex>-<recipe>.mp4`), with the usual probe columns |
| `media_file_variants.derived_from_variant_id`, `recipe_key`, `source_fingerprint`, `source_file_size`, `source_modified_at` | provenance: rendered from which final, with which recipe, from which exact bytes. Partial unique index: one export per (asset, recipe) |
| `media_export_jobs` | queue + audit trail: `status` queued → running → ready / skipped / blocked / failed / cancelled, `trigger`, `recipe` (ffmpeg version + placeholder args), source identity at run time, attempts, heartbeat, worker, redacted `error`, `error_code`, `stats` |

Statuses: `ready` (export variant written), `skipped` (source already compliant, no file),
`blocked` (HDR / wide-gamut source, needs a tone-mapping policy), `failed` (permanent, or
attempts exhausted), `cancelled`. Error codes: `source_changed`, `source_missing`,
`source_unreadable`, `source_settling`, `roots_unavailable`, `ffmpeg_missing`,
`encode_failed`, `output_invalid`, `timeout`, `worker_lost`, `cancelled`, `unexpected`.

## Recipe `reels-h264-v1`

Fixed in `media/exports.py`; settings only bound CPU and time.

- Video: libx264 High 4.1, `yuv420p` 8-bit, CRF `EXPORT_CRF` (default 18, 14–28) with a
  `EXPORT_MAX_BITRATE_KBPS` cap, preset `EXPORT_PRESET`, fixed frame rate = source rounded
  and clamped to 24–60, closed GOP of 2 s, `scale=w='min(iw,1080)':h=-2` (never upscale;
  4K → 1080x1920), `+faststart`.
- Audio: AAC 160 kbps, 48 kHz stereo, only when the source has audio.
- Threads `EXPORT_THREADS` (default 2) and `nice` `EXPORT_NICE` (default 10) keep the NAS
  responsive next to the ContentFactory proxy worker.

## Run of one job

1. Verify the source: size and mtime must equal the indexed variant, the file must be past
   `MEDIA_FINAL_SETTLE_SECONDS`, and the quick fingerprint must match. Otherwise
   `source_changed` (rescan, then queue again).
2. Read colour metadata from the stored ffprobe output: BT.2020 / PQ / HLG → `blocked`.
3. Reuse: a present export for the same recipe whose recorded source identity matches and
   whose file exists with the recorded size → `ready` with `stats.reused = true`.
4. Skip: the source satisfies the publishing profile (`media/export_profile.py`) → `skipped`.
5. Encode to `.<name>.<pid>.tmp.mp4` next to the target. While ffmpeg runs the worker writes a
   fenced heartbeat every `EXPORT_HEARTBEAT_SECONDS`; a cancelled, recovered or re-claimed job
   loses the fence, ffmpeg is killed and the temp file removed. Wall-clock budget:
   `max(EXPORT_TIMEOUT_MIN_SECONDS, EXPORT_TIMEOUT_FACTOR × duration)`; a timeout is retried
   with backoff until `max_attempts`.
6. Validate the temp file with ffprobe against the profile plus h264, `yuv420p`, 48 kHz,
   duration, audio presence and orientation of the source; any problem → `output_invalid`.
7. Finalize: fenced update to `ready`, atomic rename, export variant row written or replaced
   in place, `output_variant_id` set, one commit.

Sources are never opened for writing (and the finals mount is read-only). No video is loaded
into application memory: ffmpeg and ffprobe stream from disk.

## Consumers: which file the publisher streams (E2)

`publishing/media_checks.check_reel_media` prefers the export when **all** of these hold:
same asset, `derived_from_variant_id` is the present final, `export_is_current` (recorded
size, mtime and fingerprint equal the final as indexed), recipe in `SUPPORTED_RECIPES`, file
present on the exports root with the recorded size and mtime, and the export's own stream
facts satisfy the profile. Otherwise the final is used only if it passes on its own; otherwise
validation fails with both reasons and names the fix. The upload row pins `file_root_key`,
`file_relative_path`, `file_size`, `file_mtime`, `file_fingerprint` and `file_variant_id`;
before the first byte the publisher re-resolves the path through the root's traversal guard
(only `finals` and `exports` are accepted), re-checks size and mtime, and recomputes the
sampled fingerprint. Any difference is `media_changed`; a new attempt revalidates and may
select a different file.

Queue rows show `media_readiness` (`export_ready`, `final_ready`, `export_stale`,
`needs_export`) from indexed facts only; the final's page shows the export state (Not needed,
Not generated, Queued, Processing, Ready, Failed, Blocked, Stale) with admin actions.

## Crash consistency (E1 review)

The filesystem rename and the database commit are two steps. Every window is covered:

| Crash point | Outcome | Recovery |
| --- | --- | --- |
| while ffmpeg writes the temp file | no row, temp file left | the exporter removes temp files older than the stale window on each loop; the job is re-queued by the watchdog |
| after validation, before the fenced `ready` update | no row, temp file left | same |
| after the fenced update, before `os.replace` (same transaction, not committed) | nothing committed, temp file left | rollback on reconnect, watchdog re-queues, temp cleaned |
| after `os.replace`, before commit | target file exists, no row | the next run finds no current export, re-encodes and replaces the target atomically; nothing ever reads the orphan because consumers go through rows |
| after commit | consistent | — |

A target path is only ever produced by `os.replace`, so a reader never sees a partial file,
and a row is written only after the file was validated and renamed, so a `ready` export is
never partial. Retries are idempotent: a current export is reused, a stale one is replaced in
place, and identity (not time) decides.

## Running it

- Worker: `python -m affilio.worker.exporter` (`--once`, `--healthcheck`); compose service
  `exporter` behind the `exports` profile (`COMPOSE_PROFILES=exports` in the NAS `.env`), memory
  limit `EXPORTER_MEMORY` (default 1536M: 4K HEVC decode needs about 1 GB).
- CLI: `python -m affilio.scripts.media_export queue --asset <id>` queues one job;
  `status --job|--asset`, `cancel --job`. `queue --run` renders synchronously for development
  and is refused in production without `--allow-production`. There is no bulk mode in E1.
- Watchdog: running jobs silent for `EXPORT_STALE_AFTER_SECONDS` are re-queued (or failed).

## Resource expectations (DS923+, software x264)

About real time for 1080x1920 sources at 2 threads, roughly 0.3–0.5× for 2160x3840 (decode
bound). The first 86 finals (19 minutes of video) take 45–90 minutes of wall time and about
1–1.5 GB of exports. Memory: 300–500 MB for 1080p, up to about 1 GB for 4K.

## Rollback

`alembic downgrade 0010_overdue_review` drops `media_export_jobs`, the export variant rows and
the provenance columns (no other table changes). Export files under the `exports` root are
regenerable and can be deleted. The `exporter` service is opt-in via `COMPOSE_PROFILES=exports`
in the NAS `.env`. The deploy script verifies that the built image can run the exporter
before starting anything and health-checks it; `pnpm nas:deploy rollback` checks the previous
image too and, when that image predates the exporter, stops and removes `affilio-exporter` and
brings the stack up with the profile forced off for that run (`.env` is not modified, so the
next deploy of a supporting release starts it again). A deploy without the profile removes a
leftover exporter container; queued jobs and files are never touched by either path.
