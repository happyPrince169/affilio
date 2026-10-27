#!/usr/bin/env bash
# scripts/nas-sync.sh — synchronize the AFFILIO source tree to the NAS project folder
# (/volume1/docker/affilio/) over Tailscale SSH with rsync. Source only: it never builds,
# restarts or deploys anything. Runtime folders living next to the source on the NAS
# (data/, backups/, thumbnails/, exports/, the NAS .env files) are protected on the receiver
# even when RSYNC_DELETE=1.
#
#   scripts/nas-sync.sh dry-run   show what would change, transfer nothing
#   scripts/nas-sync.sh once      one incremental sync
#   scripts/nas-sync.sh watch     initial sync, then sync on every local change (fswatch)
#
# Configuration (environment variables; defaults suit the laptop → DS923+ setup):
#   NAS_HOST            SSH host alias                     (default: leosunnas)
#   NAS_DEST            destination directory on the NAS   (default: /volume1/docker/affilio/)
#   SSH_KEY             private key                        (default: ~/.ssh/id_ed25519)
#   NAS_DEST_PREFIX     only destinations under this path are accepted (default: /volume1/docker/)
#   RSYNC_DELETE=1      also delete remote files that no longer exist locally (opt-in, never default)
#   DRY_RUN=1           make `once` and `watch` run rsync with --dry-run (validation of the loop)
#   DEBOUNCE_SECONDS    fswatch batching latency (default: 1.5)
#   NAS_SYNC_LOG        append log lines to this file as well as stderr (optional)
#
# Exit codes: 0 ok · 1 usage/config · 2 NAS unreachable · 3 destination missing/unwritable
#             4 another sync is running · 5 rsync failed · 6 required tool missing
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"
NAS_HOST="${NAS_HOST:-leosunnas}"
NAS_DEST="${NAS_DEST:-/volume1/docker/affilio/}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
NAS_DEST_PREFIX="${NAS_DEST_PREFIX:-/volume1/docker/}"
RSYNC_DELETE="${RSYNC_DELETE:-0}"
DRY_RUN="${DRY_RUN:-0}"
DEBOUNCE_SECONDS="${DEBOUNCE_SECONDS:-1.5}"
NAS_SYNC_LOG="${NAS_SYNC_LOG:-}"
EXCLUDES="$ROOT/.rsyncignore"
LOCK_FILE="${TMPDIR:-/tmp}/affilio-nas-sync.lock"
RETRY_MIN=5
RETRY_MAX=120

# Destinations that must never be a sync target (other services on the NAS).
PROTECTED_NAMES=(content-factory contentfactory ContentFactory meobot canonora reconnect_tailscale @eaDir)

# ------------------------------------------------------------------------------ logging
log() {
  local line
  line="[$(date '+%F %T')] $*"
  printf '%s\n' "$line" >&2
  if [[ -n "$NAS_SYNC_LOG" ]]; then printf '%s\n' "$line" >> "$NAS_SYNC_LOG"; fi
}
die() { log "ERROR: $2"; exit "$1"; }

usage() {
  cat >&2 <<USAGE
Usage: $0 {dry-run|once|watch}
  dry-run  list changes without transferring anything
  once     sync the AFFILIO source tree to $NAS_HOST:$NAS_DEST
  watch    sync now, then on every local change (Ctrl-C to stop)
Environment: NAS_HOST NAS_DEST SSH_KEY NAS_DEST_PREFIX RSYNC_DELETE DRY_RUN DEBOUNCE_SECONDS NAS_SYNC_LOG
USAGE
}

# ------------------------------------------------------------------------------ validation
validate_config() {
  [[ -r "$SSH_KEY" ]] || die 1 "SSH key not readable: $SSH_KEY"
  [[ -r "$EXCLUDES" ]] || die 1 "exclusion file missing: $EXCLUDES"
  [[ -d "$ROOT/apps" && -f "$ROOT/package.json" ]] || die 1 "not the AFFILIO repository: $ROOT"
  command -v rsync >/dev/null || die 6 "rsync is not installed"
  command -v ssh >/dev/null || die 6 "ssh is not installed"

  # Normalize: exactly one trailing slash, no doubled slashes.
  NAS_DEST="${NAS_DEST%/}/"
  case "$NAS_DEST" in
    /*) ;;
    *) die 1 "NAS_DEST must be an absolute path: $NAS_DEST" ;;
  esac
  [[ "$NAS_DEST" != *"//"* ]] || die 1 "NAS_DEST contains '//': $NAS_DEST"
  [[ "$NAS_DEST" != *".."* ]] || die 1 "NAS_DEST must not contain '..': $NAS_DEST"
  [[ "$NAS_DEST" != *[[:space:]\$\`\;\|\&\<\>\(\)\'\"\\]* ]] || die 1 "NAS_DEST contains unsafe characters: $NAS_DEST"
  NAS_DEST_PREFIX="${NAS_DEST_PREFIX%/}/"
  [[ "$NAS_DEST" == "$NAS_DEST_PREFIX"* ]] || die 1 "NAS_DEST must be under $NAS_DEST_PREFIX (got $NAS_DEST)"
  [[ "$NAS_DEST" != "$NAS_DEST_PREFIX" ]] || die 1 "NAS_DEST must be a subdirectory of $NAS_DEST_PREFIX, not the prefix itself"
  local rel name
  rel="${NAS_DEST#"$NAS_DEST_PREFIX"}"
  name="${rel%%/*}"
  for protected in "${PROTECTED_NAMES[@]}"; do
    [[ "$name" != "$protected" ]] || die 1 "NAS_DEST targets a protected service directory: $NAS_DEST"
  done
  case "$NAS_HOST" in
    ""|-*) die 1 "NAS_HOST is empty or looks like an option: '$NAS_HOST'" ;;
  esac
}

ssh_opts=()
build_ssh() {
  ssh_opts=(
    -i "$SSH_KEY"
    -o IdentitiesOnly=yes
    -o BatchMode=yes
    -o ConnectTimeout=15
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
    -o ControlMaster=no
    -o ControlPath=none
  )
}

# Returns 0 ok, 2 unreachable, 3 destination missing/unwritable.
check_connection() {
  local rc=0
  # NAS_DEST is validated above (absolute, no quotes/metacharacters), so expanding it on the
  # client side into a single-quoted remote command is safe.
  # shellcheck disable=SC2029
  ssh "${ssh_opts[@]}" "$NAS_HOST" "test -d '$NAS_DEST' && test -w '$NAS_DEST'" || rc=$?
  case "$rc" in
    0) log "NAS $NAS_HOST reachable; $NAS_DEST exists and is writable"; return 0 ;;
    255) log "NAS $NAS_HOST unreachable over SSH (is Tailscale up?)"; return 2 ;;
    *)
      log "NAS $NAS_HOST reachable, but $NAS_DEST is missing or not writable."
      log "To create it (run yourself, after review):"
      log "  ssh $NAS_HOST 'mkdir -p $NAS_DEST'"
      return 3 ;;
  esac
}

# ------------------------------------------------------------------------------ rsync
rsync_args=()
build_rsync() {
  local ssh_cmd="ssh"
  local opt
  for opt in "${ssh_opts[@]}"; do ssh_cmd+=" $(printf '%q' "$opt")"; done
  rsync_args=(
    -az
    --itemize-changes
    --human-readable
    --stats
    --exclude-from="$EXCLUDES"
    --rsync-path=/usr/bin/rsync
    --timeout=120
    -e "$ssh_cmd"
  )
  # Receiver-side protection: never delete the NAS runtime folders or its .env files, even
  # with RSYNC_DELETE=1 (they are not part of the source tree).
  local protected
  for protected in /data/ /backups/ /thumbnails/ /exports/ /uploads/ /logs/ /media/ .env 'infrastructure/docker/synology/.env'; do
    rsync_args+=(--filter="P $protected")
  done
  [[ "$RSYNC_DELETE" == "1" ]] && rsync_args+=(--delete)
  [[ "$DRY_RUN" == "1" || "$MODE" == "dry-run" ]] && rsync_args+=(--dry-run)
  return 0
}

sync_once() {
  local label="sync"
  [[ "$DRY_RUN" == "1" || "$MODE" == "dry-run" ]] && label="dry-run"
  log "$label: $ROOT/ → $NAS_HOST:$NAS_DEST${RSYNC_DELETE:+ (delete=$RSYNC_DELETE)}"
  local started rc=0
  started=$(date +%s)
  rsync "${rsync_args[@]}" "$ROOT/" "$NAS_HOST:$NAS_DEST" || rc=$?
  if [[ $rc -eq 0 ]]; then
    log "$label done in $(( $(date +%s) - started ))s"
  else
    log "$label FAILED (rsync exit $rc)"
  fi
  return "$rc"
}

# ------------------------------------------------------------------------------ watch
drain_events() {
  # Swallow fswatch batches that arrived while a sync was running, plus one more debounce
  # window of stragglers, so a burst of edits costs one sync instead of several (coalescing).
  while read -r -t "$DEBOUNCE_SECONDS" _; do :; done
  return 0
}

watch_loop() {
  command -v fswatch >/dev/null || die 6 "fswatch is not installed (sudo apt install fswatch)"
  local backoff=$RETRY_MIN pending=1 line
  log "watching $ROOT (debounce ${DEBOUNCE_SECONDS}s); Ctrl-C to stop"
  # fswatch -o prints one line per batch of events. Directory excludes mirror .rsyncignore
  # so build output and caches never trigger a sync.
  fswatch -o -r -l "$DEBOUNCE_SECONDS" \
    --exclude='(^|/)\.git(/|$)' \
    --exclude='(^|/)node_modules(/|$)' \
    --exclude='(^|/)\.next(/|$)' \
    --exclude='(^|/)\.turbo(/|$)' \
    --exclude='(^|/)\.venv(/|$)' \
    --exclude='(^|/)__pycache__(/|$)' \
    --exclude='(^|/)\.pytest_cache(/|$)' \
    --exclude='(^|/)\.ruff_cache(/|$)' \
    --exclude='(^|/)\.mypy_cache(/|$)' \
    --exclude='(^|/)\.claude(/|$)' \
    --exclude='(^|/)logs(/|$)' \
    --exclude='(^|/)tmp(/|$)' \
    --exclude='\.(log|tmp|swp)$' \
    "$ROOT" |
  while true; do
    if [[ $pending -eq 0 ]]; then
      # Block until the next batch of changes.
      read -r line || break
      drain_events
      pending=1
    fi
    if sync_once; then
      pending=0
      backoff=$RETRY_MIN
    else
      # Transient failure (Tailscale down, NAS asleep): wait with capped exponential backoff,
      # keep absorbing change events meanwhile, then retry. Never a busy loop.
      log "retrying in ${backoff}s (changes keep accumulating)"
      local waited=0
      while [[ $waited -lt $backoff ]]; do
        if read -r -t 1 line; then :; fi
        waited=$((waited + 1))
      done
      backoff=$(( backoff * 2 > RETRY_MAX ? RETRY_MAX : backoff * 2 ))
    fi
  done
  log "watch stopped"
}

# ------------------------------------------------------------------------------ main
main() {
  case "$MODE" in
    dry-run|once|watch) ;;
    -h|--help|help|"") usage; exit 1 ;;
    *) usage; die 1 "unknown mode '$MODE'" ;;
  esac
  validate_config
  build_ssh
  build_rsync

  # One sync at a time per machine (a second `watch` or `once` must not race the first).
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    die 4 "another nas-sync is running (lock $LOCK_FILE)"
  fi

  local rc=0
  check_connection || rc=$?
  if [[ $rc -eq 2 ]]; then exit 2; fi
  if [[ $rc -eq 3 && "$MODE" != "dry-run" && "$DRY_RUN" != "1" ]]; then exit 3; fi
  [[ $rc -eq 3 ]] && log "dry-run continues against the missing directory (nothing is created)"

  case "$MODE" in
    dry-run|once)
      sync_once || exit 5
      ;;
    watch)
      trap 'log "stopping watch"; exit 0' INT TERM
      watch_loop
      ;;
  esac
}

main "$@"
