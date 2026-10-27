# Facebook Page Reels — integration architecture (M3B.0)

- **Status:** Design (M3B.0, 2026-10-26). Nothing in this document is implemented.
- **Companion documents:** `facebook-reels-feasibility.md` (what the official API allows, with
  sources), `meta-app-setup.md` (operator steps), `docs/milestones/M3B-plan.md` (delivery plan).
- **Scope:** publishing an approved final video as a Reel on one or more connected Facebook
  Pages through the Graph API, with per-Page caption, links, schedule, approval snapshot,
  publication record and (later) metrics. Everything else (Instagram, Shopee Video, TikTok)
  is out of scope.

Facts about Meta's API in this document are only the ones classified `VERIFIED_SUPPORTED` or
`REQUIRES_PERMISSION_OR_REVIEW` in the feasibility report; anything `UNCONFIRMED` is named as
such and designed around, never assumed.

## 1. Repository integration review (what exists, what changes)

### 1.1 What M3A/M3A.1 already provide and M3B.1 keeps unchanged

| Area | Where | Reuse in M3B.1 |
| --- | --- | --- |
| Account registry | `accounts/models.py` `SocialAccount` (`source`, `is_verified`, `status`, `capabilities`, `external_account_id`), no credential columns | Page rows are created/upgraded by the OAuth flow; `connected` + `is_verified=true` are reserved for it (`accounts/service.py::change_status` keeps refusing manual `connected`) |
| Auth & RBAC | `api/deps.py` (`Identity`, `RequireAdmin`), cookie sessions, CSRF Origin check | Connect/disconnect endpoints are `RequireAdmin`; the OAuth callback is a top-level GET that carries the session cookie (`SameSite=Lax` allows it) |
| Adapter contract | `publishing/adapters/base.py` (`capabilities / validate_media / validate_content / prepare_publication / publish / get_publication_status / reconcile_publication`) | `FacebookReelsAdapter` gets a real implementation behind the same contract; the registry (`adapters/registry.py`) and test fakes stay |
| Lifecycle | `publishing/lifecycle.py` ten states, `PUBLISHING → PUBLISHED / FAILED / NEEDS_RECONCILIATION`, explicit `retry` | No new job status. API publishing lives *inside* `PUBLISHING`; sub-phases are on a new upload-session table (§4) |
| Idempotency | `publication_jobs.intent_id` (unique), `idempotency_key`, `uq_publication_jobs_campaign_media_account`, optimistic `version`, `SELECT … FOR UPDATE` in `_lock_job` | Reused as the stable client-side identity of an intent; the Graph API offers no idempotency key, so duplicate prevention is local (§4.4) |
| Approval snapshots | `publication_snapshots` immutable; `start` refuses a stale snapshot; `assert_publishable_target` re-checks routing and classification | The worker re-runs exactly these checks before every network call |
| Evidence gate | `start()` already handles `succeeded` / `failed` / `ambiguous` outcomes from a non-assisted adapter and sets `evidence_kind = api_verified`; `uq_publication_jobs_account_external_post` | Reused verbatim; the worker reports outcomes through the same service functions so the audit trail is identical |
| Classification & routing | `routing_engine`, `release_due` holds blocked/stale jobs (`publication.release_held` audit) | Unchanged; the publisher claims only `READY_TO_PUBLISH` jobs and re-validates |
| Media roots | `media/roots.py` `RootBinding.resolve()`; finals root read-only | The upload streams bytes from `finals.resolve(relative_path)`; nothing is copied, transcoded or written |
| Worker pattern | ADR 0004: separate container, PostgreSQL queue with `SKIP LOCKED`, backoff columns, heartbeat file | A **second** worker command (`publisher`) with the same pattern; the scan/thumbnail worker is not modified |
| Audit | `core/audit.py::record_audit` (actor user/service/system, request id) | Every OAuth and publishing step writes an audit event; system actor for worker-driven transitions |
| Settings | `core/config.py` `Settings` with `SecretStr` | New `META_*` and `CREDENTIAL_*` settings; nothing is ever returned by an endpoint |

### 1.2 Minimal changes for M3B.1

1. **Dependencies** (`uv add` in `apps/api`): `httpx` promoted from dev to runtime (Graph API
   client; already in `uv.lock`), `cryptography` (AES-256-GCM for credentials; not present
   today). No other new packages.
2. **Migration 0008** (§5): `oauth_states`, `platform_connections`, `account_credentials`,
   `publication_uploads`; a few nullable columns on `social_accounts`. No change to
   `publication_jobs` or the state table.
3. **accounts module**: OAuth start/callback/select-pages/disconnect endpoints and the
   credential store service. `accounts` stays before `publishing` in `DOMAIN_MODULES`, so
   `publishing` may call `accounts.credentials.get_page_token(...)`; nothing imports backwards.
4. **publishing module**: `adapters/facebook.py` gains a Graph API client-backed
   implementation; `service.py` gains `start_api_attempt` / `advance_upload` /
   `finish_api_attempt` used by the worker; `start` keeps its contract (operator "Start" on a
   connected account enqueues an API attempt instead of returning `manual_required`).
5. **worker**: `affilio/worker/publisher.py` + `docker-entrypoint.sh publisher` + a fifth
   compose service `publisher` with its own limits. `runner.py` is untouched.
6. **web**: connect/disconnect buttons on `/accounts` (admin only, only when the API reports
   `oauth_configured=true`), a Page-selection screen, "API" badge and attempt phases on the
   publication detail. Types mirrored in `packages/shared-types`.
7. **docs**: ADR 0008 (encrypted credential store), ADR 0009 (publisher worker + upload
   sessions), `docs/architecture/{database,distribution,security,modules}.md`, Synology guide.

Not changed: the state machine, snapshots, assisted flow, Composer, routing, media scanner,
the existing worker, the proxy pipeline on the NAS.

## 2. OAuth and credential security design

### 2.1 Flow (authorization-code, confidential client, server-side only)

```
Admin (browser)            AFFILIO web (proxy)        AFFILIO API                     Meta
   │ click "Connect Facebook Page"  │                        │                            │
   │──POST /accounts/connect/facebook (cookie, Origin)──────►│ create oauth_states row     │
   │◄─ 200 {authorize_url} ─────────────────────────────────│ (state hash, org, user,     │
   │                                                        │  expires in 10 min)         │
   │──top-level navigation to authorize_url ───────────────────────────────────────────────►│ login + consent
   │◄─302 {REDIRECT_URI}?code=…&state=… ───────────────────────────────────────────────────│
   │──GET /api/v1/accounts/connect/facebook/callback?code&state (cookie) ──────────────────►│
   │                                                        │ validate state (§2.2)       │
   │                                                        │──code → short-lived user token (app secret, server)─►│
   │                                                        │──fb_exchange_token → long-lived user token ─────────►│
   │                                                        │──/debug_token (app_id, scopes, user_id, expiry) ────►│
   │                                                        │──/me/accounts (pages, tasks, page tokens) ──────────►│
   │                                                        │ encrypt + store user token;  │
   │                                                        │ cache page list (no tokens   │
   │                                                        │ leave the API process)       │
   │◄─302 /accounts/connect/facebook/{connection_id} ───────│                            │
   │──GET  …/connections/{id}/pages ────────────────────────►│ list: id, name, tasks, already-linked account id
   │──POST …/connections/{id}/pages {page_ids} ─────────────►│ create/upgrade social_accounts, store page tokens
```

Properties:

- The browser never sees a Meta token, a `code` after the callback, the app secret or the
  `appsecret_proof`. The callback answers with a redirect only.
- All Meta calls are server-to-server with `appsecret_proof` (HMAC-SHA256 of the token with
  the app secret), which Meta lets the app require ("Require App Secret" setting) so a leaked
  token is useless without the secret.
- The API uses one fixed `META_OAUTH_REDIRECT_URI` from settings. It is never derived from
  `Host`, `X-Forwarded-Host` or `Origin`; those headers only matter for the CSRF check.
  The value must be byte-identical to the one in the Meta app's *Valid OAuth Redirect URIs*
  and must be HTTPS (feasibility A2).
- Scope request is the minimum set the feasibility report verifies for Page discovery and Reel
  publishing (`pages_show_list`, `pages_manage_posts`, `pages_read_engagement`; `read_insights`
  only when M3C metrics are enabled by setting). The requested scope list is a setting so
  Advanced Access rollout can be staged.
- Meta's "Facebook Login for Business" requires a `config_id`; the API supports both
  `META_LOGIN_CONFIG_ID` (business login) and the plain `scope` form, because which one the
  operator's app type requires is an app-setup fact (`meta-app-setup.md`).

### 2.2 State validation and CSRF

`oauth_states` row per start: `id`, `organization_id`, `user_id`, `session_id` (the
`user_sessions.id` that started it), `state_hash` (SHA-256 of a 32-byte random value; the raw
value is only in the authorize URL), `redirect_uri`, `requested_scopes`, `created_at`,
`expires_at` (now + 10 min), `consumed_at`, `result` (`completed` / `denied` / `error`).

Callback acceptance requires **all** of:

1. A valid session cookie (the admin's); unauthenticated callbacks are 401 and the state row is
   left untouched (it expires).
2. `state` hashes to an unconsumed, unexpired row whose `user_id` equals the cookie's user and
   whose `organization_id` is the one the row was created for. The organization is taken from
   the state row, not from `X-Organization-Slug`.
3. The row is consumed atomically (`UPDATE … WHERE consumed_at IS NULL` affecting one row)
   before any Meta call, so a replayed callback (same `code`+`state`) fails closed.
4. `error`/`error_reason` parameters from Meta mark the row `denied` and redirect to the UI
   with a neutral message; nothing is stored.

PKCE is not part of Meta's documented manual web flow (feasibility A2), so it is not used;
`state` plus the server-held app secret are the defence (the client is confidential, so the
code alone cannot be exchanged by an attacker), and the redirect URI is protected by Meta's
*Strict Mode* exact-match rule.

### 2.3 Account-to-organization binding, Page discovery, permission verification

- A `platform_connections` row represents one Meta *user* grant for one organization:
  `provider='meta'`, `external_user_id`, `external_user_name`, `granted_scopes` (from
  `/debug_token`), `token_expires_at`, `data_access_expires_at`, `status`
  (`active` / `revoked` / `expired` / `invalid`), `connected_by_user_id`, `last_checked_at`,
  `last_error`.
- Page discovery is `GET /me/accounts?fields=id,name,category,tasks` with the long-lived
  user token. The selection screen shows id, name, category, the `tasks` Meta reports, and
  whether a `social_accounts` row with that `external_account_id` already exists in this
  organization (manual registration from M3A).
- Linking rules (`accounts/service.py::link_pages`):
  - existing row with the same `external_account_id` → upgraded in place: `source=oauth`,
    `is_verified=true`, `status=connected`, `connection_id`, `connected_at`,
    `display_name` refreshed, routing rules and publications preserved;
  - no row → created with the same fields;
  - a Page whose `tasks` lack `CREATE_CONTENT` is listed but not selectable, with the reason
    shown; if `tasks` is absent in the response (version-dependent) the Page is selectable and
    the first publish verifies permission the hard way (a 200/403 error surfaces as FAILED,
    never as PUBLISHED).
- Permission verification before every publish: `granted_scopes ⊇ required_scopes`, the Page
  credential is `active`, and `social_accounts.status = connected`. Failing any of these moves
  the job to FAILED with `last_error = page_not_connected` — no network call is made.
- Cross-organization: the unique constraint is per organization, so the same Page could be
  connected in two organizations. M3B.1 keeps this (single-tenant deployment) but the audit log
  names the connecting user; a global uniqueness rule can be added later without a data fix.

### 2.4 Token encryption at rest

Table `account_credentials` (one row per token; history kept, one *active* row per
`(subject, kind)` enforced by a partial unique index):

| Column | Purpose |
| --- | --- |
| `organization_id`, `connection_id` (FK), `social_account_id` (FK, null for user tokens) | ownership |
| `kind` | `meta_user_long_lived` \| `meta_page` (VARCHAR + CHECK) |
| `ciphertext` BYTEA, `nonce` BYTEA(12), `key_id` VARCHAR(32) | AES-256-GCM output; AAD = `kind || organization_id || subject id || key_id` so a ciphertext cannot be moved between rows |
| `token_fingerprint` CHAR(64) | SHA-256 of the plaintext: equality checks (did Meta hand us the same token?) without decrypting; never reversible |
| `scopes` JSONB, `page_tasks` JSONB | what `/debug_token` and `/me/accounts` reported at issue time |
| `issued_at`, `expires_at` (null = Meta reports no expiry), `data_access_expires_at` | lifetimes from `/debug_token` |
| `status` | `active` \| `superseded` \| `revoked` \| `invalid` |
| `last_validated_at`, `last_used_at`, `invalidated_reason` | health |
| `created_by_user_id` | audit |

Scheme: AES-256-GCM from `cryptography` (AEAD, 96-bit random nonce per encryption, 128-bit
tag). The key encryption material is `CREDENTIAL_ENCRYPTION_KEYS` — a comma-separated list of
`key_id:base64url(32 bytes)` — plus `CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID`. Decryption picks the
key by the row's `key_id`; encryption always uses the active key. With dozens of rows at most,
direct AEAD with versioned keys is simpler than per-row data keys and offers the same rotation
properties; if a KMS ever appears, the `key_id` column is where envelope-wrapped data keys slot
in. Keys never enter the database, logs, the `/system/info` endpoint or `safe_summary()`.

Access path: a single service `accounts/credentials.py` with `store_token`, `use_token` (a
context manager that decrypts, hands the plaintext to the Graph client, zeroes nothing — Python
cannot — but never returns it to a router) and `invalidate`. No Pydantic schema includes the
plaintext; the response models expose only `kind`, `status`, `issued_at`, `expires_at`,
`scopes`, `last_validated_at`.

### 2.5 Key management, rotation, backup and restore

- The key lives in the Synology `.env` next to the compose file (mode 600, already the policy
  for `POSTGRES_PASSWORD`). Generate with `openssl rand -base64 32`.
- Rotation: add `v2:<key>` to the list, set the active id to `v2`, restart, run
  `python -m affilio.scripts.rotate_credential_keys` (re-encrypts every active row under the
  active key inside one transaction, audits `credentials.rekeyed`), then remove `v1` after
  `SELECT count(*) FROM account_credentials WHERE key_id='v1'` is zero. The API refuses to
  start if the active id is not in the list, and logs a warning (never the key) if any stored
  `key_id` is unknown.
- Backups contain ciphertext only. The key must be backed up out of band (DSM Key Manager,
  the operator's password manager). A restore on a host without the key leaves accounts
  `disconnected` with reason `credential_undecryptable`; the recovery is "Reconnect" (the
  tokens are re-issuable by Meta), so key loss is an outage of connected Pages, not data loss.
- Test coverage required: encrypt/decrypt round trip, AAD mismatch rejection, unknown key id,
  rotation script against a seeded database, redaction of tokens in logs.

### 2.6 Renewal, revocation, disconnect and reconnect

- Lifetimes come from `/debug_token`, not from assumptions. The publisher re-validates a
  Page token at most once per day (`GET /{page-id}?fields=id` with `appsecret_proof`), and
  before each publish if `last_validated_at` is older than 1 hour. Error code 190 (any
  subcode) → credential `invalid`, account `disconnected`, audit `account.token_invalid`,
  UI "Reconnect needed"; queued API jobs on that account are moved to FAILED with
  `page_not_connected` (explicit retry after reconnect, matching the M3A "no blind retry" rule).
- Lifetimes (feasibility A3): the long-lived *user* token lasts about 60 days; Page tokens
  derived from it carry no expiry and are invalidated only by events (password change, role
  removal, app removal, Meta policy action). AFFILIO therefore publishes with Page tokens and
  keeps the user token only for Page re-discovery. An expiring user token is flagged 7 days
  ahead on the accounts page; renewal is a new OAuth round by an admin, which reuses the
  connection row (`external_user_id` match) and refreshes every Page token. Page tokens keep
  working after the user token expires, so an expired user token never blocks publishing.
- Disconnect (admin, `POST /accounts/{id}/disconnect`): mark the Page credential `revoked`,
  account `disconnected` (routing rules and publication history untouched); optionally
  `DELETE /{page-id}/permissions`-style revocation is **not** used because Page tokens are
  derived from the user grant. A separate `POST /accounts/connections/{id}/revoke` calls
  `DELETE /me/permissions` with the user token (revokes the app for that Meta user), then
  marks every Page of the connection disconnected.
- Meta-initiated revocation: the app's *Deauthorize callback URL* and *Data deletion request
  callback* are implemented as `POST /api/v1/integrations/meta/deauthorize` and
  `/data-deletion`, verifying `signed_request` with the app secret, marking the connection
  revoked and answering the documented JSON. They are only reachable if the operator exposes
  that path publicly; the feasibility report states what Meta requires here and the setup
  guide offers the "instructions URL" alternative. Without public exposure, revocation is
  still detected by the daily validation (190 errors).
- Reconnect: the same OAuth flow; existing Page rows are re-bound by `external_account_id`.

### 2.7 Audit and logging rules

- Audit events (`record_audit`): `account.connect_started`, `account.connect_completed`
  (external user id, scopes, page count), `account.connect_denied`, `account.page_linked`,
  `account.disconnected`, `account.token_invalid`, `credentials.rekeyed`, plus every
  publishing transition as today, and `publication.upload_phase` for each phase change.
- Logging: the Graph client logs `method`, path template (`/{page-id}/video_reels`), status
  code, `fbtrace_id`, `X-App-Usage` percentages and duration. A structlog processor redacts
  the keys `access_token`, `code`, `client_secret`, `appsecret_proof`, `signed_request`,
  `upload_url` query strings and `Authorization` headers anywhere in an event dict (tested).
  Response bodies of `/oauth/access_token`, `/debug_token` and `/me/accounts` are never logged.
  Error bodies are logged after passing through the same redactor.

### 2.8 Failure handling in the OAuth flow

| Failure | Behaviour |
| --- | --- |
| State missing / expired / consumed / wrong user | 400 `oauth_state_invalid`, audit `account.connect_failed`, UI "start again" |
| Meta `error=access_denied` | state `denied`, redirect to UI with a neutral message |
| Token exchange or `/debug_token` fails | nothing stored; audit with `fbtrace_id`; UI "Meta did not complete the authorization" |
| `/debug_token.app_id` ≠ configured app id | reject (token issued for another app) |
| Required scope not granted | connection stored as `active` with `missing_scopes`; Pages selectable only if their tasks allow publishing; UI explains which scope to grant on reconnect |
| `/me/accounts` empty | connection stored; UI "no Pages with a role were found"; never fabricate a Page |
| Operator closes the browser before selecting Pages | connection row remains; the accounts page offers "Choose Pages" for 24 h, then the user token is re-validated on next use |

## 3. Publishing lifecycle (end to end)

The job-level machine in `lifecycle.py` is unchanged. An API attempt is one
`publication_attempts` row (`mode=api`, `adapter=facebook_reels`) plus one
`publication_uploads` row that records the Meta-side session. Steps:

| # | Step | Where | Fails to |
| --- | --- | --- | --- |
| 1 | Verify approved snapshot (`approved_snapshot_id` set, `content_version` matches) | service, before enqueue and again in the worker | 409 at start; FAILED `snapshot_stale` in worker |
| 2 | Revalidate classification and channel eligibility (`assert_publishable_target`, `_classification_stale`) | service + worker | 409 `target_blocked` / `revalidation_required`; worker: job stays `READY_TO_PUBLISH`, audit `publication.release_held` |
| 3 | Verify Page connection and permissions (§2.3) | worker, no network | FAILED `page_not_connected` |
| 4 | Validate final video compatibility (§3.1) from the probe stored on the asset; confirm the file still exists and its size/mtime match the snapshot | worker, local I/O | FAILED `media_incompatible` / `media_changed` |
| 5 | Initialize upload session: `POST /{page-id}/video_reels upload_phase=start` → `video_id`, `upload_url`; persist both **before** anything else | worker | retry with backoff (safe: nothing published) |
| 6 | Upload the file bytes to `upload_url` (`POST https://rupload.facebook.com/video-upload/{version}/{video_id}`, headers `Authorization: OAuth {page token}`, `offset`, `file_size`, body `application/octet-stream`) streamed from the finals root in 4 MiB reads | worker | on interruption read `uploading_phase.bytes_transfered` from `GET /{video_id}?fields=status` and resume with that `offset` (feasibility B2a); `video_status=expired` or `upload_failed` after retries → new session (safe, nothing published) |
| 7 | Track upload: `GET /{video_id}?fields=status` until `video_status=upload_complete` / `uploading_phase.status=completed` (accept `complete` too) and `processing_phase` not `error` | worker | timeout → FAILED `upload_incomplete` (no finish was sent) |
| 8 | Finalize: persist phase `finish_requested` **then** `POST /{page-id}/video_reels upload_phase=finish video_id=… video_state=PUBLISHED description=…` (optionally `title`, `is_ai_generated`); the finish response (`success`, `post_id` if present) is stored verbatim | worker | timeout/unknown → §3.3 reconciliation; never resend |
| 9 | Verify: poll `status` until `publishing_phase.status=completed` and `publishing_phase.publish_status=published` (or `video_status=error`); read `publish_time`; fetch the Page post id / permalink by the field set confirmed on a real account (feasibility unknown #1) | worker | `error` → FAILED with Meta's `error_user_msg`; timeout → NEEDS_RECONCILIATION |
| 10 | Persist external identity: `external_post_id` (Page post id when available, else the Meta `video_id` prefixed `video:`), `external_post_url` (permalink when available; never a guessed URL), `evidence_kind=api_verified`, `published_at`; uniqueness index refuses a duplicate | service (`start()`-style evidence path) | IntegrityError → NEEDS_RECONCILIATION with details |
| 11 | Reconcile uncertain outcomes (§3.3) | worker + admin | — |

Caption composition reuses `compose_caption(content, include_links=…)` and goes into the
`description` parameter (hashtags and emoji supported; length limit and link behaviour are
unconfirmed, feasibility B6). The first version posts the approved caption text exactly as the
assisted checklist shows it; affiliate links are included only when the operator enables
"links in caption" for the job, because `clickable_links_in_caption` stays `False` until
verified. Posting links as a first comment
through the API is a separate capability (`/{post-id}/comments`, `pages_manage_engagement`)
and is **not** in M3B.1.

### 3.1 Final video compatibility (no transcoding on the NAS)

`validate_media` becomes fact-based using probe columns that already exist on `media_assets`
(`video_codec`, `audio_codec`, `pixel_format`, `bit_depth`, `width`, `height`, `frame_rate`,
`duration_seconds`, `container_format`, `bit_rate`, `file_size`) against the published Reels
specification (feasibility B3): MP4 container (MOV accepted with a warning), H.264 or H.265
video with `yuv420p` 8-bit, AAC audio at 48 kHz, portrait 9:16 (tolerance ±2 %), at least
540×960, 24–60 fps, 3–90 s, file size under 2 GB. Mismatches are **errors** before approval
(so the editor re-exports), not warnings. Where the probe is
missing (`probe_error`) the job cannot be approved for API mode. HEVC 10-bit proxies are never
candidates; only the final asset's main variant is uploaded.

### 3.2 Scheduling

M3B.1 keeps AFFILIO as the scheduler: `SCHEDULED → READY_TO_PUBLISH` by `release_due`, then the
publisher uploads just-in-time and finishes with `video_state=PUBLISHED`. The published time
therefore lags the scheduled time by upload + processing (minutes); the queue shows the
estimate. Meta-side scheduling (`video_state=SCHEDULED` + `scheduled_publish_time`) is an
opt-in in M3B.2 once its bounds and cancel semantics are verified; it would upload early and
keep the job in `PUBLISHING` until Meta reports `published`.

### 3.3 Reconciliation of uncertain outcomes

Uncertainty only exists after step 8 (a finish request may have been accepted without a
response). Because `video_id` is persisted before the request, reconciliation is exact:

1. `GET /{video_id}?fields=status,permalink_url,published,created_time` with the Page token.
2. `publishing_phase.status=completed` and `publish_status=published` → PUBLISHED
   (`api_verified`) with `publish_time` and the permalink/post id when the API returns them.
3. `video_status=error` / `expired`, `publishing_phase.publish_status=error`, or the video is
   gone (404 / code 100 subcode 33) → FAILED (`finish_rejected`, `video_expired` or
   `video_missing`); no duplicate exists because no second start/finish was ever sent.
4. Still `processing` → keep polling within the budget (30 min); after that, the job moves to
   `NEEDS_RECONCILIATION` with `video_id` in the attempt detail, and the admin reconcile screen
   offers "Check with Meta again" (runs the same lookup, `api_verified`) in addition to the
   M3A manual evidence path.

## 4. Durable state, attempts and duplicate prevention

### 4.1 `publication_uploads` (new)

| Column | Notes |
| --- | --- |
| `id`, `organization_id`, `job_id` FK, `attempt_id` FK (`publication_attempts`), `social_account_id` FK, `snapshot_id` FK | identity |
| `phase` | `pending` → `session_started` → `uploading` → `uploaded` → `finish_requested` → `processing` → `published` \| `failed` \| `abandoned` \| `unknown` (VARCHAR + CHECK) |
| `meta_video_status`, `meta_publish_status` | last `video_status` and `publishing_phase.publish_status` seen (strings as Meta sends them) |
| `meta_video_id`, `upload_url` (encrypted? no: it contains no token, but it is treated as sensitive in logs), `upload_url_expires_at` | from the start call |
| `file_relative_path`, `file_size`, `file_mtime`, `content_hash` | pinned at claim time; the upload refuses to proceed if the file changed |
| `bytes_sent` | progress mirror of `uploading_phase.bytes_transfered`; the resume `offset` (feasibility B2a) |
| `finish_requested_at`, `finish_response` JSONB, `meta_post_id`, `permalink_url`, `published_at` | finish/verify results |
| `last_status` JSONB, `last_polled_at`, `next_action_at`, `poll_count` | tracking |
| `lease_owner`, `lease_expires_at`, `attempt_count`, `max_attempts` | worker claim/lease (§4.3) |
| `error_code`, `error_subcode`, `error_message`, `fbtrace_id`, `is_transient` | last Graph error |
| `created_at`, `updated_at` | |

Index: partial unique on `job_id WHERE phase NOT IN ('published','failed','abandoned')` —
at most one live upload per job. Index on `(next_action_at) WHERE phase NOT IN (terminal)`
for claiming.

### 4.2 Attempt records

`publication_attempts` is reused as-is: one row per API attempt with `mode=api`,
`snapshot_id`, `started_by_label` (the operator who pressed Start, or `system` for scheduled
releases), `detail` holding `{upload_id, meta_video_id, phases: [...], fbtrace_ids: [...]}` and
`error`. The job's `attempts` list on the publication detail therefore shows API attempts
next to assisted ones without UI schema changes.

### 4.3 Worker claim and crash safety

- Claim: `SELECT … FROM publication_jobs WHERE status='ready_to_publish' AND platform='facebook'
  AND social_account connected … FOR UPDATE SKIP LOCKED LIMIT 1`, then the service moves the
  job to `PUBLISHING` (audit, system actor), creates the attempt and the upload row
  (`phase=pending`, `lease_owner=WORKER_ID`, `lease_expires_at=now()+10 min`), commits.
- Every phase change is its own short transaction committed before the next network call.
  Leases are renewed on each poll; a row whose lease expired and whose phase is non-terminal is
  claimable by another publisher instance, which **resumes from the persisted phase**:
  `session_started`/`uploading` → continue or restart upload (safe); `uploaded` → send finish
  (safe: no finish recorded); `finish_requested`/`processing` → reconcile only (§3.3), never
  finish again.
- Worker crash between "finish response received" and "commit": the phase is
  `finish_requested`, so the resume path is reconciliation, which finds the video published and
  records it. Exactly-once publication is therefore guaranteed by the persisted phase, not by
  the network.
- Max attempts per upload row: 5 for pre-finish network errors with backoff 30 s · 2ⁿ (cap
  15 min); zero automatic re-finish ever. Failed uploads move the job to FAILED; the operator's
  explicit `retry` creates a new attempt and a new upload row.

### 4.4 Why duplicates cannot happen

| Hazard | Guard |
| --- | --- |
| Two workers pick the same job | `FOR UPDATE SKIP LOCKED` + job status `PUBLISHING` + lease |
| Same job started twice by two admins | `_lock_job` + version check + `start` refused outside `APPROVED/SCHEDULED/READY_TO_PUBLISH` |
| Retry after a timed-out finish | phase `finish_requested` is persisted first; resume path only reconciles |
| Operator `retry` after NEEDS_RECONCILIATION | not allowed by the state table (`retry` only from FAILED, which requires a reconciled `not_published`) |
| Same Meta post recorded on two jobs | `uq_publication_jobs_account_external_post` |
| Same video published to the same Page from two campaigns | allowed by design (independent publications), but the Composer warns when another PUBLISHED/queued job exists for the same asset+Page (query exists: `_publication_counts`) |
| Meta-side retries of a start call | harmless: unfinished sessions never publish; orphaned uploads are not referenced |

## 5. Minimal database migration plan (`0008_meta_credentials_and_uploads`)

```
oauth_states            id, organization_id FK, user_id FK, session_id FK SET NULL, provider, state_hash CHAR(64) UNIQUE,
                        redirect_uri, requested_scopes jsonb, created_at, expires_at, consumed_at, result, error
platform_connections    id, organization_id FK, provider, external_user_id, external_user_name, granted_scopes jsonb,
                        missing_scopes jsonb, status active|revoked|expired|invalid, token_expires_at,
                        data_access_expires_at, connected_by_user_id FK SET NULL, last_checked_at, last_error,
                        created_at, updated_at; UNIQUE(organization_id, provider, external_user_id)
account_credentials     id, organization_id FK, connection_id FK CASCADE, social_account_id FK CASCADE (nullable),
                        kind, ciphertext bytea, nonce bytea, key_id, token_fingerprint, scopes jsonb, page_tasks jsonb,
                        issued_at, expires_at, data_access_expires_at, status, last_validated_at, last_used_at,
                        invalidated_reason, created_by_user_id, created_at, updated_at;
                        partial UNIQUE(connection_id, social_account_id, kind) WHERE status='active'
publication_uploads     (see §4.1); partial UNIQUE(job_id) WHERE phase NOT IN terminal; index(next_action_at)
social_accounts        += connection_id FK SET NULL, connected_at, last_verified_at, disconnect_reason,
                          page_tasks jsonb (nullable; what Meta reported)
```

Rules honoured: UUID keys, timezone-aware timestamps, named constraints by convention,
`organization_id` on every tenant table, VARCHAR + CHECK enums, working `downgrade` (drop the
four tables and the five columns), `tests/test_migrations.py` model/migration parity. No
backfill is needed; existing rows are unaffected. The enums module gains
`CredentialKind`, `CredentialStatus`, `ConnectionStatus`, `UploadPhase`, `OAuthStateResult`
mirrored in `packages/shared-types/src/domain.ts`.

## 6. Publisher worker and retry design

- **Process**: `python -m affilio.worker.publisher` (separate module; the existing `runner.py`
  is untouched), container `affilio-publisher` built from the same image, command
  `publisher`, limits `PUBLISHER_CPUS=0.5`, `PUBLISHER_MEMORY=256M`, only the finals mount
  (read-only) — no originals/proxies mounts, no thumbnails/exports write mounts.
- **Loop**: every `PUBLISHER_POLL_INTERVAL_SECONDS` (default 10): (a) claim up to
  `PUBLISHER_MAX_CONCURRENT_UPLOADS` (default 1) new jobs and drive them, (b) advance due
  upload rows (`next_action_at <= now`), (c) once per hour: token validation, `oauth_states`
  sweep, expired-session sweep (§7). Heartbeat file as today for the container healthcheck.
- **Bounded resource use**: one streaming upload at a time (I/O bound, no transcoding, 4 MiB
  buffer, `httpx` timeouts: connect 10 s, write 60 s per chunk, read 60 s, total per upload
  `file_size / 1 MiB/s` with a floor of 10 min); per-Page serialization (never two uploads to
  the same Page concurrently); no ffmpeg in this process.
- **Retries**: transient Graph errors (`is_transient`, codes 1/2, HTTP 5xx, network) retry the
  *current phase* with backoff; permanent errors (100, 190, 200-series permission, 368, media
  rejects) fail the attempt immediately with Meta's message. Rate-limit errors (4, 17, 32,
  613) and `X-App-Usage`/`X-Business-Use-Case-Usage` above 90 % pause the account's queue for
  one hour (`social_accounts.rate_limited_until` kept in memory + audit; no schema). The
  finish call is never retried (§4.3). Error 506 ("Duplicate posts cannot be published
  consecutively") is treated as permanent for the attempt and surfaced verbatim.
- **Publishing quota**: the Reels API allows 30 API-published posts per 24-hour moving window
  (feasibility B10). The publisher counts `published` upload rows per Page in the last 24 h
  and holds further jobs (`release_held`, reason `reels_quota`) instead of letting Meta reject
  them; the Composer shows the remaining quota per Page.
- **Webhooks**: none in M3B.1; polling with `status` is sufficient for this volume
  (feasibility B9). The design leaves room for a Page webhook receiver later, but it would
  require public inbound HTTPS, which the Synology deployment does not have.
- **Observability**: structured logs with `job_id`, `upload_id`, `phase`, `fbtrace_id`;
  per-phase durations in `publication_uploads.last_status`; the queue UI shows phase and
  `next_action_at`.

## 7. Security prerequisites and debt (summary; details in `M3B-plan.md` §7)

| Item | Development blocker | Production blocker | Proposal |
| --- | --- | --- | --- |
| HTTPS redirect URI | yes: Meta enforces HTTPS and Strict Mode on redirect URIs (feasibility A2); whether `localhost` is exempt is unconfirmed | yes | DSM reverse proxy + certificate on a real hostname; `META_OAUTH_REDIRECT_URI` fixed |
| Credential encryption key | yes (flow refuses to store without a key) | yes | `CREDENTIAL_ENCRYPTION_KEYS` + rotation script + out-of-band backup |
| Service token acts as owner | no | should fix | narrow the service identity to `media:scan` and `publishing:release` scopes; the publisher uses the DB, not the HTTP API |
| In-process login limiter | no | acceptable at `UVICORN_WORKERS=1`; document | DB-backed `login_attempts` only if workers > 1 |
| Session cleanup | no | should fix | publisher's hourly sweep deletes sessions expired/revoked > 30 days and consumed/expired `oauth_states` |
| Admin session revocation | no | should fix before exposing OAuth connect | `POST /auth/members/{id}/sessions/revoke`; suspending a user revokes all sessions |
| Reverse proxy trust | no | review | API keeps `TRUST_PROXY_HEADERS` only in compose; never derive redirect URI from headers; uvicorn `--forwarded-allow-ips` limited to the compose network |
| Backup/restore of credentials | no | yes (runbook) | §2.5 |
