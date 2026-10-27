# M3B — Facebook Page Reels through the Graph API: plan

- **Status:** M3B.0 (feasibility and architecture) complete on 2026-10-26; M3B.1 not started.
- **Depends on:** M3A accepted; M3A.1 accepted; pre-M3B hardening (commit `6378300`) merged.
- **Reads first:** `docs/integrations/facebook-reels-feasibility.md`,
  `docs/integrations/facebook-reels-architecture.md`, `docs/integrations/meta-app-setup.md`,
  `docs/milestones/M3B-meta-api-prerequisites.md` (superseded by this plan where they differ).

## 1. Goal

Publish an approved final video as a Reel on one or more connected Facebook Pages through the
official Graph API, each publication with its own caption, affiliate links, schedule, approval
snapshot, publication record and (M3C) metrics — without ever publishing twice, exposing a
token, or transcoding on the NAS.

## 2. Sub-milestones

| Sub-milestone | Scope | Exit criteria |
| --- | --- | --- |
| **M3B.0** (this) | Research, architecture, setup guide | Four documents in `docs/integrations` and `docs/milestones`; unknowns listed |
| **M3B.1** | OAuth connect/disconnect, encrypted credential store, publisher worker, Reels adapter (`PUBLISHED` only, AFFILIO-side scheduling), reconciliation, UI | A Reel is published to a test Page from AFFILIO with `api_verified` evidence; all crash/retry scenarios in §6 pass under test; security checks in §7 marked "dev" are done |
| **M3B.2** | Meta-side scheduling (`video_state=SCHEDULED`), links-in-first-comment (`pages_manage_engagement`), quota dashboard, Advanced Access decision | Scheduled Reels appear at the scheduled minute; cancel path confirmed |
| **M3C** | Reels insights import (`read_insights`), per-publication metrics | Lifetime metrics visible per publication; deprecations handled |

Only M3B.1 is specified in detail below. Do not start M3B.2 inside M3B.1.

## 3. Official API capability summary (from the feasibility report)

- Supported and verified: Business app with Standard Access for role-holders (no App
  Review); authorization-code login with `state`, HTTPS + Strict Mode redirect URIs,
  `appsecret_proof`; long-lived user token (~60 days) → non-expiring Page tokens;
  `/debug_token`; three-phase `video_reels` publish with resumable binary upload; status
  polling with `uploading/processing/publishing` phases; scheduling 10 min–29 days; lifetime
  Reels insights; rate-limit headers; 30 API-published Reels per 24 h.
- Not available: idempotency keys; Reels via the legacy `/videos` chunked flow; Reel-specific
  webhooks; `file_url` upload from a LAN-only NAS.
- Unconfirmed (need a real app): post id/permalink retrieval for a Reel, upload-session
  lifetime, finish synchronicity, `localhost` HTTPS exemption, caption limits, cancel of a
  Meta-scheduled Reel, branded-content labelling, HEVC 10-bit acceptance.

## 4. Permissions and approval

`pages_show_list`, `pages_read_engagement`, `pages_manage_posts` under **Standard Access**:
every person who connects a Page must be an Admin/Developer/Tester on the Meta app and hold
`CREATE_CONTENT` on the Page. No App Review, no Business Verification. `read_insights` is
M3C. The setup steps are in `meta-app-setup.md`.

## 5. M3B.1 implementation outline

### 5.1 Configuration (`core/config.py`)

`meta_app_id: str | None`, `meta_app_secret: SecretStr | None`, `meta_graph_api_version`
(default `v26.0`, validated `^v\d+\.\d+$`), `meta_oauth_redirect_uri: str | None` (must be
`https://` outside development), `meta_oauth_scopes` (list, default the three above),
`meta_login_config_id: str | None`, `credential_encryption_keys` (parsed `{key_id: 32 bytes}`,
`SecretStr`), `credential_encryption_active_key_id`, `publisher_poll_interval_seconds` (10),
`publisher_max_concurrent_uploads` (1), `publisher_upload_chunk_bytes` (4 MiB),
`publisher_status_poll_seconds` (15 → backoff to 60), `publisher_publish_timeout_minutes`
(30), `meta_reels_daily_quota` (30), `meta_links_in_caption_default` (false).
`is_meta_configured` property; `/system/info` exposes `integrations.meta.configured` and the
version only. `.env.example` and the Synology `.env.example` gain placeholders.

### 5.2 Migration `0008_meta_credentials_and_uploads`

Tables `oauth_states`, `platform_connections`, `account_credentials`, `publication_uploads`;
columns on `social_accounts` (`connection_id`, `connected_at`, `last_verified_at`,
`disconnect_reason`, `page_tasks`). Enums `CredentialKind`, `CredentialStatus`,
`ConnectionStatus`, `UploadPhase`, `OAuthStateResult` (VARCHAR + CHECK). Working `downgrade`.
`tests/test_migrations.py` parity. Details: architecture §5.

### 5.3 accounts module

- `accounts/crypto.py`: AES-256-GCM encrypt/decrypt with key ring, AAD binding, key id.
- `accounts/credentials.py`: `store_token`, `use_token` (context manager), `invalidate`,
  `list_for_account` (metadata only).
- `accounts/meta_client.py`: thin `httpx` client — `exchange_code`, `exchange_long_lived`,
  `debug_token`, `list_pages`, `get_page`, `revoke_app` — all with `appsecret_proof`, pinned
  version, redaction, `fbtrace_id` capture, typed `MetaApiError(code, subcode, is_transient,
  http_status, message)`.
- `accounts/oauth.py` + router: `POST /accounts/connect/facebook` (admin) →
  `{authorize_url}`; `GET /accounts/connect/facebook/callback` (cookie, state validation,
  redirect only); `GET /accounts/connections`, `GET /accounts/connections/{id}/pages`,
  `POST /accounts/connections/{id}/pages` (link/upgrade rows), `POST /accounts/{id}/disconnect`,
  `POST /accounts/connections/{id}/revoke`, `POST /accounts/{id}/verify` (manual token check).
- `accounts/service.py`: `link_pages`, `mark_disconnected`, `page_credential_for(account)`
  (returns a handle, not plaintext), `remaining_reels_quota(account)`.
- Response schemas never carry tokens; `SocialAccountResponse` gains `connection` summary
  (`connected_at`, `last_verified_at`, `token_expires_at` for the user grant, `page_tasks`).

### 5.4 publishing module

- `adapters/facebook.py`: `capabilities()` → `api_publishing=True`, `scheduling_via_api=False`
  (M3B.1), `status_lookup_via_api=True`, `clickable_links_in_caption=False`, Reels media
  validation per the spec; `publish()` is **not** used for API mode (the worker drives phases);
  new adapter methods `start_upload_session`, `upload_file`, `finish_publish`,
  `fetch_video_status`, `fetch_post_identity`, each taking a credential handle and returning
  typed results. `get_publication_status` / `reconcile_publication` become real and
  `verifiable=True`.
- `service.py`: `start` on a connected Facebook account with API mode creates the attempt +
  upload row and moves the job to `PUBLISHING` (no network in the request); new functions
  `claim_api_job`, `advance_upload`, `complete_api_attempt`, `fail_api_attempt`,
  `park_for_reconciliation`, `reconcile_with_api` (admin action "Check with Meta"), all using
  `_lock_job` and `record_audit` with a system actor when the worker calls them. `release_due`
  also holds jobs whose Page quota is exhausted (`reels_quota`).
- `lifecycle.py`: unchanged states; `reconcile` gains the API-backed outcome in the service, not
  a new action.

### 5.5 worker

- `worker/publisher.py` with `run_once`/`run_forever`, heartbeat, `SKIP LOCKED` claiming,
  leases, phase handlers, backoff, quota and rate-limit holds, hourly maintenance (token
  validation, `oauth_states` sweep, session sweep). `docker-entrypoint.sh publisher`.
- Compose: `publisher` service (same image, finals mount read-only only, `PUBLISHER_CPUS=0.5`,
  `PUBLISHER_MEMORY=256M`, healthcheck via heartbeat). The existing `worker` service and the
  NAS proxy workflow are untouched.

### 5.6 web

- `/accounts`: "Connect Facebook Page" (admin, only when `integrations.meta.configured`),
  connection list, Page selection page (`/accounts/connect/facebook/[connectionId]`),
  disconnect/verify actions, "Reconnect needed" state, user-grant expiry notice. No token ever
  rendered.
- Publication detail: mode badge (API/assisted), upload phase timeline from attempt detail,
  "Check with Meta" for admins in `NEEDS_RECONCILIATION`, quota indicator in Composer and
  queue. Types in `packages/shared-types`.

### 5.7 docs

ADR 0008 (encrypted credential store and OAuth binding), ADR 0009 (publisher worker and
upload sessions), updates to `docs/architecture/{database,distribution,security,modules}.md`,
`docs/deployment/synology.md` (TLS, publisher service, `.env` keys, backup of the key),
`README.md` (new env vars, `pnpm dev:publisher`).

## 6. Tests required before M3B.1 is reported complete

- Crypto: round trip, AAD mismatch, unknown key id, rotation script, log redaction.
- OAuth: state replay, expired state, wrong user, denied consent, app id mismatch, missing
  scope, Page without `CREATE_CONTENT`, manual row upgrade preserves rules and publications.
- Publisher (with a fake Meta server in tests via `httpx.MockTransport`): happy path; upload
  interruption and resume by offset; session `expired`; finish timeout then reconciliation
  finds `published`; finish timeout then reconciliation finds `error`; worker crash at every
  phase (lease expiry + resume) produces exactly one finish; two publishers on one queue; 190
  during publish → account disconnected, job FAILED; 506 duplicate; rate-limit headers → hold;
  quota exhaustion → `release_held`; `uq_publication_jobs_account_external_post` collision →
  NEEDS_RECONCILIATION; media changed on disk → FAILED before upload.
- Migration: upgrade/downgrade, parity.
- API: no response, log line or audit payload contains a token (assert on captured logs).
- Existing suites stay green: `uv run pytest` with `TEST_DATABASE_URL`, `pnpm lint`,
  `pnpm typecheck`, `pnpm build`.

## 7. Security prerequisites and debt

| # | Item | Dev blocker | Prod blocker | Fix (where) |
| --- | --- | --- | --- | --- |
| 1 | HTTPS redirect URI (Meta enforces HTTPS + Strict Mode) | Yes, unless `localhost` is exempt for role-holders (unknown #4); otherwise a TLS tunnel or local reverse proxy with a trusted certificate | Yes: DSM reverse proxy with a certificate, `SESSION_COOKIE_SECURE` unset | `docs/deployment/synology.md` |
| 2 | Credential encryption key present; API refuses OAuth without it | Yes (one `openssl rand`) | Yes, with out-of-band backup | config validation at startup |
| 3 | Service token acts as `owner` of an organization | No | Should fix: scope it to `media:scan`, `publishing:release` (`Identity.scopes`, `RequireScope`), role `editor`; the publisher never uses HTTP | `api/deps.py`, routers |
| 4 | In-process login rate limiter | No | Acceptable at `UVICORN_WORKERS=1`; document; DB-backed `login_attempts` if ever > 1 worker | `security.md` |
| 5 | Session cleanup | No | Should fix: hourly sweep in the publisher deletes sessions expired/revoked > 30 days and `oauth_states` consumed/expired > 1 day | `worker/publisher.py` |
| 6 | Admin session revocation | No | Should fix before enabling OAuth connect: `POST /auth/members/{id}/sessions/revoke`, suspend → revoke all, audit | `identity` |
| 7 | Reverse proxy trust (`--forwarded-allow-ips "*"`, `TRUST_PROXY_HEADERS`) | No | Review: restrict `--forwarded-allow-ips` to the compose subnet; never derive redirect URI or Origin allow-list from headers | entrypoint, `deps.py` |
| 8 | Credential key rotation | No | Runbook + script tested on a copy | `scripts/rotate_credential_keys.py` |
| 9 | Backup/restore of encrypted credentials | No | Runbook: dump contains ciphertext; key stored separately; restore without key → `credential_undecryptable` → reconnect | `synology.md` §8 |
| 10 | Deauthorize / data deletion callbacks | No | Instructions URL on a public page is sufficient; callbacks only if a public path is ever exposed | `meta-app-setup.md` |

## 8. M3B.1 implementation prompt outline

```
AFFILIO — M3B.1: Facebook Page Reels API publishing (implementation)
Read: CLAUDE.md, docs/integrations/facebook-reels-{feasibility,architecture}.md,
      docs/integrations/meta-app-setup.md, docs/milestones/M3B-plan.md.
Verify HEAD includes 6378300 and that docs/integrations exists.
Constraints: official Graph API only, pinned version; no token in responses/logs/audit;
  no transcoding; finals root read-only; no change to worker/runner.py or the NAS proxy
  workflow; no Redis; Alembic migration with downgrade; VARCHAR+CHECK enums; shared types
  mirrored; no deployment to the NAS; no real tokens in tests (httpx.MockTransport fakes).
Deliver in this order, each step with tests:
  1. Settings + crypto + credential store (+ rotation script, redaction processor).
  2. Migration 0008 + models + enums + shared types.
  3. Meta client (httpx, appsecret_proof, typed errors, retries only for transient).
  4. OAuth endpoints + Page linking + disconnect/verify + audit.
  5. Reels adapter methods + media validation per spec.
  6. Publisher worker (claim, phases, resume, finish-once, reconcile, quota, holds,
     maintenance) + entrypoint + compose service + healthcheck.
  7. Service functions and admin "Check with Meta" reconciliation.
  8. Web: accounts connect flow, Page selection, publication phases, quota.
  9. Docs: ADR 0008/0009, architecture docs, Synology guide (TLS, publisher, key backup),
     README, .env.example files.
Report: what was implemented, commands run with results, verified vs not run, limitations,
  the list of unknowns that still need a real Meta app, and next step (M3B.2).
Stop after M3B.1 acceptance criteria; do not implement Meta-side scheduling or comments.
```

## 9. Acceptance criteria for M3B.1

1. An admin who is an app tester can connect a Page; the account shows `connected`,
   `is_verified=true`, `source=oauth`, with tasks and scopes but no token.
2. Starting an approved publication on that Page enqueues an API attempt; the publisher
   uploads from the finals root, finishes once, polls to `published`, and the job becomes
   `PUBLISHED` with `evidence_kind=api_verified` and the Meta identity.
3. Every failure path in §6 ends in `FAILED` or `NEEDS_RECONCILIATION` with a human-readable
   reason and never a second finish call; fault injection tests prove it.
4. Disconnect, revocation (190) and reconnect behave as designed; routing rules and history
   survive.
5. Quota and rate-limit holds are visible in the queue; nothing is retried blindly.
6. `pnpm lint`, `pnpm typecheck`, `pnpm build`, `uv run pytest` (with `TEST_DATABASE_URL`)
   pass; the report distinguishes verified from not run; the NAS is not touched.

## 10. Unknowns that block nothing in code but must be closed with a real Meta app

See feasibility §4. The first live test (setup guide §8) must record: finish response body,
status payloads at each phase, the field set that yields the permalink, the upload-session
expiry observed, whether `localhost` works without HTTPS, and the actual `expires_at` values
from `/debug_token`. Capture them (redacted) in `docs/integrations/meta-live-test-notes.md`.
