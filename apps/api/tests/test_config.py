from __future__ import annotations

import pytest

from affilio.core.config import Settings


def test_cors_origins_accepts_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test ,")

    settings = Settings()

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_database_url_is_secret_and_not_in_safe_summary() -> None:
    settings = Settings(database_url="postgresql+psycopg://user:hunter2@db:5432/affilio")

    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings.safe_summary())
    assert settings.database_dialect == "postgresql"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        Settings(log_level="loud")
