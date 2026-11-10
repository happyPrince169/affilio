"""scripts/nas-deploy-remote.sh: deploy and rollback with the exports profile on and off.

Runs the real script against a fake `docker` (and `curl`) that record every invocation and the
COMPOSE_PROFILES override, so the exact call sequence is asserted without a daemon. No database.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "nas-deploy-remote.sh"
COMPOSE = REPO / "infrastructure" / "docker" / "synology" / "docker-compose.yml"

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Records "<COMPOSE_PROFILES marker>|<args>" per call and answers like the real CLI would.
ctl="$FAKE_DOCKER_DIR"
args="$(printf '%s' "$*" | tr '\n' ' ')"  # one line per call, even for multi-line probes
printf '%s|%s\n' "${COMPOSE_PROFILES-unset}" "$args" >> "$ctl/calls.log"
case "$1" in
  info) exit 0 ;;
  ps)
    if [[ -f "$ctl/containers" ]]; then
      if [[ "$*" == *name=^affilio-exporter* ]]; then
        grep -x affilio-exporter "$ctl/containers" || true
      else
        cat "$ctl/containers"
      fi
    fi
    exit 0 ;;
  exec)  # pg_dump for the backup; the read-only alembic_version select for the rollback guard
    if [[ "$*" == *alembic_version* ]]; then
      [[ -f "$ctl/db-unreachable" ]] && exit 1
      [[ -f "$ctl/db-revision" ]] || exit 1
      cat "$ctl/db-revision"; exit 0
    fi
    echo "PGDMP"; exit 0 ;;
  image)  # image inspect <ref>
    grep -qx "$3" "$ctl/images" ;;
  images) echo "affilio-api:latest@deadbeef"; exit 0 ;;
  tag|stop|rm) exit 0 ;;
  network)  # network inspect <name>: exists unless listed in $ctl/missing-networks
    if [[ -f "$ctl/missing-networks" ]] && grep -qx "$3" "$ctl/missing-networks"; then exit 1; fi
    exit 0 ;;
  inspect)  # health of affilio-<service>
    name="${@: -1}"
    if [[ -f "$ctl/unhealthy" ]] && grep -qx "$name" "$ctl/unhealthy"; then
      echo "exited:none"
    else
      echo "running:healthy"
    fi
    exit 0 ;;
  run)
    if [[ "$*" == *ScriptDirectory* ]]; then
      # run --rm --network none --entrypoint python <image> -c "<alembic probe>" <revision>
      image="$7"; rev="${@: -1}"
      [[ -f "$ctl/heads.$image" ]] || exit 125   # image cannot run the probe: unknown
      echo "heads=$(tr '\n' ',' < "$ctl/heads.$image" | sed 's/,$//')"
      grep -qx "$rev" "$ctl/revisions.$image" 2>/dev/null && exit 0
      exit 2
    fi
    # run --rm --entrypoint python <image> -c "<exporter probe>"
    image="$5"
    grep -qx "$image" "$ctl/supports" ;;
  compose) exit 0 ;;
  *) echo "fake docker: unexpected '$*'" >&2; exit 99 ;;
esac
"""

FAKE_CURL = """#!/usr/bin/env bash
case "$*" in
  *system/info*)
    printf '{"environment":"production","database":{"status":"ok"},'
    printf '"integrations":{"meta":{"mode":"live"}}}' ;;
  *) printf 'ok' ;;
esac
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fake NAS project tree: real script + real compose file + minimal .env + runtime dirs."""
    root = tmp_path / "affilio"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "nas-deploy-remote.sh")
    compose_dir = root / "infrastructure" / "docker" / "synology"
    compose_dir.mkdir(parents=True)
    shutil.copy(COMPOSE, compose_dir / "docker-compose.yml")
    for name in ("originals", "proxies", "finals", "thumbnails", "exports", "data"):
        (root / name).mkdir()
    for name in ("uploads", "uploads/staging", "uploads/ready"):
        (root / name).mkdir()
    env = compose_dir / ".env"
    env.write_text(
        "\n".join(
            [
                "POSTGRES_PASSWORD=not-the-example",
                "MEDIA_ADMIN_TOKEN=not-the-example-token",
                f"AFFILIO_ORIGINALS_HOST_PATH={root / 'originals'}",
                f"AFFILIO_PROXIES_HOST_PATH={root / 'proxies'}",
                f"AFFILIO_FINALS_HOST_PATH={root / 'finals'}",
                f"AFFILIO_UPLOADS_HOST_PATH={root / 'uploads'}",
                f"AFFILIO_THUMBNAILS_HOST_PATH={root / 'thumbnails'}",
                f"AFFILIO_EXPORTS_HOST_PATH={root / 'exports'}",
                f"AFFILIO_DATA_HOST_PATH={root / 'data'}",
                "META_LIVE_PUBLISHING_ENABLED=false",
                "CREDENTIAL_ENCRYPTION_KEYS=v1:dGVzdA==",
                "AFFILIO_INGEST_NETWORK=affilio-ingest-test",
                "TRUSTED_PROXY_IPS=172.18.0.0/16",
                "",
            ]
        )
    )
    env.chmod(0o600)
    (root / ".deploy").mkdir()
    (root / ".deploy" / "last-deploy").write_text("deployed_at=before\nrevision=old\n")
    (root / ".deploy" / "source-revision").write_text("revision=new\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text(FAKE_DOCKER)
    (fake / "curl").write_text(FAKE_CURL)
    for tool in ("docker", "curl"):
        (fake / tool).chmod((fake / tool).stat().st_mode | stat.S_IEXEC)
    ctl = tmp_path / "ctl"
    ctl.mkdir()
    (ctl / "images").write_text(
        "affilio-api:latest\naffilio-web:latest\naffilio-api:previous\naffilio-web:previous\n"
    )
    (ctl / "supports").write_text("")
    # Rollback guard defaults: database and the previous api image agree on one head.
    _schema(
        root,
        db="0018_upload_root",
        heads=["0018_upload_root"],
        known=["0017_upload_chunk_manifest", "0018_upload_root"],
    )
    return root


def _set_profile(project: Path, active: bool) -> None:
    env = project / "infrastructure" / "docker" / "synology" / ".env"
    lines = [
        line for line in env.read_text().splitlines() if not line.startswith("COMPOSE_PROFILES=")
    ]
    if active:
        lines.append("COMPOSE_PROFILES=exports")
    env.write_text("\n".join(lines) + "\n")


def _run(project: Path, action: str, *, stdin: str = "y\n") -> tuple[int, str, list[str]]:
    tmp = project.parent
    ctl = tmp / "ctl"
    log = ctl / "calls.log"
    if log.exists():
        log.unlink()
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
        "NAS_DOCKER": str(tmp / "bin" / "docker"),
        "AFFILIO_PROJECT_DIR": str(project),
        "AFFILIO_DEPLOY_YES": "1",
        "HEALTH_TIMEOUT": "5",
        "FAKE_DOCKER_DIR": str(ctl),
    }
    env.pop("COMPOSE_PROFILES", None)
    completed = subprocess.run(  # noqa: S603 - the script under test, fixed argv
        [
            shutil.which("bash") or "/bin/bash",
            str(project / "scripts" / "nas-deploy-remote.sh"),
            action,
        ],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project),
        timeout=120,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return completed.returncode, completed.stderr, calls


def _supports(project: Path, *images: str) -> None:
    (project.parent / "ctl" / "supports").write_text("\n".join(images) + ("\n" if images else ""))


def _containers(project: Path, *names: str) -> None:
    (project.parent / "ctl" / "containers").write_text("\n".join(names) + ("\n" if names else ""))


def _calls(calls: list[str], fragment: str) -> list[str]:
    return [c for c in calls if fragment in c]


def _schema(
    project: Path,
    *,
    db: str | None,
    heads: list[str] | None,
    known: list[str] | None,
    image: str = "affilio-api:previous",
) -> None:
    """Fake state for the rollback guard: the live alembic_version row and what the image knows."""
    ctl = project.parent / "ctl"
    for name in ("db-revision", f"heads.{image}", f"revisions.{image}"):
        (ctl / name).unlink(missing_ok=True)
    if db is not None:
        (ctl / "db-revision").write_text(db + "\n")
    if heads is not None:
        (ctl / f"heads.{image}").write_text("\n".join(heads) + "\n")
    if known is not None:
        (ctl / f"revisions.{image}").write_text("\n".join(known) + "\n")


def _assert_nothing_changed(calls: list[str]) -> None:
    """A blocked rollback may only read: inspect images, select the revision, run the probe."""
    mutating = [
        c
        for c in calls
        if any(
            k in c
            for k in (" tag ", " up ", " stop ", " rm ", "pg_restore", "downgrade", "pg_dump")
        )
    ]
    assert mutating == [], mutating
    for c in _calls(calls, "exec "):
        assert "select version_num from alembic_version" in c and "psql" in c, c
    for c in _calls(calls, "run "):
        assert "ScriptDirectory" in c and "--network none" in c, c


# ------------------------------------------------------------------------------ deploy


def test_deploy_without_profile_is_unchanged_and_removes_a_leftover_exporter(project: Path) -> None:
    _set_profile(project, False)
    _containers(project, "affilio-postgres", "affilio-api", "affilio-exporter")
    code, err, calls = _run(project, "deploy")
    assert code == 0, err
    assert "exports profile: inactive" in err
    assert not _calls(calls, "import affilio.worker.exporter")
    assert _calls(calls, "stop affilio-exporter") and _calls(calls, "rm affilio-exporter")
    up = _calls(calls, "up -d --remove-orphans")
    assert len(up) == 1 and up[0].startswith("|")  # COMPOSE_PROFILES forced empty for the up
    assert not _calls(calls, "inspect --format") or not any(
        c.endswith("affilio-exporter") for c in _calls(calls, "inspect --format")
    )
    # Existing behaviour preserved: backup, tag, build, migrate, health, record.
    assert _calls(calls, "exec affilio-postgres pg_dump")
    assert _calls(calls, "tag affilio-api:latest affilio-api:previous")
    assert _calls(calls, "compose") and _calls(calls, "build --pull api web")
    assert _calls(calls, "run --rm --no-deps api migrate")
    for service in ("postgres", "api", "worker", "publisher", "web"):
        assert any(c.endswith(f"affilio-{service}") for c in _calls(calls, "inspect --format"))
    assert (project / ".deploy" / "last-deploy").read_text().startswith("deployed_at=")


def test_deploy_with_profile_verifies_the_image_and_health_checks_the_exporter(
    project: Path,
) -> None:
    _set_profile(project, True)
    _supports(project, "affilio-api:latest")
    code, err, calls = _run(project, "deploy")
    assert code == 0, err
    assert "exports profile: active" in err and "exporter supported by the built image" in err
    probe = _calls(
        calls, "run --rm --entrypoint python affilio-api:latest -c import affilio.worker.exporter"
    )
    assert len(probe) == 1
    up = _calls(calls, "up -d --remove-orphans")
    assert len(up) == 1 and up[0].startswith("unset|")  # profile from .env left in force
    assert any(c.endswith("affilio-exporter") for c in _calls(calls, "inspect --format"))
    assert "exporter healthy" in err
    assert not _calls(calls, "stop affilio-exporter")


def test_deploy_with_profile_refuses_an_image_without_the_exporter_before_starting(
    project: Path,
) -> None:
    _set_profile(project, True)
    _supports(project)  # nothing supports it
    code, err, calls = _run(project, "deploy")
    assert code == 1
    assert "has no exporter" in err and "remove the profile from .env" in err
    assert _calls(calls, "build --pull api web")
    assert not _calls(calls, "up -d")  # nothing was started or recreated
    assert not _calls(calls, "migrate")


def test_deploy_fails_when_the_exporter_is_unhealthy(project: Path) -> None:
    _set_profile(project, True)
    _supports(project, "affilio-api:latest")
    (project.parent / "ctl" / "unhealthy").write_text("affilio-exporter\n")
    code, err, calls = _run(project, "deploy")
    assert code == 1 and "service exporter is exited:none" in err
    assert _calls(calls, "up -d --remove-orphans")


# ------------------------------------------------------------------------------ rollback


def test_rollback_with_profile_and_unsupported_previous_image_removes_the_exporter(
    project: Path,
) -> None:
    _set_profile(project, True)
    _supports(project, "affilio-api:latest")  # the new image supports it, the previous does not
    _containers(project, "affilio-postgres", "affilio-api", "affilio-exporter")
    code, err, calls = _run(project, "rollback")
    assert code == 0, err
    assert "affilio-api:previous has no exporter" in err
    probe = _calls(
        calls, "run --rm --entrypoint python affilio-api:previous -c import affilio.worker.exporter"
    )
    assert len(probe) == 1
    order = [
        c
        for c in calls
        if any(k in c for k in ("stop affilio-exporter", "rm affilio-exporter", "up -d --no-build"))
    ]
    assert [o.split("|", 1)[1].split()[0] for o in order] == ["stop", "rm", "compose"]
    assert order[-1].startswith(
        "|"
    )  # COMPOSE_PROFILES forced empty: compose does not start the exporter
    assert _calls(calls, "tag affilio-api:previous affilio-api:latest")
    assert not any(c.endswith("affilio-exporter") for c in _calls(calls, "inspect --format"))
    assert "stays in .env" in err
    # .env was not modified
    env = (project / "infrastructure" / "docker" / "synology" / ".env").read_text()
    assert "COMPOSE_PROFILES=exports" in env


def test_rollback_with_profile_and_supported_previous_image_keeps_the_exporter(
    project: Path,
) -> None:
    _set_profile(project, True)
    _supports(project, "affilio-api:latest", "affilio-api:previous")
    _containers(project, "affilio-postgres", "affilio-exporter")
    code, err, calls = _run(project, "rollback")
    assert code == 0, err
    assert "it is kept and health-checked" in err
    assert not _calls(calls, "stop affilio-exporter") and not _calls(calls, "rm affilio-exporter")
    up = _calls(calls, "up -d --no-build")
    assert len(up) == 1 and up[0].startswith("unset|")
    assert any(c.endswith("affilio-exporter") for c in _calls(calls, "inspect --format"))


def test_rollback_without_profile_is_unchanged(project: Path) -> None:
    _set_profile(project, False)
    _supports(project)
    code, err, calls = _run(project, "rollback")
    assert code == 0, err
    assert not _calls(calls, "import affilio.worker.exporter")
    assert not _calls(calls, "stop affilio-exporter")
    up = _calls(calls, "up -d --no-build")
    assert len(up) == 1 and up[0].startswith("unset|")
    for service in ("postgres", "api", "worker", "publisher", "web"):
        assert any(c.endswith(f"affilio-{service}") for c in _calls(calls, "inspect --format"))
    assert not any(c.endswith("affilio-exporter") for c in _calls(calls, "inspect --format"))


def test_rollback_refuses_when_no_previous_image_exists(project: Path) -> None:
    _set_profile(project, True)
    (project.parent / "ctl" / "images").write_text("affilio-api:latest\naffilio-web:latest\n")
    code, err, calls = _run(project, "rollback")
    assert code == 1 and "nothing to roll back to" in err
    assert not _calls(calls, "up -d")


def test_rollback_aborts_on_no_answer_without_touching_anything(project: Path) -> None:
    _set_profile(project, True)
    _supports(project, "affilio-api:latest")
    code, err, calls = _run(project, "rollback", stdin="n\n")
    assert code == 1 and "aborted" in err
    assert not _calls(calls, "tag ") and not _calls(calls, "stop ") and not _calls(calls, "up -d")


def test_check_reports_the_profile_state(project: Path) -> None:
    _set_profile(project, True)
    code, err, _ = _run(project, "check")
    assert code == 0 and "exports profile: active" in err
    _set_profile(project, False)
    code, err, _ = _run(project, "check")
    assert code == 0 and "exports profile: inactive" in err


# ------------------------------------------------------------ MeoScout ingest network (P4B)


def _set_env(project: Path, name: str, value: str | None) -> None:
    env = project / "infrastructure" / "docker" / "synology" / ".env"
    lines = [line for line in env.read_text().splitlines() if not line.startswith(f"{name}=")]
    if value is not None:
        lines.append(f"{name}={value}")
    env.write_text("\n".join(lines) + "\n")


def test_check_refuses_a_wildcard_or_missing_trusted_proxy_setting(project: Path) -> None:
    _set_env(project, "TRUSTED_PROXY_IPS", "*")
    code, out, _ = _run(project, "check")
    assert code != 0 and "TRUSTED_PROXY_IPS must not contain" in out
    _set_env(project, "TRUSTED_PROXY_IPS", None)
    code, out, _ = _run(project, "check")
    assert code != 0 and "TRUSTED_PROXY_IPS is unset" in out


def test_check_requires_the_ingest_network_name(project: Path) -> None:
    _set_env(project, "AFFILIO_INGEST_NETWORK", None)
    code, out, _ = _run(project, "check")
    assert code != 0 and "AFFILIO_INGEST_NETWORK is unset" in out


def test_deploy_stops_before_building_when_the_ingest_network_does_not_exist(
    project: Path,
) -> None:
    (project.parent / "ctl" / "missing-networks").write_text("affilio-ingest-test\n")
    code, out, calls = _run(project, "deploy")
    assert code != 0 and "ingest network 'affilio-ingest-test' does not exist" in out
    assert not any(" build " in c for c in calls), calls
    # Neither a backup nor the rollback re-tag happened.
    assert not any(c.endswith("pg_dump") or " tag " in c for c in calls), calls


def test_deploy_checks_the_ingest_network_then_proceeds(project: Path) -> None:
    code, out, calls = _run(project, "deploy")
    assert code == 0, out
    assert "ingest network 'affilio-ingest-test' present" in out
    inspect = [c for c in calls if "network inspect affilio-ingest-test" in c]
    assert len(inspect) == 1


# ------------------------------------------------------------------ rollback schema guard (P4B)


def test_rollback_checks_the_schema_before_touching_tags_or_containers(project: Path) -> None:
    _containers(project, "affilio-postgres", "affilio-api")
    code, err, calls = _run(project, "rollback")
    assert code == 0, err
    assert "database at '0018_upload_root' = head of affilio-api:previous, compatible" in err
    probe = [i for i, c in enumerate(calls) if "ScriptDirectory" in c]
    select = [i for i, c in enumerate(calls) if "alembic_version" in c]
    first_change = min(i for i, c in enumerate(calls) if " tag " in c or " up " in c)
    assert len(probe) == 1 and len(select) == 1
    assert select[0] < probe[0] < first_change
    assert "--network none" in calls[probe[0]] and "affilio-api:previous" in calls[probe[0]]
    assert calls[probe[0]].endswith("0018_upload_root")  # the live revision is what is probed


def test_rollback_is_blocked_when_the_database_is_ahead_of_the_previous_image(
    project: Path,
) -> None:
    # The P4B case: 0019/0020 applied, the previous image's scripts end at 0018.
    _schema(
        project,
        db="0020_ingest_field_ownership",
        heads=["0018_upload_root"],
        known=["0018_upload_root"],
    )
    _containers(project, "affilio-postgres", "affilio-api")
    code, err, calls = _run(project, "rollback")
    assert code != 0
    assert "which affilio-api:previous does not know (its head: 0018_upload_root)" in err
    assert "Can't locate revision" in err
    assert "never downgrades or restores the database" in err
    assert "nothing was changed" in err.lower()
    _assert_nothing_changed(calls)
    assert (project.parent / "ctl" / "db-revision").read_text() == "0020_ingest_field_ownership\n"


def test_rollback_is_blocked_when_the_database_is_behind_the_previous_image_head(
    project: Path,
) -> None:
    # Known revision but not the head: the old image would migrate forward on start-up.
    _schema(
        project,
        db="0017_upload_chunk_manifest",
        heads=["0018_upload_root"],
        known=["0017_upload_chunk_manifest", "0018_upload_root"],
    )
    _containers(project, "affilio-postgres")
    code, err, calls = _run(project, "rollback")
    assert code != 0
    assert "would migrate the database on start-up" in err
    _assert_nothing_changed(calls)


def test_rollback_is_blocked_when_the_revision_cannot_be_read(project: Path) -> None:
    for state in ("unreachable", "missing"):
        if state == "unreachable":
            (project.parent / "ctl" / "db-unreachable").write_text("")
        else:
            (project.parent / "ctl" / "db-unreachable").unlink()
            _schema(project, db=None, heads=["0018_upload_root"], known=["0018_upload_root"])
        code, err, calls = _run(project, "rollback")
        assert code != 0, state
        assert "cannot read the database revision" in err, state
        assert not _calls(calls, "ScriptDirectory"), state  # no probe without a revision to probe
        _assert_nothing_changed(calls)


def test_rollback_is_blocked_when_the_previous_image_cannot_report_its_migrations(
    project: Path,
) -> None:
    _schema(project, db="0018_upload_root", heads=None, known=None)  # probe exits 125
    code, err, calls = _run(project, "rollback")
    assert code != 0
    assert "cannot determine which migrations affilio-api:previous supports" in err
    _assert_nothing_changed(calls)


def test_rollback_is_blocked_when_the_previous_image_has_several_heads(project: Path) -> None:
    _schema(
        project,
        db="0018_upload_root",
        heads=["0018_upload_root", "0018b_branch"],
        known=["0018_upload_root"],
    )
    code, err, calls = _run(project, "rollback")
    assert code != 0
    assert "reports no single migration head" in err
    _assert_nothing_changed(calls)


def test_rollback_guard_does_not_hardcode_the_revision(project: Path) -> None:
    # Whatever the previous image's head is, a matching database passes and a mismatch is refused.
    _schema(project, db="0042_future", heads=["0042_future"], known=["0041_x", "0042_future"])
    _containers(project, "affilio-postgres")
    code, err, _ = _run(project, "rollback")
    assert code == 0, err
    _schema(project, db="0043_newer", heads=["0042_future"], known=["0041_x", "0042_future"])
    code, err, calls = _run(project, "rollback")
    assert code != 0 and "does not know (its head: 0042_future)" in err
    _assert_nothing_changed(calls)
