# Publishing engine (M3B.2A)

How AFFILIO turns a `READY_TO_PUBLISH` publication on a connected Facebook Page into a Reel
through the provider API, durably and without duplicate posts. Implemented by
`publishing/engine.py`, `publishing/eligibility.py`, `publishing/media_checks.py`, the
`affilio.worker.publisher` process, and the Reels calls of the provider adapters
(`accounts/providers/{base,mock,live}.py`). ADR 0009 records the decisions.

## 1. Where it sits in the job state machine

The ten-state machine of `publishing/lifecycle.py` is unchanged. API publishing lives inside
`PUBLISHING`; the engine reports outcomes through the same transitions assisted publishing
uses:

```
SCHEDULED ──release_due──► READY_TO_PUBLISH ──dispatch/start (API-eligible)──► PUBLISHING
                                 │ (assisted targets wait for the operator)         │
                                 ▼                                                  ▼
                          operator checklist                       publication_uploads row
                                                                  ┌──────────────────────┐
  pending → validating → session_starting → uploading ⇄ retry_wait │ one live row per job │
      → upload_complete → processing → finalizing → verifying      └──────────────────────┘
                                             │            │
                 failed (permanent) ◄────────┤            ├──────► published  → job PUBLISHED
                                             │            │                     (api_verified)
                 needs_reconciliation ◄──────┴────────────┘       → job NEEDS_RECONCILIATION
                      │ admin "Check with provider" (never re-sends the publish request)
                      ├─ provider says published → PUBLISHED
                      ├─ provider says not published / error / missing → FAILED (explicit retry)
                      └─ still unknown → stays parked
  abandoned: the job left PUBLISHING by another path while an upload was live.
```

| Brief term | Job status | Upload phase |
| --- | --- | --- |
| Waiting for schedule | `SCHEDULED` | — |
| Ready to publish | `READY_TO_PUBLISH` | — |
| Upload initialization | `PUBLISHING` | `pending` → `validating` → `session_starting` |
| Upload in progress | `PUBLISHING` | `uploading` |
| Upload completed | `PUBLISHING` | `upload_complete` |
| Processing | `PUBLISHING` | `processing` |
| Finalization requested | `PUBLISHING` | `finalizing` (`publish_request_status = sent`) then `verifying` |
| Published | `PUBLISHED` | `published` |
| Retryable failure | `PUBLISHING` | `retry_wait` (`next_action_at` = backoff, `retry_count`) |
| Permanent failure | `FAILED` | `failed` |
| Needs reconciliation | `NEEDS_RECONCILIATION` | `needs_reconciliation` |

Every phase change writes `publication.upload_phase` to `audit_events` (system actor from
the worker, the admin for reconciliation) with the from/to phase, retry count, provider
video id and error code. Job transitions are audited as before (`publication.start`,
`publication.confirm`, `publication.fail`, `publication.report_uncertain`,
`publication.reconcile`, `publication.release_held`).

## 2. Durable upload record (`publication_uploads`)

One row per API attempt, linked 1:1 to `publication_attempts` (`mode = api`). Columns cover
the brief's list: job, Page (`social_account_id`), media asset, provider, upload session
(`provider_session_id` = upload URL), provider video id, bytes sent/total, processing status
snapshot, publish request status (`not_sent → sent → acknowledged → confirmed | rejected |
unknown`), external post id/url, last error (+code, transient flag), retry count/max, lease
owner/expiry, `next_action_at`, started/finished/published timestamps.

Invariants:

- `uq_publication_uploads_job_live`: at most one non-terminal upload per job.
- `uq_publication_uploads_attempt_id`: one upload per attempt.
- `uq_publication_jobs_account_external_post` (M3A): one job per provider post per Page.
- Provider identifiers are committed before the next call that depends on them; the finish
  request is committed as `sent` before it is made.

## 3. Phase handlers (one provider call kind per step)

| Phase | What the step does | Transient error | Permanent error |
| --- | --- | --- | --- |
| `pending`/`validating` | `check_reel_media`: approved final, file present, size/mtime unchanged, settled, readable, container/codec/pixel format/duration/resolution/orientation/frame rate/size within the documented bounds; pins path, size, mtime | — | `media_incompatible` |
| `session_starting` | `start_reel_upload` → store `provider_video_id`, `provider_session_id` | retry | permission/provider code |
| `uploading` | stream chunks (`PUBLISHER_UPLOAD_CHUNK_BYTES`) from the finals mount at `bytes_sent`; commit after every chunk; renews the lease; stops at the step time budget and continues on the next claim | re-sync `bytes_sent` from `reel_status.bytes_transferred`, retry | `media_changed`, provider rejects |
| `upload_complete`/`processing` | `reel_status`; `ready` → `finalizing`; `processing` → poll again after `PUBLISHER_STATUS_POLL_SECONDS`; `uploading` → back to `uploading` with the provider's offset | retry | `processing_failed`, `processing_timeout` |
| `finalizing` | if `publish_request_status != not_sent` → ambiguous (a finish was already sent); else commit `sent` + timestamp, call `finish_reel` once; success → `acknowledged`, `verifying` | **never retried**: timeout/unknown → `needs_reconciliation` | auth rejected → `token_invalid`; provider code → `finish_rejected_*` |
| `verifying` | `reel_status` until `publish_status = published`, then `reel_identity` (post id, permalink) → `published`; `error` → `publish_failed`; timeout → `needs_reconciliation` | retry | as listed |
| `retry_wait` | resumes the phase stored in `last_status.resume_phase` when `next_action_at` arrives | — | — |

Error classification (`engine.classify`): `TokenInvalidError` → permanent `token_invalid`
(+ the Page is marked disconnected); `RateLimitedError` → transient `rate_limited` with a
durable per-Page hold (`social_accounts.publish_hold_until/reason`); other
`ProviderUnavailableError`/transient codes → retry; permission codes (10, 200–299, 368) and
unknown provider codes → permanent; vault errors → permanent. Backoff:
`min(base·2^(n−1), max) + jitter`, bounded by `max_retries` → `retries_exhausted`.

## 4. Worker (`affilio.worker.publisher`)

- Separate process/container (`publisher`); the scan/thumbnail worker is unchanged.
- Loop: heartbeat (DB row in `publisher_workers` + file) → `release_due_everywhere` (UTC) →
  `abandon_orphans` → `dispatch_ready` → `claim_uploads(limit = free pool slots)` → run each
  step in a bounded `ThreadPoolExecutor` (`PUBLISHER_MAX_CONCURRENT_UPLOADS`) → wait on the
  stop event (1 s while busy, `PUBLISHER_POLL_INTERVAL_SECONDS` otherwise). No busy loop, no
  in-memory queue: the only process state is the set of in-flight futures.
- Claiming: `SELECT … FOR UPDATE SKIP LOCKED` on due rows whose lease is absent or expired,
  excluding Pages on hold and Pages with another leased upload (per-Page serialization), one
  row per Page per batch. Leases last `PUBLISHER_LEASE_SECONDS` and are renewed per chunk.
- Recovery: an expired lease is reclaimable; the step resumes from the persisted phase. A
  claim that finds `finalizing` with `publish_request_status = sent` goes straight to
  reconciliation. `last_status.recovered_from` records the dead worker.
- Dispatch re-checks everything at dispatch time: approved snapshot current, routing verdict,
  classification unchanged, account connected/can publish, mock/live mode match, Page hold,
  daily quota (`META_REELS_DAILY_QUOTA`, documented 30 per 24 h; exceeding it sets a
  `reels_quota` hold until the oldest publication ages out).
- Graceful shutdown: SIGTERM stops claiming, drains in-flight steps (each bounded by the step
  time budget), marks the worker row stopped. Restarting with the same or a new worker id
  continues from the database (`test_restart_safety_*`).
- Synology sizing: `PUBLISHER_CPUS=0.5`, `PUBLISHER_MEMORY=256M`, concurrency 1, 4 MiB chunks
  streamed from the read-only finals mount; no ffmpeg, no transcoding, no copies.

## 5. Scheduling

`scheduled_at` is UTC; the UI displays it in the organization timezone. The worker releases
due jobs with the existing `release_due` (which already holds blocked or reclassified jobs
and audits `publication.release_held`), then dispatches API-eligible ones. Jobs without a
current approved snapshot, revoked or cancelled jobs, disconnected Pages, mock Pages in live
mode (and vice versa), blocked targets and held Pages are never dispatched; assisted targets
stay in `READY_TO_PUBLISH` for the operator. A job that leaves `PUBLISHING` while an upload is
live is abandoned on the next worker pass and its publish request is never sent.

### 5.1 Missed schedules (restart / downtime policy)

`release_due` compares each due job's lateness with `PUBLISHER_OVERDUE_GRACE_MINUTES`
(default 120). Within the grace window the job is released after the usual revalidation and
the lateness is recorded on the `publication.release` audit event. Beyond it:

- policy `review` (default): the job stays `SCHEDULED`, `overdue_review_at` is set once and
  `publication.release_held` (`reason = overdue_beyond_grace`, lateness, grace) is audited
  once; the queue shows "overdue · operator review" and the detail page offers **Release
  now** (`POST /publishing/publications/{id}/release`, editor), which re-runs the eligibility
  checks and audits `publication.release` with `overdue_override = true`. Rescheduling or
  unscheduling clears the flag.
- policy `publish`: the job is released like any due job and the release is audited with
  `reason = overdue_published_by_policy`.

The policy runs on every publisher pass, so a restarted or long-stopped stack applies it to
everything that accumulated while it was down (`test_overdue_policy.py`).

## 6. Exactly-once? No — at-least-once with duplicate guards

Execution is at-least-once: a step can run twice if a worker dies after the provider call and
before the commit. What prevents duplicate *posts*:

| Hazard | Guard |
| --- | --- |
| Two workers advance one upload | row lock + lease; a lost lease ends the step without side effects |
| Second upload for one job | partial unique index `uq_publication_uploads_job_live` |
| Finish sent twice | `publish_request_status` committed as `sent` before the call; `finalizing` + `sent` on re-entry → reconciliation, never a resend; retries never return to `finalizing` |
| Retry after an unknown finish | parked; only a provider status check resolves it; "not applied" fails the attempt and an explicit retry starts a **new** upload (new video id) |
| Same post claimed by two jobs | `uq_publication_jobs_account_external_post` → `duplicate_post_identity` → reconciliation |
| Start/upload repeated after a crash | harmless: an unfinished upload session never publishes |

**Residual risk (documented, not solved):** Meta documents no idempotency key for
`video_reels` (feasibility B12). If the finish request reaches Meta and the response is lost
*and* the later status check also fails to reflect the publication (for example the video
is deleted on the Page before reconciliation), an admin could mark the attempt
`not_published` and retry, producing a second post. The engine makes this require a human
decision; it cannot make it impossible.

## 7. Mock provider behaviour

`MockMetaProvider` keeps a per-process video registry and a per-Page fault queue
(`script_fault(page_id, fault)`, exposed as mock actions `script_fault`/`clear_faults`).
Faults are consumed in order by the first matching call: `start_timeout`, `start_rate_limit`,
`start_permission_denied`, `upload_timeout` (half the chunk lands, resume by offset),
`upload_reject`, `status_outage`, `processing_delay`, `processing_error`,
`finish_timeout_ambiguous` (applied, response lost), `finish_timeout_lost` (not applied),
`finish_reject`, `identity_missing`; the global outage switch and token revocation from
M3B.1A also apply. Mock publications carry `external_post_url = mock://facebook/reel/<id>`
and every row is `is_mock = true`; the UI labels them "Simulated (mock)". The registry is
process-local: the publisher container and the API container do not share scripted faults
(sufficient for development; tests run both in one process).

## 8. Live provider status

`LiveMetaProvider` implements the documented calls (start, rupload with `offset`/`file_size`,
status, finish with `video_state=PUBLISHED`, identity) but `eligibility.py` refuses real Pages
while `META_LIVE_PUBLISHING_ENABLED=false`, and the compose file keeps it false. Activation:
`docs/integrations/meta-live-publishing-activation.md`.
