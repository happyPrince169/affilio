#!/usr/bin/env bash
# scripts/nas-upload-probe.sh — U7 Phase B hard-link and permission probe. Runs ON THE NAS,
# inside the api or worker container (docker exec) or as the service uid on the host.
#
# Proves, with disposable files only, that the upload design works on the real share:
#   1. the staging directory accepts an exclusive create and pwrite (API path);
#   2. a hard link from staging into a sibling directory succeeds (same filesystem);
#   3. linking onto an existing name fails with EEXIST (never overwrites);
#   4. unlink of the staging copy keeps the linked file intact; cleanup leaves nothing.
# Optionally (--owner-dir) it links one dot-prefixed probe file into a real owner folder and
# removes it again, which proves the worker's create right on the finals tree. Dot-prefixed
# names are ignored by the scanner. Nothing else in FINAL is read or written.
#
# Dry run by default: prints the plan. `--apply` executes. Every path must be under the
# staging directory or the explicitly named owner directory; anything else is refused.
set -Eeuo pipefail

STAGING=""
OWNER_DIR=""
APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --staging) STAGING="$2"; shift 2 ;;
    --owner-dir) OWNER_DIR="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    *) echo "usage: $0 --staging <FINAL>/.affilio-incoming [--owner-dir <FINAL>/<Owner>/<Cat>/<Type>] [--apply]" >&2; exit 2 ;;
  esac
done
[[ -n "$STAGING" ]] || { echo "ERROR: --staging is required" >&2; exit 2; }
case "$STAGING" in */.affilio-incoming) ;; *) echo "ERROR: --staging must end with /.affilio-incoming" >&2; exit 2 ;; esac
[[ -d "$STAGING" ]] || { echo "ERROR: staging directory does not exist (create it in Phase B first)" >&2; exit 3; }
FINAL_ROOT="${STAGING%/.affilio-incoming}"
if [[ -n "$OWNER_DIR" ]]; then
  case "$OWNER_DIR" in "$FINAL_ROOT"/*/*/*) ;; *) echo "ERROR: --owner-dir must be <FINAL>/<Owner>/<Category>/<Type>" >&2; exit 2 ;; esac
  [[ -d "$OWNER_DIR" && ! -L "$OWNER_DIR" ]] || { echo "ERROR: owner dir missing or a symlink" >&2; exit 3; }
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
PROBE_DIR="$STAGING/probe-$TS"
SRC="$PROBE_DIR/src.part"
DST_DIR="$PROBE_DIR/dest"
OWNER_PROBE="${OWNER_DIR:+$OWNER_DIR/.affilio-probe-$TS.tmp}"

echo "identity: $(id)"
echo "staging:  $STAGING"
echo "probe:    $PROBE_DIR (created and removed by this run)"
[[ -n "$OWNER_PROBE" ]] && echo "owner:    $OWNER_PROBE (linked and removed by this run)"
if [[ $APPLY -ne 1 ]]; then echo "dry run: add --apply to execute"; exit 0; fi

cleanup() {
  set +e
  [[ -n "$OWNER_PROBE" ]] && rm -f -- "$OWNER_PROBE"
  rm -f -- "$DST_DIR/linked.mp4" "$DST_DIR/occupied.mp4" "$SRC"
  rmdir -- "$DST_DIR" "$PROBE_DIR" 2>/dev/null
}
trap cleanup EXIT

mkdir -m 0750 "$PROBE_DIR"; mkdir -m 0750 "$DST_DIR"
# 1. exclusive create + write (what the API does)
( set -o noclobber; : > "$SRC" )
printf 'affilio-upload-probe %s\n' "$TS" >> "$SRC"
chmod 0640 "$SRC"
echo "ok  1 exclusive create and write in staging"
# 2. hard link into a sibling directory on the same filesystem
ln -- "$SRC" "$DST_DIR/linked.mp4"
[[ "$(stat -c %i "$SRC")" == "$(stat -c %i "$DST_DIR/linked.mp4")" ]] || { echo "FAIL 2 link is not the same inode" >&2; exit 1; }
echo "ok  2 hard link created (nlink=$(stat -c %h "$SRC"))"
# 3. never overwrite: linking onto an existing name must fail
: > "$DST_DIR/occupied.mp4"
if ln -- "$SRC" "$DST_DIR/occupied.mp4" 2>/dev/null; then echo "FAIL 3 link overwrote an existing name" >&2; exit 1; fi
[[ "$(stat -c %s "$DST_DIR/occupied.mp4")" == "0" ]] || { echo "FAIL 3 existing file changed" >&2; exit 1; }
echo "ok  3 existing name refused (EEXIST), untouched"
# 4. unlink staging keeps the linked file
rm -- "$SRC"
[[ -s "$DST_DIR/linked.mp4" ]] || { echo "FAIL 4 linked file lost after unlinking staging" >&2; exit 1; }
echo "ok  4 staging copy removed, placed file intact"
# 5. optional: create right in a real owner folder, dot-prefixed, removed immediately
if [[ -n "$OWNER_PROBE" ]]; then
  ln -- "$DST_DIR/linked.mp4" "$OWNER_PROBE"
  [[ -s "$OWNER_PROBE" ]] || { echo "FAIL 5 owner-dir link missing" >&2; exit 1; }
  rm -- "$OWNER_PROBE"
  echo "ok  5 link into $(basename "$OWNER_DIR") and removal succeeded"
fi
echo "probe passed; cleanup runs on exit"
