# M3B — Meta API publishing: prerequisites and plan

- **Status:** Planned (not started). Nothing here is implemented in M3A.
- **Depends on:** M3A accepted; Meta developer app approved for the required permissions.

## What M3A already provides

- `BasePublishingAdapter` contract with `capabilities / validate / prepare / publish /
  get_publication_status / reconcile_publication`; the service layer interprets
  `PublishOutcome` and never marks PUBLISHED without an external id or URL.
- `evidence_kind = api_verified` reserved; `publication_attempts.mode = api` reserved.
- `social_accounts.is_verified`, `source = oauth`, `status = connected` reserved for a
  successful authorization; no credential columns on the account row.
- Durable intents (`intent_id`) suitable for idempotent upload/publish calls.

## Prerequisites (outside the codebase)

1. A Meta developer app in the operator's Business Manager with **Facebook Login for
   Business** and the permissions needed to publish Reels to a Page (`pages_show_list`,
   `pages_read_engagement`, `pages_manage_posts`, `publish_video`/Reels-specific scopes as
   current Meta docs require). App Review approval for those permissions.
2. Confirmation, against current Meta documentation, of the Reels publishing endpoints,
   upload protocol (resumable upload), scheduling parameters and status polling, plus rate
   limits. Nothing is assumed from memory: capability facts in `PlatformCapabilities` must be
   updated only after verification.
3. A decision on token storage: a separate `account_credentials` table encrypted at rest
   (application-level key from settings, rotated), never returned by the API, never logged.
4. An HTTPS origin for the OAuth redirect URI (the LAN deployment needs TLS or a tunnel).

## Planned work

- `accounts`: OAuth start/callback endpoints, Page selection, token exchange (long-lived Page
  tokens), encrypted storage, disconnect/revoke, `is_verified=true` + `status=connected`.
- `publishing/adapters/facebook.py`: implement `publish` (upload + publish or schedule),
  `get_publication_status`, `reconcile_publication` (status lookup by id); set
  `api_publishing=True`, `scheduling_via_api`, `status_lookup_via_api` accordingly.
- Service: API attempts (`mode=api`), `api_verified` evidence, automatic status reconciliation
  of `NEEDS_RECONCILIATION` jobs via the adapter (still no blind retry), worker tick that
  releases due jobs and starts API attempts for connected accounts only.
- UI: connect button only when OAuth is configured; per-account capability badges; API vs
  assisted mode per publication.

## Non-goals carried forward

- No unofficial endpoints, browser automation, cookie replay or scraping.
- Shopee Video publishing stays assisted until an official API is confirmed.
