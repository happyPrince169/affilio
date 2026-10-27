# Security design (M3A)

## Identity

- **Local accounts.** Users sign in with email + password. Passwords are hashed with
  argon2id (`argon2-cffi`, 64 MiB, t=3, p=2) in `affilio.core.security`; hashes are never
  returned or logged. A dummy hash is verified for unknown emails so timing does not reveal
  valid accounts. Minimum password length is 10.
- **Server-side sessions.** Login creates a `user_sessions` row holding only the SHA-256 of a
  256-bit random token. The raw token is set as the `affilio_session` cookie: `HttpOnly`,
  `SameSite=Lax`, `Path=/`, `Secure` outside development/test (`SESSION_COOKIE_SECURE`
  overrides for plain-HTTP LAN deployments). Sessions slide (`SESSION_TTL_DAYS`, default 14),
  can be revoked, and are revoked wholesale on password change (except the current one).
  Nothing identity-related is stored in `localStorage` or exposed to JavaScript.
- **Service identity.** `X-Affilio-Admin-Token` (constant-time compared with
  `MEDIA_ADMIN_TOKEN`) authenticates CLI/cron calls such as scan triggers and
  `release-due`. It acts as `owner` of `DEFAULT_ORGANIZATION_SLUG` (or the header-selected
  organization) and is reported as `identity_kind = service`. Browsers never hold it: the
  Next.js proxy does not inject it any more.

## Authorization

`affilio/api/deps.py` resolves an `Identity` for every request under `/api/v1` except health,
system info and login/logout. Resolution fails closed:

1. Session cookie → user (401 `authentication_required` if missing, expired, revoked,
   tampered, or the user is not active).
2. Organization = the one named by `X-Organization-Slug` **if the user is a member**, else the
   user's first membership; a non-member slug is 403 `forbidden` with the slug echoed.
3. Role from `organization_memberships`: `viewer < editor < admin < owner`.
4. Otherwise the service token, otherwise 401.

Routers declare the minimum role with `CurrentIdentity` (any role), `RequireEditor`
(create/edit/submit/schedule/start/confirm/fail/retry/cancel, product/procurement/production
writes) or `RequireAdmin` (approve, revoke approval, reconcile, account registry changes,
member management, purchase-request decisions, handoff review, scan triggers, audit log).
`GET /auth/me` returns the effective permissions so the UI hides what the API would refuse;
the API remains the enforcement point. Service functions take `organization_id` on every
query, so a valid identity still cannot read or write another tenant's rows (404).

## CSRF

Cookie-authenticated unsafe requests (anything but GET/HEAD/OPTIONS) must carry an `Origin`
that is either in `CORS_ORIGINS` or equal to the request host (`X-Forwarded-Host` when the
Next.js proxy is in front). A `Sec-Fetch-Site: cross-site` request is rejected the same way
(403 `csrf_rejected`). `SameSite=Lax` is the second layer. Reads are never blocked.

## Web proxy

`apps/web/src/app/api/[...path]/route.ts` forwards `cookie`, `origin`, `sec-fetch-site` and
`user-agent` to the API, sets `X-Forwarded-Host/Proto/For`, passes every `Set-Cookie` back,
and attaches no credentials of its own. An unauthenticated browser request therefore reaches
the API unauthenticated.

## Audit

`audit_events` records actor kind (`user` / `service` / `system`), actor user id and label,
action, entity type/id, structured data and the request id for logins, logouts, password
changes, member and organization changes, account registry changes, link changes and every
publication transition. Admins read it via `GET /auth/audit`.

## Secrets and tokens

- Platform tokens live only in the credential vault below; `social_accounts` has no
  credential column.
- Settings that are secrets are `SecretStr`; `.env.example` holds placeholders only.
- Passwords are never accepted on the command line: `affilio.scripts.create_user` reads them
  from `AFFILIO_NEW_USER_PASSWORD` or an interactive prompt.

## Login rate limiting (pre-M3B hardening)

`POST /auth/login` counts **failed** attempts per client IP (the socket peer, or the first
`X-Forwarded-For` hop only when `TRUST_PROXY_HEADERS=true`, which the Synology compose sets
because the API has no published port) and per email in a sliding window
(`AUTH_LOGIN_MAX_ATTEMPTS_PER_MINUTE`, default 10, over `AUTH_LOGIN_WINDOW_SECONDS`,
default 60). Over the limit the endpoint answers 429 `too_many_attempts` with a
`Retry-After` header and performs no password verification. A successful sign-in clears the
email bucket. Failed attempts are audited (`auth.login_failed`, organization-less).
The limiter is in-process (`affilio.core.ratelimit`): the Synology deployment runs one
uvicorn worker, so the limit is exact there; with N workers it is N × limit. A shared store
is deliberately not introduced.

## Credential vault (M3B.1A)

- **Where.** `account_credentials` holds one row per token version: `ciphertext`, `nonce`,
  `key_id`, a SHA-256 `token_fingerprint`, scopes, lifetimes and status. The plaintext exists
  only inside `accounts/vault.py::open_token` and is handed straight to a provider call.
- **How.** AES-256-GCM (`cryptography`) with a 96-bit random nonce. The associated data binds
  each ciphertext to organization, connection, subject (Meta user or Page id), credential kind,
  key id and version, so a ciphertext copied to another row fails authentication.
- **Keys.** `CREDENTIAL_ENCRYPTION_KEYS` is a key ring (`key_id:base64(32 bytes)`, comma
  separated); `CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID` names the key used for new encryptions
  (defaults to the only key). Keys come from the environment / `.env` (mode 600) and are never
  in the database, logs, audit data or `/system/info`.
- **Fail closed.** Without a usable ring the connection endpoints answer 503
  `credential_vault_unavailable` and store nothing. A row whose key id is unknown or whose
  tag does not verify is `credential_undecryptable`: the Page is marked disconnected
  (`disconnect_reason`) and must be reconnected; no plaintext fallback exists.
- **Rotation.** Add `v2:<key>`, set the active id to `v2`, restart, run
  `python -m affilio.scripts.rotate_credential_keys` (re-encrypts active rows in one
  transaction; `--check` lists rows per key id), then remove `v1` once no row uses it.
- **Versioning and revocation.** Reconnecting stores a new version and marks the previous
  row `superseded`; disconnect marks it `revoked`; a provider rejection (190) marks it
  `invalid` with the reason. History is kept; only one row per subject is `active`
  (partial unique index).
- **Backup and restore.** Database dumps contain ciphertext only. Back the key ring up
  separately (password manager / DSM Key Manager). Restoring a dump without the key leaves
  every connected Page as `credential_undecryptable` → "Reconnect"; tokens are re-issuable by
  Meta, so key loss is an outage, not data loss. Test the restore path before relying on it.

## Meta connections (M3B.1A)

- **Flow.** Admin → `POST /accounts/connect/facebook` creates an `oauth_states` row (256-bit
  random state, SHA-256 stored, 10-minute TTL, bound to the user, session and organization)
  and returns the authorize URL; the browser navigates there; the callback
  (`GET …/callback`, cookie-authenticated, admin) consumes the state exactly once
  (`UPDATE … WHERE consumed_at IS NULL`), exchanges the code server-side, inspects the token
  (`/debug_token`: app id, scopes, expiry), discovers Pages, stores the user and Page tokens
  encrypted, and answers with a redirect only. Denials, replays, expired or foreign states,
  forged codes and provider errors all end as an audited attempt plus a redirect carrying an
  error code; nothing is stored.
- **Redirect URI.** `META_OAUTH_REDIRECT_URI` is fixed configuration (https outside
  development), never derived from request headers; it must match Meta's Strict Mode entry.
- **Binding.** A `platform_connections` row is unique per (organization, provider, Meta user).
  Pages become `social_accounts` rows (`source=oauth`, `is_verified=true`,
  `status=connected`); an existing manual row with the same Page id is upgraded in place.
  Reconnecting re-binds every OAuth row automatically and versions the credentials.
- **Health.** "Verify" asks the provider whether a stored token still works; an invalid
  token marks the Page (or the whole grant) disconnected with a reason. Disconnect and revoke
  are admin actions; revoke also calls the provider's permission revocation when possible.
- **Mock provider.** `META_PROVIDER_MODE=mock` (refused outside development/test) replaces the
  Graph API with a deterministic in-process provider. Mock tokens are prefixed `mock:` and
  rejected by the live provider; every mock connection, credential and account carries
  `is_mock=true` and is labelled as mock in the API and UI. Mock-only endpoints answer 404
  `mock_mode_disabled` in live mode.
- **Audit.** `account.connect_started/completed/denied/failed`, `account.page_linked`,
  `account.verified`, `account.token_invalid`, `account.disconnected`,
  `connection.verified/token_invalid/revoked`, `mock.action` — never with a token.

## Publishing engine (M3B.2A)

- The publisher decrypts a Page token only inside one step and hands it to the provider call;
  it is never written to `publication_uploads`, audit data, logs or responses. Upload rows
  expose provider ids and error codes only.
- Live publishing is refused for real Pages while `META_LIVE_PUBLISHING_ENABLED=false`
  (`publishing/eligibility.py`), mock Pages are refused in live mode, and the mock and live
  providers reject each other's tokens.
- Blocked targets, reclassified videos, disconnected Pages, revoked approvals and held Pages
  are re-checked at dispatch and at every step; a job that leaves `PUBLISHING` has its upload
  abandoned before any publish request.
- `reconcile-provider` is admin-only and never re-sends the publish request; the queue and
  engine status are organization-scoped like every other read.

## Service token scope (M3B.1A)

`X-Affilio-Admin-Token` may only call the allow-listed internal operations
(`api/deps.py::SERVICE_TOKEN_ALLOWED`): `GET /auth/me`, `GET /media/roots`, scan triggers and
status (`/media/scans…`), and `POST /publishing/queue/release-due`. Any other route answers
403 `service_scope_denied` even with a valid token.

## Session hygiene (M3B.1A)

- The worker runs `affilio.worker.maintenance` hourly (`MAINTENANCE_INTERVAL_SECONDS`) and
  `python -m affilio.scripts.maintenance` runs it once: sessions expired or revoked for more
  than `SESSION_RETENTION_DAYS` and finished/expired OAuth attempts older than
  `OAUTH_STATE_RETENTION_HOURS` are deleted. Live sessions are never touched.
- `POST /auth/members/{id}/sessions/revoke` (admin) signs a member out everywhere; owners can
  only be signed out by an owner. Audited as `member.sessions_revoked`.

## Review notes (pre-M3B)

| Area | State | Note |
| --- | --- | --- |
| Session expiry | sliding 14 days, touched at most every 15 min | expired/revoked rows purged by the worker (M3B.1A) |
| Revocation | logout revokes the session; password change revokes all others; suspended users fail resolution; admin "sign out everywhere" (M3B.1A) | — |
| Cookie | HttpOnly, SameSite=Lax, Path=/, Secure unless `SESSION_COOKIE_SECURE=false` | the Synology example sets `false` for plain-HTTP LAN; switch to unset once TLS terminates on the DSM reverse proxy |
| CSRF | Origin / Sec-Fetch-Site check on cookie writes + Lax | `X-Forwarded-Host` is trusted from the web proxy only because the API has no published port |
| Roles | enforced per router (`RequireEditor` / `RequireAdmin`), services scope by organization | viewer cannot mutate anything; owner is only assignable through the seed or SQL |
| Service token | owner role **only on the allow-listed routes** (scans, release-due, `/auth/me`) | narrowed in M3B.1A |
| Seed credentials | `seed_dev` refuses outside development/test; production users via `create_user` with password from env or prompt | `SEED_ADMIN_PASSWORD` default is documented as a local placeholder and never used in production |

## Known limitations

- No password reset flow; an admin resets with `create_user --reset-password`.
- Session cookies are host-scoped: in development the web app and the API must be reached
  through the same host name (`localhost:3000` and `localhost:8000`), or use the same-origin
  proxy (`NEXT_PUBLIC_API_URL=""`).
- The login rate limiter is in-process (exact with one uvicorn worker).
