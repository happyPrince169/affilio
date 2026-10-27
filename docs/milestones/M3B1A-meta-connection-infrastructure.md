# M3B.1A — Meta connection infrastructure (offline-first)

- **Status:** Implemented 2026-10-28; verified locally (see §5); awaiting acceptance.
- **Depends on:** M3B.0 research (`docs/integrations/`), pre-M3B hardening (`6378300`).
- **Decision record:** ADR 0008.
- **Out of scope (M3B.1B):** uploads, the publisher worker, `api_publishing=true`, Meta-side
  scheduling. No Reel is published by this milestone and no Meta API is called in tests.

## 1. What was implemented

| Area | Delivered |
| --- | --- |
| Credential vault | `accounts/crypto.py` (AES-256-GCM, versioned key ring, AAD row binding, fail-closed), `accounts/vault.py` (store/open/invalidate/rotate), `scripts/rotate_credential_keys.py` |
| Models + migration 0008 | `platform_connections`, `account_credentials` (versioned), `oauth_states`; `social_accounts` += `connection_id`, `connected_at`, `last_verified_at`, `disconnect_reason`, `page_tasks`, `is_mock`; working downgrade; parity test |
| Provider adapter | `accounts/providers/{base,live,mock}.py`; `get_provider(settings)`; live client implements the documented calls with `appsecret_proof` (tested with `httpx.MockTransport`, never run against Meta) |
| OAuth infrastructure | `accounts/connections.py` + `connect_router.py`: start (random 256-bit state, SHA-256 stored, 10-min TTL, user+organization bound), callback (one-time consumption, denial, tampering, provider errors → audited attempt + redirect with an error code), Page discovery, linking (manual rows upgraded in place), verify, disconnect, reconnect, revoke, credential metadata, attempts list |
| Mock provider | Scenarios `success`, `missing_permissions`, `no_pages`, `denied`, `outage`, `invalid_app`; runtime switches (revoke page/user token, outage); `mock:` token prefix refused by the live provider; `is_mock` flag end to end; mode refused outside development/test |
| Security debt | Service token allow-list (403 `service_scope_denied` elsewhere); `purge_sessions` + `purge_oauth_states` run hourly by the worker and via `scripts/maintenance.py`; `POST /auth/members/{id}/sessions/revoke` (admin; owners only by owners) |
| Frontend | Accounts page: Connect Facebook (config-aware, MOCK badge + scenario picker), connection cards (status, scopes, missing scopes, expiry, verify/revoke/choose Pages), per-Page connection status, permission status, last verified, Verify / Disconnect / Reconnect, config error notice with missing setting names, Page selection route `/accounts/connect/facebook/[connectionId]`; Settings: "Sign out everywhere" |
| Configuration | `META_*`, `CREDENTIAL_ENCRYPTION_*`, `WEB_BASE_URL`, retention settings in `core/config.py`, both `.env.example` files, the Synology compose file (live mode forced), `/system/info.integrations.meta` |
| Docs | ADR 0008, `security.md`, `database.md`, `modules.md`, `synology.md`, README, `meta-app-activation-checklist.md`, this document |

## 2. Endpoints

```
GET  /accounts/connect/facebook/config          any role   mode, configured, vault_ready, missing names
POST /accounts/connect/facebook                 admin      {mock_scenario?} → {authorize_url, state_expires_at}
GET  /accounts/connect/facebook/callback        admin      state/code → 303 to the web app (never JSON tokens)
GET  /accounts/connect/facebook/attempts        admin      recent OAuth attempts
POST /accounts/connect/facebook/mock/actions    admin      mock mode only (404 otherwise)
GET  /accounts/connections[/{id}[/pages]]       any role   grants and discovered Pages (no tokens)
POST /accounts/connections/{id}/pages           admin      link Pages → connected accounts
POST /accounts/connections/{id}/verify|revoke   admin
POST /accounts/{id}/disconnect|verify           admin
GET  /accounts/{id}/credentials                 admin      metadata only
POST /auth/members/{id}/sessions/revoke         admin
```

## 3. Acceptance criteria (from the brief) and how they are met

1. *Start locally without a Meta app, enable mock mode, connect Pages, verify the lifecycle.*
   `META_PROVIDER_MODE=mock` + `CREDENTIAL_ENCRYPTION_KEYS` in `.env`; the accounts page
   shows a MOCK badge and scenario picker; the smoke run in §5 walked connect → choose Pages →
   verify → simulate revoke → verify (disconnected) → reconnect → revoke grant.
2. *Production rejects mock credentials and never shows a mock Page as real.* Settings refuse
   mock mode outside development/test (test `test_mock_mode_is_refused_outside_development`);
   the live provider refuses `mock:` tokens before any network call; `is_mock` is on the
   connection, the credential and the account, and the label reads "Mock connection
   (development only, not a real Page)".
3. *Replace the mock with the live adapter without redesigning the database.* Both providers
   implement one interface; the live client exists and is unit-tested; activation is
   configuration (`meta-app-activation-checklist.md`).

## 4. Tests added

`test_credential_vault.py` (10), `test_meta_live_provider.py` (7), `test_meta_connections.py`
(18), `test_security_debt.py` (5); `test_migrations.py` extended with the new tables;
`test_products_api.py` updated for the narrowed service token.

## 5. Verification record (2026-10-28)

See the completion report for the exact command output. Commands executed:
`uv run pytest` with `TEST_DATABASE_URL` (full backend suite), `pnpm lint`, `pnpm typecheck`,
`pnpm build`, `docker build` for both images, and a Playwright smoke run through the mock
connection lifecycle in the browser.

## 6. Limitations and next step

- The live provider has never talked to Meta; the first live run follows the activation
  checklist and records the unknowns from the feasibility report.
- Mock switches are process-local (one API process): sufficient for development and tests.
- `api_publishing` stays `false` for every account until M3B.1B implements upload sessions
  and the publisher worker (`facebook-reels-architecture.md` §3–§6).
- Next: M3B.1B — Reels upload/publish through the connected Pages (API attempts, upload
  sessions, publisher container, reconciliation).
