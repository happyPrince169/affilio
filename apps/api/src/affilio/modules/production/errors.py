from __future__ import annotations

from fastapi import status

from affilio.core.errors import AppError


class InvalidProductionTransitionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "invalid_production_transition"


class DuplicateJobAssociationError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_job_association"
