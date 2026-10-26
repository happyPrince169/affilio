"""Category schemas. Mirrored in packages/shared-types/src/taxonomy.ts."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import CategoryStatus

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned[:80]


class CategoryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class _CategoryRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", check_fields=False)
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            msg = "name must not be blank"
            raise ValueError(msg)
        return value.strip()

    @field_validator("slug", check_fields=False)
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not _SLUG_RE.match(cleaned):
            msg = "slug must be lowercase letters, digits and single hyphens"
            raise ValueError(msg)
        return cleaned


class CategoryCreate(_CategoryRules):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=80)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2_000)
    sort_order: int = Field(default=0, ge=0, le=10_000)


class CategoryUpdate(_CategoryRules):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=80)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2_000)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    status: CategoryStatus | None = None


class CategoryResponse(CategoryModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    slug: str
    name: str
    description: str | None
    status: CategoryStatus
    sort_order: int
    depth: int
    path: str  # "Men's Sports / Pickleball"
    path_slugs: list[str]
    children_count: int
    created_at: datetime
    updated_at: datetime


class CategoryRef(CategoryModel):
    id: uuid.UUID
    slug: str
    name: str
    path: str
