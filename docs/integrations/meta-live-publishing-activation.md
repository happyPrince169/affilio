# Live Reels publishing activation checklist (M3B.2A → live)

Prerequisite: `meta-app-activation-checklist.md` completed (live connections work, a test Page
is connected with `permission_status = ok`). Everything below is configuration plus a
supervised first run; no code or schema changes.

## Before enabling

- [ ] `docs/integrations/facebook-reels-feasibility.md` unknowns #1–#3 (post id/permalink
      fields, upload session lifetime, finish semantics) have answers from a Graph API Explorer
      session with the real app; record them in `meta-live-test-notes.md`.
- [ ] The publisher container runs healthy in mock mode on the NAS (`docker compose ps`,
      engine card shows a worker) and the finals mount is readable from it.
- [ ] At least one approved final passes `check_reel_media` (publication detail shows no
      media errors) and is short (≤ 15 s) for the first live attempt.
- [ ] Backups taken (`db-backup.sh`) and `.env` (vault key) stored out of band.

## Enable

- [ ] `.env`: `META_LIVE_PUBLISHING_ENABLED=true`, `PUBLISHER_MAX_CONCURRENT_UPLOADS=1`,
      `META_REELS_DAILY_QUOTA=30` (documented cap), `PUBLISHER_MAX_RETRIES=3` for the first day.
- [ ] `docker compose up -d api publisher`; `/api/v1/system/info` → `meta.mode = live`;
      engine card → "live publishing" without the "off" badge.

## First supervised publication (one job, one Page)

- [ ] Create one publication on the test Page, approve it, **Publish now via API**.
- [ ] Watch phases: `validating → session_starting → uploading (progress) → upload_complete →
      processing → finalizing → verifying → published`. Note durations.
- [ ] Verify on Facebook that exactly one Reel appeared with the approved caption.
- [ ] Publication detail shows `evidence_kind = api_verified`, a real `external_post_url`
      (`https://www.facebook.com/...`), no "Simulated" badge.
- [ ] `SELECT publish_request_status, finish_response FROM publication_uploads` → `confirmed`;
      record what `finish_response` and `reel_identity` returned (unknown #1/#2).
- [ ] Engine card shows `published_last_24h = 1` for the Page.

## Failure drills (test Page only)

- [ ] Stop the publisher mid-upload (`docker compose stop publisher`), wait past the lease,
      start it again: the upload resumes from the provider offset; one Reel results.
- [ ] Disconnect the Page from Facebook during processing: attempt fails `token_invalid`,
      Page shows "reconnect needed", no post.
- [ ] If an attempt lands in **Needs reconciliation**: use "Check with provider" first; only
      choose "not published" after confirming on the Page that no Reel exists (residual
      duplicate risk, `publishing-engine.md` §6).

## Rollback

`META_LIVE_PUBLISHING_ENABLED=false` + restart `api` and `publisher`: real Pages become
"live publishing disabled" (assisted), in-flight uploads finish their current step and then
fail `live_publishing_disabled` on the next claim; nothing is re-sent.
