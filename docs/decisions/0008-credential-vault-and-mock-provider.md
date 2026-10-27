# ADR 0008 — Encrypted credential vault, provider adapter, and a mock Meta provider

- **Status:** Accepted (M3B.1A, 2026-10-28)
- **Context:** M3B publishes Reels through the Graph API, which needs Page access tokens stored
  server-side for months. No Meta developer app exists yet, so development cannot wait for
  real tokens, yet the schema and the security model must not be redesigned when the live
  integration arrives. Meta's Platform Terms require "administrative, physical, and technical
  safeguards" for tokens and forbid sharing them (`facebook-reels-feasibility.md`, A8).
- **Decision:**
  1. **Vault.** Tokens live only in `account_credentials`, encrypted with AES-256-GCM
     (`cryptography`) under a versioned key ring from `CREDENTIAL_ENCRYPTION_KEYS`. Each
     ciphertext is bound by AEAD associated data to its organization, connection, subject,
     kind, key id and version, so rows cannot be swapped or moved. Decryption happens only in
     `accounts/vault.py` and the plaintext is passed straight to a provider call; no router,
     schema, log or audit record ever contains it. Without a key the vault refuses to work
     (503 `credential_vault_unavailable`): there is no plaintext fallback.
  2. **Models.** A `platform_connections` row per Meta user grant, `account_credentials`
     versions (one active per connection+subject+kind, older ones `superseded`), and
     `oauth_states` for one-time, expiring, user-bound authorization attempts. The existing
     `social_accounts` row remains the single Page identity and gains health columns
     (`connection_id`, `connected_at`, `last_verified_at`, `disconnect_reason`, `page_tasks`,
     `is_mock`); manual registrations are upgraded in place, never duplicated.
  3. **Provider adapter.** `accounts/providers/base.py` defines the only interface the service
     uses. `LiveMetaProvider` implements the documented Graph API calls with `appsecret_proof`
     and typed errors (190 → `TokenInvalidError`, throttling/5xx → `ProviderUnavailableError`).
     `MockMetaProvider` is a deterministic in-process stand-in with named scenarios and
     runtime switches (revoke, outage). `get_provider(settings)` is the only factory.
  4. **Mock isolation.** `META_PROVIDER_MODE=mock` is refused by settings validation outside
     `development`/`test`. Mock tokens carry a `mock:` prefix that the live provider rejects
     (and the mock provider rejects anything else); every mock connection, credential and
     account is flagged `is_mock` and labelled as such in API responses and the UI, and mock
     accounts keep `api_publishing=false`.
  5. **Service token scope.** The CLI/cron token is confined to an explicit allow-list
     (scan triggers and status, release-due, its own identity); everything else answers 403
     `service_scope_denied`.
- **Consequences:** development and automated tests exercise the full connection lifecycle
  with no Meta app; activating the live provider is configuration
  (`meta-app-activation-checklist.md`) plus the first live run, with no migration. The
  encryption key becomes an operational secret with its own backup; losing it costs a reconnect
  of every Page, not data. Key rotation is a documented, scripted procedure
  (`scripts/rotate_credential_keys.py`). Publishing itself (upload sessions, publisher worker)
  is M3B.1B and keeps the design in `facebook-reels-architecture.md`.
