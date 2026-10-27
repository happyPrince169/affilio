#!/usr/bin/env bash
# scripts/nas-sync.sh — copy the AFFILIO source tree to the NAS project folder
# (/volume1/docker/affilio/) over Tailscale SSH with rsync. Explicit, one-shot, source only:
# it never builds images, never starts or restarts containers, never runs migrations and
# never triggers a deployment (that is scripts/nas-deploy.sh, run separately).
#
#   scripts/nas-sync.sh dry-run   show what would change, transfer nothing
#   scripts/nas-sync.sh once      synchronize (default for `pnpm nas:sync`)
#
# Source files deleted locally are removed on the NAS too (so a stale file can never end up in
# a build), but the NAS-only runtime folders and secrets inside the project directory
# (data/, backups/, thumbnails/, exports/, uploads/, logs/, media/, .deploy/, .env files) are
# protected on the receiver and never deleted or overwritten. RSYNC_DELETE=0 disables deletion.
#
# Configuration (environment variables):
#   NAS_HOST            SSH host alias                     (default: leosunnas)
#   NAS_DEST            destination directory on the NAS   (default: /volume1/docker/affilio/)
#   SSH_KEY             private key                        (default: ~/.ssh/id_ed25519)
#   NAS_DEST_PREFIX     only destinations under this path are accepted (default: /volume1/docker/)
#   RSYNC_DELETE        1 (default) remove source files deleted locally; 0 keep them
#   NAS_SYNC_LOG        append log lines to this file as well as stderr (optional)
#
# Exit codes: 0 ok · 1 usage/config · 2 NAS unreachable · 3 destination missing/unwritable
#             4 another sync is running · 5 rsync failed · 6 required tool missing
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-once}"
NAS_HOST="${NAS_HOST:-leosunnas}"
NAS_DEST="${NAS_DEST:-/volume1/docker/affilio/}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
NAS_DEST_PREFIX="${NAS_DEST_PREFIX:-/volume1/docker/}"
RSYNC_DELETE="${RSYNC_DELETE:-1}"
NAS_SYNC_LOG="${NAS_SYNC_LOG:-}"
EXCLUDES="$ROOT/.rsyncignore"
LOCK_FILE="${TMPDIR:-/tmp}/affilio-nas-sync.lock"

# Destinations that must never be a sync target (other services on the NAS).
PROTECTED_NAMES=(content-factory contentfactory ContentFactory meobot canonora reconnect_tailscale @eaDir)
# Receiver-side paths inside NAS_DEST that rsync may never delete or overwrite.
PROTECTED_PATHS=(/data/ /backups/ /thumbnails/ /exports/ /uploads/ /logs/ /media/ /.deploy/ .env '.env.*' 'infrastructure/docker/synology/.env')

log() {
  local line
  line="[$(date '+%F %T')] $*"
  printf '%s\n' "$line" >&2
  if [[ -n "$NAS_SYNC_LOG" ]]; then printf '%s\n' "$line" >> "$NAS_SYNC_LOG"; fi
}
die() { log "ERROR: $2"; exit "$1"; }

usage() {
  cat >&2 <<USAGE
Usage: $0 [dry-run|once]
  dry-run  list changes without transferring anything
  once     synchronize the source tree to $NAS_HOST:$NAS_DEST (default)
Environment: NAS_HOST NAS_DEST SSH_KEY NAS_DEST_PREFIX RSYNC_DELETE NAS_SYNC_LOG
This never builds, restarts or deploys anything; use scripts/nas-deploy.sh for that.
USAGE
}

validate_config() {
  [[ -r "$SSH_KEY" ]] || die 1 "SSH key not readable: $SSH_KEY"
  [[ -r "$EXCLUDES" ]] || die 1 "exclusion file missing: $EXCLUDES"
  [[ -d "$ROOT/apps" && -f "$ROOT/package.json" ]] || die 1 "not the AFFILIO repository: $ROOT"
  command -v rsync >/dev/null || die 6 "rsync is not installed"
  command -v ssh >/dev/null || die 6 "ssh is not installed"
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
  local rel name protected
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
    -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none
  )
}

# Returns 0 ok, 2 unreachable, 3 destination missing/unwritable.
check_connection() {
  local rc=0
  # NAS_DEST is validated above (absolute, no quotes or metacharacters): safe to expand here.
  # shellcheck disable=SC2029
  ssh "${ssh_opts[@]}" "$NAS_HOST" "test -d '$NAS_DEST' && test -w '$NAS_DEST'" || rc=$?
  case "$rc" in
    0) log "NAS $NAS_HOST reachable; $NAS_DEST exists and is writable"; return 0 ;;
    255) log "NAS $NAS_HOST unreachable over SSH (is Tailscale up?)"; return 2 ;;
    *)
      log "NAS $NAS_HOST reachable, but $NAS_DEST is missing or not writable."
      log "Create it yourself after review:  ssh $NAS_HOST 'mkdir -p $NAS_DEST'"
      return 3 ;;
  esac
}

rsync_args=()
build_rsync() {
  local ssh_cmd="ssh" opt protected
  for opt in "${ssh_opts[@]}"; do ssh_cmd+=" $(printf '%q' "$opt")"; done
  rsync_args=(
    -az --itemize-changes --human-readable --stats
    --exclude-from="$EXCLUDES" --rsync-path=/usr/bin/rsync --timeout=120 -e "$ssh_cmd"
  )
  for protected in "${PROTECTED_PATHS[@]}"; do rsync_args+=(--filter="P $protected"); done
  if [[ "$RSYNC_DELETE" == "1" ]]; then rsync_args+=(--delete); fi
  if [[ "$MODE" == "dry-run" ]]; then rsync_args+=(--dry-run); fi
}

# Record which local revision was synced so `nas:deploy` and `nas:status` can show it.
source_revision() {
  local head dirty
  head="$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
  dirty="clean"
  if ! git -C "$ROOT" diff --quiet 2>/dev/null || ! git -C "$ROOT" diff --cached --quiet 2>/dev/null; then dirty="dirty"; fi
  printf 'revision=%s\nworking_tree=%s\nsynced_at=%s\nsynced_from=%s\n' \
    "$head" "$dirty" "$(date -u '+%FT%TZ')" "$(hostname)"
}

sync_once() {
  local label="sync" started rc=0
  [[ "$MODE" == "dry-run" ]] && label="dry-run"
  log "$label: $ROOT/ → $NAS_HOST:$NAS_DEST (delete=$RSYNC_DELETE, protected: ${PROTECTED_PATHS[*]})"
  started=$(date +%s)
  rsync "${rsync_args[@]}" "$ROOT/" "$NAS_HOST:$NAS_DEST" || rc=$?
  if [[ $rc -ne 0 ]]; then log "$label FAILED (rsync exit $rc)"; return "$rc"; fi
  if [[ "$MODE" != "dry-run" ]]; then
    # shellcheck disable=SC2029
    source_revision | ssh "${ssh_opts[@]}" "$NAS_HOST" "mkdir -p '$NAS_DEST.deploy' && cat > '$NAS_DEST.deploy/source-revision'" \
      || log "warning: could not record the source revision on the NAS"
  fi
  log "$label done in $(( $(date +%s) - started ))s"
}

main() {
  case "$MODE" in
    dry-run|once) ;;
    -h|--help|help) usage; exit 1 ;;
    *) usage; die 1 "unknown mode '$MODE' (watch mode was removed; sync is explicit)" ;;
  esac
  validate_config
  build_ssh
  build_rsync
  exec 9>"$LOCK_FILE"
  flock -n 9 || die 4 "another nas-sync is running (lock $LOCK_FILE)"
  local rc=0
  check_connection || rc=$?
  [[ $rc -ne 2 ]] || exit 2
  if [[ $rc -eq 3 ]]; then
    [[ "$MODE" == "dry-run" ]] || exit 3
    log "dry-run continues against the missing directory (nothing is created)"
  fi
  sync_once || exit 5
  [[ "$MODE" == "dry-run" ]] || log "source is on the NAS; deploy it when you want with: pnpm nas:deploy"
}

main "$@"
