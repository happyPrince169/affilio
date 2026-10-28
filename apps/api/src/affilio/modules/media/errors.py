"""Media-module error types mapped to the API error envelope."""

from __future__ import annotations

from fastapi import status

from affilio.core.errors import AppError


class InvalidMediaPathError(AppError):
    """A relative path escaped (or tried to escape) its media root."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid_media_path"


class MediaRootNotConfiguredError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "media_root_not_configured"


class MediaRootUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "media_root_unavailable"


class ScanAlreadyActiveError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "scan_already_active"


class ScanConfigurationError(Exception):
    """Raised inside the worker when a scan cannot run (root missing/unreadable)."""


class ScanAbortedError(Exception):
    """Raised inside the worker when the job row no longer belongs to this run: it was
    cancelled, recovered by the watchdog, or re-claimed by another worker. The scanner rolls
    back its uncommitted work and nothing is written after it (M3C.1 step 1)."""


class ScanNotCancellableError(AppError):
    """Only queued or running scans can be cancelled."""

    status_code = status.HTTP_409_CONFLICT
    code = "scan_not_cancellable"


class ExportNotEligibleError(AppError):
    """Only active final videos with a present file can be exported (E1)."""

    status_code = status.HTTP_409_CONFLICT
    code = "export_not_eligible"


class ExportAlreadyActiveError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "export_already_active"


class ExportNotCancellableError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "export_not_cancellable"


class ExportAbortedError(Exception):
    """Raised inside the export worker when the job no longer belongs to this run (cancelled,
    recovered by the watchdog or re-claimed). ffmpeg is killed, temp output removed, nothing
    is written after it."""


class InvalidFolderPathError(AppError):
    """Folder rule path is unsafe or malformed (M3A.1)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_folder_path"


class SuggestionNameMismatchError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "suggestion_name_mismatch"
