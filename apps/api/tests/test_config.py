from __future__ import annotations

from pathlib import Path

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


def test_empty_media_paths_mean_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MEDIA_ORIGINALS_PATH", "")
    monkeypatch.setenv("MEDIA_THUMBNAILS_PATH", f"  {tmp_path}  ")

    settings = Settings()

    assert settings.media_originals_path is None
    assert settings.media_thumbnails_path == tmp_path


def test_empty_compose_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Synology compose file forwards `${VAR:-}` as empty strings; they must not break
    startup and must fall back to the production default (Secure cookie, no domain)."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("META_PROVIDER_MODE", "live")  # the local .env may select mock mode
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "")
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "")
    monkeypatch.setenv("META_APP_ID", "")
    monkeypatch.setenv("META_APP_SECRET", "")
    monkeypatch.setenv("META_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", "")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.test")
    settings = Settings()
    assert settings.session_cookie_secure is None and settings.cookie_secure is True
    assert settings.session_cookie_domain is None
    assert settings.meta_live_missing_settings == [
        "META_APP_ID",
        "META_APP_SECRET",
        "META_OAUTH_REDIRECT_URI",
    ]
    assert settings.credential_encryption_keys is None
    assert settings.cors_origins == ["https://app.example.test"]


def test_scan_watchdog_rejects_a_stale_window_shorter_than_three_heartbeats() -> None:
    with pytest.raises(ValueError, match="MEDIA_SCAN_STALE_AFTER_SECONDS"):
        Settings(media_scan_heartbeat_seconds=30, media_scan_stale_after_seconds=60)
    with pytest.raises(ValueError, match="MEDIA_SCAN_HEARTBEAT_SECONDS"):
        Settings(media_scan_heartbeat_seconds=-1)
    ok = Settings(media_scan_heartbeat_seconds=30, media_scan_stale_after_seconds=90)
    assert ok.media_scan_stale_after_seconds == 90
    # Tests may disable the interval entirely; the stale window then only needs to be positive.
    assert Settings(media_scan_heartbeat_seconds=0, media_scan_stale_after_seconds=1)
