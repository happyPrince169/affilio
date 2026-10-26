"""Product-module errors mapped to the API error envelope."""

from __future__ import annotations

from fastapi import status

from affilio.core.errors import AppError


class InvalidProductUrlError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_product_url"


class DuplicateProductError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_product"


class InvalidStatusTransitionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "invalid_status_transition"


class WinnerConfirmationRequiredError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "winner_confirmation_required"


class DuplicateMediaLinkError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_media_link"
