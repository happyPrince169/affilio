# Export pipeline E1 — Foundation (publish-ready exports)

- **Status:** Implemented 2026-10-30; verified locally (full backend suite, real ffmpeg
  fixtures); not deployed. ADR 0010. Architecture: `docs/architecture/media-exports.md`.
- **Depends on:** M3C.1 steps 1–3 (job fence, watchdog, redaction), M2B finals root.
- **Not in scope (E2/E3):** publisher using the export, exports mount on the publisher,
  approval and publication triggers, backfill of the existing finals, UI.

## Delivered

| Area | Files |
| --- | --- |
| Migration 0011 | `media_export_jobs`; provenance columns + partial unique index on `media_file_variants`; working downgrade (parity test) |
| Profile | `media/export_profile.py`: publishing profile from ffprobe output, HDR / wide-gamut detection, audio helpers |
| Generator | `media/exports.py`: fixed recipe `reels-h264-v1`, queue service (enqueue / claim / cancel / watchdog), `ExportGenerator` (source identity, reuse, skip, blocked, fenced heartbeats, temp + atomic finalize, output validation, redacted errors), `export_is_current` for consumers |
| Worker | `worker/exporter.py` (`--once`, `--healthcheck`), entrypoint verb `exporter`, compose service `exporter` behind the `exports` profile with `EXPORTER_MEMORY`, `EXPORT_*` settings with bounds |
| CLI | `scripts/media_export.py` queue / status / cancel for one final; `--run` refused in production without `--allow-production` |
| Tests | `tests/test_export_profile.py` (profile, HDR, recipe, bounds), `tests/test_media_exports.py` (real ffmpeg: 10-bit HEVC → H.264 ready, reuse and staleness, compliant skip, HDR blocked, eligibility, cancellation kills ffmpeg, timeout retry → fail, recovered zombie cannot finalize, watchdog exhaustion, redacted ffmpeg error, CLI incl. production guard) |
| Docs | ADR 0010, `media-exports.md`, database.md 0011, README, Synology guide |

## Acceptance criteria

- [x] Sources never modified (test snapshots the finals root before/after); no new asset rows.
- [x] Export variant linked to its source variant with recipe and source identity.
- [x] Output validated by ffprobe before the row exists; invalid output never leaves a file.
- [x] Changed source → `source_changed`; older export reported stale by `export_is_current`.
- [x] Compliant source → `skipped`, no file. HDR/PQ/HLG → `blocked`, no file.
- [x] No user-controlled ffmpeg arguments; settings bounded by validator.
- [x] Streaming only: ffmpeg/ffprobe read from disk; nothing loads a video into memory.
- [x] Cancellation, watchdog, retries with backoff, fenced finalization, redacted errors.
- [x] No automatic triggers, no bulk mode, no deployment, live publishing untouched.

## Known limits

- The HDR decision is binary (block); tone mapping is a separate policy.
- `output_invalid` and `encode_failed` are not retried (a second run would produce the same
  result); operators re-queue after fixing the source.
- Export files of superseded recipes or deleted assets are not garbage-collected yet.
