# Channel routing (M3A.1)

Deterministic, explainable matching of classified final videos to registered social
accounts. No LLM, no external API, no engagement prediction: a **routing relevance score**.

## Channel profile (`accounts` module)

`channel_profiles` (one per `social_accounts` row, separate table to keep the registry
lean): `primary_category_id`, `description`, `target_audience`, `language`, `country`,
`routing_enabled`. `channel_category_rules`: `(account, category)` unique with
`match_policy` `allow` | `prefer` | `block` and `priority`. Admins replace the whole profile
with `PUT /accounts/{id}/channel-profile`; everyone can read it. A manual (unverified)
Page may carry a profile and receive assisted-publishing recommendations; it is never shown
as connected.

## Engine (`accounts/routing_engine.py`)

Inputs: the asset's primary and secondary category ids, the category tree, every account of
the organization with profile and rules. Output per account: `eligible`, `score`,
`recommended`, `reasons[]`, `blocking_reason`, `matched_categories[]`.

**Inheritance.** A rule on a category applies to all its descendants. For each asset
category the engine walks the lineage (category, parent, grandparent, …) and uses the
*nearest* rule for that account. A rule on Men's Sports therefore covers Pickleball unless a
more specific Pickleball rule exists for the same account.

**Eligibility (checked first).**

| Condition | Result |
| --- | --- |
| account `status = disabled` | ineligible, "Account is disabled" |
| profile `routing_enabled = false` | ineligible, "Routing is disabled for this account" |
| nearest rule for the primary **or any secondary** is `block` | ineligible, "<Category> is blocked for this account (inherited from <Ancestor>)" |

`block` always wins, regardless of depth or priority of allow/prefer rules.

**Score (eligible accounts).**

| Signal | Points |
| --- | --- |
| `prefer` on the primary category (direct or inherited) | +100 |
| `allow` on the primary category | +70 |
| `prefer` on a secondary category | +25 each |
| `allow` on a secondary category | +15 each |
| profile `primary_category` is in the primary's lineage | +10 |

`recommended = score ≥ 70` (`RECOMMEND_THRESHOLD`). Score 0 means "no rule matches": the
account stays eligible but is listed under *low relevance* and can only be chosen explicitly
with a warning. Ordering: eligible first, then score descending, then account name.

`ENGINE_VERSION = "rules-v1"` is returned with every response so future rankers (M4
analytics, M5 agents) can be introduced behind the same `rank()` signature and compared.

## API

`GET /api/v1/distribution/recommendations?media_asset_id=…&campaign_id=…` returns the
detected categories, `media_approved`, and three lists: `recommended`, `low_relevance`,
`blocked`, each item with its explanation. With `campaign_id`, `already_targeted` marks
accounts that already have a non-cancelled publication for that video in that campaign.

Recommendations never create anything. The Composer turns the operator's selection into
drafts through the existing `POST /publishing/publications/batch`, so uniqueness per
campaign × media × account, idempotency keys, optimistic locking, approved-finals-only and
the full approval → schedule → assisted publishing state machine stay in force.

## Examples

ANTOR Men | Sport & Style: Men's Sports PREFER, Men's Fashion ALLOW, Women's Fashion BLOCK,
focus Men's Sports. A Pickleball video (secondary Kitchen) scores 100 + 10 = 110 →
recommended. A Women's Fashion video is blocked.

Home & Living Page: Home & Living PREFER, Mom & Baby ALLOW. The Pickleball video (secondary
Kitchen) scores 25 → eligible, low relevance. A Kitchen video scores 100 → recommended.

Mom & Baby Page: Mom & Baby PREFER, Kitchen BLOCK. The Pickleball video is blocked because of
its Kitchen secondary category, even though nothing else matches.

## Safety before publishing (hardening)

The verdict is **re-evaluated against the video's current classification at every
publishing step**, not only in the Composer (`publishing/service.py::assert_publishable_target`):

- `approve`, `schedule`, `start`, `retry` refuse a target that is now blocked, disabled or
  routing-off with 409 `target_blocked`. The publication keeps its status; nothing is
  retargeted or cancelled automatically. Every publication read reports
  `target_blocked_reason`.
- `POST /publishing/queue/release-due` skips such jobs (audit `publication.release_held`)
  instead of moving them to READY_TO_PUBLISH.
- If the video was reclassified after approval (`classification_stale`), `schedule`,
  `start` and `retry` are refused with 409 `revalidation_required` until an admin either
  **revalidates** (`POST …/revalidate`, records `revalidated_at/by`, snapshot untouched) or
  **revokes** the approval. A later reclassification flags the job again; a new approval
  clears the marker. Blocked targets cannot be revalidated.
- `revalidation_required = classification_stale or target_blocked_reason is not None` is the
  single flag the UI uses to surface the decision.
