# M1 — Synology-Native Media Library

- **Status:** Implemented 2026-10-26 (verified locally; not yet deployed to the NAS)
- **Depends on:** M0 accepted; read access to the originals/proxies shares; ffmpeg (bundled in
  the API image; installed locally for development)
- **Unlocks:** M2 (asset linking), M3 (content variants reference assets)

## Scope

Index and manage video files stored on the Synology NAS without touching the existing media or
proxy workflow, and prepare the production deployment on the NAS itself.

## Deliverables

| Area | Delivered |
| --- | --- |
| Data model | `media_roots`, `media_assets`, `media_file_variants`, `media_scan_jobs` (migration `0002_media_library`), organization-scoped |
| Indexing | Incremental scanner: size+mtime short-circuit, sampled fingerprint, selective full hash for duplicates, move/rename detection, missing/rediscovered tracking, Synology service-dir skipping, per-directory permission error reporting, ffprobe metadata, proxy linking by pattern |
| Processing | Worker container (DB-backed queue, `SKIP LOCKED`), retries with backoff, async thumbnails via ffmpeg (proxy preferred, single-threaded, bounded concurrency, atomic writes) |
| API | `/api/v1/media/{roots,stats,assets,assets/{id},assets/{id}/thumbnail,scans,scans/status,scans/{id}}`; `POST /media/scans` behind the admin token |
| Web | Content Library: grid, thumbnails, debounced search, status/orientation/proxy/sort/duplicate filters, pagination, scan card with root availability, scan history, empty/error states, asset detail page; live media tile on the dashboard; same-origin `/api` proxy route |
| Deployment | `apps/api/Dockerfile` (ffmpeg, non-root, tini), `apps/web/Dockerfile` (standalone), `infrastructure/docker/synology/docker-compose.yml` with RO/RW mounts and resource limits, backup/restore scripts, `docs/deployment/synology.md` |
| Tests | 79 backend tests: roots/path safety, probe parsing (rotation, bit depth), fingerprints, incremental scan, moves, duplicates, unsupported media, permission failures, missing root, proxy linking, worker claim/retry, real-ffmpeg thumbnail, API filters/pagination/thumbnail/admin gating/404s/traversal |

## Acceptance criteria

- [x] Scanning a folder tree indexes videos with correct duration/resolution/codec (verified with ffmpeg-generated clips)
- [x] Re-scanning is incremental: unchanged files cause zero reads and zero probes
- [x] Renamed/moved files keep their asset; duplicates are linked after full-hash confirmation
- [x] Missing files are flagged and recovered; unreadable directories are reported, not fatal
- [x] Proxies are linked by configurable pattern and preferred for thumbnails
- [x] Nothing is ever written under originals or proxies (test asserts no `.jpg`/temp files there)
- [x] Scan jobs retry with backoff and surface errors in the UI
- [x] Thumbnails are served by asset id only; relative paths are validated against root escape
- [x] Web UI lists, searches, filters, paginates, shows detail and scan status
- [x] `pnpm check` (lint, typecheck, test, build) passes; Synology compose file validates
- [x] Docker images built locally (`affilio-api:m1` 726 MB with ffmpeg 5.1, `affilio-web:m1` 215 MB) and smoke-tested: imports, uid 1026, pages served, `/api/*` proxied
- [ ] Deployed on a test share on the NAS and scanned real footage (requires approval)

## Known risks / open assumptions

- **Proxy naming convention is assumed**, not confirmed (`MEDIA_PROXY_PATTERNS`). Verify on a
  real original/proxy pair before the first production scan.
- NAS CPU architecture assumed x86_64; ARM64 needs cross-built images.
- No authentication: read endpoints and the UI are LAN-open; mutating endpoints need the admin
  token. Documented in the deployment guide; resolved in M3.
- Very large libraries: the first scan probes every file once (ffprobe over the share); plan
  for hours on tens of thousands of files. Subsequent scans are cheap.
- ffmpeg 4.x (Ubuntu 20.04) locally vs 5.x in the image: thumbnail/probe flags used are
  compatible with both; CI runs the Debian version.

## Verification record (2026-10-26, Ubuntu workstation)

- `pnpm check` (ESLint, tsc, Ruff, pytest with `TEST_DATABASE_URL`, Next build): passed.
- End-to-end on the dev database with ffmpeg-generated demo media (4 clips, 1 duplicate copy,
  1 corrupt file, 2 proxies): first scan `added=6 unsupported=1 duplicates=1 proxies_linked=2`,
  5 thumbnails rendered, zero files written under originals/proxies; second scan
  `unchanged=6`; worker `run_once` claimed and completed a UI-triggered scan.
- Browser (system Chrome via Playwright): grid with thumbnails, status filter, search, empty
  state, scan trigger + toast, detail page with 10-bit proxy notice and variants table, dark
  mode, mobile layout, 404 asset page.
- Same-origin proxy: `GET /api/v1/media/assets/{id}/thumbnail` through the Next server returned
  `image/jpeg`; traversal attempts returned 404.
- **Not done:** deployment on the NAS (needs approval), CI run on GitHub (no remote push yet).
