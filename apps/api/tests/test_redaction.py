"""Error-text redaction (M3C.1 step 3 + hardening). Pure unit tests, no database."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from affilio.core.config import Settings
from affilio.core.redaction import (
    INPUT_CAP,
    PATH_PLACEHOLDER,
    REDACTED,
    Redactor,
    redact_message,
)

R = PATH_PLACEHOLDER
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
SPACED = Redactor.from_paths({"originals": "/volume1/My Media/Camera Originals"})


@pytest.mark.parametrize(
    ("redactor", "raw", "expected"),
    [
        # ---------------------------------------------------------------- media roots
        (
            ROOTS,
            "[Errno 13] Permission denied: '/media/originals/290826/2'",
            "[Errno 13] Permission denied: 'originals/290826/2'",
        ),
        (
            ROOTS,
            "/media/originals/230826/C1603.MP4: Invalid data found when processing input",
            "originals/230826/C1603.MP4: Invalid data found when processing input",
        ),
        (
            ROOTS,
            "ffprobe failed for '/media/finals/Hang/T9/NC cặp đôi .mp4' (moov atom not found)",
            "ffprobe failed for 'finals/Hang/T9/NC cặp đôi .mp4' (moov atom not found)",
        ),
        (
            ROOTS,
            "/media/finals/Minh Anh/T9/Couple ao be.mp4: Permission denied",
            "finals/Minh Anh/T9/Couple ao be.mp4: Permission denied",
        ),
        (ROOTS, "cannot scan /media/originals/ right now", "cannot scan originals/ right now"),
        (ROOTS, "cannot scan /media/originals right now", "cannot scan originals right now"),
        (
            NAS,
            "[Errno 5] Input/output error: '/volume1/ContentFactory/fashion/FINAL/Hao/T9/x.mp4'",
            "[Errno 5] Input/output error: 'finals/Hao/T9/x.mp4'",
        ),
        (
            SPACED,
            "stat failed: '/volume1/My Media/Camera Originals/day 1/clip one.MP4'",
            "stat failed: 'originals/day 1/clip one.MP4'",
        ),
        # ---------------------------------------------------- paths outside every root
        (
            ROOTS,
            "/volume1/ContentFactory/Cafe/1/clip.mp4: Permission denied",
            f"{R}/clip.mp4: Permission denied",
        ),
        (ROOTS, "/no/such/ffmpeg not found", f"{R}/ffmpeg not found"),
        (ROOTS, "wrote /tmp/affilio-worker-heartbeat", f"wrote {R}/affilio-worker-heartbeat"),
        # quoted path with spaces: directory part gone, file name kept, quotes kept
        (
            ROOTS,
            "cannot open '/volume1/My Media/Private shoot/clip one.mp4'",
            f"cannot open '{R}/clip one.mp4'",
        ),
        (
            ROOTS,
            'cannot open "/volume1/My Media/Private shoot/clip one.mp4"',
            f'cannot open "{R}/clip one.mp4"',
        ),
        # unquoted path with spaces: nothing of the directory survives
        (
            ROOTS,
            "/volume1/My Media/Private shoot/clip one.mp4: Permission denied",
            f"{R}/clip one.mp4: Permission denied",
        ),
        (ROOTS, "scan of /volume1/Dữ liệu/Hàng mới/ failed", f"scan of {R}/Hàng mới failed"),
        (
            ROOTS,
            "/volume1/Dữ liệu/Hàng mới/clip cuối.mp4, then /srv/other/b.mp4",
            f"{R}/clip cuối.mp4, then {R}/b.mp4",
        ),
        # prose after an unquoted path is absorbed into the last component but never lost
        (ROOTS, "/tmp/x failed to open", f"{R}/x failed to open"),  # noqa: S108
        # a lone slash is harmless
        (ROOTS, "mounted at / (rw)", "mounted at / (rw)"),
        # subprocess error with a command list: executable and input both redacted
        (
            ROOTS,
            "Command '['/usr/bin/ffmpeg', '-i', '/media/originals/x.mp4']' returned non-zero "
            "exit status 1.",
            f"Command '['{R}/ffmpeg', '-i', 'originals/x.mp4']' returned non-zero exit status 1.",
        ),
        # ------------------------------------------------- multi-line / ANSI / control
        (
            ROOTS,
            "\x1b[31m[mov,mp4 @ 0x55d1c0] moov atom not found\x1b[0m\n"
            "/media/proxies/x.mp4: Invalid data\n",
            "[mov,mp4 @ 0x55d1c0] moov atom not found proxies/x.mp4: Invalid data",
        ),
        (ROOTS, "a\x00b\tc\r\nd", "a b c d"),
        # ----------------------------------------------------------- URL credentials
        (
            ROOTS,
            "OperationalError: connection failed: "
            "postgresql+psycopg://affilio:s3cret@postgres:5432/affilio",
            "OperationalError: connection failed: "
            f"postgresql+psycopg://{REDACTED}:{REDACTED}@postgres:5432/affilio",
        ),
        (ROOTS, "redis://:p%40ss@cache:6379/0", f"redis://{REDACTED}:{REDACTED}@cache:6379/0"),
        (ROOTS, "amqp://guest@broker/", f"amqp://{REDACTED}:{REDACTED}@broker/"),
        (
            ROOTS,
            "GET https://graph.facebook.com/v26.0/me?fields=id&access_token=EAAB123456 -> 400",
            f"GET https://graph.facebook.com/v26.0/me?fields=id&access_token={REDACTED} -> 400",
        ),
        # ------------------------------------------------------------ secret fields
        (ROOTS, "password: my secret", f"password: {REDACTED}"),
        (ROOTS, "api_key: sensitive value", f"api_key: {REDACTED}"),
        (ROOTS, "authorization: Bearer sensitive-token", f"authorization: {REDACTED}"),
        (ROOTS, "token: sensitive-token", f"token: {REDACTED}"),
        (ROOTS, "x-api-key: abc def ghi; retry later", f"x-api-key: {REDACTED}; retry later"),
        (ROOTS, "db password: two words, user=bob", f"db password: {REDACTED}, user=bob"),
        (
            ROOTS,
            "env MEDIA_ADMIN_TOKEN=0123456789abcdef POSTGRES_PASSWORD='hunter two' kept",
            f"env MEDIA_ADMIN_TOKEN={REDACTED} POSTGRES_PASSWORD='{REDACTED}' kept",
        ),
        (ROOTS, 'Password = "two words" then more', f'Password = "{REDACTED}" then more'),
        (
            ROOTS,
            "header Authorization: Bearer EAABsbCS1iHgBA.ZZZ",
            f"header Authorization: {REDACTED}",
        ),
        (ROOTS, "Bearer EAABsbCS1iHgBA.ZZZ rejected", f"Bearer {REDACTED} rejected"),
        # several secrets in one message, mixed forms
        (
            ROOTS,
            "client_secret=abc123 user=mia password: open sesame; cookie: affilio_session=xyz",
            f"client_secret={REDACTED} user=mia password: {REDACTED}; cookie: {REDACTED}",
        ),
        # ------------------------------------------------------------- JSON payloads
        (
            ROOTS,
            'payload {"access_token": "EAAB1234567890", "page_id": "42"}',
            f'payload {{"access_token": "{REDACTED}", "page_id": "42"}}',
        ),
        (
            ROOTS,
            '{"error":{"message":"Invalid OAuth access token","code":190,'
            '"access_token":"EAAB","client_secret":"shh"},"user":"mia"}',
            '{"error":{"message":"Invalid OAuth access token","code":190,'
            f'"access_token":"{REDACTED}","client_secret":"{REDACTED}"}},"user":"mia"}}',
        ),
        (
            ROOTS,
            '{"password": "with \\" escaped", "note": "kept"}',
            f'{{"password": "{REDACTED}" escaped", "note": "kept"}}',
        ),
        # ----------------------------------------------- prose that must stay readable
        (
            ROOTS,
            "token_invalid: The access token has expired (Meta error 190)",
            "token_invalid: The access token has expired (Meta error 190)",
        ),
        (
            ROOTS,
            "credential_unavailable: vault key v1 is not loaded",
            "credential_unavailable: vault key v1 is not loaded",
        ),
        (ROOTS, "password_reset_required: ask an admin", "password_reset_required: ask an admin"),
        (
            ROOTS,
            "The access token has expired; reconnect the Page",
            "The access token has expired; reconnect the Page",
        ),
        (
            ROOTS,
            "Meta answered HTTP 400 for https://graph.facebook.com/v26.0/me/accounts",
            "Meta answered HTTP 400 for https://graph.facebook.com/v26.0/me/accounts",
        ),
        (
            ROOTS,
            "pix_fmt yuv420p10le, fps 30000/1001, h264/aac",
            "pix_fmt yuv420p10le, fps 30000/1001, h264/aac",
        ),
        (ROOTS, "cwd: /tmp/work", f"cwd: {R}/work"),
        # ------------------------------------------------------------- malformed input
        (ROOTS, "password=", "password="),
        (ROOTS, "token:", "token:"),
        (ROOTS, "'/media/originals/unterminated", "'originals/unterminated"),
        (ROOTS, "'/volume1/private/unterminated", f"'{R}/unterminated"),
        (ROOTS, "://@ ::: /// \"\" ''", "://@ ::: /// \"\" ''"),  # bare slashes are not a path
        (ROOTS, "", ""),
        (ROOTS, None, ""),
        (ROOTS, 42, "42"),
        (ROOTS, b"bytes: '/media/finals/x.mp4'", "b\"bytes: 'finals/x.mp4'\""),
    ],
)
def test_redaction_cases(redactor: Redactor, raw: object, expected: str) -> None:
    assert redactor.message(raw, limit=0) == expected


def test_unexpected_database_and_subprocess_errors() -> None:
    db = (
        "sqlalchemy.exc.OperationalError: (psycopg.OperationalError) connection failed: "
        'connection to server at "postgres" (172.19.0.2), port 5432 failed: FATAL: password '
        'authentication failed for user "affilio"\n(Background on this error at: '
        "https://sqlalche.me/e/20/e3q8)\n[SQL: SELECT 1] [parameters: {'password': 'leak'}]"
    )
    out = ROOTS.message(db, limit=0)
    assert "leak" not in out
    assert "password authentication failed" in out  # prose kept
    assert "https://sqlalche.me/e/20/e3q8" in out  # URL path kept
    assert f"'password': '{REDACTED}'" in out

    sub = (
        "subprocess.CalledProcessError: Command '['/usr/bin/ffprobe', '-v', 'error', "
        "'/media/originals/290826/1/C1726.MP4']' returned non-zero exit status 1."
    )
    assert ROOTS.message(sub, limit=0) == (
        f"subprocess.CalledProcessError: Command '['{R}/ffprobe', '-v', 'error', "
        "'originals/290826/1/C1726.MP4']' returned non-zero exit status 1."
    )

    timeout = (
        "TimeoutExpired: Command '/usr/bin/ffmpeg -i /media/proxies/x.mp4' timed out after "
        "120 seconds"
    )
    assert ROOTS.message(timeout, limit=0) == (
        f"TimeoutExpired: Command '{R}/ffmpeg -i proxies/x.mp4' timed out after 120 seconds"
    )


def test_truncation_is_bounded_and_marked() -> None:
    long = "x" * 1200
    assert ROOTS.message(long, limit=100) == "x" * 99 + "…"
    assert len(ROOTS.message(long, limit=1000)) == 1000
    assert ROOTS.message(long, limit=0) == long  # explicit no-limit
    # Input is capped before any pattern runs; a secret that falls inside the cap is masked.
    huge = "password=" + "s" * (INPUT_CAP * 2)
    out = ROOTS.message(huge, limit=0)
    assert out == f"password={REDACTED}" and len(out) < INPUT_CAP


@pytest.mark.parametrize(
    "payload",
    [
        "/a b c d e f g h i j k l m n o p q r s t u v w x y z " * 400,
        "/" + " " * 5000 + "x",
        "/" * 10_000,
        "'" * 10_000 + "/" * 5_000,
        "password: " + "word " * 4_000,
        "token=" + "a" * 19_000,
        "https://user:" + "p" * 10_000 + "@host/" * 3,
        "\x1b[" + "9;" * 5_000 + "m",
        ("/media/originals/" + "ü " * 50 + "/") * 150,
        "a=b " * 5_000,
    ],
)
def test_adversarial_inputs_finish_quickly(payload: str) -> None:
    started = time.perf_counter()
    out = ROOTS.message(payload, limit=1000)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"redaction took {elapsed:.2f}s"
    assert len(out) <= 1000


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
    assert redactor.message(f"{tmp_path}/proxies/c.mp4 broken") == f"{R}/c.mp4 broken"
    assert str(tmp_path) not in redact_message(f"{tmp_path}/x", settings=settings)
