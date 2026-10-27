# Meta app activation checklist (switching from mock to live)

Use this once a Meta developer app exists. Nothing here changes code or the database: the
live provider is already implemented behind the same adapter the mock uses.

## Before touching the NAS

- [ ] Meta app created per `meta-app-setup.md` §3–§6 (Business app, Facebook Login, Strict
      Mode, Enforce HTTPS, redirect URI, app roles for every AFFILIO admin who will connect).
- [ ] The AFFILIO host is reachable by the admins' browsers over **HTTPS** at the hostname used
      in the redirect URI (DSM reverse proxy + certificate). Meta's servers never call it.
- [ ] `SESSION_COOKIE_SECURE` left unset (TLS) so the admin session survives the redirect.
- [ ] Vault key generated (`openssl rand -base64 32`) and stored in the operator's password
      manager **before** it is put into `.env`.

## `.env` on the NAS (`infrastructure/docker/synology/.env`)

- [ ] `META_APP_ID`, `META_APP_SECRET` from App Dashboard → Settings → Basic.
- [ ] `META_OAUTH_REDIRECT_URI=https://<host>/api/v1/accounts/connect/facebook/callback`,
      byte-identical to the dashboard entry.
- [ ] `META_GRAPH_API_VERSION` pinned (v25.0 until 2028-07-29, or v26.0).
- [ ] `META_LOGIN_CONFIG_ID` only if the dashboard forced Facebook Login for Business.
- [ ] `CREDENTIAL_ENCRYPTION_KEYS=v1:<key>` and `CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID=v1`.
- [ ] `META_PROVIDER_MODE` is **not** set to `mock` (the compose file forces `live`; the API
      refuses to start with mock outside development).
- [ ] `docker compose up -d`; `curl /api/v1/system/info` shows
      `integrations.meta = {mode: "live", configured: true}`.

## First live run (test Page)

Record every observation (redacted) in `docs/integrations/meta-live-test-notes.md`.

- [ ] Accounts page: the "Connect Facebook Page" button is enabled and no MOCK badge exists.
- [ ] Connect as an admin who holds an app role: consent screen shows exactly the requested
      permissions; callback lands on the Page selection screen.
- [ ] Connection card shows the Meta user, granted scopes, `token_expires_at` ≈ +60 days,
      no missing scopes.
- [ ] Discovered Pages list the expected `tasks`; a Page without `CREATE_CONTENT` is not
      selectable.
- [ ] Link one test Page: account becomes **Connected** (not mock), `permission_status = ok`,
      credentials endpoint shows `meta_page` v1 `active` with `expires_at = null`.
      Note the actual `/debug_token` values in the test notes (feasibility unknown #12).
- [ ] "Verify" on the Page and on the connection succeed and update the timestamps.
- [ ] Remove the app from the Meta user (Facebook → Settings → Apps and websites), then
      "Verify": the Page flips to **Disconnected (token invalid)**.
- [ ] Reconnect: same `social_accounts` row, credential v2 active, v1 superseded.
- [ ] Revoke the connection from AFFILIO: Meta shows the app removed; all Pages disconnected.
- [ ] Database check: `SELECT key_id, status, count(*) FROM account_credentials GROUP BY 1,2`
      — only ciphertext columns exist; a search of the API logs for `EAA` finds nothing.
- [ ] Back up `.env` (key) and the database; restore on a scratch host without the key and
      confirm the accounts show `credential_undecryptable` and "Reconnect" works.

## Unknowns to close on this run (from `facebook-reels-feasibility.md` §4)

- [ ] #4 whether `localhost` works as a redirect URI for role-holders without HTTPS (dev).
- [ ] #5 which permissions the dashboard forced (`business_management`?) and whether the
      login product is Facebook Login or Facebook Login for Business.
- [ ] #7 the deauthorize callback payload, if the callback URL was configured.
- [ ] #12 `expires_at` values from `/debug_token` for user and Page tokens.
- [ ] Whether `/me/accounts` returned `tasks` for every Page (the UI treats a missing field as
      "unknown" and lets the first publish decide).

## Rollback

Unset the four `META_*` values and restart: the accounts page returns to "not configured",
existing connections stay stored (encrypted) and can be verified or revoked later.
