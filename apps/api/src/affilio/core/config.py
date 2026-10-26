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

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[3]  # apps/api (or /app inside the container)
# Repository root two levels up when running from the monorepo; inside the image /app has no
# such ancestor, so fall back to the API directory itself (its .env, if any, is still read).
REPO_ROOT = API_DIR.parents[1] if len(API_DIR.parents) > 1 else API_DIR

AppEnv = Literal["development", "test", "staging", "production"]
LogFormat = Literal["console", "json"]


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
    # Final handoff: a file is indexed only once its mtime is older than this (upload settled).
    media_final_settle_seconds: int = 120
    media_temp_name_markers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".part", ".tmp", ".crdownload", ".partial", "~$", ".uploading"]
    )
    # Required (header X-Affilio-Admin-Token) for mutating media endpoints outside development.
    media_admin_token: SecretStr | None = None

    # --- background worker ---
    worker_poll_interval_seconds: float = 5.0
    worker_heartbeat_path: Path = Path("/tmp/affilio-worker-heartbeat")  # noqa: S108

    @field_validator(
        "cors_origins",
        "media_video_extensions",
        "media_ignore_names",
        "media_proxy_patterns",
        "media_temp_name_markers",
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

    @field_validator("media_admin_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

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
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings. Call `get_settings.cache_clear()` in tests when needed."""
    return Settings()
