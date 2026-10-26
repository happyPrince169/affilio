# M3 — Publishing Workflow

> Split in October 2026 into **M3A — Distribution Core & Publishing Workflow**
> (`M3A-distribution-core.md`, implemented) and **M3B — Meta API publishing**
> (`M3B-meta-api-prerequisites.md`, planned). The original scope below is kept for history;
> M3A delivered authentication, the account registry, campaigns, approvals, UTC scheduling and
> assisted publishing with operator-reported evidence. Facebook Reels replaced Shopee Video as
> the first target because Meta offers an official, documented publishing API path.

- **Status:** Superseded by M3A / M3B
- **Depends on:** M0, M1, M2 accepted; authentication + RBAC (delivered within this
  milestone or immediately before); **confirmation of Shopee Video API availability and
  permissions**
- **Unlocks:** M4 (published content identity for attribution), M5 (agent proposals)

## Scope

Plan, review, approve, schedule, and publish content. Content is platform-agnostic with
per-platform variants. Publishing to Shopee Video is **assisted**: if an official API with
the required permissions is confirmed, AFFILIO publishes through it; otherwise AFFILIO
produces a manual publish checklist (caption, assets, links) and records the manual publish.

## Deliverables

- Authentication (decide local password vs. OIDC; fill `users.password_hash` or drop it),
  organization context resolution, `require_role` dependency.
- `content` module: `content_items`, `content_variants` (per platform: caption, hashtags,
  asset refs, product refs), state machine draft → review → approved → scheduled → published
  → failed, calendar queries.
- `publishing` module: `publish_jobs`, `account_credentials` (encrypted at rest, never
  returned by API), Shopee client **only if official**, manual-publish recording.
- `accounts`: connect/disconnect flows via official OAuth where provided; honest status.
- Web: Publishing Calendar (month/week), content editor, approval UI, Accounts page.

## Acceptance criteria

- A content item moves through every state with an audit trail.
- Scheduled items are either published via the official API or surfaced as a manual
  checklist, and the resulting external post id (if any) is stored.
- Credentials are encrypted, rotatable, and never logged or exposed.
- No unofficial API, browser automation, or cookie replay exists in the codebase.

## Known risks

- **Primary:** Shopee Video publishing API may be unavailable or restricted. The manual
  checklist path is the fallback and must be first-class, not an afterthought.
- Scheduling needs a reliable trigger → likely the point where a worker + Redis is justified.
- Multi-platform variants add UI complexity; keep Shopee-only in the first iteration.
