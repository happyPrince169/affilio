# M1 — Media Library

- **Status:** Planned
- **Depends on:** M0 accepted; read access from the API host to the Synology NAS share;
  FFmpeg on the API host
- **Unlocks:** M2 (asset linking), M3 (content variants reference assets)

## Scope

Index short-form vertical video (and supporting images) stored on the Synology NAS so that
operators can browse, search, and tag assets per organization, and so later milestones can
reference them. Originals stay on the NAS; AFFILIO stores metadata and thumbnails.

## Deliverables

- `media` module: `media_assets` (path, checksum, size, mime, duration, width/height, codec,
  thumbnail path, `organization_id`), `media_tags`, `media_asset_tags`.
- NAS adapter reading a configured mount/SMB path (`NAS_MEDIA_ROOT`); a scan command that
  upserts assets by checksum and marks missing files.
- FFmpeg/ffprobe wrapper for technical metadata and thumbnail extraction.
- API: list/search/filter assets, get asset, update tags, trigger scan, scan status.
- Web: Content Library page with grid/list views, filters, asset detail drawer, scan button.
- Tests: adapter against a temp directory fixture, ffprobe parsing, API contract.

## Acceptance criteria

- A scan of a sample NAS folder populates assets with correct duration/resolution.
- Re-scanning is idempotent; renamed files are detected by checksum.
- The UI lists, searches, and tags assets; thumbnails display.
- No original media is copied into the repository or database.

## Known risks

- NAS access from the dev machine vs. a server differs (mount paths, permissions).
- Large libraries make synchronous scanning slow → first concrete case for a background
  worker; decide Redis/worker here, not before.
- FFmpeg availability and version differences across environments.
