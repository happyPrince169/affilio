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


class OwnerMappingInvalidCategoryError(AppError):
    """The category is not an active value under the production-owner facet root."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "owner_mapping_invalid_category"


class OwnerMappingConflictError(AppError):
    """The member already holds an active mapping to that production owner."""

    status_code = status.HTTP_409_CONFLICT
    code = "owner_mapping_conflict"


# ---------------------------------------------------------------- editor uploads (U4)


class UploadForbiddenError(AppError):
    """The caller may not submit for that owner / editor, or act on that session."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "upload_forbidden"


class UploadInvalidTaxonomyError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "upload_invalid_taxonomy"


class UploadInvalidRevisionError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "upload_invalid_revision"


class UploadInvalidFileError(AppError):
    """Filename, extension or size refused at session creation."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "upload_invalid_file"


class UploadQuotaExceededError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "upload_quota_exceeded"


class UploadStateError(AppError):
    """The operation is not valid in the session's current state."""

    status_code = status.HTTP_409_CONFLICT
    code = "upload_invalid_state"


class UploadGoneError(AppError):
    """Expired or aborted session."""

    status_code = status.HTTP_410_GONE
    code = "upload_gone"


class UploadOffsetMismatchError(AppError):
    """`Upload-Offset` differs from the committed offset; details carry the server value."""

    status_code = status.HTTP_409_CONFLICT
    code = "upload_offset_mismatch"


class UploadLockedError(AppError):
    """Another chunk for this session is being written."""

    status_code = status.HTTP_423_LOCKED
    code = "upload_locked"


class UploadChunkTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "upload_chunk_too_large"


class UploadChunkDigestMismatchError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "upload_chunk_digest_mismatch"


class UploadIncompleteError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "upload_incomplete"


class UploadStorageUnavailableError(AppError):
    """The staging directory is not configured or not writable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "upload_storage_unavailable"
