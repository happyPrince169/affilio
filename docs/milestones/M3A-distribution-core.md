# M3A — Distribution Core & Publishing Workflow

- **Status:** Implemented, verified locally (2026-10-26); awaiting acceptance
- **Depends on:** M0, M1, M2A, M2B accepted
- **Unlocks:** M3B (Meta API publishing behind the same adapter contract), M4 (published
  content identity for attribution)
- **Design docs:** `docs/architecture/distribution.md`, `docs/architecture/security.md`,
  ADR 0006 (sessions + RBAC), ADR 0007 (assisted publishing + snapshots)

## Scope

Turn approved final videos into publication intents for several Facebook Pages (or other
registered accounts), with unique content per target, human approval, UTC scheduling, and an
assisted publishing loop where the operator publishes in the platform UI and records the
result. No live publishing, no Meta API calls, no connected accounts.

## Delivered

### Authentication and authorization
- Email + password login (argon2id), server-side sessions (`user_sessions`, hashed tokens),
  `HttpOnly; SameSite=Lax; Secure` cookie, logout, password change (revokes other sessions).
- `Identity` resolution in `api/deps.py`: session → membership-checked organization → role;
  service token only for CLI/cron; fail closed (401/403); Origin/Sec-Fetch-Site CSRF check.
- Roles `viewer < editor < admin < owner` enforced on every router; existing media, products,
  procurement and production routers migrated (`RequireEditor` / `RequireAdmin`).
- Members and organization settings (`/auth/members`, `/auth/organization`), `GET /auth/me`
  with effective permissions, audit log (`audit_events`, `GET /auth/audit`).
- Next.js proxy is identity-neutral (forwards cookies, never injects the service token);
  `apiFetch` sends credentials; `RequireAuth` guard + `/login` page; user menu; Settings page.
- Dev seed creates a local owner from settings; `affilio.scripts.create_user` for others.

### Social account registry
- `social_accounts` extended (handle, type, capabilities, `is_verified=false`, `source`,
  profile URL, notes). Manual registration is always `disconnected` + unverified; status can
  only be toggled disabled ↔ disconnected. Accounts page with honest labels.

### Content: affiliate link library
- `affiliate_links` with http(s)-only validation, normalized duplicate detection (partial
  unique index on active links), archive/restore, optional product reference. Links page.

### Publishing
- Campaigns (`campaigns`, `campaign_media` restricted to approved finals, `campaign_targets`).
- `publication_jobs` with unique `(campaign, media, account)`, stable `intent_id`, optional
  `idempotency_key`, optimistic `version`, `content_version`, ordered `publication_links`.
- Ten-state machine (`lifecycle.py`), immutable `publication_snapshots` on approval, edit →
  DRAFT invalidation, UTC scheduling with conflict detection (`PUBLICATION_MIN_GAP_MINUTES`,
  audited override), `release-due`, queue, calendar window, stats.
- Evidence-gated PUBLISHED (`operator_reported`), duplicate-evidence rejection, attempts
  history, explicit retry only, `NEEDS_RECONCILIATION` resolved by admins.
- Adapter contract (`BasePublishingAdapter`), `AssistedPublishingAdapter`,
  `FacebookReelsAdapter` placeholder with explicit capabilities; no network I/O.
- Distribution Center UI: Campaigns, Composer (one final → unique content per Page),
  Calendar (day/week/month in organization timezone), Queue, Links, Publication detail with
  checklist (copy caption/links, open Page), confirm / report problem / reconcile dialogs.

### Database
- Migration `0005_distribution_core` with working downgrade (verified by the migration tests).

## Acceptance criteria → verification

| Criterion | Evidence |
| --- | --- |
| Unauthenticated requests are refused | `tests/test_auth.py::test_unauthenticated_requests_fail_closed`, smoke step 1 |
| Cross-organization access refused | `test_cross_organization_access_is_refused`, `test_products_scoped_by_organization_header` |
| RBAC enforced at the API | `test_role_hierarchy_enforced_at_api`, `test_publishing.py::test_invalid_transitions_and_rbac_on_actions`, `test_viewer_cannot_mutate_links_or_accounts` |
| Audit events carry user ids | `test_audit_trail_records_user_ids`, `test_full_assisted_workflow…` |
| Unapproved media cannot be distributed | `test_campaign_requires_approved_finals`, `test_unapproved_media_and_disabled_target_rejected` |
| Multi-Page unique content, multi-link ordering | `test_multi_page_unique_content_and_multi_link_order`, smoke steps 9–10 |
| Edit after approval invalidates approval | `test_edit_after_approval_invalidates_snapshot`, smoke step 12 |
| Invalid transitions rejected | `test_invalid_transitions_and_rbac_on_actions`, `test_full_assisted_workflow…` |
| Scheduling conflicts, UTC storage | `test_scheduling_rules_and_conflicts` |
| Idempotency and duplicates | `test_idempotency_and_duplicate_publications` |
| Concurrent updates | `test_optimistic_locking_rejects_stale_versions` |
| Manual confirmation validation, duplicate evidence | `test_full_assisted_workflow…`, `test_duplicate_evidence_is_rejected` |
| Reconciliation, no auto retry | `test_failure_retry_and_reconciliation`, smoke steps 15–16 |
| Migration drift | `tests/test_migrations.py` (upgrade, autogenerate diff = [], constraints, downgrade) |
| Adapters never publish | `test_adapters_declare_capabilities_and_never_publish`, `FakeNetworkAdapter` call counts |
| Desktop + mobile UI | Playwright smoke test (19 steps, screenshots in the session scratchpad) |

## Explicitly not done (by design)

- No Meta/Graph API calls, no OAuth, no tokens, no live publishing, no Shopee Video API.
- No automatic scheduler process: due items are released by `POST /publishing/queue/release-due`
  (UI button or cron with the service token). A worker tick can be added when M3B needs it.
- No login rate limiting / password reset; no OIDC. See `docs/architecture/security.md`.
- Not deployed to the NAS.

## M3B prerequisites

See `docs/milestones/M3B-meta-api-prerequisites.md`.
