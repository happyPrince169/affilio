"""Centralized, typed application configuration.

Values are read from (lowest to highest precedence):
  1. defaults declared below
  2. the repository-root `.env`
  3. `apps/api/.env`
  4. process environment variables

Nothing here should ever be returned from an HTTP endpoint verbatim; see `safe_summary()`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[3]  # apps/api (or /app inside the container)
# Repository root two levels up when running from the monorepo; inside the image /app has no
# such ancestor, so fall back to the API directory itself (its .env, if any, is still read).
REPO_ROOT = API_DIR.parents[1] if len(API_DIR.parents) > 1 else API_DIR

AppEnv = Literal["development", "test", "staging", "production"]
LogFormat = Literal["console", "json"]
MetaProviderMode = Literal["live", "mock"]

# Settings a live Meta connection needs. Names only; reported by the API when missing.
META_LIVE_REQUIRED = ("META_APP_ID", "META_APP_SECRET", "META_OAUTH_REDIRECT_URI")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application ---
    app_name: str = "AFFILIO API"
    app_env: AppEnv = "development"
    debug: bool = False
    docs_enabled: bool = True
    api_v1_prefix: str = "/api/v1"

    # --- logging ---
    log_level: str = "INFO"
    log_format: LogFormat = "console"

    # --- http ---
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- database ---
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://affilio:affilio@localhost:5440/affilio"
    )
    database_echo: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 5
    database_connect_timeout_seconds: int = 3

    # --- organization context (until authentication exists) ---
    # Requests may name an organization with the X-Organization-Slug header; otherwise this
    # slug is used. Replaced by authenticated membership resolution in M3.
    default_organization_slug: str = "demo-org"

    # --- media library (M1) ---
    # Container/host paths for the four logical media roots. Unset roots are "not configured".
    # Only the thumbnails and exports roots are ever written to.
    media_originals_path: Path | None = None
    media_proxies_path: Path | None = None
    # Finished videos uploaded by editors (read-only; indexed independently of originals).
    media_finals_path: Path | None = None
    media_thumbnails_path: Path | None = None
    media_exports_path: Path | None = None
    media_video_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            ".mp4",
            ".mov",
            ".m4v",
            ".mkv",
            ".avi",
            ".mts",
            ".m2ts",
            ".webm",
            ".mxf",
            ".3gp",
        ]
    )
    # Names skipped during scans. Synology creates @eaDir / #recycle / #snapshot everywhere.
    media_ignore_names: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["@eaDir", "#recycle", "#snapshot", ".DS_Store", "Thumbs.db"]
    )
    # Candidate proxy filenames relative to the SAME directory under the proxies root.
    # Placeholders: {stem} (filename without extension), {name} (full filename), {ext}.
    media_proxy_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["{stem}.mp4", "{stem}_proxy.mp4", "{name}.mp4", "{stem}.mov"]
    )
    media_ffprobe_path: str = "ffprobe"
    media_ffmpeg_path: str = "ffmpeg"
    media_probe_timeout_seconds: int = 60
    media_thumbnail_timeout_seconds: int = 120
    media_thumbnail_width: int = 640
    media_thumbnail_concurrency: int = 1
    media_thumbnail_max_attempts: int = 3
    media_scan_max_attempts: int = 3
    media_scan_max_errors: int = 200
    media_scan_commit_every: int = 200
    # Watchdog (M3C.1 step 1): a running scan refreshes `heartbeat_at` (and commits progress) at
    # least every MEDIA_SCAN_HEARTBEAT_SECONDS; a running job whose heartbeat is older than
    # MEDIA_SCAN_STALE_AFTER_SECONDS is treated as abandoned by a dead worker and re-queued
    # (or failed once max_attempts is spent). Stale must be at least 3x the heartbeat.
    media_scan_heartbeat_seconds: int = 30
    media_scan_stale_after_seconds: int = 600
    # Final handoff: a file is indexed only once its mtime is older than this (upload settled).
    media_final_settle_seconds: int = 120
    # --- editor uploads (ADR 0012, amendment 2: dedicated upload root) ---
    # AFFILIO_UPLOADS root (`staging/` and `ready/` inside it). The API needs `staging/`
    # writable; the worker needs the whole root writable and MEDIA_UPLOADS_WRITABLE=true to
    # finalize. Legacy finals (MEDIA_FINALS_PATH) stay read-only in every process.
    media_uploads_path: Path | None = None
    media_uploads_writable: bool = False
    upload_chunk_bytes: int = 8 * 1024 * 1024
    upload_max_file_bytes: int = 8 * 1024 * 1024 * 1024
    upload_max_active_sessions_per_user: int = 20
    upload_session_ttl_hours: int = 48
    upload_staging_max_bytes: int = 100 * 1024 * 1024 * 1024
    upload_free_space_headroom_bytes: int = 1024 * 1024 * 1024
    upload_finalize_max_attempts: int = 3
    upload_finalize_lease_seconds: int = 600
    media_temp_name_markers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".part", ".tmp", ".crdownload", ".partial", "~$", ".uploading"]
    )
    # Required (header X-Affilio-Admin-Token) for mutating media endpoints outside development.
    media_admin_token: SecretStr | None = None

    # --- authentication (M3A) ---
    session_cookie_name: str = "affilio_session"
    session_ttl_days: int = 14
    # None → secure cookies outside development/test. Set explicitly behind plain-HTTP LAN setups.
    session_cookie_secure: bool | None = None
    session_cookie_domain: str | None = None
    # Failed sign-in attempts allowed per client IP and per email within the window before
    # /auth/login answers 429. In-process; see affilio.core.ratelimit for the scope.
    auth_login_max_attempts_per_minute: int = 10
    auth_login_window_seconds: int = 60
    # Trust X-Forwarded-For for the client IP (rate limiting). Only behind the web proxy /
    # a reverse proxy that overwrites the header; off by default so clients cannot spoof it.
    trust_proxy_headers: bool = False
    # Development seed only (affilio.scripts.seed_dev refuses to run outside dev/test).
    seed_admin_email: str = "owner@affilio.local"
    seed_admin_password: SecretStr = SecretStr("affilio-dev-owner")

    # --- publishing (M3A) ---
    publication_min_gap_minutes: int = 30

    # --- Meta / Facebook Page connection (M3B.1A) ---
    # `live` talks to the Graph API through the live provider (needs the app settings below);
    # `mock` uses the deterministic in-process provider for development and tests and is
    # refused outside development/test (validated below).
    meta_provider_mode: MetaProviderMode = "live"
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_graph_api_version: str = "v26.0"
    # Browser-facing callback URL, byte-identical to the Meta app's "Valid OAuth Redirect URIs".
    # Never derived from request headers. Must be https outside development/test.
    meta_oauth_redirect_uri: str | None = None
    meta_oauth_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
    )
    # Facebook Login for Business configuration id (replaces `scope` when set).
    meta_login_config_id: str | None = None
    meta_oauth_state_ttl_seconds: int = 600
    # Where the OAuth callback sends the browser afterwards. Empty → relative to the API host
    # (correct behind the same-origin web proxy); set to the web origin in split-host dev.
    web_base_url: str | None = None

    # --- credential vault (M3B.1A) ---
    # Key ring: comma-separated `key_id:base64(32 bytes)` entries. Generate each key with
    # `openssl rand -base64 32`. Never committed; backed up separately from the database.
    credential_encryption_keys: SecretStr | None = None
    # Key used for new encryptions; defaults to the only key when exactly one is configured.
    credential_encryption_active_key_id: str | None = None

    # --- Reels publishing engine (M3B.2A) ---
    # Live Reels publishing stays off until the Meta app exists and the activation checklist
    # has been run; the engine fails closed for non-mock Pages while this is False.
    meta_live_publishing_enabled: bool = False
    meta_reels_daily_quota: int = 30  # documented: 30 API-published Reels per 24 h per Page
    publisher_poll_interval_seconds: float = 5.0
    publisher_max_concurrent_uploads: int = 2  # global step concurrency (thread pool size)
    publisher_lease_seconds: int = 120
    publisher_upload_chunk_bytes: int = 4 * 1024 * 1024
    publisher_max_retries: int = 5
    publisher_backoff_base_seconds: float = 30.0
    publisher_backoff_max_seconds: float = 900.0
    publisher_status_poll_seconds: float = 15.0
    publisher_publish_timeout_minutes: int = 30
    publisher_rate_limit_hold_minutes: int = 60
    # Scheduled jobs missed during downtime (restart policy): within the grace window they are
    # released after the usual revalidation; beyond it `review` flags them for an operator
    # (per-job "Release now"), `publish` releases them anyway. Every decision is audited.
    publisher_overdue_grace_minutes: int = 120
    publisher_overdue_policy: Literal["review", "publish"] = "review"
    publisher_heartbeat_path: Path = Path("/tmp/affilio-publisher-heartbeat")  # noqa: S108
    # Media acceptance for Reels (feasibility B3); the engine never transcodes.
    reels_min_duration_seconds: float = 3.0
    reels_max_duration_seconds: float = 90.0
    reels_min_width: int = 540
    reels_min_height: int = 960
    reels_max_file_bytes: int = 2 * 1024 * 1024 * 1024
    reels_min_frame_rate: float = 23.0
    reels_max_frame_rate: float = 61.0

    # --- publish-ready exports (Export pipeline E1) ---
    # The recipe is fixed and versioned in code (`media/exports.py`); these knobs only bound
    # CPU/time. No user-supplied ffmpeg arguments exist anywhere.
    export_threads: int = 2  # ffmpeg -threads (leave cores for the API and the NAS)
    export_nice: int = 10  # scheduling priority of ffmpeg (0 = normal, 19 = lowest)
    export_preset: Literal["ultrafast", "veryfast", "faster", "fast", "medium", "slow"] = "medium"
    export_crf: int = 18  # x264 constant quality (lower = larger/better), bounded 14..28
    export_max_bitrate_kbps: int = 12_000
    export_audio_bitrate_kbps: int = 160
    # Wall-clock budget per export: factor x media duration, never below the minimum.
    export_timeout_factor: float = 20.0
    export_timeout_min_seconds: int = 120
    export_heartbeat_seconds: float = 15.0  # fenced heartbeat/cancel check while ffmpeg runs
    export_stale_after_seconds: int = 900  # watchdog: re-queue running jobs silent this long
    export_max_attempts: int = 3
    export_poll_interval_seconds: float = 5.0
    exporter_heartbeat_path: Path = Path("/tmp/affilio-exporter-heartbeat")  # noqa: S108

    # --- maintenance (M3B.1A) ---
    # Expired/revoked sessions and finished OAuth attempts are deleted after these windows.
    session_retention_days: int = 30
    oauth_state_retention_hours: int = 24
    maintenance_interval_seconds: int = 3600

    # --- background worker ---
    worker_poll_interval_seconds: float = 5.0
    worker_heartbeat_path: Path = Path("/tmp/affilio-worker-heartbeat")  # noqa: S108

    @field_validator(
        "cors_origins",
        "media_video_extensions",
        "media_ignore_names",
        "media_proxy_patterns",
        "media_temp_name_markers",
        "meta_oauth_scopes",
        mode="before",
    )
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("media_video_extensions")
    @classmethod
    def _normalize_extensions(cls, value: list[str]) -> list[str]:
        normalized = []
        for ext in value:
            ext = ext.strip().lower()
            if not ext:
                continue
            normalized.append(ext if ext.startswith(".") else f".{ext}")
        return normalized

    @field_validator(
        "media_originals_path",
        "media_proxies_path",
        "media_finals_path",
        "media_uploads_path",
        "media_thumbnails_path",
        "media_exports_path",
        mode="before",
    )
    @classmethod
    def _empty_path_is_none(cls, value: object) -> object:
        # An empty `MEDIA_X_PATH=` line must mean "not configured", never Path("") == CWD.
        if isinstance(value, str):
            stripped = value.strip()
            return Path(stripped).expanduser() if stripped else None
        return value

    @field_validator(
        "media_admin_token",
        "meta_app_id",
        "meta_app_secret",
        "meta_oauth_redirect_uri",
        "meta_login_config_id",
        "credential_encryption_keys",
        "credential_encryption_active_key_id",
        "web_base_url",
        # Compose passes `${SESSION_COOKIE_SECURE:-}` / `${SESSION_COOKIE_DOMAIN:-}` as empty
        # strings when unset; an empty value must mean "not configured", not a parse error.
        "session_cookie_secure",
        "session_cookie_domain",
        mode="before",
    )
    @classmethod
    def _empty_token_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("meta_graph_api_version")
    @classmethod
    def _graph_version(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized.startswith("v") or not normalized[1:].replace(".", "", 1).isdigit():
            msg = f"META_GRAPH_API_VERSION must look like v26.0, got {value!r}"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def _meta_mode_guard(self) -> Settings:
        """Fail at startup rather than at request time for unsafe Meta configurations."""
        if self.meta_provider_mode == "mock" and not self.is_development:
            msg = (
                "META_PROVIDER_MODE=mock is only allowed when APP_ENV is development or test; "
                "mock Facebook connections must never exist in staging or production"
            )
            raise ValueError(msg)
        if (
            self.meta_oauth_redirect_uri
            and not self.is_development
            and not self.meta_oauth_redirect_uri.lower().startswith("https://")
        ):
            msg = "META_OAUTH_REDIRECT_URI must use https outside development/test"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _export_bounds_guard(self) -> Settings:
        """Keep the transcoder inside sane CPU/quality bounds whatever the environment says."""
        problems = []
        if not 1 <= self.export_threads <= 8:
            problems.append("EXPORT_THREADS must be between 1 and 8")
        if not 0 <= self.export_nice <= 19:
            problems.append("EXPORT_NICE must be between 0 and 19")
        if not 14 <= self.export_crf <= 28:
            problems.append("EXPORT_CRF must be between 14 and 28")
        if not 2_000 <= self.export_max_bitrate_kbps <= 40_000:
            problems.append("EXPORT_MAX_BITRATE_KBPS must be between 2000 and 40000")
        if not 64 <= self.export_audio_bitrate_kbps <= 320:
            problems.append("EXPORT_AUDIO_BITRATE_KBPS must be between 64 and 320")
        if self.export_timeout_factor < 0 or self.export_timeout_min_seconds < 1:
            problems.append(
                "EXPORT_TIMEOUT_FACTOR must be >= 0 and EXPORT_TIMEOUT_MIN_SECONDS >= 1"
            )
        if self.export_heartbeat_seconds < 0:
            problems.append("EXPORT_HEARTBEAT_SECONDS must be >= 0")
        minimum = max(3 * self.export_heartbeat_seconds, 1)
        if self.export_stale_after_seconds < minimum:
            problems.append(
                "EXPORT_STALE_AFTER_SECONDS must be at least 3x EXPORT_HEARTBEAT_SECONDS "
                f"(>= {minimum:.0f}s)"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _scan_watchdog_guard(self) -> Settings:
        """A stale window shorter than a few heartbeats would recover healthy scans."""
        if self.media_scan_heartbeat_seconds < 0:
            msg = "MEDIA_SCAN_HEARTBEAT_SECONDS must be >= 0"
            raise ValueError(msg)
        minimum = max(3 * self.media_scan_heartbeat_seconds, 1)
        if self.media_scan_stale_after_seconds < minimum:
            msg = (
                "MEDIA_SCAN_STALE_AFTER_SECONDS must be at least 3x MEDIA_SCAN_HEARTBEAT_SECONDS "
                f"(>= {minimum}s), got {self.media_scan_stale_after_seconds}s"
            )
            raise ValueError(msg)
        return self

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            msg = f"Unsupported LOG_LEVEL: {value}"
            raise ValueError(msg)
        return normalized

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env in {"development", "test"}

    @property
    def cookie_secure(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return not self.is_development

    @property
    def meta_live_missing_settings(self) -> list[str]:
        """Names (never values) of the live-provider settings that are not configured."""
        present = {
            "META_APP_ID": bool(self.meta_app_id),
            "META_APP_SECRET": bool(self.meta_app_secret),
            "META_OAUTH_REDIRECT_URI": bool(self.meta_oauth_redirect_uri),
        }
        return [name for name in META_LIVE_REQUIRED if not present[name]]

    @property
    def is_meta_configured(self) -> bool:
        if self.meta_provider_mode == "mock":
            return True
        return not self.meta_live_missing_settings

    @property
    def database_dialect(self) -> str:
        """Driver-agnostic dialect name, safe to expose (e.g. `postgresql`)."""
        return self.database_url.get_secret_value().split(":", 1)[0].split("+", 1)[0]

    def safe_summary(self) -> dict[str, object]:
        """Non-sensitive subset of settings suitable for logs and diagnostics."""
        return {
            "app_env": self.app_env,
            "debug": self.debug,
            "api_v1_prefix": self.api_v1_prefix,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "cors_origins": self.cors_origins,
            "database_dialect": self.database_dialect,
            "meta_provider_mode": self.meta_provider_mode,
            "meta_configured": self.is_meta_configured,
            "meta_live_publishing_enabled": self.meta_live_publishing_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings. Call `get_settings.cache_clear()` in tests when needed."""
    return Settings()
