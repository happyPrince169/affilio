"""Category tree services: CRUD with cycle and sibling-slug protection, ancestry helpers."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from affilio.core.enums import CategoryStatus
from affilio.core.errors import NotFoundError
from affilio.modules.taxonomy.errors import (
    CategoryArchivedError,
    CategoryCycleError,
    CategoryInUseError,
    DuplicateCategorySlugError,
)
from affilio.modules.taxonomy.models import UNCATEGORIZED_SLUG, ContentCategory
from affilio.modules.taxonomy.schemas import (
    CategoryCreate,
    CategoryRef,
    CategoryResponse,
    CategoryUpdate,
    slugify,
)

MAX_DEPTH = 6

# Tables that may reference a category; checked before destructive changes. Registered by the
# owning modules at import time so taxonomy never imports modules listed after it.
_REFERENCE_CHECKS: list[tuple[str, str]] = []  # (table, column)


def register_reference(table: str, column: str) -> None:
    if (table, column) not in _REFERENCE_CHECKS:
        _REFERENCE_CHECKS.append((table, column))


# ------------------------------------------------------------------ tree snapshot


@dataclass(slots=True)
class CategoryTree:
    """In-memory snapshot of an organization's categories for ancestry questions."""

    by_id: dict[uuid.UUID, ContentCategory] = field(default_factory=dict)

    def ancestors(self, category_id: uuid.UUID) -> list[ContentCategory]:
        """Nearest first, root last; excludes the category itself."""
        out: list[ContentCategory] = []
        seen: set[uuid.UUID] = set()
        current = self.by_id.get(category_id)
        while current is not None and current.parent_id is not None:
            if current.parent_id in seen:
                break  # defensive: cycles are prevented on write
            seen.add(current.parent_id)
            current = self.by_id.get(current.parent_id)
            if current is not None:
                out.append(current)
        return out

    def lineage(self, category_id: uuid.UUID) -> list[uuid.UUID]:
        """[self, parent, grandparent, …]"""
        return [category_id, *(c.id for c in self.ancestors(category_id))]

    def descendants(self, category_id: uuid.UUID) -> set[uuid.UUID]:
        children: dict[uuid.UUID | None, list[uuid.UUID]] = {}
        for cat in self.by_id.values():
            children.setdefault(cat.parent_id, []).append(cat.id)
        out: set[uuid.UUID] = set()
        stack = [category_id]
        while stack:
            current = stack.pop()
            for child in children.get(current, []):
                if child not in out:
                    out.add(child)
                    stack.append(child)
        return out

    def depth(self, category_id: uuid.UUID) -> int:
        return len(self.ancestors(category_id))

    def path(self, category_id: uuid.UUID) -> str:
        chain = [self.by_id[category_id], *self.ancestors(category_id)]
        return " / ".join(c.name for c in reversed(chain))

    def path_slugs(self, category_id: uuid.UUID) -> list[str]:
        chain = [self.by_id[category_id], *self.ancestors(category_id)]
        return [c.slug for c in reversed(chain)]

    def ref(self, category_id: uuid.UUID) -> CategoryRef:
        cat = self.by_id[category_id]
        return CategoryRef(id=cat.id, slug=cat.slug, name=cat.name, path=self.path(cat.id))


def load_tree(session: Session, organization_id: uuid.UUID) -> CategoryTree:
    rows = session.scalars(
        select(ContentCategory).where(ContentCategory.organization_id == organization_id)
    )
    return CategoryTree(by_id={c.id: c for c in rows})


# ------------------------------------------------------------------ queries


def get_category(
    session: Session, organization_id: uuid.UUID, category_id: uuid.UUID
) -> ContentCategory:
    category = session.scalar(
        select(ContentCategory).where(
            ContentCategory.organization_id == organization_id, ContentCategory.id == category_id
        )
    )
    if category is None:
        raise NotFoundError("Category not found")
    return category


def get_categories(
    session: Session, organization_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ContentCategory]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return {}
    found = {
        c.id: c
        for c in session.scalars(
            select(ContentCategory).where(
                ContentCategory.organization_id == organization_id,
                ContentCategory.id.in_(wanted),
            )
        )
    }
    missing = [str(i) for i in wanted if i not in found]
    if missing:
        raise NotFoundError("Category not found", details={"missing": missing})
    return found


def to_response(tree: CategoryTree, category: ContentCategory) -> CategoryResponse:
    children_count = sum(1 for c in tree.by_id.values() if c.parent_id == category.id)
    return CategoryResponse(
        id=category.id,
        parent_id=category.parent_id,
        slug=category.slug,
        name=category.name,
        description=category.description,
        status=category.status,
        sort_order=category.sort_order,
        depth=tree.depth(category.id),
        path=tree.path(category.id),
        path_slugs=tree.path_slugs(category.id),
        children_count=children_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


def list_categories(
    session: Session, organization_id: uuid.UUID, *, include_archived: bool = False
) -> list[CategoryResponse]:
    """Depth-first order (parents before children), sorted by sort_order then name."""
    tree = load_tree(session, organization_id)
    children: dict[uuid.UUID | None, list[ContentCategory]] = {}
    for cat in tree.by_id.values():
        if not include_archived and cat.status == CategoryStatus.ARCHIVED:
            continue
        children.setdefault(cat.parent_id, []).append(cat)
    for group in children.values():
        group.sort(key=lambda c: (c.sort_order, c.name.lower()))
    out: list[CategoryResponse] = []

    def walk(parent_id: uuid.UUID | None, depth: int) -> None:
        if depth > MAX_DEPTH + 1:
            return
        for cat in children.get(parent_id, []):
            out.append(to_response(tree, cat))
            walk(cat.id, depth + 1)

    walk(None, 0)
    # Orphans whose parent is archived/missing from the filtered map still show up.
    listed = {c.id for c in out}
    for cat in tree.by_id.values():
        if cat.id not in listed and (include_archived or cat.status == CategoryStatus.ACTIVE):
            out.append(to_response(tree, cat))
    return out


# ------------------------------------------------------------------ mutations


def _assert_no_cycle(
    tree: CategoryTree, category_id: uuid.UUID, new_parent: uuid.UUID | None
) -> None:
    if new_parent is None:
        return
    if new_parent == category_id:
        raise CategoryCycleError("A category cannot be its own parent")
    if new_parent in tree.descendants(category_id):
        raise CategoryCycleError("Moving a category under its own descendant would create a cycle")
    if tree.depth(new_parent) + 1 > MAX_DEPTH:
        raise CategoryCycleError(f"Category depth is limited to {MAX_DEPTH} levels")


def create_category(
    session: Session, organization_id: uuid.UUID, data: CategoryCreate
) -> ContentCategory:
    parent = get_category(session, organization_id, data.parent_id) if data.parent_id else None
    if parent is not None:
        if parent.status == CategoryStatus.ARCHIVED:
            raise CategoryArchivedError("Cannot add a child under an archived category")
        tree = load_tree(session, organization_id)
        if tree.depth(parent.id) + 1 > MAX_DEPTH:
            raise CategoryCycleError(f"Category depth is limited to {MAX_DEPTH} levels")
    slug = data.slug or slugify(data.name) or "category"
    category = ContentCategory(
        id=uuid.uuid4(),
        organization_id=organization_id,
        parent_id=data.parent_id,
        slug=slug,
        name=data.name,
        description=(data.description or "").strip() or None,
        sort_order=data.sort_order,
    )
    session.add(category)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise DuplicateCategorySlugError(
            "A sibling category with this slug already exists", details={"slug": slug}
        ) from exc
    return category


def update_category(
    session: Session, organization_id: uuid.UUID, category_id: uuid.UUID, data: CategoryUpdate
) -> ContentCategory:
    category = get_category(session, organization_id, category_id)
    changes = data.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        new_parent = changes["parent_id"]
        if new_parent is not None:
            parent = get_category(session, organization_id, new_parent)
            if parent.status == CategoryStatus.ARCHIVED:
                raise CategoryArchivedError("Cannot move under an archived category")
        _assert_no_cycle(load_tree(session, organization_id), category.id, new_parent)
    if changes.get("status") == CategoryStatus.ARCHIVED:
        _assert_archivable(session, organization_id, category)
    for key, value in changes.items():
        setattr(category, key, value)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise DuplicateCategorySlugError(
            "A sibling category with this slug already exists", details={"slug": category.slug}
        ) from exc
    return category


def count_references(session: Session, category_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, column in _REFERENCE_CHECKS:
        # table/column come from register_reference() constants, never from user input.
        n = session.execute(
            text(f'SELECT count(*) FROM "{table}" WHERE "{column}" = :cid'),  # noqa: S608
            {"cid": category_id},
        ).scalar_one()
        if n:
            counts[table] = int(n)
    return counts


def _assert_archivable(
    session: Session, organization_id: uuid.UUID, category: ContentCategory
) -> None:
    if category.slug == UNCATEGORIZED_SLUG and category.parent_id is None:
        raise CategoryInUseError("The Uncategorized fallback cannot be archived")
    active_children = session.scalar(
        select(func.count()).where(
            ContentCategory.parent_id == category.id,
            ContentCategory.status == CategoryStatus.ACTIVE,
        )
    )
    if active_children:
        raise CategoryInUseError(
            "Archive or move the child categories first",
            details={"active_children": int(active_children)},
        )


def delete_category(session: Session, organization_id: uuid.UUID, category_id: uuid.UUID) -> None:
    """Hard delete only when nothing references the category; otherwise archive instead."""
    category = get_category(session, organization_id, category_id)
    _assert_archivable(session, organization_id, category)
    refs = count_references(session, category.id)
    children = session.scalar(select(func.count()).where(ContentCategory.parent_id == category.id))
    if refs or children:
        raise CategoryInUseError(
            "Category is referenced by media, folder rules or channel rules; archive it instead",
            details={"references": refs, "children": int(children or 0)},
        )
    session.delete(category)
    session.flush()


def ensure_uncategorized(session: Session, organization_id: uuid.UUID) -> ContentCategory:
    existing = session.scalar(
        select(ContentCategory).where(
            ContentCategory.organization_id == organization_id,
            ContentCategory.parent_id.is_(None),
            ContentCategory.slug == UNCATEGORIZED_SLUG,
        )
    )
    if existing is not None:
        return existing
    category = ContentCategory(
        id=uuid.uuid4(),
        organization_id=organization_id,
        parent_id=None,
        slug=UNCATEGORIZED_SLUG,
        name="Uncategorized",
        description="Fallback for final videos whose folder has no mapping rule.",
        sort_order=9_999,
    )
    session.add(category)
    session.flush()
    return category
