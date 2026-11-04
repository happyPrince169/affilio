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
printf '%s|%s\n' "${COMPOSE_PROFILES-unset}" "$*" >> "$ctl/calls.log"
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
  exec) echo "PGDMP"; exit 0 ;;
  image)  # image inspect <ref>
    grep -qx "$3" "$ctl/images" ;;
  images) echo "affilio-api:latest@deadbeef"; exit 0 ;;
  tag|stop|rm) exit 0 ;;
  inspect)  # health of affilio-<service>
    name="${@: -1}"
    if [[ -f "$ctl/unhealthy" ]] && grep -qx "$name" "$ctl/unhealthy"; then
      echo "exited:none"
    else
      echo "running:healthy"
    fi
    exit 0 ;;
  run)  # run --rm --entrypoint python <image> -c "<probe>"
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
