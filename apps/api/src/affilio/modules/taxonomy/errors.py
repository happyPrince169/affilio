from __future__ import annotations

from fastapi import status

from affilio.core.errors import AppError


class CategoryCycleError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "category_cycle"


class DuplicateCategorySlugError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_category_slug"


class CategoryInUseError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "category_in_use"


class CategoryArchivedError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "category_archived"
