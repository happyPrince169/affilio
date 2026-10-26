"""Channel matching engine (lives in `accounts` so `publishing` may re-check eligibility
before any publishing step; the `routing` module exposes it over HTTP).

Inputs: the asset's category ids (primary + secondary), the category tree, and each account's
profile + rules. Output per account: eligibility, score, reasons.

Rule inheritance: a rule on a category applies to all its descendants. For a given asset
category, the *nearest* rule in the lineage (the category itself, then parent, …) is the one
that applies for that account. Within one category several rules cannot exist (unique), so
ties only arise between lineage levels and are resolved by nearness.

Scoring (documented in docs/architecture/channel-routing.md):
    PREFER on primary        +100
    ALLOW  on primary         +70
    PREFER on a secondary     +25 each
    ALLOW  on a secondary     +15 each
    profile.primary_category in the primary's lineage  +10
BLOCK on the primary or any secondary → ineligible (BLOCK beats ALLOW/PREFER regardless of
depth). Routing disabled / account disabled → ineligible. Score 0 → "not recommended" but
still selectable with a warning (low relevance). Recommended threshold: score ≥ 70.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from affilio.core.enums import ChannelMatchPolicy, SocialAccountStatus
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.taxonomy.service import CategoryTree

SCORE_PREFER_PRIMARY = 100
SCORE_ALLOW_PRIMARY = 70
SCORE_PREFER_SECONDARY = 25
SCORE_ALLOW_SECONDARY = 15
SCORE_PROFILE_PRIMARY = 10
RECOMMEND_THRESHOLD = 70
ENGINE_VERSION = "rules-v1"


@dataclass(slots=True)
class Recommendation:
    account_id: uuid.UUID
    eligible: bool
    score: int
    recommended: bool
    reasons: list[str] = field(default_factory=list)
    blocking_reason: str | None = None
    matched_category_ids: list[uuid.UUID] = field(default_factory=list)


def _nearest_rule(account: SocialAccount, lineage: list[uuid.UUID]):
    by_category = {r.category_id: r for r in account.category_rules}
    for depth, cid in enumerate(lineage):
        rule = by_category.get(cid)
        if rule is not None:
            return rule, depth
    return None, None


def evaluate_account(
    account: SocialAccount,
    *,
    primary_id: uuid.UUID | None,
    secondary_ids: list[uuid.UUID],
    tree: CategoryTree,
) -> Recommendation:
    rec = Recommendation(account_id=account.id, eligible=True, score=0, recommended=False)
    profile = account.channel_profile
    if account.status == SocialAccountStatus.DISABLED:
        rec.eligible = False
        rec.blocking_reason = "Account is disabled"
        return rec
    if profile is not None and not profile.routing_enabled:
        rec.eligible = False
        rec.blocking_reason = "Routing is disabled for this account"
        return rec
    if primary_id is None and not secondary_ids:
        rec.reasons.append("Video has no category")
        return rec

    def name(cid: uuid.UUID) -> str:
        return tree.by_id[cid].name if cid in tree.by_id else str(cid)

    # BLOCK precedence: any blocked category (primary or secondary) makes the account ineligible.
    for cid in [primary_id, *secondary_ids]:
        if cid is None:
            continue
        rule, depth = _nearest_rule(account, tree.lineage(cid))
        if rule is not None and rule.match_policy == ChannelMatchPolicy.BLOCK:
            rec.eligible = False
            via = "" if depth == 0 else f" (inherited from {name(rule.category_id)})"
            rec.blocking_reason = f"{name(cid)} is blocked for this account{via}"
            rec.score = 0
            return rec

    if primary_id is not None:
        rule, depth = _nearest_rule(account, tree.lineage(primary_id))
        if rule is not None:
            via = "" if depth == 0 else f" via {name(rule.category_id)}"
            if rule.match_policy == ChannelMatchPolicy.PREFER:
                rec.score += SCORE_PREFER_PRIMARY
                rec.reasons.append(
                    f"Preferred primary category {name(primary_id)}{via} (+{SCORE_PREFER_PRIMARY})"
                )
            else:
                rec.score += SCORE_ALLOW_PRIMARY
                rec.reasons.append(
                    f"Allowed primary category {name(primary_id)}{via} (+{SCORE_ALLOW_PRIMARY})"
                )
            rec.matched_category_ids.append(primary_id)
        if profile is not None and profile.primary_category_id in tree.lineage(primary_id):
            rec.score += SCORE_PROFILE_PRIMARY
            rec.reasons.append(
                f"Account focus is {name(profile.primary_category_id)} (+{SCORE_PROFILE_PRIMARY})"
            )

    for cid in secondary_ids:
        rule, depth = _nearest_rule(account, tree.lineage(cid))
        if rule is None:
            continue
        via = "" if depth == 0 else f" via {name(rule.category_id)}"
        if rule.match_policy == ChannelMatchPolicy.PREFER:
            rec.score += SCORE_PREFER_SECONDARY
            rec.reasons.append(
                f"Preferred secondary category {name(cid)}{via} (+{SCORE_PREFER_SECONDARY})"
            )
        else:
            rec.score += SCORE_ALLOW_SECONDARY
            rec.reasons.append(
                f"Allowed secondary category {name(cid)}{via} (+{SCORE_ALLOW_SECONDARY})"
            )
        rec.matched_category_ids.append(cid)

    if rec.score == 0:
        rec.reasons.append("No category rule matches this video")
    rec.recommended = rec.score >= RECOMMEND_THRESHOLD
    return rec


def rank(
    accounts: list[SocialAccount],
    *,
    primary_id: uuid.UUID | None,
    secondary_ids: list[uuid.UUID],
    tree: CategoryTree,
) -> list[Recommendation]:
    results = [
        evaluate_account(a, primary_id=primary_id, secondary_ids=secondary_ids, tree=tree)
        for a in accounts
    ]
    order = {a.id: i for i, a in enumerate(accounts)}  # stable: accounts come sorted by name
    results.sort(key=lambda r: (not r.eligible, -r.score, order[r.account_id]))
    return results
