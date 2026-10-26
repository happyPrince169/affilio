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


class InvalidFolderPathError(AppError):
    """Folder rule path is unsafe or malformed (M3A.1)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_folder_path"
