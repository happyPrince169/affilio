#!/usr/bin/env bash
# scripts/nas-b2b-host.sh — U7 Phase B2B host-side steps on the NAS, as the administrator
# identity (ACL operations on the one folder; `docker run` needs sudo and is not done here).
#
# ONE destination directory only. Subcommands, run in this order, each reviewed before the next:
#   preflight                 read-only: paths, ACLs, no probe leftovers
#   capture                   baseline: full ACL of the type dir and staging, ACL dump of every
#                             dir and file under FINAL (staging excluded), inode/size/mtime/ctime
#                             listing; writes a run manifest
#   apply-acl                 add the single create-file entry on the type dir; PASS only when the
#                             current ACL equals the baseline plus exactly that entry
#   prepare-protected         disposable protected file in the type dir with a legacy file's ACL
#                             copied onto it (the legacy file is only read); recorded in the manifest
#   inspect <probe-token>     read-only: stat, SHA-256 and ACL of every artefact of that token
#                             (run BEFORE cleanup: it is the authoritative ACL record of the
#                             placed file for the B3.1 owner-rights probe)
#   cleanup <probe-token>     remove the probe artefacts of that token and the manifest's protected
#                             file by exact name; directories only when empty; never recursive
#   restore-acl               remove exactly the entry this run added (unique fixed-string match);
#                             PASS only when the complete ACL equals the baseline again
#   verify                    PASS when nothing changed outside the allowed set (type dir metadata,
#                             and its ACL while the entry is applied); otherwise BLOCKED, exit 1
#
# Every path, the tool and the baseline directory are overridable for local rehearsals with a
# mocked synoacltool (FINAL, TYPE_DIR, LEGACY_REF, B2B_DIR, SYNOACLTOOL). Nothing here writes to
# legacy media. Exit codes: 0 pass, 1 blocked/failed, 2 usage, 3 precondition.
set -Eeuo pipefail

F="${FINAL:-/volume1/ContentFactory/fashion/FINAL}"
TYPE_REL="${TYPE_DIR:-Hang/Mens-Fashion/Try-On}"
D="$F/$TYPE_REL"
S="$F/.affilio-incoming"
T="${SYNOACLTOOL:-/usr/syno/bin/synoacltool}"
ENTRY="user:hoangtuhanhphuc:allow:-w-----------:----"   # create file, this folder only
B="${B2B_DIR:-}"
LEGACY_REF="${LEGACY_REF:-$F/Hang/T9/Couple be.mp4}"

die() { echo "ERROR: $*" >&2; exit "${2:-1}"; }
need_baseline() { [ -n "$B" ] && [ -f "$B/typedir-acl.txt" ] || die "run capture first and export B2B_DIR=<its directory>" 2; }

# ACL entry lines of a path with the volatile [index] stripped; order, principals, permissions,
# inheritance flags and levels are kept verbatim.
acl_entries() { "$T" -get "$1" | { grep -E '^[[:space:]]*\[[0-9]+\] ' || true; } | sed -E 's/^[[:space:]]*\[[0-9]+\] //'; }
acl_dump() {   # "<path><TAB><entry>" for every dir and file under FINAL except staging and probe artefacts
  find "$F" -mindepth 0 \( -type d -o -type f \) ! -path "$S" ! -path "$S/*" \
    ! -name '.affilio-probe-*' ! -name '.affilio-protected-*' ! -name '.affilio-dir-*' -print0 | sort -z \
    | while IFS= read -r -d '' p; do echo "== $p"; acl_entries "$p" | sed "s|^|$p	|"; done
}
listing() {    # inode nlink size mtime ctime path, except staging and probe artefacts
  find "$F" -mindepth 1 \( -type d -o -type f \) ! -path "$S" ! -path "$S/*" \
    ! -name '.affilio-probe-*' ! -name '.affilio-protected-*' ! -name '.affilio-dir-*' \
    -printf '%i %n %s %T@ %C@ %p\n' | sort
}
# Indices of lines equal to our entry (fixed-string, whole entry text up to the level marker).
entry_indices() {
  "$T" -get "$D" | { grep -F -- "] $ENTRY " || true; } | sed -nE 's/^[^[]*\[([0-9]+)\].*/\1/p'
}
manual_recovery() {
  echo "MANUAL RECOVERY REQUIRED for $D" >&2
  echo "  baseline entries ($B/typedir-acl.txt):" >&2; sed 's/^/    /' "$B/typedir-entries.txt" >&2
  echo "  current ACL:" >&2; "$T" -get "$D" | sed 's/^/    /' >&2
  echo "  remove only the line(s) not in the baseline with: $T -del \"$D\" <index>" >&2
  echo "  then re-run: $0 restore-acl" >&2
}

case "${1:-}" in
  preflight)
    echo "== identity: $(id)"; echo "== tool: $T"; echo "== type dir: $D"
    [ -d "$D" ] && [ ! -L "$D" ] || die "type dir missing or a symlink" 3
    [ -d "$S" ] && [ ! -L "$S" ] || die "staging dir missing or a symlink" 3
    [ -f "$LEGACY_REF" ] || die "legacy reference file missing (read-only use)" 3
    echo "== type dir ACL:"; acl_entries "$D" | sed 's/^/   /'
    echo "== staging ACL:"; acl_entries "$S" | sed 's/^/   /'; echo "   entries under staging: $(find "$S" -mindepth 1 | wc -l)"
    echo "== probe artefacts in type dir: $(find "$D" -maxdepth 1 -name '.affilio-*' | wc -l)"
    echo "== legacy reference ACL (read-only): $LEGACY_REF"; acl_entries "$LEGACY_REF" | sed 's/^/   /'
    n=$(entry_indices | wc -l); echo "== our entry present: $n time(s)"
    ;;
  capture)
    B="${B:-/volume1/docker/affilio/backups/b2b-$(date -u +%Y%m%dT%H%M%SZ)}"
    mkdir -p "$B"; chmod 750 "$B"
    "$T" -get "$D" > "$B/typedir-acl.txt"; acl_entries "$D" > "$B/typedir-entries.txt"
    "$T" -get "$S" > "$B/staging-acl.txt"
    acl_dump > "$B/acl-all.txt"; listing > "$B/listing.txt"
    { echo "type_dir=$D"; echo "entry=$ENTRY"; echo "captured_at=$(date -u +%FT%TZ)"; } > "$B/manifest"
    echo "baseline in $B: $(wc -l < "$B/typedir-entries.txt") type-dir entries, $(wc -l < "$B/acl-all.txt") ACL lines, $(wc -l < "$B/listing.txt") listed paths"
    echo "export B2B_DIR=$B"
    ;;
  apply-acl)
    need_baseline
    if ! diff -q "$B/typedir-entries.txt" <(acl_entries "$D") >/dev/null; then
      echo "type dir ACL differs from the baseline before any change; re-capture first" >&2; exit 1; fi
    n=$(entry_indices | wc -l)
    [ "$n" -eq 0 ] || die "entry already present ($n); nothing added" 1
    "$T" -add "$D" "$ENTRY"
    added=$(diff <(cat "$B/typedir-entries.txt") <(acl_entries "$D") | grep -E '^> ' | sed 's/^> //' || true)
    removed=$(diff <(cat "$B/typedir-entries.txt") <(acl_entries "$D") | grep -E '^< ' || true)
    count=$(printf '%s' "$added" | grep -c . || true)
    if [ -n "$removed" ] || [ "$count" -ne 1 ] || [ "${added% (level:*}" != "$ENTRY" ]; then
      echo "FAIL: ACL after add is not baseline + our entry (added $count line(s): ${added:-none}; removed: ${removed:-none})" >&2
      manual_recovery; exit 1
    fi
    idx=$(entry_indices); echo "added_index=$idx" >> "$B/manifest"
    echo "PASS apply-acl: $D now equals baseline + [$idx] $ENTRY"
    ;;
  prepare-protected)
    need_baseline
    name=".affilio-protected-$(date -u +%Y%m%dT%H%M%SZ)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n').tmp"
    p="$D/$name"; ( set -o noclobber; printf 'affilio protected probe\n' > "$p" )
    "$T" -copy "$LEGACY_REF" "$p"   # reads the legacy ACL, writes it onto the disposable file only
    if diff -q <(acl_entries "$LEGACY_REF" | sed -E 's/ \(level:[0-9]+\)$//') <(acl_entries "$p" | sed -E 's/ \(level:[0-9]+\)$//') >/dev/null; then
      echo "PASS prepare-protected: $p carries the legacy reference ACL"
    else
      echo "FAIL: protected file ACL differs from the legacy reference" >&2; acl_entries "$p" >&2; exit 1
    fi
    echo "protected=$name" >> "$B/manifest"; echo "export PROTECTED=$name"
    ;;
  inspect)
    token="${2:-}"; [ -n "$token" ] || die "usage: $0 inspect <probe-token>" 2
    case "$token" in *[!A-Za-z0-9T-]*) die "token must be the probe token (letters, digits, T, -)" 2 ;; esac
    for f in "$D/.affilio-probe-$token.mp4" "$D/.affilio-probe-$token-link.mp4" "$D/.affilio-probe-$token-renamed.mp4" \
             "$S/probe-$token/src.part" "$S/probe-$token/stolen" "$S/probe-$token/relinked" "$S/probe-$token"; do
      if [ -e "$f" ] || [ -L "$f" ]; then
        echo "== $f"
        stat -c 'type=%F owner=%U:%G mode=%a inode=%i nlink=%h size=%s mtime=%Y ctime=%Z' -- "$f"
        if [ -f "$f" ] && [ ! -L "$f" ]; then echo "sha256=$(sha256sum -- "$f" | cut -c1-64)"; fi
        "$T" -get "$f" || echo "(ACL not readable: $T exit $?)"
      fi
    done
    echo "== $D (type dir entries)"; acl_entries "$D"
    ;;
  cleanup)
    need_baseline
    token="${2:-}"
    prot=$(sed -n 's/^protected=//p' "$B/manifest" | tail -1)
    rc=0
    remove_file() { if [ -f "$1" ] && [ ! -L "$1" ]; then rm -v -- "$1"; elif [ -e "$1" ]; then echo "SKIP (not a regular file): $1"; rc=1; fi; }
    remove_empty_dir() { if [ -d "$1" ] && [ ! -L "$1" ]; then rmdir -v -- "$1" || { echo "NOT EMPTY, left in place: $1"; rc=1; }; fi; }
    [ -n "$prot" ] && { remove_file "$D/$prot"; remove_file "$D/$prot.renamed"; }
    if [ -n "$token" ]; then
      case "$token" in *[!A-Za-z0-9T-]*) die "token must be the probe token (letters, digits, T, -)" 2 ;; esac
      remove_file "$D/.affilio-probe-$token.mp4"
      remove_file "$D/.affilio-probe-$token-link.mp4"; remove_file "$D/.affilio-probe-$token-renamed.mp4"
      remove_empty_dir "$D/.affilio-dir-$token"
      remove_file "$S/probe-$token/src.part"; remove_file "$S/probe-$token/stolen"; remove_file "$S/probe-$token/relinked"
      remove_empty_dir "$S/probe-$token"
    else
      echo "no probe token given: only the manifest's protected file was handled"
    fi
    left=$(find "$D" -maxdepth 1 -name '.affilio-*' | wc -l); under=$(find "$S" -mindepth 1 | wc -l)
    echo "remaining probe artefacts: $left in type dir, $under under staging"
    [ "$left" -eq 0 ] && [ "$under" -eq 0 ] && [ "$rc" -eq 0 ] && echo "PASS cleanup" || { echo "BLOCKED cleanup: artefacts remain (list above); re-run with the probe token or remove by exact name" >&2; exit 1; }
    ;;
  restore-acl)
    need_baseline
    mapfile -t idxs < <(entry_indices)
    if [ "${#idxs[@]}" -eq 1 ]; then
      "$T" -del "$D" "${idxs[0]}"; echo "removed entry index ${idxs[0]}"
    elif [ "${#idxs[@]}" -eq 0 ]; then
      echo "our entry is not present; verifying the baseline"
    else
      echo "FAIL: ${#idxs[@]} entries match ours; refusing to guess" >&2; manual_recovery; exit 1
    fi
    if diff "$B/typedir-entries.txt" <(acl_entries "$D"); then
      echo "PASS restore-acl: $D equals the captured baseline"
    else
      echo "FAIL: ACL after removal still differs from the baseline" >&2; manual_recovery; exit 1
    fi
    ;;
  verify)
    need_baseline
    status=0
    # Listing: only the type dir's own line may change (mtime/ctime move when entries are added
    # or files created); any other line is an unexpected change to media or folders.
    unexpected=$(diff "$B/listing.txt" <(listing) | grep -E '^[<>] ' | grep -vF " $D" || true)
    if [ -n "$unexpected" ]; then echo "BLOCKED: unexpected metadata changes:"; echo "$unexpected"; status=1; else echo "ok listing: no change outside the type dir"; fi
    # ACL dump: while our entry is applied the type dir block may differ by exactly that entry;
    # after restore nothing may differ.
    acl_diff=$(diff "$B/acl-all.txt" <(acl_dump) | grep -E '^[<>] ' || true)
    allowed_line="> $D"$'\t'"$ENTRY (level:0)"   # our entry, on the type dir only
    other=$(printf '%s\n' "$acl_diff" | grep -v -x -F -- "$allowed_line" | grep -v '^$' || true)
    if [ -n "$other" ]; then echo "BLOCKED: unexpected ACL changes:"; echo "$other"; status=1; else echo "ok acl: nothing changed outside our entry"; fi
    [ "$status" -eq 0 ] && echo "PASS verify" || exit 1
    ;;
  *) echo "usage: $0 preflight|capture|apply-acl|prepare-protected|inspect <probe-token>|cleanup <probe-token>|restore-acl|verify" >&2; exit 2 ;;
esac
