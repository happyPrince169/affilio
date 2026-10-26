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

API_DIR = Path(__file__).resolve().parents[3]  # apps/api
REPO_ROOT = API_DIR.parents[1]  # repository root

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

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
