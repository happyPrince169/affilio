"""U7 Phase B2B scripts rehearsed locally against a mocked `synoacltool` and disposable
directories: exact-entry identification at any index, normalized tool output, missing and
duplicate entries, partial application, restoration to the exact baseline, scoped cleanup,
verification that flags unexpected changes, and the container probe's FAIL verdict on
unexpectedly successful destructive tests. No NAS path is ever used."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash") or "/bin/bash"
PYTHON = sys.executable
HOST = REPO / "scripts" / "nas-b2b-host.sh"
PROBE = REPO / "scripts" / "nas_finalize_probe.py"
ENTRY = "user:hoangtuhanhphuc:allow:-w-----------:----"
LEGACY_ENTRIES = [
    "user:hoangtuhanhphuc:allow:r-x---a-R-c--:---- (level:4)",
    "group:administrators:allow:rwxpdDaARWc--:---- (level:5)",
    "user:dangtung:allow:rwxpdDaARWc--:---- (level:5)",
]
DIR_ENTRIES = [
    "user:hoangtuhanhphuc:allow:r-x---a-R-c--:fd-- (level:4)",
    "group:administrators:allow:rwxpdDaARWc--:fd-- (level:5)",
    "user:dangtung:allow:rwxpdDaARWc--:fd-- (level:5)",
]

# A tiny stand-in for synoacltool: ACLs live in <store>/<encoded path>; -get prints the real
# tool's layout; -add inserts at the front (MOCK_INSERT=front, the renumbering case) or the end;
# MOCK_NORMALIZE rewrites our entry's permission string the way a normalising tool might;
# MOCK_FAIL_ADD makes -add exit 1 without changing anything (partial-application case).
MOCK = r"""#!/usr/bin/env python3
import os, sys
store = os.environ["MOCK_ACL_STORE"]
def key(p): return os.path.join(store, os.path.realpath(p).replace("/", "%"))
def load(p):
    try: return open(key(p)).read().splitlines()
    except FileNotFoundError: return []
def save(p, lines): open(key(p), "w").write("\n".join(lines) + ("\n" if lines else ""))
op, path = sys.argv[1], sys.argv[2]
if op == "-get":
    for header in ("ACL version: 1 ", "Archive: is_inherit,is_support_ACL ",
                   "Owner: [dangtung(user)] ", "--------------------- "):
        print(header)
    for i, line in enumerate(load(path)): print(f"\t [{i}] {line}")
elif op == "-add":
    if os.environ.get("MOCK_FAIL_ADD"): print("(synoacltool.c, 1) permission denied"); sys.exit(1)
    entry = sys.argv[3]
    if os.environ.get("MOCK_NORMALIZE") and entry.endswith(":----"):
        entry = entry.replace("-w-----------", "-w----a-R-c--")
    lines = load(path); line = f"{entry} (level:0)"
    front = os.environ.get("MOCK_INSERT", "front") == "front"
    save(path, ([line] + lines) if front else (lines + [line]))
elif op == "-del":
    lines = load(path); idx = int(sys.argv[3]); del lines[idx]; save(path, lines)
elif op == "-copy":
    save(sys.argv[3], load(path))
else:
    sys.exit(2)
"""


@pytest.fixture
def world(tmp_path: Path):
    final = tmp_path / "FINAL"
    typedir = final / "Hang" / "Mens-Fashion" / "Try-On"
    staging = final / ".affilio-incoming"
    legacy = final / "Hang" / "T9"
    for d in (typedir, staging, legacy, final / "Hao" / "T9"):
        d.mkdir(parents=True)
    legacy_file = legacy / "Couple be.mp4"
    legacy_file.write_bytes(b"legacy video bytes")
    (final / "Hao" / "T9" / "other.mp4").write_bytes(b"other")
    store = tmp_path / "aclstore"
    store.mkdir()
    tool = tmp_path / "synoacltool"
    tool.write_text(MOCK)
    tool.chmod(0o755)
    env = {
        **os.environ,
        "FINAL": str(final),
        "TYPE_DIR": "Hang/Mens-Fashion/Try-On",
        "LEGACY_REF": str(legacy_file),
        "SYNOACLTOOL": str(tool),
        "MOCK_ACL_STORE": str(store),
        "B2B_DIR": str(tmp_path / "baseline"),
    }

    # seed ACLs the way the NAS has them
    def seed(path: Path, lines: list[str]) -> None:
        (store / str(path.resolve()).replace("/", "%")).write_text("\n".join(lines) + "\n")

    for d in (
        final,
        final / "Hang",
        final / "Hang" / "Mens-Fashion",
        typedir,
        legacy,
        final / "Hao",
        final / "Hao" / "T9",
    ):
        seed(d, DIR_ENTRIES)
    seed(legacy_file, LEGACY_ENTRIES)
    seed(final / "Hao" / "T9" / "other.mp4", LEGACY_ENTRIES)
    seed(staging, ["user:hoangtuhanhphuc:allow:rwxpdDaARWc--:fd-- (level:0)"])
    return {
        "final": final,
        "typedir": typedir,
        "staging": staging,
        "legacy": legacy_file,
        "env": env,
        "store": store,
        "seed": seed,
    }


def host(world, *args, **extra_env) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - scripts under test, fixed argv
        [BASH, str(HOST), *args],
        env={**world["env"], **extra_env},
        capture_output=True,
        text=True,
        check=False,
    )


def entries(world, path: Path) -> list[str]:
    key = world["store"] / str(path.resolve()).replace("/", "%")
    return key.read_text().splitlines() if key.exists() else []


# ---------------------------------------------------------------- host script


def test_capture_apply_restore_round_trip_with_front_insertion(world) -> None:
    assert host(world, "preflight").returncode == 0
    cap = host(world, "capture")
    assert cap.returncode == 0, cap.stderr
    before = entries(world, world["typedir"])
    applied = host(world, "apply-acl", MOCK_INSERT="front")
    assert applied.returncode == 0, applied.stderr + applied.stdout
    assert "PASS apply-acl" in applied.stdout and "[0] " in applied.stdout
    assert entries(world, world["typedir"]) == [f"{ENTRY} (level:0)", *before]
    # the index moved every original entry; verify must still see only our entry as a change
    ver = host(world, "verify")
    assert ver.returncode == 0, ver.stdout + ver.stderr
    assert "PASS verify" in ver.stdout
    restored = host(world, "restore-acl")
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "PASS restore-acl" in restored.stdout
    assert entries(world, world["typedir"]) == before
    assert host(world, "verify").returncode == 0
    # nothing outside the type dir was touched
    assert entries(world, world["legacy"]) == LEGACY_ENTRIES
    assert entries(world, world["final"]) == DIR_ENTRIES


def test_apply_at_end_index_also_passes(world) -> None:
    host(world, "capture")
    applied = host(world, "apply-acl", MOCK_INSERT="back")
    assert applied.returncode == 0 and "[3] " in applied.stdout
    assert host(world, "restore-acl").returncode == 0


def test_normalized_tool_output_is_detected_and_not_deleted_blindly(world) -> None:
    host(world, "capture")
    applied = host(world, "apply-acl", MOCK_NORMALIZE="1")
    assert applied.returncode == 1
    assert (
        "not baseline + our entry" in applied.stderr
        and "MANUAL RECOVERY REQUIRED" in applied.stderr
    )
    # the normalized line is present; restore refuses to guess and prints recovery instructions
    assert entries(world, world["typedir"])[0].startswith(
        "user:hoangtuhanhphuc:allow:-w----a-R-c--"
    )
    restored = host(world, "restore-acl")
    assert restored.returncode == 1 and "MANUAL RECOVERY REQUIRED" in restored.stderr
    assert len(entries(world, world["typedir"])) == 4  # nothing deleted


def test_duplicate_entries_block_restore(world) -> None:
    host(world, "capture")
    world["seed"](world["typedir"], [f"{ENTRY} (level:0)", *DIR_ENTRIES, f"{ENTRY} (level:0)"])
    applied = host(world, "apply-acl")
    assert (
        applied.returncode == 1 and "differs from the baseline before any change" in applied.stderr
    )
    restored = host(world, "restore-acl")
    assert restored.returncode == 1 and "2 entries match ours; refusing to guess" in restored.stderr
    assert len(entries(world, world["typedir"])) == 5


def test_missing_entry_restore_verifies_baseline(world) -> None:
    host(world, "capture")
    restored = host(world, "restore-acl")
    assert (
        restored.returncode == 0
        and "not present" in restored.stdout
        and "PASS restore-acl" in restored.stdout
    )


def test_partial_application_failure_leaves_baseline_and_reports(world) -> None:
    host(world, "capture")
    applied = host(world, "apply-acl", MOCK_FAIL_ADD="1")
    assert applied.returncode != 0
    assert entries(world, world["typedir"]) == DIR_ENTRIES
    assert host(world, "verify").returncode == 0


def test_verify_blocks_on_changes_outside_the_type_dir(world) -> None:
    host(world, "capture")
    # a legacy file's ACL gains a write entry (what a propagated ACL change would look like)
    world["seed"](world["legacy"], [*LEGACY_ENTRIES, f"{ENTRY} (level:0)"])
    ver = host(world, "verify")
    assert ver.returncode == 1 and "unexpected ACL changes" in ver.stdout
    world["seed"](world["legacy"], LEGACY_ENTRIES)
    # a legacy file's bytes change
    (world["final"] / "Hao" / "T9" / "other.mp4").write_bytes(b"other-changed")
    ver = host(world, "verify")
    assert (
        ver.returncode == 1
        and "unexpected metadata changes" in ver.stdout
        and "other.mp4" in ver.stdout
    )


def test_protected_file_and_scoped_cleanup(world) -> None:
    host(world, "capture")
    prep = host(world, "prepare-protected")
    assert prep.returncode == 0 and "PASS prepare-protected" in prep.stdout
    name = next(
        line for line in prep.stdout.splitlines() if line.startswith("export PROTECTED=")
    ).split("=", 1)[1]
    protected = world["typedir"] / name
    assert protected.exists() and name.startswith(".affilio-protected-")
    assert entries(world, protected) == LEGACY_ENTRIES
    assert (Path(world["env"]["B2B_DIR"]) / "manifest").read_text().count("protected=") == 1
    # the type dir's own metadata changed (a file was created): allowed by verify
    assert host(world, "verify").returncode == 0
    # probe artefacts from one token, plus a stray leftover dir, plus an unrelated media file
    token = "20260101T000000Z-deadbeef"
    (world["typedir"] / f".affilio-probe-{token}.mp4").write_bytes(b"x")
    (world["typedir"] / f".affilio-dir-{token}").mkdir()
    (world["staging"] / f"probe-{token}").mkdir()
    (world["staging"] / f"probe-{token}" / "src.part").write_bytes(b"y")
    (world["typedir"] / ".affilio-dir-othertoken").mkdir()
    (world["typedir"] / ".affilio-dir-othertoken" / "keep").write_bytes(b"not empty")
    (world["typedir"] / "real-video.mp4").write_bytes(b"editor upload")
    without = host(world, "cleanup")
    assert (
        without.returncode == 1
        and "no probe token given" in without.stdout
        and not protected.exists()
    )
    assert (world["typedir"] / f".affilio-probe-{token}.mp4").exists()
    with_token = host(world, "cleanup", token)
    assert with_token.returncode == 1, with_token.stdout  # the other token's dir remains
    assert not (world["typedir"] / f".affilio-probe-{token}.mp4").exists()
    assert not (world["typedir"] / f".affilio-dir-{token}").exists()
    assert not (world["staging"] / f"probe-{token}").exists()
    assert (world["typedir"] / ".affilio-dir-othertoken" / "keep").exists()  # never recursive
    assert (world["typedir"] / "real-video.mp4").read_bytes() == b"editor upload"
    assert "remaining probe artefacts: 1 in type dir" in with_token.stdout
    bad = host(world, "cleanup", "../x")
    assert bad.returncode == 2


# ---------------------------------------------------------------- container probe (local rehearsal)


def probe(final: Path, protected: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - scripts under test, fixed argv
        [
            PYTHON,
            "-I",
            str(PROBE),
            "--final",
            str(final),
            "--type-dir",
            "Hang/Mens-Fashion/Try-On",
            "--protected",
            protected,
            "--apply",
            "--simulate",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    final = tmp_path / "FINAL"
    (final / ".affilio-incoming").mkdir(parents=True)
    (final / "Hang" / "Mens-Fashion" / "Try-On").mkdir(parents=True)
    return final


def test_probe_fails_when_destructive_negatives_succeed_and_cleans_up(tree: Path) -> None:
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    protected = typedir / ".affilio-protected-sim.tmp"
    protected.write_bytes(b"x")
    protected.chmod(0o444)
    # POSIX lets the owner rename/unlink/utime: every one of those must be reported as FAIL
    result = probe(tree, protected.name, "--expect-placed-unlink", "allowed")
    assert result.returncode == 1
    out = result.stdout
    assert (
        "ok   T2 hard link into type dir" in out
        and "ok   T3 link onto existing name refused" in out
    )
    assert "FAIL T4g protected: unlink: UNEXPECTEDLY SUCCEEDED" in out
    assert "FAIL T4e type dir: mkdir" in out
    assert '"verdict": "FAIL"' in out
    # everything the identity could remove is gone; no probe artefact remains
    assert sorted(p.name for p in typedir.iterdir()) == []
    assert list((tree / ".affilio-incoming").iterdir()) == []


def test_probe_summary_carries_token_and_verdict(tree: Path) -> None:
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    protected = typedir / ".affilio-protected-sim.tmp"
    protected.write_bytes(b"x")
    protected.chmod(0o444)
    result = probe(tree, protected.name, "--expect-placed-unlink", "denied")
    assert result.returncode == 1
    assert "FAIL T6 unlink of placed name is denied: observed: allowed" in result.stdout
    summary = next(line for line in result.stdout.splitlines() if line.startswith("summary:"))
    assert '"token"' in summary and '"verdict": "FAIL"' in summary
    assert "T5 staging unlink keeps placed file" in summary


def test_probe_leftovers_are_reported_and_host_cleanup_removes_them_by_token(world) -> None:
    """A crash before the staging unlink leaves artefacts; the probe reports them, exits
    non-zero, and the host cleanup removes exactly that token's files."""
    host(world, "capture")
    typedir = world["typedir"]
    protected = typedir / ".affilio-protected-sim.tmp"
    protected.write_bytes(b"x")
    protected.chmod(0o444)
    result = subprocess.run(  # noqa: S603 - scripts under test, fixed argv
        [
            PYTHON,
            "-I",
            str(PROBE),
            "--final",
            str(world["final"]),
            "--type-dir",
            "Hang/Mens-Fashion/Try-On",
            "--protected",
            protected.name,
            "--apply",
            "--simulate",
            "--simulate-leftover",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1 and "simulated crash" in result.stderr
    summary = next(line for line in result.stdout.splitlines() if line.startswith("summary:"))
    token = summary.split('"token": "')[1].split('"')[0]
    placed = typedir / f".affilio-probe-{token}.mp4"
    assert placed.exists() and f"{placed}" in summary  # the placed file is reported as a leftover
    assert not (
        world["staging"] / f"probe-{token}" / "src.part"
    ).exists()  # cleanup removed the file it could
    protected.unlink(missing_ok=True)  # POSIX let the simulated identity remove it already
    cleaned = host(world, "cleanup", token)
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    assert "PASS cleanup" in cleaned.stdout and not placed.exists()
    assert list(world["staging"].iterdir()) == []


def test_probe_refuses_bad_arguments(tree: Path) -> None:
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    (typedir / ".affilio-protected-sim.tmp").write_bytes(b"x")
    escape = subprocess.run(  # noqa: S603 - scripts under test, fixed argv
        [
            PYTHON,
            "-I",
            str(PROBE),
            "--final",
            str(tree),
            "--type-dir",
            "../etc",
            "--protected",
            ".p",
            "--apply",
            "--simulate",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert escape.returncode == 2 and "escapes FINAL" in escape.stdout
    notdot = probe(tree, "visible.tmp")
    assert notdot.returncode == 2
    missing = probe(tree, ".absent.tmp")
    assert missing.returncode == 3
    dry = subprocess.run(  # noqa: S603 - scripts under test, fixed argv
        [
            PYTHON,
            "-I",
            str(PROBE),
            "--final",
            str(tree),
            "--type-dir",
            "Hang/Mens-Fashion/Try-On",
            "--protected",
            ".affilio-protected-sim.tmp",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0 and "dry run" in dry.stdout
    assert list((tree / ".affilio-incoming").iterdir()) == []


def test_probe_identity_guard_ignores_the_primary_gid_but_not_extra_groups(tree: Path) -> None:
    """Inside the container os.getgroups() returns [primary gid]; that is the service identity.
    Any other group must still be refused. Rehearsed by shadowing os.getgroups/os.getgid."""
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    (typedir / ".affilio-protected-sim.tmp").write_bytes(b"x")
    shim = tree / "shim.py"

    def run(groups: list[int], gid: int) -> subprocess.CompletedProcess:
        shim.write_text(
            "import os, runpy, sys\n"
            f"os.getgroups = lambda: {groups!r}\n"
            f"os.getgid = lambda: {gid}\n"
            f"sys.argv = ['probe', '--final', {str(tree)!r},"
            " '--type-dir', 'Hang/Mens-Fashion/Try-On',"
            " '--protected', '.affilio-protected-sim.tmp',"
            " '--apply', '--expect-placed-unlink', 'allowed']\n"
            f"runpy.run_path({str(PROBE)!r}, run_name='__main__')\n"
        )
        return subprocess.run(  # noqa: S603 - scripts under test, fixed argv
            [PYTHON, str(shim)], capture_output=True, text=True, check=False
        )

    refused = run([100, 101], 100)
    assert refused.returncode == 1 and "supplementary groups [101]" in refused.stdout
    assert not list(typedir.glob(".affilio-probe-*"))  # refused before any write
    accepted = run([100], 100)
    assert "FAIL identity" not in accepted.stdout and "ok   T1" in accepted.stdout
    empty = run([], 100)
    assert "FAIL identity" not in empty.stdout
