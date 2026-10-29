#!/usr/bin/env python3
"""U7 Phase B2B — finalization probe, run INSIDE an isolated container as the service identity
(uid 1026, gid 100, no supplementary groups) with ONE read-write bind of FINAL at --final.

Proves, with disposable files only, what the worker will do and what it must not be able to do:

  T1 exclusive create + write + fsync of a staging file under <final>/.affilio-incoming/<probe>/
  T2 hard link of that staging file into <final>/<type-dir>/ : same device and inode, nlink 2,
     readable through the new name
  T3 linking onto the existing name again is refused (EEXIST), content untouched
  T4 negative tests on the administrator-prepared protected file (legacy-like ACL):
     open for write, unlink, rename, hard-link to a new name, utime → every one refused;
     mkdir in the type directory → refused (no create-subfolder right granted)
  T5 unlink of the staging name keeps the placed file readable (nlink back to 1)
  T6 unlink of the placed name from the type directory: reported against --expect-placed-unlink
     (allowed with the current staging ACL that inherits delete into files; denied once the
     staging ACL is split into a directory entry and a file entry without delete)
  T7 cleanup of the probe directory; the placed file is left for host-side cleanup when the
     service identity may not delete it

Stdlib only. Dry run by default; `--apply` executes. Every path is derived from the three
arguments and a unique probe token; nothing else under FINAL is opened for writing. Legacy
media is never opened, even read-only. Exit 0 only when every expectation holds.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import stat
import sys
import time

DENIED = {errno.EACCES, errno.EPERM}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", required=True, help="container path of the FINAL bind mount")
    ap.add_argument("--type-dir", required=True, help="relative Owner/Category/Type directory")
    ap.add_argument("--protected", required=True, help="name of the protected test file in type-dir")
    ap.add_argument("--expect-placed-unlink", choices=["allowed", "denied"], default="allowed")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--simulate", action="store_true", help="local dry rehearsal only: skip the service-identity guard")
    args = ap.parse_args()

    final = os.path.realpath(args.final)
    staging_root = os.path.join(final, ".affilio-incoming")
    type_dir = os.path.normpath(os.path.join(final, args.type_dir))
    if ".." in args.type_dir.split("/") or not type_dir.startswith(final + os.sep):
        print("ERROR: --type-dir escapes FINAL"); return 2
    if "/" in args.protected or args.protected.startswith("..") or not args.protected.startswith("."):
        print("ERROR: --protected must be a dot-prefixed basename"); return 2
    for p in (final, staging_root, type_dir):
        if os.path.islink(p) or not os.path.isdir(p):
            print(f"ERROR: {p} missing or a symlink"); return 3
    protected = os.path.join(type_dir, args.protected)
    if os.path.islink(protected) or not os.path.isfile(protected):
        print(f"ERROR: protected test file missing: {protected}"); return 3

    token = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}"
    probe_dir = os.path.join(staging_root, f"probe-{token}")
    src = os.path.join(probe_dir, "src.part")
    placed_name = f".affilio-probe-{token}.mp4"
    placed = os.path.join(type_dir, placed_name)
    print(json.dumps({
        "identity": {"uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups()},
        "probe_dir": probe_dir, "placed": placed, "protected": protected,
        "expect_placed_unlink": args.expect_placed_unlink, "mode": "apply" if args.apply else "dry-run",
    }, indent=1))
    if not args.apply:
        print("dry run: add --apply to execute"); return 0
    if os.getgroups() and not args.simulate:
        print("FAIL identity has supplementary groups; not the service identity"); return 1

    results: list[tuple[str, bool, str]] = []
    failed = False

    def check(name: str, ok: bool, detail: str) -> None:
        nonlocal failed
        results.append((name, ok, detail))
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
        failed = failed or not ok

    def expect_denied(name: str, fn) -> None:
        try:
            fn()
        except OSError as exc:
            check(name, exc.errno in DENIED or exc.errno == errno.EEXIST,
                  f"refused with {errno.errorcode.get(exc.errno, exc.errno)}")
            return
        check(name, False, "UNEXPECTEDLY SUCCEEDED")

    payload = f"affilio-b2b-probe {token}\n".encode()
    try:
        # T1
        os.mkdir(probe_dir, 0o750)
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        os.write(fd, payload); os.fsync(fd); os.close(fd)
        check("T1 exclusive create+write+fsync in staging", os.lstat(src).st_size == len(payload), src)
        # T2
        os.link(src, placed, follow_symlinks=False)
        a, b = os.lstat(src), os.lstat(placed)
        same = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and b.st_nlink == 2
        with open(placed, "rb") as h:
            readable = h.read() == payload
        check("T2 hard link into type dir", same and readable,
              f"dev={b.st_dev} ino={b.st_ino} nlink={b.st_nlink} readable={readable}")
        # T3
        try:
            os.link(src, placed, follow_symlinks=False)
            check("T3 link onto existing name refused", False, "UNEXPECTEDLY SUCCEEDED")
        except FileExistsError:
            check("T3 link onto existing name refused", os.lstat(placed).st_nlink == 2, "EEXIST, placed file untouched")
        # T4 negatives on the protected file (never read its content). Non-destructive
        # attempts first; the destructive ones last so an unexpected success cannot mask the
        # others. Each check guards against the file having disappeared.
        before = os.lstat(protected)
        extra_dir = os.path.join(type_dir, f".affilio-dir-{token}")
        expect_denied("T4a protected: open for write", lambda: os.close(os.open(protected, os.O_WRONLY | os.O_NOFOLLOW)))
        expect_denied("T4b protected: open read-write", lambda: os.close(os.open(protected, os.O_RDWR | os.O_NOFOLLOW)))
        expect_denied("T4c protected: utime", lambda: os.utime(protected, (before.st_atime, before.st_mtime)))
        expect_denied("T4d protected: hard-link to a new name", lambda: os.link(protected, os.path.join(probe_dir, "stolen"), follow_symlinks=False))
        expect_denied("T4e type dir: mkdir (no create-subfolder right)", lambda: os.mkdir(extra_dir))
        expect_denied("T4f protected: rename", lambda: os.rename(protected, protected + ".renamed"))
        if os.path.lexists(protected + ".renamed") and not os.path.lexists(protected):
            os.rename(protected + ".renamed", protected)  # undo an unexpected rename
            print("     (unexpected rename undone)")
        expect_denied("T4g protected: unlink", lambda: os.unlink(protected))
        if os.path.lexists(protected):
            after = os.lstat(protected)
            check("T4h protected file unchanged",
                  (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                  f"ino={after.st_ino} size={after.st_size}")
        else:
            check("T4h protected file unchanged", False, "protected file is GONE")
        # T5
        os.unlink(src)
        c = os.lstat(placed)
        with open(placed, "rb") as h:
            still = h.read() == payload
        check("T5 staging unlink keeps placed file", c.st_nlink == 1 and still, f"nlink={c.st_nlink} readable={still}")
        # T6
        try:
            os.unlink(placed)
            outcome = "allowed"
        except OSError as exc:
            outcome = "denied" if exc.errno in DENIED else f"error {errno.errorcode.get(exc.errno, exc.errno)}"
        check(f"T6 unlink of placed name is {args.expect_placed_unlink}", outcome == args.expect_placed_unlink, f"observed: {outcome}")
    finally:
        # T7 cleanup of everything this identity may remove; report the rest.
        leftovers = []
        for p in (src, os.path.join(probe_dir, "stolen")):
            if os.path.lexists(p):
                try: os.unlink(p)
                except OSError as exc: leftovers.append(f"{p} ({errno.errorcode.get(exc.errno, exc.errno)})")
        extra = os.path.join(type_dir, f".affilio-dir-{token}")
        if os.path.isdir(extra):
            try: os.rmdir(extra)
            except OSError as exc: leftovers.append(f"{extra} ({errno.errorcode.get(exc.errno, exc.errno)})")
        if os.path.isdir(probe_dir):
            try: os.rmdir(probe_dir)
            except OSError as exc: leftovers.append(f"{probe_dir} ({errno.errorcode.get(exc.errno, exc.errno)})")
        if os.path.lexists(placed):
            leftovers.append(f"{placed} (expected when the service identity may not delete in the type dir)")
        print("T7 host-side cleanup needed for:", leftovers or "nothing")
        print("summary:", json.dumps({"passed": [n for n, ok, _ in results if ok], "failed": [n for n, ok, _ in results if not ok], "leftovers": leftovers}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
