"""Publishing error types (all rendered through the standard error envelope)."""

from __future__ import annotations

from fastapi import status

from affilio.core.errors import AppError


class InvalidPublicationTransitionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "invalid_publication_transition"


class VersionConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "version_conflict"


class DuplicatePublicationError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_publication"


class ScheduleConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "schedule_conflict"


class DuplicateEvidenceError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_evidence"


class MediaNotApprovedError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "media_not_approved"


class TargetUnavailableError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "target_unavailable"


class ContentInvalidError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "content_invalid"


class EvidenceInvalidError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "evidence_invalid"


class ScheduleInvalidError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "schedule_invalid"


class CampaignClosedError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "campaign_closed"


class TargetBlockedError(AppError):
    """The target account is no longer eligible for this video (channel rules / disabled)."""

    status_code = status.HTTP_409_CONFLICT
    code = "target_blocked"


class RevalidationRequiredError(AppError):
    """The video was reclassified after approval; an admin must revalidate or revoke."""

    status_code = status.HTTP_409_CONFLICT
    code = "revalidation_required"
