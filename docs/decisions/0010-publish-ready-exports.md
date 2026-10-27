# ADR 0010 — Publish-ready exports as provenance-carrying variants of the final asset

- **Status:** Accepted (Export pipeline E1, 2026-10-30)
- **Context:** 86 of the first 92 indexed finals are HEVC 10-bit (`yuv420p10le`), which the
  Reels pre-upload validator refuses because Meta documents 4:2:0 and never confirms 10-bit.
  The DS923+ has no GPU, two cores shared with the ContentFactory proxy worker, a read-only
  finals mount, and 11 TB of disk. Sources must stay immutable, asset ids stable, and nothing
  may transcode on the NAS by accident.
- **Decision:**
  1. **An export is a variant, not an asset.** The rendered file is a `media_file_variants`
     row of kind `export` on the same asset as the final, under the read-write `exports` root,
     with provenance columns: `derived_from_variant_id`, `recipe_key`, and the source's
     `source_file_size` / `source_modified_at` / `source_fingerprint` at render time. At most
     one export per (asset, recipe) exists; a re-export replaces file and row in place.
  2. **Staleness is decided by identity, never by time.** A consumer may use an export only
     while those recorded values match the final variant as currently indexed
     (`exports.export_is_current`). A changed final makes every older export stale.
  3. **The recipe is fixed and versioned in code** (`reels-h264-v1`: H.264 High 4.1, 8-bit
     `yuv420p`, CRF 18 capped at 12 Mbps, fixed 24–60 fps, closed 2 s GOP, no upscaling,
     4K→1080x1920, AAC 160 kbps 48 kHz stereo, `+faststart`). Settings only bound CPU and time
     (threads, nice, preset, CRF within 14–28, timeout). No user-supplied ffmpeg arguments.
  4. **Policy before conversion.** A source that already satisfies the profile is `skipped`
     (no file). A source with HDR / PQ / HLG / BT.2020 metadata is `blocked` for a separate
     tone-mapping decision rather than converted silently.
  5. **The output is validated before it exists.** ffmpeg writes a temp file next to the
     target; ffprobe must confirm the publishing profile (and h264 / yuv420p / 48 kHz /
     matching duration, audio and orientation) before the atomic rename and the row.
  6. **Same reliability model as scans.** `media_export_jobs` is the queue and audit trail:
     SKIP LOCKED claims, attempts with backoff, fenced heartbeats while ffmpeg runs,
     cancellation that kills ffmpeg at the next heartbeat, a watchdog for stale running jobs,
     redacted errors, structured `stats`.
  7. **A dedicated worker** (`affilio.worker.exporter`, compose service `exporter`, opt-in
     via the `exports` profile) runs one ffmpeg at a time with its own memory limit, mounting
     finals read-only and exports read-write; the API, scan worker and publisher never
     transcode.
- **Consequences:**
  - E1 renders nothing on its own: jobs are queued one at a time by the CLI (`--run` refuses
    in production without an explicit flag); no approval or publishing trigger, no backfill.
  - E2 teaches `media_checks` and the engine to prefer a current export and mounts the
    exports root read-only on the publisher; E3 adds triggers and the backfill.
  - Removing the feature is one `downgrade`: export rows go with the provenance columns.
