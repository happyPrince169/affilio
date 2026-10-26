# Distribution architecture (M3A)

How AFFILIO turns an approved final video into publications on several accounts without ever
publishing on an operator's behalf. Implemented by the `accounts`, `content` and `publishing`
modules (`apps/api/src/affilio/modules/`) and the Distribution Center UI
(`apps/web/src/components/distribution/`).

## Domain model

```
Campaign ──< CampaignMedia >── MediaAsset (asset_kind=final, handoff_status=approved)
   │
   ├──< CampaignTarget >── SocialAccount (registry; manual = unverified, disconnected)
   │
   └──< PublicationJob  (one per campaign × media × account; unique)
            ├── intent_id (unique, stable), idempotency_key (unique per org, optional)
            ├── live content: title, caption, cta, hashtags[], content_notes, PublicationLink[] (ordered)
            ├── version (optimistic lock), content_version (bumped on every content edit)
            ├── status (state machine below), scheduled_at (UTC), status_changed_at
            ├── approved_snapshot_id ──> PublicationSnapshot (immutable copy of approved content)
            ├── evidence: external_post_url/id, evidence_kind, published_at, confirmed_by_*
            └──< PublicationAttempt (attempt_no, mode, adapter, status, snapshot_id, detail)
AffiliateLink (library: validated http(s) URL, normalized duplicate check, archive)
AuditEvent (actor user id/label, action, entity, request id) — written for every transition
```

Rules enforced in `publishing/service.py`, never in routers:

- Only `final` assets with `handoff_status = approved` and `status = active` can join a
  campaign or a publication (`media_not_approved`, 422).
- Disabled accounts cannot be targets (`target_unavailable`, 422). Manual accounts are
  allowed targets but stay labelled "unverified, not connected".
- `(campaign, media, account)` is unique (`duplicate_publication`, 409). The same request with
  the same `idempotency_key` returns the existing job (200 instead of 201).
- Every mutation sends the `version` it last saw; the row is locked (`SELECT … FOR UPDATE`)
  and a mismatch is `version_conflict` (409). Every mutation increments `version`.
- Affiliate links are attached by id from the library, kept in the order given, and copied
  (label + URL) into the snapshot at approval. Archived links block submission.

## State machine

```
            submit             approve                schedule            release / due
 DRAFT ─────────────► READY_FOR_REVIEW ───────► APPROVED ───────────► SCHEDULED ───────────► READY_TO_PUBLISH
   ▲   ◄───────────── (return_to_draft)            │  ▲  (unschedule)      │                      │
   │                                               │  └──────────────────────┘                      │
   │  revoke_approval / content edit (any approved state)                                            │
   │◄──────────────────────────────────────────────┴───────────────────────────────────────────────┘
   │                                                      start (APPROVED | SCHEDULED | READY_TO_PUBLISH)
   │                                                                      │
   │                                                                      ▼
   │           confirm (evidence)  ◄───────────────────────────────  PUBLISHING  ──── report_uncertain ──► NEEDS_RECONCILIATION
   │           PUBLISHED (terminal)                                       │                                     │ reconcile (admin)
   │                                                                 fail │                     published ◄─────┤─────► not_published
   │  revise                                                              ▼                                     │ still_unknown (stays)
   └◄─────────────────────────────────────────────────────────────── FAILED ◄────────────────────────────────────┘
                                                                         │ retry (explicit, needs current snapshot)
                                                                         └──────────────────────────────► READY_TO_PUBLISH
 cancel: DRAFT, READY_FOR_REVIEW, APPROVED, SCHEDULED, READY_TO_PUBLISH, FAILED ──► CANCELLED (terminal)
```

The table of actions, allowed source states and minimum roles is `publishing/lifecycle.py`
(`ACTIONS`). `allowed_actions` is returned on every publication so the UI only offers what the
server will accept.

Invariants:

- **Nothing reaches PUBLISHED without evidence.** `confirm` and `reconcile(published)` require
  an http(s) post URL whose host belongs to the target platform; the post id is derived when
  possible. Evidence is stored as `evidence_kind = operator_reported`; `api_verified` is
  reserved for M3B adapters. The same post (id or normalized URL) can confirm only one job
  (`duplicate_evidence`, 409; backed by a partial unique index per account).
- **Nothing is retried automatically.** `PUBLISHING → NEEDS_RECONCILIATION` parks the job until
  an admin checks the platform; `retry` is an explicit operator action from `FAILED` only.
- **Approval is a snapshot.** `approve` freezes title, caption, CTA, hashtags, links, media
  facts and target facts into `publication_snapshots`. Snapshots are never updated. Editing
  content afterwards bumps `content_version`, moves the job back to `DRAFT` and clears
  `approved_snapshot_id`; old snapshots remain as history. `schedule`, `start` and `retry`
  refuse to run unless the current snapshot matches `content_version`.
- **Scheduling is UTC and never publishes.** `scheduled_at` must be timezone-aware (converted
  to UTC), not in the past, and at least `PUBLICATION_MIN_GAP_MINUTES` away from another
  pending publication on the same account unless `allow_conflict` is set (the override is
  audited). `POST /publishing/queue/release-due` moves due `SCHEDULED` jobs to
  `READY_TO_PUBLISH`; the UI shows them in the queue. Display uses the organization timezone
  (`organizations.timezone`).
- **Every transition is audited** with the acting user id, the from/to status, the note, and
  the request id (`audit_events`).

## Assisted publishing

`GET /publishing/publications/{id}/prepare` builds the operator checklist from the approved
snapshot: ordered steps, the exact caption text (caption + CTA + links + hashtags), the link
list, a best-effort URL to open the Page, platform notes, and the NAS path of the final
video. The UI offers "copy caption", "copy links" and "open Page" and then records the
result:

- **I published it** → `confirm` with the post URL (operator-reported evidence).
- **It did not work** → `fail` with `outcome=failed` (nothing live) or `outcome=ambiguous`
  (unsure → `NEEDS_RECONCILIATION`).
- **Reconcile** (admin) → `published` with evidence, `not_published` with a note, or
  `still_unknown`.

Attempts record who started which attempt against which snapshot, and how it ended.

## Adapter interface

`publishing/adapters/base.py` defines `BasePublishingAdapter`:

| Method | Purpose | M3A behaviour |
| --- | --- | --- |
| `capabilities()` | Honest `PlatformCapabilities` (assisted/api publishing, scheduling, status lookup, clickable links, caption/hashtag limits) | Facebook: `api_publishing=False`, `clickable_links_in_caption=False`, 2200 chars, 30 hashtags |
| `validate_media(media)` | Final + approved + active, container/orientation/duration hints | errors block submit/approve; warnings are shown |
| `validate_content(content)` | caption required, composed length, hashtag count, link schemes | same |
| `check_capabilities({...})` | Refuse features the platform lacks | used by tests; API publishing is refused |
| `prepare_publication(...)` | Checklist + caption text + open URL from a snapshot | pure function, no I/O |
| `publish(prepared, intent_id)` | Perform publishing | returns `manual_required`; **no network** |
| `get_publication_status(...)` | Read back status | `unknown`, `verifiable=False` |
| `reconcile_publication(...)` | Interpret evidence | operator evidence → `published` / `not_published`, else `still_unknown` |

`AssistedPublishingAdapter` implements the contract for any platform without I/O.
`FacebookReelsAdapter` extends it with Reels facts and instructions; it is a placeholder for
the authorized Graph API integration planned in M3B and performs no API calls. Adapters are
resolved per platform via `adapters/registry.py`; tests swap in `FakeNetworkAdapter` through
`register_adapter`. Adapters never change job status: the service layer owns the state
machine and interprets `PublishOutcome` defensively (success without an external id is not
accepted as PUBLISHED).

## Social account registry

`social_accounts` gained `handle`, `account_type`, `capabilities` (copied from
`PLATFORM_CAPABILITIES` at registration), `is_verified` (false until OAuth), `source`
(`manual` | `oauth`), `profile_url`, `notes`, `registered_by`. Manual registration always
produces `status = disconnected`, `is_verified = false`; the only manual status changes are
`disabled` ↔ `disconnected`. `connected` is reserved for a successful OAuth flow (M3B). No
token column exists; credentials will live in a separate encrypted store.

## Frontend

Distribution Center under `/distribution`: Campaigns (list + detail with media, targets,
publications), Composer (one final → unique content per Page, batch create with idempotency
keys), Calendar (day / week / month in the organization timezone), Queue (grouped by urgency,
"Release due"), Links library, Publication detail (content editor, snapshot/approval card,
assisted checklist with copy/open actions, attempts, audit trail, action bar driven by
`allowed_actions`). `/accounts` is the registry; `/settings` holds organization timezone,
members and password. All data comes from the API; nothing is fabricated and no account is
ever shown as connected.

## Folder classification and routing (M3A.1)

Final videos carry categories derived from their NAS folder (`docs/architecture/content-taxonomy.md`).
The Composer asks `GET /distribution/recommendations` for rule-based Page recommendations
(`docs/architecture/channel-routing.md`) and creates drafts through the batch endpoint above:
no new write path, so uniqueness, idempotency and approval rules are unchanged. When a video
is reclassified after a publication was approved (for example the file moved to another
category folder), the publication exposes `classification_stale = true` and the UI shows a
review warning; the approved snapshot keeps the path and content it was approved with.
Publishing steps are refused (409 `revalidation_required` / `target_blocked`) until an admin
revalidates or revokes the approval, and `release-due` holds such jobs; see
`docs/architecture/channel-routing.md#safety-before-publishing-hardening`. The `revalidate`
action (admin) is part of the state table in `lifecycle.py` and never changes status.
