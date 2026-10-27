# M3B.2A — Durable Reels publishing engine (offline-first)

- **Status:** Implemented 2026-10-29; verified locally (see completion report); awaiting
  acceptance. ADR 0009. Architecture: `docs/architecture/publishing-engine.md`.
- **Depends on:** M3B.1A (connections, vault, mock provider).
- **Not in scope:** real Facebook publishing (`META_LIVE_PUBLISHING_ENABLED` stays false),
  Meta-side scheduling, links-in-first-comment, analytics (M4).

## Delivered

| Area | Files |
| --- | --- |
| Migration 0009 | `publication_uploads`, `publisher_workers`, `social_accounts.publish_hold_*`; working downgrade; parity test |
| Engine | `publishing/engine.py` (dispatch, claim/lease, phase handlers, retry/backoff, holds, reconciliation, abandon), `publishing/eligibility.py`, `publishing/media_checks.py` |
| Service/API | `enqueue_api_attempt`, `start` routes API-eligible jobs to the engine, `reconcile_with_provider`, `engine_status`; routes `GET /publishing/engine/status`, `POST /publishing/publications/{id}/reconcile-provider`; summaries carry `publish_mode`, `api_eligibility_reason`, `upload`, `target_is_mock`, holds |
| Worker | `affilio/worker/publisher.py` (`--once`, `--healthcheck`), entrypoint `publisher`, compose service `affilio-publisher` (finals mount only, 0.5 CPU / 256 M) |
| Providers | Reels calls on the contract; mock faults + video registry; live calls gated |
| UI | Queue: engine card (workers, mode, phase counts, per-Page status/holds/quota), per-row phase/progress/retry/error; detail: API panel with progress, publish-request status, earlier attempts, "Check with provider"; simulated publications labelled, mock permalinks never rendered as links |
| Config | `META_LIVE_PUBLISHING_ENABLED`, `META_REELS_DAILY_QUOTA`, `PUBLISHER_*`, `REELS_*` bounds; both `.env.example` files; Synology compose |
| Tests | `test_publishing_engine.py` (26), `test_media_checks.py` (13); existing suites updated |

## Acceptance check

Select an approved final → create publications for two mock Pages → schedule or publish
now → the publisher drives `validating → uploading → processing → finalizing → verifying →
published` per Page; each has its own caption, snapshot, attempt, upload row and (simulated)
post id. Worker restart mid-upload resumes; an ambiguous finish parks the job until "Check
with provider" confirms it without re-sending. No Facebook post is created (mock only; live
Pages are refused by eligibility).

## Known limitations

- Mock fault scripting is process-local (API vs publisher containers).
- Live Reels calls are untested against Meta; see the activation checklist.
- Residual duplicate risk after a lost finish *and* a wrong manual "not published" decision
  (`publishing-engine.md` §6).
