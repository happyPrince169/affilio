"""Category tree services: CRUD with cycle and sibling-slug protection, ancestry helpers."""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from affilio.core.enums import CategoryFacet, CategoryStatus
from affilio.core.errors import NotFoundError
from affilio.modules.taxonomy.errors import (
    CategoryArchivedError,
    CategoryCycleError,
    CategoryFacetMismatchError,
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

# Facet roots: (slug, name, sort_order). Created on demand by `ensure_facet_roots`; their
# descendants are the facet's values (Production owner / Hang, Product category / Sports, ...).
FACET_ROOTS: dict[CategoryFacet, tuple[str, str, int]] = {
    CategoryFacet.PRODUCTION_OWNER: ("production-owner", "Production owner", 10),
    CategoryFacet.PRODUCT_CATEGORY: ("product-category", "Product category", 20),
    CategoryFacet.CONTENT_TYPE: ("content-type", "Content type", 30),
}

_APOSTROPHES = "'\u2019\u2018`\u02bc"
_NON_WORD = re.compile(r"[^0-9a-z\u00c0-\u024f\u1e00-\u1eff]+")


def normalize_name(value: str) -> str:
    """Matching key for folder names and category names/slugs: NFKC, case-folded, apostrophes
    dropped, every other non-alphanumeric run collapsed to one space. "Men's Fashion",
    "mens-fashion" and "MENS_FASHION" all normalize to "mens fashion"; Vietnamese letters
    are kept so "Minh Anh" and "minh-anh" match while "Hào" and "Hao" do not."""
    text_value = unicodedata.normalize("NFKC", value or "").casefold()
    for mark in _APOSTROPHES:
        text_value = text_value.replace(mark, "")
    return _NON_WORD.sub(" ", text_value).strip()[:160]


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

    def facet_root(self, facet: CategoryFacet) -> ContentCategory | None:
        return next(
            (c for c in self.by_id.values() if c.parent_id is None and c.facet == facet), None
        )

    def facet_roots(self) -> dict[CategoryFacet, ContentCategory]:
        return {f: r for f in CategoryFacet if (r := self.facet_root(f)) is not None}

    def facet_values(self, facet: CategoryFacet) -> list[ContentCategory]:
        """Active categories under the facet root (any depth), excluding the root itself."""
        root = self.facet_root(facet)
        if root is None:
            return []
        ids = self.descendants(root.id)
        return [self.by_id[i] for i in ids if self.by_id[i].status == CategoryStatus.ACTIVE]

    def find_facet_value(self, facet: CategoryFacet, raw_name: str) -> ContentCategory | None:
        """Active facet value whose name or slug normalizes like `raw_name` (a folder name)."""
        wanted = normalize_name(raw_name)
        if not wanted:
            return None
        for cat in sorted(self.facet_values(facet), key=lambda c: (self.depth(c.id), c.name)):
            if normalize_name(cat.name) == wanted or normalize_name(cat.slug) == wanted:
                return cat
        return None


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
        facet=category.facet,
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
    facet = data.facet
    if parent is not None:
        if parent.status == CategoryStatus.ARCHIVED:
            raise CategoryArchivedError("Cannot add a child under an archived category")
        tree = load_tree(session, organization_id)
        if tree.depth(parent.id) + 1 > MAX_DEPTH:
            raise CategoryCycleError(f"Category depth is limited to {MAX_DEPTH} levels")
        if facet is not None and facet != parent.facet:
            raise CategoryFacetMismatchError(
                "A child category inherits its parent's facet",
                details={"parent_facet": parent.facet.value if parent.facet else None},
            )
        facet = parent.facet
    slug = data.slug or slugify(data.name) or "category"
    category = ContentCategory(
        id=uuid.uuid4(),
        organization_id=organization_id,
        parent_id=data.parent_id,
        slug=slug,
        name=data.name,
        description=(data.description or "").strip() or None,
        sort_order=data.sort_order,
        facet=facet,
    )
    session.add(category)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise DuplicateCategorySlugError(
            "A sibling category with this slug (or a root for this facet) already exists",
            details={"slug": slug, "facet": facet.value if facet else None},
        ) from exc
    return category


def update_category(
    session: Session, organization_id: uuid.UUID, category_id: uuid.UUID, data: CategoryUpdate
) -> ContentCategory:
    category = get_category(session, organization_id, category_id)
    changes = data.model_dump(exclude_unset=True)
    moved_facet: CategoryFacet | object | None = _UNSET
    if "parent_id" in changes:
        new_parent = changes["parent_id"]
        tree = load_tree(session, organization_id)
        if new_parent is not None:
            parent = get_category(session, organization_id, new_parent)
            if parent.status == CategoryStatus.ARCHIVED:
                raise CategoryArchivedError("Cannot move under an archived category")
            moved_facet = parent.facet
        elif category.facet is not None and tree.facet_root(category.facet) is not None:
            # A value moved to the top level would become a second root for its facet.
            moved_facet = None
        _assert_no_cycle(tree, category.id, new_parent)
    if changes.get("status") == CategoryStatus.ARCHIVED:
        _assert_archivable(session, organization_id, category)
    for key, value in changes.items():
        setattr(category, key, value)
    if moved_facet is not _UNSET:
        # The subtree follows its new parent's facet (or loses it at the top level).
        tree = load_tree(session, organization_id)
        category.facet = moved_facet  # type: ignore[assignment]
        for child_id in tree.descendants(category.id):
            tree.by_id[child_id].facet = moved_facet  # type: ignore[assignment]
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise DuplicateCategorySlugError(
            "A sibling category with this slug already exists", details={"slug": category.slug}
        ) from exc
    return category


_UNSET = object()


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
    if category.facet is not None and category.parent_id is None:
        raise CategoryInUseError("A facet root cannot be archived; archive its values instead")
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


def facet_roots(
    session: Session, organization_id: uuid.UUID
) -> dict[CategoryFacet, ContentCategory]:
    return load_tree(session, organization_id).facet_roots()


def facets_enabled(session: Session, organization_id: uuid.UUID) -> bool:
    """Path-based facet classification runs only once every facet root exists."""
    return len(facet_roots(session, organization_id)) == len(CategoryFacet)


def ensure_facet_roots(
    session: Session, organization_id: uuid.UUID
) -> dict[CategoryFacet, ContentCategory]:
    """Create the missing facet roots (idempotent). A top-level category that already uses a
    root's slug is adopted as that root instead of duplicated."""
    tree = load_tree(session, organization_id)
    roots = tree.facet_roots()
    for facet, (slug, name, sort_order) in FACET_ROOTS.items():
        if facet in roots:
            continue
        existing = next(
            (
                c
                for c in tree.by_id.values()
                if c.parent_id is None and c.slug == slug and c.facet is None
            ),
            None,
        )
        if existing is not None:
            existing.facet = facet
            for child_id in tree.descendants(existing.id):
                tree.by_id[child_id].facet = facet
            roots[facet] = existing
            continue
        root = ContentCategory(
            id=uuid.uuid4(),
            organization_id=organization_id,
            parent_id=None,
            slug=slug,
            name=name,
            description=f"Facet root: {name.lower()} values derived from the finals path.",
            sort_order=sort_order,
            facet=facet,
        )
        session.add(root)
        roots[facet] = root
    session.flush()
    return roots
