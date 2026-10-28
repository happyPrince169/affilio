"""Folder-driven multi-facet classification of final videos.

Target layout under the finals root:

    {ProductionOwner}/{ProductCategory}/{ContentType}/{video.mp4}

Each segment names a value of one facet (`CategoryFacet`); values are categories under the
organization's facet roots (taxonomy). Matching is by normalized name or slug
(`taxonomy.service.normalize_name`): the folder `Mens-Fashion` resolves to the category
"Men's Fashion" (slug `mens-fashion`). Deeper folders below the content type are ignored.

Legacy and incomplete paths (`Hang/T9/video.mp4`, `Hang/video.mp4`) keep their M3A.1
behaviour (folder rules, Uncategorized fallback); at most the owner facet is derived from the
first segment and they never produce suggestions.

Unknown values on a structured path of a *valid* (probed) final are recorded once per
(organization, facet, normalized name) as a pending `category_suggestions` row; nothing is
created in the taxonomy until an admin approves it. Approval creates (or reuses) the category
and re-runs the path classification on the finals already indexed under that folder name,
without touching files, asset identity or handoff decisions.

`production_period` ("YYYY-MM", UTC) is stamped from the asset's first ingestion time when the
asset row is created and never rewritten by rescans or moves.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import (
    CategoryFacet,
    CategoryStatus,
    CategorySuggestionStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaRootKind,
    MediaVariantKind,
)
from affilio.core.errors import ConflictError, NotFoundError
from affilio.modules.media import classification
from affilio.modules.media.errors import SuggestionNameMismatchError
from affilio.modules.media.models import (
    CategorySuggestion,
    MediaAsset,
    MediaContentCategory,
    MediaFileVariant,
    MediaRoot,
)
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory
from affilio.modules.taxonomy.schemas import CategoryCreate, slugify
from affilio.modules.taxonomy.service import CategoryTree, normalize_name

# Legacy second-level folders are production periods ("T9", "2026-09", "202609"), never a
# product category. They mark a legacy path and are not classified or suggested.
PERIOD_FOLDER = re.compile(r"^(?:t\d{1,2}|\d{4}[-_]?\d{2})$", re.IGNORECASE)

# Facet order of the structured path.
PATH_FACETS: tuple[CategoryFacet, ...] = (
    CategoryFacet.PRODUCTION_OWNER,
    CategoryFacet.PRODUCT_CATEGORY,
    CategoryFacet.CONTENT_TYPE,
)

MAX_SUGGESTION_NAME = 120


def production_period_for(first_seen_at: datetime) -> str:
    """Calendar month (UTC) of the first ingestion: "2026-10"."""
    return first_seen_at.astimezone(UTC).strftime("%Y-%m")


# ---------------------------------------------------------------- parsing


@dataclass(slots=True, frozen=True)
class ParsedFinalPath:
    """Facet values named by a final's folders. `structured` paths have all three segments;
    `legacy` paths are `<owner>/<period>/`; anything else is `incomplete` (owner only)."""

    segments: tuple[str, ...]
    values: dict[CategoryFacet, str]
    structured: bool
    legacy: bool
    reason: str | None  # why the path is not structured (for reports and tests)

    @property
    def owner(self) -> str | None:
        return self.values.get(CategoryFacet.PRODUCTION_OWNER)


def _valid_segment(segment: str) -> bool:
    return bool(segment) and not segment.startswith(".") and bool(normalize_name(segment))


def parse_final_path(relative_path: str) -> ParsedFinalPath:
    folder = classification.folder_of(relative_path)
    segments = tuple(s for s in folder.split("/") if s) if folder else ()
    if not segments:
        return ParsedFinalPath(segments, {}, False, False, "file is directly under the root")
    if not all(_valid_segment(s) for s in segments):
        return ParsedFinalPath(segments, {}, False, False, "folder name is not usable")
    owner = segments[0]
    if len(segments) == 1:
        values = {CategoryFacet.PRODUCTION_OWNER: owner}
        return ParsedFinalPath(segments, values, False, False, "missing product category folder")
    if len(segments) == 2:
        values = {CategoryFacet.PRODUCTION_OWNER: owner}
        if PERIOD_FOLDER.match(segments[1]):
            return ParsedFinalPath(segments, values, False, True, "legacy period folder")
        return ParsedFinalPath(segments, values, False, False, "missing content type folder")
    if PERIOD_FOLDER.match(segments[1]) or PERIOD_FOLDER.match(segments[2]):
        values = {CategoryFacet.PRODUCTION_OWNER: owner}
        return ParsedFinalPath(segments, values, False, True, "legacy period folder")
    values = dict(zip(PATH_FACETS, segments[:3], strict=True))
    return ParsedFinalPath(segments, values, True, False, None)


# ---------------------------------------------------------------- resolution


@dataclass(slots=True)
class UnknownValue:
    facet: CategoryFacet
    name: str
    folder: str  # folder path that introduced the value, e.g. "Hao/Streetwear"


@dataclass(slots=True)
class PathClassification:
    parsed: ParsedFinalPath
    assignments: dict[CategoryFacet, uuid.UUID] = field(default_factory=dict)
    unknown: list[UnknownValue] = field(default_factory=list)


@dataclass(slots=True)
class FacetContext:
    """Per-scan snapshot of the organization's facet values (one tree load per scan)."""

    organization_id: uuid.UUID
    tree: CategoryTree
    enabled: bool
    _cache: dict[tuple[CategoryFacet, str], uuid.UUID | None] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, organization_id: uuid.UUID) -> FacetContext:
        tree = taxonomy_service.load_tree(session, organization_id)
        enabled = len(tree.facet_roots()) == len(CategoryFacet)
        return cls(organization_id=organization_id, tree=tree, enabled=enabled)

    def resolve(self, facet: CategoryFacet, raw_name: str) -> uuid.UUID | None:
        key = (facet, normalize_name(raw_name))
        if key not in self._cache:
            match = self.tree.find_facet_value(facet, raw_name)
            self._cache[key] = match.id if match else None
        return self._cache[key]

    def classify(self, relative_path: str) -> PathClassification:
        """Facet assignments for a final at `relative_path`; unknown values of structured paths
        are returned for suggestion. Disabled facets classify nothing."""
        parsed = parse_final_path(relative_path)
        result = PathClassification(parsed=parsed)
        if not self.enabled:
            return result
        for facet in PATH_FACETS:
            raw = parsed.values.get(facet)
            if raw is None:
                continue
            category_id = self.resolve(facet, raw)
            if category_id is not None:
                result.assignments[facet] = category_id
            elif parsed.structured:
                index = PATH_FACETS.index(facet)
                folder = "/".join(parsed.segments[: index + 1])
                result.unknown.append(UnknownValue(facet=facet, name=raw, folder=folder))
        return result


# ---------------------------------------------------------------- suggestions


@dataclass(slots=True)
class SuggestionHit:
    facet: CategoryFacet
    name: str
    folder: str
    example_relative_path: str
    files: int = 1


class SuggestionCollector:
    """Accumulates unknown facet values seen during one scan, keyed by normalized name, so a
    folder with 40 videos yields one suggestion row and one write."""

    def __init__(self) -> None:
        self.hits: dict[tuple[CategoryFacet, str], SuggestionHit] = {}

    def add(self, unknown: UnknownValue, relative_path: str) -> None:
        key = (unknown.facet, normalize_name(unknown.name))
        hit = self.hits.get(key)
        if hit is None:
            self.hits[key] = SuggestionHit(
                facet=unknown.facet,
                name=unknown.name[:MAX_SUGGESTION_NAME],
                folder=unknown.folder,
                example_relative_path=relative_path,
            )
        else:
            hit.files += 1


def record_suggestions(
    session: Session,
    organization_id: uuid.UUID,
    root: MediaRoot,
    collector: SuggestionCollector,
    *,
    tree: CategoryTree,
    now: datetime,
) -> int:
    """Upsert pending suggestions for the collected hits. Returns the number created.

    Rejected suggestions stay rejected (only `last_seen_at`/`seen_count` move). An approved
    suggestion whose category has since been archived or deleted reopens as pending, because
    the folder is producing finals again that nothing classifies.
    """
    if not collector.hits:
        return 0
    keys = list(collector.hits)
    existing = {
        (s.facet, s.normalized_name): s
        for s in session.scalars(
            select(CategorySuggestion).where(
                CategorySuggestion.organization_id == organization_id,
                CategorySuggestion.facet.in_({facet for facet, _ in keys}),
                CategorySuggestion.normalized_name.in_({name for _, name in keys}),
            )
        )
    }
    created = 0
    for key, hit in collector.hits.items():
        row = existing.get(key)
        if row is None:
            session.add(
                CategorySuggestion(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    media_root_id=root.id,
                    facet=hit.facet,
                    name=hit.name,
                    normalized_name=key[1],
                    relative_folder_path=hit.folder,
                    example_relative_path=hit.example_relative_path,
                    status=CategorySuggestionStatus.PENDING,
                    seen_count=hit.files,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            created += 1
            continue
        row.last_seen_at = now
        row.seen_count = hit.files
        if row.example_relative_path is None:
            row.example_relative_path = hit.example_relative_path
        if row.status == CategorySuggestionStatus.APPROVED:
            category = tree.by_id.get(row.category_id) if row.category_id else None
            if category is None or category.status != CategoryStatus.ACTIVE:
                row.status = CategorySuggestionStatus.PENDING
                row.decided_at = None
                row.decided_by = None
    session.flush()
    return created


def list_suggestions(
    session: Session,
    organization_id: uuid.UUID,
    *,
    status: CategorySuggestionStatus | None = None,
) -> list[CategorySuggestion]:
    stmt = (
        select(CategorySuggestion)
        .options(selectinload(CategorySuggestion.root), selectinload(CategorySuggestion.category))
        .where(CategorySuggestion.organization_id == organization_id)
        .order_by(CategorySuggestion.facet, CategorySuggestion.first_seen_at)
    )
    if status is not None:
        stmt = stmt.where(CategorySuggestion.status == status)
    return list(session.scalars(stmt))


def get_suggestion(
    session: Session, organization_id: uuid.UUID, suggestion_id: uuid.UUID
) -> CategorySuggestion:
    row = session.scalar(
        select(CategorySuggestion)
        .options(selectinload(CategorySuggestion.root), selectinload(CategorySuggestion.category))
        .where(
            CategorySuggestion.organization_id == organization_id,
            CategorySuggestion.id == suggestion_id,
        )
    )
    if row is None:
        raise NotFoundError("Category suggestion not found")
    return row


def _display_name(folder_name: str) -> str:
    return re.sub(r"[-_]+", " ", folder_name).strip()[:MAX_SUGGESTION_NAME] or folder_name


@dataclass(slots=True)
class SuggestionDecision:
    suggestion: CategorySuggestion
    category: ContentCategory | None
    reclassified_assets: int


def approve_suggestion(
    session: Session,
    organization_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    *,
    name: str | None = None,
    slug: str | None = None,
    decided_by: str | None = None,
    now: datetime | None = None,
) -> SuggestionDecision:
    """Create (or adopt) the category for a suggested facet value and classify the finals
    already indexed under that folder name. Files, asset ids and handoff state are untouched."""
    now = now or datetime.now(UTC)
    suggestion = get_suggestion(session, organization_id, suggestion_id)
    if suggestion.status == CategorySuggestionStatus.APPROVED:
        raise ConflictError("Suggestion is already approved")
    roots = taxonomy_service.facet_roots(session, organization_id)
    root = roots.get(suggestion.facet)
    if root is None:
        raise ConflictError(
            "Facet classification is not enabled: create the facet roots first",
            details={"facet": suggestion.facet.value},
        )
    tree = taxonomy_service.load_tree(session, organization_id)
    category = tree.find_facet_value(suggestion.facet, suggestion.name)
    if category is None:
        chosen_name = (name or "").strip() or _display_name(suggestion.name)
        chosen_slug = (slug or "").strip() or slugify(chosen_name)
        # The category must keep matching the folder, otherwise the next video in that folder
        # would be suggested all over again.
        if suggestion.normalized_name not in {
            normalize_name(chosen_name),
            normalize_name(chosen_slug),
        }:
            raise SuggestionNameMismatchError(
                "Name or slug must match the folder name",
                details={
                    "folder": suggestion.name,
                    "normalized": suggestion.normalized_name,
                    "name": chosen_name,
                    "slug": chosen_slug,
                },
            )
        category = taxonomy_service.create_category(
            session,
            organization_id,
            CategoryCreate(name=chosen_name, slug=chosen_slug, parent_id=root.id),
        )
    suggestion.status = CategorySuggestionStatus.APPROVED
    suggestion.category_id = category.id
    suggestion.decided_at = now
    suggestion.decided_by = decided_by
    suggestion.note = None
    session.flush()
    count = reclassify_facet_value(
        session, organization_id, suggestion.facet, suggestion.normalized_name, now=now
    )
    return SuggestionDecision(suggestion=suggestion, category=category, reclassified_assets=count)


def reject_suggestion(
    session: Session,
    organization_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    *,
    note: str | None = None,
    decided_by: str | None = None,
    now: datetime | None = None,
) -> CategorySuggestion:
    suggestion = get_suggestion(session, organization_id, suggestion_id)
    if suggestion.status == CategorySuggestionStatus.APPROVED:
        raise ConflictError("An approved suggestion cannot be rejected; archive the category")
    suggestion.status = CategorySuggestionStatus.REJECTED
    suggestion.note = (note or "").strip() or None
    suggestion.decided_at = now or datetime.now(UTC)
    suggestion.decided_by = decided_by
    session.flush()
    return suggestion


# ---------------------------------------------------------------- reclassification


def reclassify_facet_value(
    session: Session,
    organization_id: uuid.UUID,
    facet: CategoryFacet,
    normalized_name: str,
    *,
    now: datetime | None = None,
) -> int:
    """Re-run the path classification for every indexed final whose structured path names
    `normalized_name` in `facet`. Reads only database rows (no filesystem access)."""
    now = now or datetime.now(UTC)
    context = FacetContext.load(session, organization_id)
    if not context.enabled:
        return 0
    rows = session.execute(
        select(MediaFileVariant, MediaRoot)
        .join(MediaRoot, MediaRoot.id == MediaFileVariant.media_root_id)
        .join(MediaAsset, MediaAsset.id == MediaFileVariant.asset_id)
        .options(
            selectinload(MediaFileVariant.asset)
            .selectinload(MediaAsset.category_assignments)
            .selectinload(MediaContentCategory.category)
        )
        .where(
            MediaAsset.organization_id == organization_id,
            MediaAsset.asset_kind == MediaAssetKind.FINAL,
            MediaFileVariant.kind == MediaVariantKind.FINAL,
            MediaRoot.kind == MediaRootKind.FINAL,
        )
    ).all()
    rules_by_root: dict[uuid.UUID, list] = {}
    count = 0
    for variant, root in rows:
        parsed = parse_final_path(variant.relative_path)
        raw = parsed.values.get(facet)
        if not parsed.structured or raw is None or normalize_name(raw) != normalized_name:
            continue
        if root.id not in rules_by_root:
            rules_by_root[root.id] = classification.rules_for_root(
                session, organization_id, root.id
            )
        result = context.classify(variant.relative_path)
        classification.apply_folder_classification(
            session,
            variant.asset,
            root=root,
            relative_path=variant.relative_path,
            rules=rules_by_root[root.id],
            now=now,
            facet_assignments=result.assignments,
        )
        count += 1
    return count


def suggestion_is_valid_source(asset: MediaAsset) -> bool:
    """Only finals that probed as real videos may introduce new facet values."""
    return asset.status == MediaAssetStatus.ACTIVE


__all__ = [
    "PATH_FACETS",
    "FacetContext",
    "ParsedFinalPath",
    "PathClassification",
    "SuggestionCollector",
    "SuggestionDecision",
    "approve_suggestion",
    "get_suggestion",
    "list_suggestions",
    "parse_final_path",
    "production_period_for",
    "reclassify_facet_value",
    "record_suggestions",
    "reject_suggestion",
    "suggestion_is_valid_source",
]
