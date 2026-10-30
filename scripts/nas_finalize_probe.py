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
  T8 owner-rights tests on the placed file, which this identity OWNS (B3.1, 2026-10-30): the
     B2B negatives ran against a file owned by another user, so they say nothing about what
     the owner may do. Each attempt is independent, non-destructive attempts come first, a
     success is a FAIL even when the probe reverses it, and the file's inode, link count,
     size, SHA-256, mode, mtime, ctime and extended attributes are captured before and after:
       T8a open for write          T8b open read-write        T8c truncate (to the same size)
       T8d utime (same times)      T8e chmod (same mode)      T8f ACL rewrite (same bytes)
       T8g user xattr create       T8h hard link to a new name in the type directory
       T8i hard link into staging (+T8i2 write through that name if it was created)
       T8j rename within the type directory          T8k integrity after all of the above
     T8f rewrites the ACL extended attribute (--acl-xattr, default system.synoacl) with its
     own current bytes and XATTR_REPLACE: a refusal proves nothing changed, a success changes
     nothing but is a FAIL. When the attribute cannot be listed or read with this identity
     the test is UNVERIFIED (exit 4 when nothing else failed), never silently passed; the
     authoritative ACL record is host-side (`nas-b2b-host.sh inspect <token>` before cleanup).
     No capability, supplementary group or privileged container is used for any of this.
  T6 unlink of the placed name from the type directory, checked against the explicit
     --expect-placed-unlink contract, then the placed file's state is re-checked against the
     same contract (denied: still present, nlink 1, readable; allowed: gone):
       denied   verified contract for the production NAS (Synology DSM, btrfs, synoacl
                mount; B2B run 20261030T100545Z-89a005e1): a hard link into the type
                directory re-inherits that directory's ACL onto the inode, the service user
                keeps read and execute only, and the type directory grants no delete-child
       allowed  for a configuration where the inode keeps the staging ACL (ordinary POSIX
                semantics, or any NAS where the B2B observation does not reproduce)
     There is no default: the operator states the contract for the configuration under test
     and any other outcome is a FAIL. The original B2B run used `allowed` and exited 1 on
     exactly this mismatch while every security check passed.
  T7 cleanup of the probe directory; the placed file is left for host-side cleanup when the
     service identity may not delete it. Any other leftover is a FAIL.

Exit codes: 0 every expectation held and nothing is unverified; 1 at least one FAIL;
4 no FAIL but at least one UNVERIFIED; 2 bad arguments; 3 missing paths. The summary line
carries "evidence": "nas" or "simulated" (--simulate) so a local rehearsal can never be
mistaken for Synology evidence.

Stdlib only. Dry run by default; `--apply` executes. Every path is derived from the three
arguments and a unique probe token; nothing else under FINAL is opened for writing. Legacy
media is never opened, even read-only. Exit 0 only when every expectation holds.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
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
    ap.add_argument(
        "--expect-placed-unlink", choices=["allowed", "denied"], required=True,
        help="T6 contract: 'denied' is the verified Synology btrfs/synoacl outcome; "
             "'allowed' for configurations where the inode keeps the staging ACL",
    )
    ap.add_argument("--acl-xattr", default="system.synoacl",
                    help="name of the ACL extended attribute rewritten in place by T8f")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--simulate", action="store_true", help="local dry rehearsal only: skip the service-identity guard")
    ap.add_argument("--simulate-leftover", action="store_true", help="local rehearsal only: leave the staging file behind to exercise host cleanup")
    ap.add_argument(
        "--simulate-placed-unlink", choices=["allowed", "denied"], default=None,
        help="local rehearsal only: 'denied' makes the type directory read-only just before "
             "T6 so the kernel refuses the unlink (restored afterwards); 'allowed' leaves it",
    )
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
        "identity": {"uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups(),
                     "extra_groups": sorted(g for g in set(os.getgroups()) if g != os.getgid())},
        "probe_dir": probe_dir, "placed": placed, "protected": protected,
        "expect_placed_unlink": args.expect_placed_unlink, "mode": "apply" if args.apply else "dry-run",
    }, indent=1))
    if not args.apply:
        print("dry run: add --apply to execute"); return 0
    if (args.simulate_leftover or args.simulate_placed_unlink) and not args.simulate:
        print("ERROR: --simulate-leftover and --simulate-placed-unlink require --simulate"); return 2
    # Docker lists the primary gid itself in the supplementary set ([100] for uid 1026:100);
    # only groups beyond the primary gid mean the identity is not the service identity.
    extra_groups = sorted(g for g in set(os.getgroups()) if g != os.getgid())
    if extra_groups and not args.simulate:
        print(f"FAIL identity has supplementary groups {extra_groups}; not the service identity"); return 1

    results: list[tuple[str, bool, str]] = []
    unverified: list[tuple[str, str]] = []
    failed = False

    def check(name: str, ok: bool, detail: str) -> None:
        nonlocal failed
        results.append((name, ok, detail))
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
        failed = failed or not ok

    def mark_unverified(name: str, detail: str) -> None:
        unverified.append((name, detail))
        print(f"UNVERIFIED {name}: {detail}")

    def expect_denied(name: str, fn) -> bool:
        """Permission-denied tests accept only EACCES or EPERM. Any other errno is a FAIL too:
        it means the operation was refused for the wrong reason (missing file, wrong path).
        Returns True when the operation was refused, False when it succeeded (so the caller
        can reverse it; the FAIL is already recorded)."""
        try:
            fn()
        except OSError as exc:
            check(name, exc.errno in DENIED, f"refused with {errno.errorcode.get(exc.errno, exc.errno)}")
            return True
        except Exception as exc:  # noqa: BLE001 - a probe bug must not abort the other tests
            check(name, False, f"raised {type(exc).__name__}: {exc}")
            return True
        check(name, False, "UNEXPECTEDLY SUCCEEDED (counts as FAIL even if reversed)")
        return False

    def on_fd(path: str, op) -> None:
        """Apply `op(fd)` to the file behind `path` opened read-only without following
        symlinks (fchmod/futimens need no write access to open the descriptor)."""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            op(fd)
        finally:
            os.close(fd)

    def errname(exc: OSError) -> str:
        return errno.errorcode.get(exc.errno, str(exc.errno))

    def snapshot(path: str) -> dict:
        """Everything the owner could change, captured without opening for write."""
        st = os.lstat(path)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        xattrs: dict[str, str] = {}
        try:
            names = sorted(os.listxattr(path, follow_symlinks=False))
        except OSError as exc:
            names, xattrs = [], {"<listxattr>": errname(exc)}
        for n in names:
            try:
                xattrs[n] = hashlib.sha256(os.getxattr(path, n, follow_symlinks=False)).hexdigest()[:16]
            except OSError as exc:
                xattrs[n] = f"<{errname(exc)}>"
        return {"ino": st.st_ino, "nlink": st.st_nlink, "size": st.st_size, "sha256": h.hexdigest(),
                "mode": oct(stat.S_IMODE(st.st_mode)), "uid": st.st_uid, "mtime_ns": st.st_mtime_ns,
                "ctime_ns": st.st_ctime_ns, "xattrs": xattrs}

    payload = f"affilio-b2b-probe {token}\n".encode()
    type_dir_mode: int | None = None
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
        if os.path.lexists(os.path.join(probe_dir, "stolen")):  # reverse now so T8i starts clean
            try:
                os.unlink(os.path.join(probe_dir, "stolen")); print("     (T4d: unexpected link removed)")
            except OSError as exc:
                print(f"     (T4d: unexpected link could not be removed: {errname(exc)})")
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
        if args.simulate_leftover:
            raise RuntimeError("simulated crash before the staging unlink")
        os.unlink(src)
        c = os.lstat(placed)
        with open(placed, "rb") as h:
            still = h.read() == payload
        check("T5 staging unlink keeps placed file", c.st_nlink == 1 and still, f"nlink={c.st_nlink} readable={still}")

        # T8 owner-rights tests on the placed file. The probe owns it; every attempt is
        # independent and guarded, non-destructive ones first, each success reversed where
        # possible and recorded as FAIL regardless. The protected file is not touched here.
        link_name = os.path.join(type_dir, f".affilio-probe-{token}-link.mp4")
        renamed_name = os.path.join(type_dir, f".affilio-probe-{token}-renamed.mp4")
        relinked = os.path.join(probe_dir, "relinked")
        pre = snapshot(placed)
        print("T8 placed file before:", json.dumps(pre, sort_keys=True))

        def owner_test(name: str, fn, undo=None) -> None:
            """Run one T8 attempt only while the placed file still exists; reverse an
            unexpected success with `undo` (its own failure is reported, never hidden)."""
            if not os.path.lexists(placed):
                check(name, False, "placed file missing before the attempt")
                return
            if not expect_denied(name, fn) and undo is not None:
                try:
                    undo()
                    print(f"     ({name}: unexpected success reversed)")
                except OSError as exc:
                    print(f"     ({name}: could not reverse: {errname(exc)})")
            if os.path.lexists(placed):
                now_snap = snapshot(placed)
                changed = [k for k in ("ino", "size", "sha256") if now_snap[k] != pre[k]]
                if changed:
                    check(f"{name} left content/identity unchanged", False, f"changed: {changed}")

        owner_test("T8a placed: open for write", lambda: os.close(os.open(placed, os.O_WRONLY | os.O_NOFOLLOW)))
        owner_test("T8b placed: open read-write", lambda: os.close(os.open(placed, os.O_RDWR | os.O_NOFOLLOW)))
        owner_test("T8c placed: truncate to the same size", lambda: os.truncate(placed, pre["size"]))
        owner_test("T8d placed: utime to the same times",
                   lambda: on_fd(placed, lambda fd: os.utime(fd, ns=(os.lstat(placed).st_atime_ns, pre["mtime_ns"]))))
        owner_test("T8e placed: chmod to the same mode",
                   lambda: on_fd(placed, lambda fd: os.fchmod(fd, int(pre["mode"], 8))))
        # T8f ACL rewrite in place: only with the attribute's own current bytes.
        acl_name = args.acl_xattr
        try:
            names = os.listxattr(placed, follow_symlinks=False)
        except OSError as exc:
            names, list_err = [], errname(exc)
        else:
            list_err = None
        if acl_name in names:
            try:
                acl_bytes = os.getxattr(placed, acl_name, follow_symlinks=False)
            except OSError as exc:
                mark_unverified("T8f placed: ACL rewrite", f"{acl_name} listed but unreadable ({errname(exc)})")
            else:
                owner_test("T8f placed: ACL rewrite with its own bytes",
                           lambda: os.setxattr(placed, acl_name, acl_bytes, os.XATTR_REPLACE, follow_symlinks=False))
        else:
            mark_unverified("T8f placed: ACL rewrite",
                            f"{acl_name} not listed" + (f" (listxattr {list_err})" if list_err else f"; listed: {sorted(names)}")
                            + "; re-run with --acl-xattr <name> once the host-side listing names it")
        owner_test("T8g placed: create a user xattr",
                   lambda: os.setxattr(placed, "user.affilio-probe", b"1", os.XATTR_CREATE, follow_symlinks=False),
                   undo=lambda: os.removexattr(placed, "user.affilio-probe", follow_symlinks=False))
        owner_test("T8h placed: hard link to a new name in the type dir",
                   lambda: os.link(placed, link_name, follow_symlinks=False),
                   undo=lambda: os.unlink(link_name))
        owner_test("T8i placed: hard link into staging",
                   lambda: os.link(placed, relinked, follow_symlinks=False))
        if os.path.lexists(relinked):
            # The link exists: does the second name grant what the first does not?
            owner_test("T8i2 placed: write through the staging-linked name (escalation)",
                       lambda: os.close(os.open(relinked, os.O_WRONLY | os.O_NOFOLLOW)))
            try:
                os.unlink(relinked)
                print("     (T8i: staging link removed)")
            except OSError as exc:
                print(f"     (T8i: staging link could not be removed: {errname(exc)})")
        owner_test("T8j placed: rename within the type dir",
                   lambda: os.rename(placed, renamed_name),
                   undo=lambda: os.rename(renamed_name, placed))
        if os.path.lexists(placed):
            post = snapshot(placed)
            print("T8 placed file after:", json.dumps(post, sort_keys=True))
            diff = sorted(k for k in pre if post.get(k) != pre[k])
            check("T8k placed file unchanged after owner-rights tests", not diff and post["nlink"] == 1,
                  "identical" if not diff else f"changed: {diff}")
        else:
            check("T8k placed file unchanged after owner-rights tests", False, "placed file is GONE")
        # T6
        if args.simulate_placed_unlink == "denied":
            type_dir_mode = stat.S_IMODE(os.lstat(type_dir).st_mode)
            os.chmod(type_dir, 0o555)  # rehearsal: a real EACCES from the kernel, restored below
        try:
            os.unlink(placed)
            outcome = "allowed"
        except OSError as exc:
            outcome = "denied" if exc.errno in DENIED else f"error {errno.errorcode.get(exc.errno, exc.errno)}"
        check(f"T6 unlink of placed name is {args.expect_placed_unlink}", outcome == args.expect_placed_unlink, f"observed: {outcome}")
        # T6b: the placed file's state must agree with the contract, whatever T6 reported.
        if args.expect_placed_unlink == "denied":
            try:
                d = os.lstat(placed)
                with open(placed, "rb") as h:
                    intact = h.read() == payload
                check("T6b placed file intact after refused unlink",
                      stat.S_ISREG(d.st_mode) and d.st_nlink == 1 and d.st_size == len(payload) and intact,
                      f"ino={d.st_ino} nlink={d.st_nlink} size={d.st_size} readable={intact}")
            except OSError as exc:
                check("T6b placed file intact after refused unlink", False,
                      f"placed file missing or unreadable ({errno.errorcode.get(exc.errno, exc.errno)})")
        else:
            check("T6b placed name gone after allowed unlink", not os.path.lexists(placed),
                  "absent" if not os.path.lexists(placed) else "STILL PRESENT")
    finally:
        if type_dir_mode is not None:
            os.chmod(type_dir, type_dir_mode)
        # T7 cleanup of everything this identity may remove; report the rest. Only the placed
        # file is an expected leftover, and only under the `denied` contract.
        leftovers = []
        unexpected_leftovers = []
        for p in (src, os.path.join(probe_dir, "stolen"), os.path.join(probe_dir, "relinked"),
                  os.path.join(type_dir, f".affilio-probe-{token}-link.mp4"),
                  os.path.join(type_dir, f".affilio-probe-{token}-renamed.mp4")):
            if os.path.lexists(p):
                try: os.unlink(p)
                except OSError as exc:
                    leftovers.append(f"{p} ({errno.errorcode.get(exc.errno, exc.errno)})")
                    unexpected_leftovers.append(p)
        extra = os.path.join(type_dir, f".affilio-dir-{token}")
        if os.path.isdir(extra):
            try: os.rmdir(extra)
            except OSError as exc:
                leftovers.append(f"{extra} ({errno.errorcode.get(exc.errno, exc.errno)})")
                unexpected_leftovers.append(extra)
        if os.path.isdir(probe_dir):
            try: os.rmdir(probe_dir)
            except OSError as exc:
                leftovers.append(f"{probe_dir} ({errno.errorcode.get(exc.errno, exc.errno)})")
                unexpected_leftovers.append(probe_dir)
        if os.path.lexists(placed):
            if args.expect_placed_unlink == "denied":
                leftovers.append(f"{placed} (expected: the service identity may not delete it in the type dir)")
            else:
                leftovers.append(f"{placed} (unexpected under the allowed contract)")
                unexpected_leftovers.append(placed)
        if unexpected_leftovers:
            check("T7 cleanup complete", False, f"could not remove: {unexpected_leftovers}")
        print("T7 host-side cleanup needed for:", leftovers or "nothing")
        verdict = "FAIL" if failed else ("PASS_WITH_UNVERIFIED" if unverified else "PASS")
        print("summary:", json.dumps({"token": token, "verdict": verdict,
                                      "evidence": "simulated" if args.simulate else "nas",
                                      "passed": [n for n, ok, _ in results if ok],
                                      "failed": [n for n, ok, _ in results if not ok],
                                      "unverified": [n for n, _ in unverified], "leftovers": leftovers}))
    return 1 if failed else (4 if unverified else 0)


if __name__ == "__main__":
    sys.exit(main())
