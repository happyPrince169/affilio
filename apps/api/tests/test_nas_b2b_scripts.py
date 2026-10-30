"""U7 Phase B2B scripts rehearsed locally against a mocked `synoacltool` and disposable
directories: exact-entry identification at any index, normalized tool output, missing and
duplicate entries, partial application, restoration to the exact baseline, scoped cleanup,
verification that flags unexpected changes, and the container probe's FAIL verdict on
unexpectedly successful destructive tests. No NAS path is ever used."""

from __future__ import annotations

import json
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
    # the post-condition is checked against the contract too, not against T6's outcome
    assert "FAIL T6b placed file intact after refused unlink: placed file missing" in result.stdout
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
            "--expect-placed-unlink",
            "allowed",
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
            "--expect-placed-unlink",
            "denied",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert escape.returncode == 2 and "escapes FINAL" in escape.stdout
    notdot = probe(tree, "visible.tmp", "--expect-placed-unlink", "denied")
    assert notdot.returncode == 2 and "dot-prefixed" in notdot.stdout
    missing = probe(tree, ".absent.tmp", "--expect-placed-unlink", "denied")
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
            "--expect-placed-unlink",
            "denied",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0 and "dry run" in dry.stdout
    assert list((tree / ".affilio-incoming").iterdir()) == []


def test_probe_requires_an_explicit_t6_contract(tree: Path) -> None:
    """There is no default for --expect-placed-unlink: the operator states the contract for
    the configuration under test (B2B 2026-10-30 verified `denied` on the Synology NAS)."""
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    (typedir / ".affilio-protected-sim.tmp").write_bytes(b"x")
    result = probe(tree, ".affilio-protected-sim.tmp")
    assert result.returncode == 2 and "--expect-placed-unlink" in result.stderr
    assert sorted(p.name for p in typedir.iterdir()) == [".affilio-protected-sim.tmp"]
    assert list((tree / ".affilio-incoming").iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes; no real EACCES")
def test_probe_t6_denied_contract_passes_when_the_kernel_refuses_the_unlink(tree: Path) -> None:
    """The verified Synology outcome, rehearsed with a real EACCES: the type directory is
    made read-only just before T6. T6 and T6b pass, the placed file stays for host cleanup
    and the directory mode is restored. (The run still exits 1: POSIX lets the file owner
    perform the T4 negatives, which the probe correctly reports as FAIL.)"""
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    typedir.chmod(0o750)
    protected = typedir / ".affilio-protected-sim.tmp"
    protected.write_bytes(b"x")
    protected.chmod(0o444)
    result = probe(
        tree,
        protected.name,
        "--expect-placed-unlink",
        "denied",
        "--simulate-placed-unlink",
        "denied",
    )
    out = result.stdout
    assert "ok   T5 staging unlink keeps placed file" in out
    assert "ok   T6 unlink of placed name is denied: observed: denied" in out
    assert "ok   T6b placed file intact after refused unlink" in out
    assert "FAIL T6" not in out
    summary = next(line for line in out.splitlines() if line.startswith("summary:"))
    token = summary.split('"token": "')[1].split('"')[0]
    placed = typedir / f".affilio-probe-{token}.mp4"
    assert placed.exists() and placed.stat().st_nlink == 1 and str(placed) in summary
    assert (typedir.stat().st_mode & 0o777) == 0o750  # restored for host-side cleanup
    assert list((tree / ".affilio-incoming").iterdir()) == []  # probe dir removed


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes; no real EACCES")
def test_probe_t6_unexpected_denial_fails_strictly(tree: Path) -> None:
    """Regression for the B2B run of 2026-10-30: contract `allowed`, kernel refuses the
    unlink. T6 and T6b both FAIL, the verdict is FAIL, exit 1, and the placed file is
    reported for host cleanup. Nothing is retried or silently accepted."""
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    protected = typedir / ".affilio-protected-sim.tmp"
    protected.write_bytes(b"x")
    protected.chmod(0o444)
    result = probe(
        tree,
        protected.name,
        "--expect-placed-unlink",
        "allowed",
        "--simulate-placed-unlink",
        "denied",
    )
    out = result.stdout
    assert result.returncode == 1
    assert "FAIL T6 unlink of placed name is allowed: observed: denied" in out
    assert "FAIL T6b placed name gone after allowed unlink: STILL PRESENT" in out
    summary = next(line for line in out.splitlines() if line.startswith("summary:"))
    assert '"verdict": "FAIL"' in summary
    assert "T6 unlink of placed name is allowed" in summary.split('"failed"')[1]
    token = summary.split('"token": "')[1].split('"')[0]
    assert (typedir / f".affilio-probe-{token}.mp4").exists()


def test_probe_simulation_flags_require_simulate(tree: Path) -> None:
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    (typedir / ".affilio-protected-sim.tmp").write_bytes(b"x")
    result = subprocess.run(  # noqa: S603 - scripts under test, fixed argv
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
            "--apply",
            "--expect-placed-unlink",
            "denied",
            "--simulate-placed-unlink",
            "denied",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "require --simulate" in result.stdout
    assert not list(typedir.glob(".affilio-probe-*"))


# ------------------------------------------------- T8 owner-rights (mocked Synology ACL)
#
# Local Linux lets a file's owner write, chmod, utime, link, rename and unlink it, so POSIX
# cannot rehearse the Synology outcome. The shim below runs the probe with os.* wrapped by a
# policy that refuses selected operations on the probe's own placed file and on the protected
# file with EACCES, and lists a fake ACL attribute on the placed file. Every result from it is
# "evidence": "simulated"; only a run on the NAS is Synology evidence.

SHIM = r"""
import errno, os, runpy, sys
POLICY = __POLICY__          # op -> "deny" | "allow"
ACL_XATTR = __ACL__          # attribute name the mock lists on the placed file, or None
def base(p): return os.path.basename(os.fspath(p)) if not isinstance(p, int) else ""
def placed(p): return base(p).startswith(".affilio-probe-") and base(p).endswith(".mp4")
def protected(p): return base(p).startswith(".affilio-protected-")
def deny(): raise PermissionError(errno.EACCES, "mock synoacl: denied")
def denies(op): return POLICY.get(op) == "deny"
OPS = ("open", "close", "truncate", "utime", "fchmod", "link", "rename", "unlink", "mkdir",
       "rmdir", "listxattr", "getxattr", "setxattr")
real = {n: getattr(os, n) for n in OPS}
placed_fds = set()
def m_open(path, flags, mode=0o777, *, dir_fd=None):
    writing = flags & (os.O_WRONLY | os.O_RDWR)
    if writing and (placed(path) and denies("open") or protected(path) and denies("protected")):
        deny()
    fd = real["open"](path, flags, mode, dir_fd=dir_fd)
    if placed(path): placed_fds.add(fd)
    return fd
def m_close(fd):
    placed_fds.discard(fd); real["close"](fd)
def m_truncate(path, length):
    if placed(path) and denies("truncate"): deny()
    real["truncate"](path, length)
def m_utime(path, times=None, *, ns=None, dir_fd=None, follow_symlinks=True):
    on_placed = isinstance(path, int) and path in placed_fds
    if (on_placed and denies("utime")) or (protected(path) and denies("protected")): deny()
    real["utime"](path, times, ns=ns, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
def m_fchmod(fd, mode):
    if fd in placed_fds and denies("chmod"): deny()
    real["fchmod"](fd, mode)
def m_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
    if (placed(src) and denies("link")) or (protected(src) and denies("protected")): deny()
    real["link"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd,
                 follow_symlinks=follow_symlinks)
def m_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    if (placed(src) and denies("rename")) or (protected(src) and denies("protected")): deny()
    real["rename"](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
def m_unlink(path, *, dir_fd=None):
    if placed(path) and not base(path).endswith("-link.mp4") and denies("unlink"): deny()
    if protected(path) and denies("protected"): deny()
    real["unlink"](path, dir_fd=dir_fd)
def m_mkdir(path, mode=0o777, *, dir_fd=None):
    if base(path).startswith(".affilio-dir-") and denies("protected"): deny()
    real["mkdir"](path, mode, dir_fd=dir_fd)
def m_rmdir(path, *, dir_fd=None):
    if base(path).startswith("probe-") and denies("rmdir"): deny()
    real["rmdir"](path, dir_fd=dir_fd)
def m_listxattr(path=None, *, follow_symlinks=True):
    names = real["listxattr"](path, follow_symlinks=follow_symlinks)
    return names + [ACL_XATTR] if (placed(path) and ACL_XATTR) else names
def m_getxattr(path, attribute, *, follow_symlinks=True):
    if placed(path) and attribute == ACL_XATTR: return b"mock-synoacl-bytes"
    return real["getxattr"](path, attribute, follow_symlinks=follow_symlinks)
def m_setxattr(path, attribute, value, flags=0, *, follow_symlinks=True):
    if placed(path) and attribute == ACL_XATTR:
        if denies("setxattr"): deny()
        return None
    if placed(path) and attribute.startswith("user.") and denies("user_xattr"): deny()
    real["setxattr"](path, attribute, value, flags, follow_symlinks=follow_symlinks)
for name, fn in (("open", m_open), ("close", m_close), ("truncate", m_truncate), ("utime", m_utime),
                 ("fchmod", m_fchmod), ("link", m_link), ("rename", m_rename), ("unlink", m_unlink),
                 ("mkdir", m_mkdir), ("rmdir", m_rmdir), ("listxattr", m_listxattr),
                 ("getxattr", m_getxattr), ("setxattr", m_setxattr)):
    setattr(os, name, fn)
sys.argv = __ARGV__
runpy.run_path(__PROBE__, run_name="__main__")
"""

DENY_ALL = {
    op: "deny"
    for op in (
        "open",
        "truncate",
        "utime",
        "chmod",
        "setxattr",
        "user_xattr",
        "link",
        "rename",
        "unlink",
        "protected",
    )
}


def mocked_probe(
    final: Path,
    policy: dict[str, str],
    *,
    acl: str | None = "system.synoacl",
    expect: str = "denied",
) -> subprocess.CompletedProcess:
    typedir = final / "Hang" / "Mens-Fashion" / "Try-On"
    protected = typedir / ".affilio-protected-sim.tmp"
    if not protected.exists():
        protected.write_bytes(b"x")
        protected.chmod(0o444)
    argv = [
        "probe",
        "--final",
        str(final),
        "--type-dir",
        "Hang/Mens-Fashion/Try-On",
        "--protected",
        protected.name,
        "--apply",
        "--simulate",
        "--expect-placed-unlink",
        expect,
    ]
    shim = final.parent / "acl-shim.py"
    shim.write_text(
        SHIM.replace("__POLICY__", repr(policy))
        .replace("__ACL__", repr(acl))
        .replace("__ARGV__", repr(argv))
        .replace("__PROBE__", repr(str(PROBE)))
    )
    return subprocess.run(  # noqa: S603 - scripts under test, fixed argv
        [PYTHON, str(shim)], capture_output=True, text=True, check=False
    )


def summary_of(result: subprocess.CompletedProcess) -> dict:
    line = next(line for line in result.stdout.splitlines() if line.startswith("summary:"))
    return json.loads(line[len("summary:") :])


def test_t8_all_denied_under_the_mock_is_a_full_pass(tree: Path) -> None:
    """Simulated contract: every owner-rights attempt refused, ACL attribute readable and its
    rewrite refused. Exit 0, verdict PASS, evidence marked simulated, the placed file is the
    only (expected) leftover."""
    result = mocked_probe(tree, DENY_ALL)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    for name in (
        "T8a placed: open for write",
        "T8b placed: open read-write",
        "T8c placed: truncate to the same size",
        "T8d placed: utime to the same times",
        "T8e placed: chmod to the same mode",
        "T8f placed: ACL rewrite with its own bytes",
        "T8g placed: create a user xattr",
        "T8h placed: hard link to a new name in the type dir",
        "T8i placed: hard link into staging",
        "T8j placed: rename within the type dir",
    ):
        assert f"ok   {name}: refused with EACCES" in out, name
    assert "ok   T8k placed file unchanged after owner-rights tests: identical" in out
    assert "ok   T6 unlink of placed name is denied: observed: denied" in out
    assert "FAIL" not in out and "UNVERIFIED" not in out
    summary = summary_of(result)
    assert summary["verdict"] == "PASS" and summary["evidence"] == "simulated"
    assert summary["failed"] == [] and summary["unverified"] == []
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    placed = typedir / f".affilio-probe-{summary['token']}.mp4"
    assert (
        placed.exists()
        and len(summary["leftovers"]) == 1
        and str(placed) in summary["leftovers"][0]
    )
    assert list((tree / ".affilio-incoming").iterdir()) == []
    assert "T8 placed file before:" in out and "T8 placed file after:" in out


def test_t8_acl_attribute_not_listed_is_unverified_exit_4(tree: Path) -> None:
    result = mocked_probe(tree, DENY_ALL, acl=None)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "UNVERIFIED T8f placed: ACL rewrite: system.synoacl not listed" in result.stdout
    summary = summary_of(result)
    assert summary["verdict"] == "PASS_WITH_UNVERIFIED" and summary["failed"] == []
    assert summary["unverified"] == ["T8f placed: ACL rewrite"]


def test_t8_unexpected_write_is_a_fail(tree: Path) -> None:
    result = mocked_probe(tree, {**DENY_ALL, "open": "allow"})
    out = result.stdout
    assert result.returncode == 1
    assert "FAIL T8a placed: open for write: UNEXPECTEDLY SUCCEEDED" in out
    assert "FAIL T8b placed: open read-write: UNEXPECTEDLY SUCCEEDED" in out
    assert "ok   T8c placed: truncate to the same size" in out  # later tests still ran
    assert "ok   T8k placed file unchanged after owner-rights tests: identical" in out
    summary = summary_of(result)
    assert summary["verdict"] == "FAIL"
    assert summary["failed"] == ["T8a placed: open for write", "T8b placed: open read-write"]


def test_t8_unexpected_chmod_is_a_fail(tree: Path) -> None:
    result = mocked_probe(tree, {**DENY_ALL, "chmod": "allow"})
    assert result.returncode == 1
    assert "FAIL T8e placed: chmod to the same mode: UNEXPECTEDLY SUCCEEDED" in result.stdout
    summary = summary_of(result)
    assert summary["failed"] == ["T8e placed: chmod to the same mode"]
    assert "T8k placed file unchanged after owner-rights tests" in summary["passed"]


def test_t8_unexpected_rename_and_unlink_are_fails(tree: Path) -> None:
    result = mocked_probe(tree, {**DENY_ALL, "rename": "allow", "unlink": "allow"})
    out = result.stdout
    assert result.returncode == 1
    assert "FAIL T8j placed: rename within the type dir: UNEXPECTEDLY SUCCEEDED" in out
    assert "(T8j placed: rename within the type dir: unexpected success reversed)" in out
    # the reversed rename may still move the inode's ctime; T8k must report exactly that or nothing
    t8k = next(line for line in out.splitlines() if "T8k placed file unchanged" in line)
    assert t8k.endswith(": identical") or t8k.endswith(": changed: ['ctime_ns']"), t8k
    assert "FAIL T6 unlink of placed name is denied: observed: allowed" in out
    assert "FAIL T6b placed file intact after refused unlink: placed file missing" in out
    summary = summary_of(result)
    failed = [f for f in summary["failed"] if not f.startswith("T8k")]
    assert failed == [
        "T8j placed: rename within the type dir",
        "T6 unlink of placed name is denied",
        "T6b placed file intact after refused unlink",
    ]
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    assert not list(typedir.glob(".affilio-probe-*"))


def test_t8_relink_into_staging_reports_escalation(tree: Path) -> None:
    """Links allowed, writes refused through the placed name: the probe creates the second
    name, measures that a write through it succeeds (the escalation path), removes the
    link and reports every step as FAIL."""
    result = mocked_probe(tree, {**DENY_ALL, "link": "allow"})
    out = result.stdout
    assert result.returncode == 1
    assert "FAIL T8h placed: hard link to a new name in the type dir: UNEXPECTEDLY SUCCEEDED" in out
    assert (
        "(T8h placed: hard link to a new name in the type dir: unexpected success reversed)" in out
    )
    assert "FAIL T8i placed: hard link into staging: UNEXPECTEDLY SUCCEEDED" in out
    assert (
        "FAIL T8i2 placed: write through the staging-linked name (escalation): "
        "UNEXPECTEDLY SUCCEEDED" in out
    )
    assert "(T8i: staging link removed)" in out
    summary = summary_of(result)
    assert summary["failed"] == [
        "T8h placed: hard link to a new name in the type dir",
        "T8i placed: hard link into staging",
        "T8i2 placed: write through the staging-linked name (escalation)",
    ]
    typedir = tree / "Hang" / "Mens-Fashion" / "Try-On"
    assert sorted(p.name for p in typedir.glob(".affilio-probe-*")) == [
        f".affilio-probe-{summary['token']}.mp4"
    ]
    assert list((tree / ".affilio-incoming").iterdir()) == []


def test_t8_partial_cleanup_failure_fails_and_host_cleanup_finishes(world) -> None:
    host(world, "capture")
    result = mocked_probe(world["final"], {**DENY_ALL, "rmdir": "deny"})
    out = result.stdout
    assert result.returncode == 1
    assert "FAIL T7 cleanup complete: could not remove:" in out
    summary = summary_of(result)
    assert summary["verdict"] == "FAIL" and summary["failed"] == ["T7 cleanup complete"]
    token = summary["token"]
    probe_dir = world["staging"] / f"probe-{token}"
    assert probe_dir.is_dir() and any(str(probe_dir) in left for left in summary["leftovers"])
    (world["typedir"] / ".affilio-protected-sim.tmp").unlink()  # the manifest names another file
    cleaned = host(world, "cleanup", token)
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    assert not probe_dir.exists() and not list(world["typedir"].glob(".affilio-probe-*"))


def test_host_inspect_is_read_only_and_lists_token_artefacts(world) -> None:
    token = "20261030T120000Z-abcd1234"
    placed = world["typedir"] / f".affilio-probe-{token}.mp4"
    placed.write_bytes(b"placed")
    probe_dir = world["staging"] / f"probe-{token}"
    probe_dir.mkdir()
    (probe_dir / "src.part").write_bytes(b"src")
    world["seed"](placed, [e.replace("fd--", "----") for e in DIR_ENTRIES])
    before = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in (placed, probe_dir / "src.part")}
    result = host(world, "inspect", token)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"== {placed}" in result.stdout and f"== {probe_dir / 'src.part'}" in result.stdout
    assert (
        "sha256=" in result.stdout
        and "user:hoangtuhanhphuc:allow:r-x---a-R-c--:---- (level:4)" in result.stdout
    )
    assert "== " + str(world["typedir"]) + " (type dir entries)" in result.stdout
    assert {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in before} == before
    assert host(world, "inspect").returncode == 2
    assert host(world, "inspect", "../x").returncode == 2


def test_host_cleanup_removes_the_t8_names_by_exact_token(world) -> None:
    host(world, "capture")
    token = "20261030T120000Z-abcd1234"
    names = [
        world["typedir"] / f".affilio-probe-{token}.mp4",
        world["typedir"] / f".affilio-probe-{token}-link.mp4",
        world["typedir"] / f".affilio-probe-{token}-renamed.mp4",
        world["staging"] / f"probe-{token}" / "relinked",
    ]
    (world["staging"] / f"probe-{token}").mkdir()
    for n in names:
        n.write_bytes(b"x")
    other = world["typedir"] / ".affilio-probe-othertoken.mp4"
    other.write_bytes(b"keep")
    cleaned = host(world, "cleanup", token)
    assert cleaned.returncode == 1  # the other token's file remains and is reported
    assert all(not n.exists() for n in names) and not (world["staging"] / f"probe-{token}").exists()
    assert other.exists() and "remaining probe artefacts: 1 in type dir" in cleaned.stdout


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
