"""scripts/db-backup-daily.sh against a fake docker: verified dump, integrity refusals, the
weekly restore test into a scratch database, retention that never touches deploy dumps, and
the status file / exit code Task Scheduler relies on. No database, no daemon."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "db-backup-daily.sh"

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Answers the docker calls the backup script makes; behaviour switched by files in $FAKE_CTL.
ctl="$FAKE_CTL"
printf '%s\n' "$*" >> "$ctl/calls.log"
case "$1" in
  ps) [[ -f "$ctl/down" ]] || echo affilio-postgres; exit 0 ;;
  exec)
    args="$*"
    case "$args" in
      *pg_dump*)
        if [[ -f "$ctl/dump-fails" ]]; then exit 1; fi
        if [[ -f "$ctl/small-dump" ]]; then printf 'PGDMP'
        else head -c 8192 /dev/zero | tr '\0' 'x'; fi
        exit 0 ;;
      *"pg_restore --list"*)
        cat >/dev/null
        if [[ -f "$ctl/corrupt" ]]; then
          echo "pg_restore: error: not a valid archive" >&2; exit 1
        fi
        if [[ -f "$ctl/no-alembic" ]]; then
          echo "1; 0 0 TABLE DATA public products affilio"
        else
          echo "1; 0 0 TABLE DATA public alembic_version affilio"
          echo "2; 0 0 TABLE DATA public products affilio"
        fi
        exit 0 ;;
      *pg_restore*)  # the restore test
        cat >/dev/null
        if [[ -f "$ctl/restore-fails" ]]; then
          echo "pg_restore: error: could not execute query" >&2; exit 1
        fi
        exit 0 ;;
      *"CREATE DATABASE"*) echo created >> "$ctl/scratch.log"; exit 0 ;;
      *"DROP DATABASE"*) echo dropped >> "$ctl/scratch.log"; exit 0 ;;
      *information_schema.tables*) echo 43; exit 0 ;;
      *alembic_version*) echo 0020_ingest_field_ownership; exit 0 ;;
    esac
    echo "fake docker: unexpected exec '$args'" >&2; exit 99 ;;
  *) echo "fake docker: unexpected '$*'" >&2; exit 99 ;;
esac
"""


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    backups = tmp_path / "backups"
    backups.mkdir()
    ctl = tmp_path / "ctl"
    ctl.mkdir()
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(FAKE_DOCKER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return {"backups": backups, "ctl": ctl, "docker": fake}


def run(world: dict[str, Path], *, dow: str = "3", env: dict[str, str] | None = None):
    environment = {
        **os.environ,
        "NAS_DOCKER": str(world["docker"]),
        "FAKE_CTL": str(world["ctl"]),
        "BACKUP_DIR": str(world["backups"]),
        "BACKUP_STAMP": env.pop("BACKUP_STAMP", "20260101-030000") if env else "20260101-030000",
        "BACKUP_DOW": dow,
        "MIN_BYTES": "1024",
        **(env or {}),
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, the script under test
        [shutil.which("bash") or "/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    calls = (
        (world["ctl"] / "calls.log").read_text().splitlines()
        if (world["ctl"] / "calls.log").exists()
        else []
    )
    return completed.returncode, completed.stdout + completed.stderr, calls


def status(world: dict[str, Path]) -> dict[str, str]:
    text = (world["backups"] / "last-backup.status").read_text()
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_daily_run_keeps_a_verified_dump_with_checksum_and_status(world: dict[str, Path]) -> None:
    code, out, calls = run(world)
    assert code == 0, out
    dump = world["backups"] / "daily-20260101-030000.dump"
    assert dump.exists() and dump.stat().st_size == 8192
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    assert (
        (world["backups"] / "daily-20260101-030000.dump.sha256")
        .read_text()
        .endswith("daily-20260101-030000.dump\n")
    )
    assert not list(world["backups"].glob("*.tmp"))
    st = status(world)
    assert st["status"] == "ok" and st["restore_test"] == "skipped" and st["bytes"] == "8192"
    assert "pg_restore --list" in " ".join(calls)
    assert not any("DROP DATABASE" in c for c in calls)  # no restore test on a weekday
    assert not any(c.endswith(".dump") and "weekly" in c for c in calls)


@pytest.mark.parametrize(
    ("switch", "message"),
    [
        ("small-dump", "not kept"),
        ("corrupt", "pg_restore --list rejected"),
        ("no-alembic", "no alembic_version table"),
        ("dump-fails", "pg_dump failed"),
        ("down", "is not running"),
    ],
)
def test_bad_dumps_are_refused_and_reported(
    world: dict[str, Path], switch: str, message: str
) -> None:
    (world["ctl"] / switch).write_text("")
    code, out, _ = run(world)
    assert code == 1
    assert message in out
    assert not list(world["backups"].glob("daily-*.dump")), "a refused dump must not be kept"
    assert not list(world["backups"].glob("*.tmp"))
    assert status(world)["status"] == "fail"


def test_weekly_day_adds_a_weekly_copy_and_a_restore_test_in_a_scratch_database(
    world: dict[str, Path],
) -> None:
    code, out, calls = run(world, dow="0")
    assert code == 0, out
    assert (world["backups"] / "weekly-20260101-030000.dump").exists()
    assert (world["backups"] / "weekly-20260101-030000.dump.sha256").exists()
    st = status(world)
    assert st["status"] == "ok" and st["restore_test"] == "ok"
    assert "restore test ok: 43 tables, revision 0020_ingest_field_ownership" in out
    scratch = (world["ctl"] / "scratch.log").read_text().split()
    # one psql call does drop-if-exists + create (logged once as created), then the drop after
    assert scratch == ["created", "dropped"]
    restores = [c for c in calls if "pg_restore -U" in c]
    assert restores and all("affilio_restore_test" in c for c in restores)
    assert not any("pg_restore -U" in c and "-d affilio " in c for c in calls), (
        "never restores into the production database"
    )


def test_restore_test_failure_fails_the_run_but_keeps_the_dump_and_drops_the_scratch(
    world: dict[str, Path],
) -> None:
    (world["ctl"] / "restore-fails").write_text("")
    code, out, _ = run(world, dow="0")
    assert code == 1
    assert "restore test FAILED" in out
    assert (world["backups"] / "daily-20260101-030000.dump").exists()
    st = status(world)
    assert st["status"] == "fail" and st["restore_test"] == "fail"
    assert (world["ctl"] / "scratch.log").read_text().split()[-1] == "dropped"


def test_retention_prunes_only_daily_and_weekly_dumps(world: dict[str, Path]) -> None:
    for i in range(16):
        f = world["backups"] / f"daily-202512{i + 10:02d}-030000.dump"
        f.write_text("old")
        (world["backups"] / f"{f.name}.sha256").write_text("x")
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))
    for i in range(9):
        f = world["backups"] / f"weekly-202511{i + 10:02d}-030000.dump"
        f.write_text("old")
        os.utime(f, (1_690_000_000 + i, 1_690_000_000 + i))
    deploy = world["backups"] / "pre-deploy-20261110-210604.dump"
    deploy.write_text("deploy dump")
    env_backup = world["backups"] / "env-before-p4b-20261110T134419Z"
    env_backup.write_text("secret config")
    code, out, _ = run(world, dow="0", env={"DAILY_KEEP": "14", "WEEKLY_KEEP": "8"})
    assert code == 0, out
    daily = sorted(p.name for p in world["backups"].glob("daily-*.dump"))
    weekly = sorted(p.name for p in world["backups"].glob("weekly-*.dump"))
    assert len(daily) == 14 and daily[-1] == "daily-20260101-030000.dump"
    assert "daily-20251210-030000.dump" not in daily and "daily-20251211-030000.dump" not in daily
    assert not (world["backups"] / "daily-20251210-030000.dump.sha256").exists()
    assert len(weekly) == 8 and weekly[-1] == "weekly-20260101-030000.dump"
    assert deploy.read_text() == "deploy dump" and env_backup.read_text() == "secret config"
    assert "pruned daily-20251210-030000.dump" in out


def test_status_file_is_what_nas_status_shows(world: dict[str, Path]) -> None:
    run(world)
    text = (world["backups"] / "last-backup.status").read_text()
    for key in (
        "status=",
        "finished_at=",
        "file=",
        "bytes=",
        "sha256=",
        "restore_test=",
        "message=",
    ):
        assert key in text
