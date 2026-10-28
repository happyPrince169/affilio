"""Folder-based classification of final videos (M3A.1).

Rule resolution for a file at `a/b/c/video.mp4` (folder `a/b/c`):
1. Consider active rules of the same FINAL root.
2. A rule matches if its path equals the folder (exact) or, with `include_subfolders`, is an
   ancestor of it ("" = root matches everything when include_subfolders).
3. The most specific rule wins (deepest matching path). Ties → higher `priority`, then the
   older rule (created_at, then id) so results are deterministic.
4. No match → the organization's Uncategorized fallback.

Classification is idempotent: the folder-derived assignment is a single row with
`assignment_source = folder`; it is rewritten only when the resolved category changes.
Manual rows are never touched by the scanner. A manual primary wins over the folder one.

Facet classification (`media/facets.py`) adds derived rows with `assignment_source = path`
(one per facet named by a structured path). Derived primary precedence: a folder rule, then
the path's product category, then the Uncategorized fallback, which is only written when
neither of the first two applies. A manual primary still wins over all of them.
"""

from __future__ import annotations

import posixpath
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import (
    CategoryAssignmentSource,
    CategoryFacet,
    MediaRootKind,
    MediaVariantKind,
)
from affilio.core.errors import ConflictError, NotFoundError
from affilio.modules.media.errors import InvalidFolderPathError
from affilio.modules.media.models import (
    FolderCategoryRule,
    MediaAsset,
    MediaContentCategory,
    MediaFileVariant,
    MediaRoot,
)
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory
from affilio.modules.taxonomy.service import CategoryTree

MAX_FOLDER_DEPTH = 16


# ---------------------------------------------------------------- paths


def normalize_folder_path(value: str) -> str:
    """Safe, portable POSIX folder path relative to the root. '' is the root itself."""
    raw = (value or "").strip().replace("\\", "/")
    if "\x00" in raw:
        raise InvalidFolderPathError("Folder path contains a NUL byte")
    if any(segment == ".." for segment in raw.split("/")):
        raise InvalidFolderPathError("Folder path must not contain '..' segments")
    cleaned = posixpath.normpath("/" + raw).lstrip("/")
    if cleaned == ".":
        cleaned = ""
    if cleaned.startswith("..") or "/../" in f"/{cleaned}/":
        raise InvalidFolderPathError("Folder path must stay inside the root")
    if posixpath.isabs(raw) and cleaned != raw.lstrip("/"):
        raise InvalidFolderPathError("Folder path must be relative to the root")
    if cleaned.count("/") >= MAX_FOLDER_DEPTH:
        raise InvalidFolderPathError(f"Folder path deeper than {MAX_FOLDER_DEPTH} levels")
    return cleaned


def folder_of(relative_file_path: str) -> str:
    head, _, _ = relative_file_path.rpartition("/")
    return head


def _matches(rule_path: str, folder: str, include_subfolders: bool) -> bool:
    if rule_path == folder:
        return True
    if not include_subfolders:
        return False
    return rule_path == "" or folder.startswith(rule_path + "/")


def resolve_rule(rules: list[FolderCategoryRule], folder: str) -> FolderCategoryRule | None:
    candidates = [
        r
        for r in rules
        if r.is_active and _matches(r.relative_folder_path, folder, r.include_subfolders)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: (
            -(len(r.relative_folder_path.split("/")) if r.relative_folder_path else 0),
            -r.priority,
            r.created_at or datetime.max.replace(tzinfo=UTC),
            str(r.id),
        )
    )
    return candidates[0]


# ---------------------------------------------------------------- rule CRUD


def list_rules(session: Session, organization_id: uuid.UUID) -> list[FolderCategoryRule]:
    return list(
        session.scalars(
            select(FolderCategoryRule)
            .options(
                selectinload(FolderCategoryRule.root), selectinload(FolderCategoryRule.category)
            )
            .where(FolderCategoryRule.organization_id == organization_id)
            .order_by(FolderCategoryRule.relative_folder_path, FolderCategoryRule.priority.desc())
        )
    )


def rules_for_root(
    session: Session, organization_id: uuid.UUID, root_id: uuid.UUID
) -> list[FolderCategoryRule]:
    return list(
        session.scalars(
            select(FolderCategoryRule).where(
                FolderCategoryRule.organization_id == organization_id,
                FolderCategoryRule.media_root_id == root_id,
            )
        )
    )


def get_rule(
    session: Session, organization_id: uuid.UUID, rule_id: uuid.UUID
) -> FolderCategoryRule:
    rule = session.scalar(
        select(FolderCategoryRule)
        .options(selectinload(FolderCategoryRule.root), selectinload(FolderCategoryRule.category))
        .where(
            FolderCategoryRule.organization_id == organization_id, FolderCategoryRule.id == rule_id
        )
    )
    if rule is None:
        raise NotFoundError("Folder rule not found")
    return rule


def _final_root(session: Session, organization_id: uuid.UUID, root_id: uuid.UUID) -> MediaRoot:
    root = session.scalar(
        select(MediaRoot).where(
            MediaRoot.organization_id == organization_id, MediaRoot.id == root_id
        )
    )
    if root is None:
        raise NotFoundError("Media root not found")
    if root.kind != MediaRootKind.FINAL:
        raise ConflictError(
            "Folder rules apply to final-video roots only",
            details={"root_key": root.key, "kind": root.kind.value},
        )
    return root


def create_rule(
    session: Session,
    organization_id: uuid.UUID,
    *,
    media_root_id: uuid.UUID,
    relative_folder_path: str,
    category_id: uuid.UUID,
    include_subfolders: bool,
    priority: int,
    is_active: bool,
) -> FolderCategoryRule:
    root = _final_root(session, organization_id, media_root_id)
    category = taxonomy_service.get_category(session, organization_id, category_id)
    path = normalize_folder_path(relative_folder_path)
    clash = session.scalar(
        select(FolderCategoryRule).where(
            FolderCategoryRule.media_root_id == root.id,
            FolderCategoryRule.relative_folder_path == path,
        )
    )
    if clash is not None:
        raise ConflictError(
            "A rule for this folder already exists", details={"rule_id": str(clash.id)}
        )
    rule = FolderCategoryRule(
        id=uuid.uuid4(),
        organization_id=organization_id,
        media_root_id=root.id,
        relative_folder_path=path,
        category_id=category.id,
        include_subfolders=include_subfolders,
        priority=priority,
        is_active=is_active,
    )
    session.add(rule)
    session.flush()
    return get_rule(session, organization_id, rule.id)


def update_rule(
    session: Session, organization_id: uuid.UUID, rule_id: uuid.UUID, changes: dict
) -> FolderCategoryRule:
    rule = get_rule(session, organization_id, rule_id)
    if "category_id" in changes:
        taxonomy_service.get_category(session, organization_id, changes["category_id"])
    if "relative_folder_path" in changes:
        path = normalize_folder_path(changes["relative_folder_path"])
        clash = session.scalar(
            select(FolderCategoryRule).where(
                FolderCategoryRule.media_root_id == rule.media_root_id,
                FolderCategoryRule.relative_folder_path == path,
                FolderCategoryRule.id != rule.id,
            )
        )
        if clash is not None:
            raise ConflictError(
                "A rule for this folder already exists", details={"rule_id": str(clash.id)}
            )
        changes["relative_folder_path"] = path
    for key, value in changes.items():
        setattr(rule, key, value)
    session.flush()
    return rule


def delete_rule(session: Session, organization_id: uuid.UUID, rule_id: uuid.UUID) -> None:
    rule = get_rule(session, organization_id, rule_id)
    session.delete(rule)
    session.flush()


# ---------------------------------------------------------------- reports


@dataclass(slots=True)
class FolderReport:
    root_id: uuid.UUID
    root_key: str
    folder: str
    file_count: int
    rule: FolderCategoryRule | None


def folder_report(session: Session, organization_id: uuid.UUID) -> list[FolderReport]:
    """Every folder that holds final videos, with the rule that would classify it (or None)."""
    rows = session.execute(
        select(MediaRoot.id, MediaRoot.key, MediaFileVariant.relative_path)
        .join(MediaFileVariant, MediaFileVariant.media_root_id == MediaRoot.id)
        .where(
            MediaRoot.organization_id == organization_id,
            MediaRoot.kind == MediaRootKind.FINAL,
            MediaFileVariant.kind == MediaVariantKind.FINAL,
        )
    ).all()
    counts: dict[tuple[uuid.UUID, str, str], int] = {}
    for root_id, root_key, rel in rows:
        key = (root_id, root_key, folder_of(rel))
        counts[key] = counts.get(key, 0) + 1
    rules_by_root: dict[uuid.UUID, list[FolderCategoryRule]] = {}
    for rule in list_rules(session, organization_id):
        rules_by_root.setdefault(rule.media_root_id, []).append(rule)
    out = [
        FolderReport(
            root_id=root_id,
            root_key=root_key,
            folder=folder,
            file_count=n,
            rule=resolve_rule(rules_by_root.get(root_id, []), folder),
        )
        for (root_id, root_key, folder), n in counts.items()
    ]
    out.sort(key=lambda r: (r.root_key, r.folder))
    return out


# ---------------------------------------------------------------- assignments


DERIVED_SOURCES = {CategoryAssignmentSource.FOLDER, CategoryAssignmentSource.PATH}


def _assignments(asset: MediaAsset) -> list[MediaContentCategory]:
    return list(asset.category_assignments)


def _is_derived(assignment: MediaContentCategory) -> bool:
    return assignment.assignment_source in DERIVED_SOURCES


def derived_primary(assignments: list[MediaContentCategory]) -> MediaContentCategory | None:
    """The derived row that holds primacy when no manual primary exists: the folder row
    (rule or fallback) first, then the path row of the product category facet."""
    folder = next(
        (a for a in assignments if a.assignment_source == CategoryAssignmentSource.FOLDER), None
    )
    if folder is not None:
        return folder
    return next(
        (
            a
            for a in assignments
            if a.assignment_source == CategoryAssignmentSource.PATH
            and a.category is not None
            and a.category.facet == CategoryFacet.PRODUCT_CATEGORY
        ),
        None,
    )


def apply_folder_classification(
    session: Session,
    asset: MediaAsset,
    *,
    root: MediaRoot,
    relative_path: str,
    rules: list[FolderCategoryRule] | None = None,
    now: datetime | None = None,
    facet_assignments: dict[CategoryFacet, uuid.UUID] | None = None,
) -> bool:
    """Ensure the derived assignments (folder rule / fallback and path facets) reflect the
    file's folders. Returns True when a previously derived classification changed (callers
    may then flag downstream reviews); the first classification is not a change."""
    if root.kind != MediaRootKind.FINAL:
        return False
    now = now or datetime.now(UTC)
    rule_set = (
        rules if rules is not None else rules_for_root(session, asset.organization_id, root.id)
    )
    rule = resolve_rule(rule_set, folder_of(relative_path))
    facets = dict(facet_assignments or {})
    product_id = facets.get(CategoryFacet.PRODUCT_CATEGORY)

    # Desired derived rows, in primacy order: folder rule → product category → fallback.
    desired: dict[uuid.UUID, CategoryAssignmentSource] = {}
    if rule is not None:
        desired[rule.category_id] = CategoryAssignmentSource.FOLDER
    elif product_id is None:
        fallback = taxonomy_service.ensure_uncategorized(session, asset.organization_id).id
        desired[fallback] = CategoryAssignmentSource.FOLDER
    for facet in (
        CategoryFacet.PRODUCT_CATEGORY,
        CategoryFacet.PRODUCTION_OWNER,
        CategoryFacet.CONTENT_TYPE,
    ):
        category_id = facets.get(facet)
        if category_id is not None and category_id not in desired:
            desired[category_id] = CategoryAssignmentSource.PATH
    primary_candidate = next(iter(desired), None)

    assignments = _assignments(asset)
    existing = {a.category_id: a.assignment_source for a in assignments if _is_derived(a)}
    manual_primary = any(a.is_primary and not _is_derived(a) for a in assignments)
    old_primary = next((a for a in assignments if a.is_primary and _is_derived(a)), None)
    if existing == desired:
        # Idempotent: nothing differs → no writes (primary flag may still need fixing).
        if not manual_primary and not any(a.is_primary for a in assignments):
            for a in assignments:
                if _is_derived(a) and a.category_id == primary_candidate:
                    a.is_primary = True
        return False

    changed = bool(existing) and (
        set(existing) != set(desired)
        or (old_primary is not None and old_primary.category_id != primary_candidate)
    )
    absorbed_primary: uuid.UUID | None = None
    for a in list(asset.category_assignments):
        if _is_derived(a) and desired.get(a.category_id) != a.assignment_source:
            asset.category_assignments.remove(a)
        elif not _is_derived(a) and a.category_id in desired:
            # A manual assignment of a now-derived category is absorbed by the derived row;
            # if it was the manual primary, that category stays primary.
            if a.is_primary:
                absorbed_primary = a.category_id
            asset.category_assignments.remove(a)
    session.flush()
    kept = {a.category_id for a in asset.category_assignments}
    for category_id, source in desired.items():
        if category_id in kept:
            continue
        asset.category_assignments.append(
            MediaContentCategory(
                id=uuid.uuid4(),
                organization_id=asset.organization_id,
                media_asset_id=asset.id,
                category_id=category_id,
                is_primary=False,
                assignment_source=source,
            )
        )
    primary_id = (
        None if manual_primary else absorbed_primary if absorbed_primary else primary_candidate
    )
    for a in asset.category_assignments:
        if _is_derived(a):
            a.is_primary = primary_id is not None and a.category_id == primary_id
    if changed:
        asset.classification_changed_at = now
    session.flush()
    return changed


def set_manual_categories(
    session: Session,
    organization_id: uuid.UUID,
    asset: MediaAsset,
    *,
    primary_category_id: uuid.UUID | None,
    secondary_category_ids: list[uuid.UUID],
) -> None:
    """Operator override. `primary_category_id=None` returns primacy to the derived primary
    (folder rule / fallback, else the path's product category). Secondary categories are
    replaced wholesale; derived rows (folder and path) are preserved."""
    wanted = [*([primary_category_id] if primary_category_id else []), *secondary_category_ids]
    taxonomy_service.get_categories(session, organization_id, wanted)
    assignments = _assignments(asset)
    derived_rows = [a for a in assignments if _is_derived(a)]
    derived_by_category = {a.category_id: a for a in derived_rows}
    for a in assignments:
        if not _is_derived(a):
            asset.category_assignments.remove(a)
    for a in derived_rows:
        a.is_primary = False
    session.flush()
    if primary_category_id is None:
        fallback_primary = derived_primary(derived_rows)
        if fallback_primary is not None:
            fallback_primary.is_primary = True
    for category_id in dict.fromkeys(wanted):
        derived = derived_by_category.get(category_id)
        if derived is not None:
            if category_id == primary_category_id:
                derived.is_primary = True
            continue
        asset.category_assignments.append(
            MediaContentCategory(
                id=uuid.uuid4(),
                organization_id=organization_id,
                media_asset_id=asset.id,
                category_id=category_id,
                is_primary=category_id == primary_category_id,
                assignment_source=CategoryAssignmentSource.MANUAL,
            )
        )
    asset.classification_changed_at = datetime.now(UTC)
    session.flush()


@dataclass(slots=True)
class AssetClassification:
    primary: MediaContentCategory | None
    secondary: list[MediaContentCategory]
    folder_category_id: uuid.UUID | None

    @property
    def category_ids(self) -> list[uuid.UUID]:
        return [
            *([self.primary.category_id] if self.primary else []),
            *(a.category_id for a in self.secondary),
        ]


def classification_of(asset: MediaAsset) -> AssetClassification:
    assignments = _assignments(asset)
    primary = next((a for a in assignments if a.is_primary), None)
    secondary = sorted(
        (a for a in assignments if not a.is_primary),
        key=lambda a: a.created_at or datetime.max.replace(tzinfo=UTC),
    )
    folder = next(
        (a for a in assignments if a.assignment_source == CategoryAssignmentSource.FOLDER), None
    )
    return AssetClassification(
        primary=primary,
        secondary=secondary,
        folder_category_id=folder.category_id if folder else None,
    )


def asset_ids_in_categories(
    session: Session, organization_id: uuid.UUID, category_ids: set[uuid.UUID]
):
    """Subquery of asset ids having any assignment in the given categories."""
    return (
        select(MediaContentCategory.media_asset_id)
        .where(
            MediaContentCategory.organization_id == organization_id,
            MediaContentCategory.category_id.in_(list(category_ids)),
        )
        .scalar_subquery()
    )


def category_counts(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(MediaContentCategory.category_id, func.count())
        .where(
            MediaContentCategory.organization_id == organization_id,
            MediaContentCategory.is_primary.is_(True),
        )
        .group_by(MediaContentCategory.category_id)
    )
    return {cid: int(n) for cid, n in rows}


def tree_for(session: Session, organization_id: uuid.UUID) -> CategoryTree:
    return taxonomy_service.load_tree(session, organization_id)


__all__ = ["ContentCategory"]
