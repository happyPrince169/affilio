"""Error-text redaction (M3C.1 step 3). Pure unit tests, no database."""

from __future__ import annotations

from pathlib import Path

import pytest

from affilio.core.config import Settings
from affilio.core.redaction import PATH_PLACEHOLDER, REDACTED, Redactor, redact_message

ROOTS = Redactor.from_paths(
    {
        "originals": "/media/originals",
        "proxies": "/media/proxies",
        "finals": "/media/finals",
        "thumbnails": "/data/thumbnails",
    }
)
NAS = Redactor.from_paths(
    {
        "originals": "/volume1/ContentFactory/fashion/originals",
        "proxies": "/volume1/ContentFactory/fashion/proxies",
        "finals": "/volume1/ContentFactory/fashion/FINAL",
    }
)


@pytest.mark.parametrize(
    ("redactor", "raw", "expected"),
    [
        # OSError text with a quoted container path keeps the relative path and the quotes.
        (
            ROOTS,
            "[Errno 13] Permission denied: '/media/originals/290826/2'",
            "[Errno 13] Permission denied: 'originals/290826/2'",
        ),
        # ffprobe stderr names the input file first.
        (
            ROOTS,
            "/media/originals/230826/C1603.MP4: Invalid data found when processing input",
            "originals/230826/C1603.MP4: Invalid data found when processing input",
        ),
        # Spaces and Unicode inside a known root survive intact.
        (
            ROOTS,
            "ffprobe failed for '/media/finals/Hang/T9/NC cặp đôi .mp4' (moov atom not found)",
            "ffprobe failed for 'finals/Hang/T9/NC cặp đôi .mp4' (moov atom not found)",
        ),
        # The root directory itself, with or without a trailing slash.
        (ROOTS, "cannot scan /media/originals/ right now", "cannot scan originals/ right now"),
        (ROOTS, "cannot scan /media/originals right now", "cannot scan originals right now"),
        # Host (NAS) paths when the CLI runs with host mounts.
        (
            NAS,
            "[Errno 5] Input/output error: '/volume1/ContentFactory/fashion/FINAL/Hao/T9/x.mp4'",
            "[Errno 5] Input/output error: 'finals/Hao/T9/x.mp4'",
        ),
        # Absolute paths outside every root keep only their last component.
        (
            ROOTS,
            "/volume1/ContentFactory/Cafe/1/clip.mp4: Permission denied",
            f"{PATH_PLACEHOLDER}/clip.mp4: Permission denied",
        ),
        (ROOTS, "/no/such/ffmpeg not found", f"{PATH_PLACEHOLDER}/ffmpeg not found"),
        (
            ROOTS,
            "wrote /tmp/affilio-worker-heartbeat",
            f"wrote {PATH_PLACEHOLDER}/affilio-worker-heartbeat",
        ),
        # Multi-line ffmpeg stderr with ANSI colour and a path collapses to one clean line.
        (
            ROOTS,
            "\x1b[31m[mov,mp4 @ 0x55d1c0] moov atom not found\x1b[0m\n"
            "/media/proxies/x.mp4: Invalid data\n",
            "[mov,mp4 @ 0x55d1c0] moov atom not found proxies/x.mp4: Invalid data",
        ),
        # Connection URLs lose their credentials, keep host and database.
        (
            ROOTS,
            "OperationalError: connection failed: postgresql+psycopg://affilio:s3cret@postgres:5432/affilio",
            f"OperationalError: connection failed: postgresql+psycopg://{REDACTED}:{REDACTED}@postgres:5432/affilio",
        ),
        # key=value secrets and bearer tokens.
        (
            ROOTS,
            "env MEDIA_ADMIN_TOKEN=0123456789abcdef POSTGRES_PASSWORD='hunter two' kept",
            f"env MEDIA_ADMIN_TOKEN={REDACTED} POSTGRES_PASSWORD='{REDACTED}' kept",
        ),
        (
            ROOTS,
            'payload {"access_token": "EAAB1234567890", "page_id": "42"}',
            f'payload {{"access_token": "{REDACTED}", "page_id": "42"}}',
        ),
        (
            ROOTS,
            "header Authorization: Bearer EAABsbCS1iHgBA.ZZZ",
            f"header Authorization: Bearer {REDACTED}",
        ),
        # Prose that merely mentions a token is left readable.
        (
            ROOTS,
            "token_invalid: The access token has expired (Meta error 190)",
            "token_invalid: The access token has expired (Meta error 190)",
        ),
        # URL paths are not filesystem paths.
        (
            ROOTS,
            "Meta answered HTTP 400 for https://graph.facebook.com/v26.0/me/accounts",
            "Meta answered HTTP 400 for https://graph.facebook.com/v26.0/me/accounts",
        ),
        # Codec descriptors and fractions are not paths either.
        (
            ROOTS,
            "pix_fmt yuv420p10le, fps 30000/1001, h264/aac",
            "pix_fmt yuv420p10le, fps 30000/1001, h264/aac",
        ),
        (ROOTS, None, ""),
    ],
)
def test_redaction_cases(redactor: Redactor, raw: object, expected: str) -> None:
    assert redactor.message(raw) == expected


def test_truncation_is_bounded_and_marked() -> None:
    long = "x" * 1200
    assert ROOTS.message(long, limit=100) == "x" * 99 + "…"
    assert len(ROOTS.message(long, limit=1000)) == 1000
    assert ROOTS.message(long, limit=0) == long  # explicit no-limit


def test_from_settings_uses_configured_roots_and_their_realpaths(tmp_path: Path) -> None:
    real = tmp_path / "real-originals"
    real.mkdir()
    link = tmp_path / "originals-link"
    link.symlink_to(real)
    settings = Settings(
        media_originals_path=link,
        media_finals_path=tmp_path / "finals",
        media_proxies_path=None,
    )
    redactor = Redactor.from_settings(settings)
    assert redactor.message(f"stat failed: '{link}/a.mp4'") == "stat failed: 'originals/a.mp4'"
    assert redactor.message(f"stat failed: '{real}/a.mp4'") == "stat failed: 'originals/a.mp4'"
    assert redactor.message(f"{tmp_path / 'finals'}/b.mp4 broken") == "finals/b.mp4 broken"
    # Unconfigured root → generic path handling, never the raw path.
    assert (
        redactor.message(f"{tmp_path}/proxies/c.mp4 broken") == f"{PATH_PLACEHOLDER}/c.mp4 broken"
    )
    assert str(tmp_path) not in redact_message(f"{tmp_path}/x", settings=settings)
