#!/usr/bin/env bash
# scripts/nas-b2b-host.sh — U7 Phase B2B host-side steps on the NAS, as the administrator
# identity (no sudo needed for ACLs on folders the account may manage; `docker run` needs sudo).
#
# One destination directory only. Subcommands, meant to be run in this order and each
# reviewed before the next:
#   preflight            read-only: paths, ACLs, no probe leftovers
#   capture              baseline: ACL dump of the type dir, staging and every legacy file;
#                        inode/size/mtime/ctime listing of FINAL (excluding staging)
#   apply-acl            add the single create-file entry on the type dir, verify it
#   prepare-protected    create the disposable protected file with a legacy file's ACL copied
#   cleanup              remove the disposable files (protected file, any placed probe file)
#   restore-acl          remove exactly the entry this script added, verify against baseline
#   verify               compare a fresh listing and ACL dump with the baseline
#
# Nothing here touches legacy files for writing; `synoacltool -copy` only READS the legacy
# file's ACL. Baselines live under /volume1/docker/affilio/backups/b2b-<ts>/.
set -Eeuo pipefail

F="${FINAL:-/volume1/ContentFactory/fashion/FINAL}"
TYPE_REL="${TYPE_DIR:-Hang/Mens-Fashion/Try-On}"
D="$F/$TYPE_REL"
S="$F/.affilio-incoming"
T=/usr/syno/bin/synoacltool
ENTRY="user:hoangtuhanhphuc:allow:-w-----------:----"   # create file, this folder only
B="${B2B_DIR:-/volume1/docker/affilio/backups/b2b-$(date -u +%Y%m%dT%H%M%SZ)}"
LEGACY_REF="${LEGACY_REF:-$F/Hang/T9/Couple be.mp4}"

acl_dump() { # every dir and file under FINAL except staging, ACL text per path
  find "$F" -mindepth 0 \( -type d -o -type f \) ! -path "$S" ! -path "$S/*" -print0 | sort -z \
    | xargs -0 -I{} sh -c 'echo "== {}"; /usr/syno/bin/synoacltool -get "{}" | grep -E "allow|deny|Owner"'
}
listing() { # inode, nlink, size, mtime, ctime of every file and dir except staging and the probe names
  find "$F" -mindepth 1 \( -type d -o -type f \) ! -path "$S" ! -path "$S/*" \
    ! -name '.affilio-probe-*' ! -name '.affilio-protected-*' -printf '%i %n %s %T@ %C@ %p\n' | sort
}
entry_index() { # index of OUR entry on the type dir, or empty
  $T -get "$D" | sed -nE "s/^\s*\[([0-9]+)\] $(printf '%s' "$ENTRY" | sed 's/[][\.*^$-]/\\&/g')\s.*/\1/p" | head -1
}

case "${1:-}" in
  preflight)
    echo "== identity: $(id)"; echo "== type dir: $D"; [ -d "$D" ] && [ ! -L "$D" ] || { echo "missing or symlink"; exit 3; }
    $T -get "$D" | grep -E "Owner|allow|deny"
    echo "== staging:"; $T -get "$S" | grep -E "Owner|allow|deny"; echo "   entries under staging: $(find "$S" -mindepth 1 | wc -l)"
    echo "== probe leftovers in type dir: $(find "$D" -maxdepth 1 -name '.affilio-*' | wc -l)"
    echo "== legacy reference ACL (read-only): $LEGACY_REF"; $T -get "$LEGACY_REF" | grep -E "allow|deny"
    echo "== our entry already present? index='$(entry_index)'"
    ;;
  capture)
    mkdir -p "$B"; chmod 750 "$B"
    $T -get "$D" > "$B/typedir-acl.txt"; $T -get "$S" > "$B/staging-acl.txt"
    acl_dump > "$B/acl-all.txt"; listing > "$B/listing.txt"
    echo "baseline in $B: $(wc -l < "$B/acl-all.txt") ACL lines, $(wc -l < "$B/listing.txt") entries"; echo "export B2B_DIR=$B"
    ;;
  apply-acl)
    [ -n "${B2B_DIR:-}" ] && [ -f "$B2B_DIR/typedir-acl.txt" ] || { echo "run capture first and export B2B_DIR"; exit 2; }
    [ -z "$(entry_index)" ] || { echo "entry already present; nothing added"; exit 0; }
    $T -add "$D" "$ENTRY"
    idx="$(entry_index)"; [ -n "$idx" ] || { echo "entry not found after add"; exit 1; }
    echo "added entry index $idx on $D"; $T -get "$D" | grep -E "allow|deny"
    diff <(grep -E "allow|deny" "$B2B_DIR/typedir-acl.txt") <($T -get "$D" | grep -E "allow|deny" | grep -vF "$ENTRY") && echo "only our entry differs from baseline"
    ;;
  prepare-protected)
    name=".affilio-protected-$(date -u +%Y%m%dT%H%M%SZ)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n').tmp"
    p="$D/$name"; ( set -o noclobber; printf 'affilio protected probe\n' > "$p" )
    $T -copy "$LEGACY_REF" "$p"   # reads the legacy ACL, writes it onto the disposable file only
    echo "protected test file: $p"; $T -get "$p" | grep -E "Owner|allow|deny"
    diff <($T -get "$LEGACY_REF" | grep -E "allow|deny" | sed 's/(level:[0-9]*)//') <($T -get "$p" | grep -E "allow|deny" | sed 's/(level:[0-9]*)//') && echo "ACL identical to the legacy reference"
    echo "export PROTECTED=$name"
    ;;
  cleanup)
    for p in "$D"/.affilio-protected-*.tmp "$D"/.affilio-probe-*.mp4 "$D"/.affilio-protected-*.tmp.renamed; do [ -e "$p" ] && { rm -v -- "$p"; } || true; done
    for d in "$S"/probe-*; do [ -d "$d" ] && { rm -rv -- "$d"; } || true; done
    echo "remaining probe artefacts: $(find "$D" -maxdepth 1 -name '.affilio-*' | wc -l) in type dir, $(find "$S" -mindepth 1 | wc -l) under staging"
    ;;
  restore-acl)
    [ -n "${B2B_DIR:-}" ] && [ -f "$B2B_DIR/typedir-acl.txt" ] || { echo "export B2B_DIR to the capture directory"; exit 2; }
    idx="$(entry_index)"
    if [ -z "$idx" ]; then echo "our entry is not present; nothing to remove"; else $T -del "$D" "$idx"; echo "removed entry index $idx"; fi
    diff <(grep -E "allow|deny" "$B2B_DIR/typedir-acl.txt") <($T -get "$D" | grep -E "allow|deny") && echo "type dir ACL restored to baseline"
    ;;
  verify)
    [ -n "${B2B_DIR:-}" ] || { echo "export B2B_DIR"; exit 2; }
    echo "== listing diff (expected: only the type dir's ctime line after ACL changes)"; diff "$B2B_DIR/listing.txt" <(listing) || true
    echo "== ACL diff (expected: only the type dir while the entry is applied; none after restore)"; diff "$B2B_DIR/acl-all.txt" <(acl_dump) || true
    ;;
  *) echo "usage: $0 preflight|capture|apply-acl|prepare-protected|cleanup|restore-acl|verify"; exit 2 ;;
esac
