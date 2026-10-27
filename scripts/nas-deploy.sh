#!/usr/bin/env bash
# scripts/nas-deploy.sh — deploy AFFILIO on the NAS from the source that is ALREADY in
# /volume1/docker/affilio/ (put there earlier by `pnpm nas:sync`). This script copies nothing,
# runs no rsync and no git; it only executes scripts/nas-deploy-remote.sh on the NAS over an
# interactive SSH session (the NAS asks for the sudo password; Docker is root-only there).
#
#   scripts/nas-deploy.sh check      preflight only: config, .env, compose validity, live-publishing guard
#   scripts/nas-deploy.sh deploy     backup → build AFFILIO images → migrate → start → health checks
#   scripts/nas-deploy.sh rollback   switch the AFFILIO containers back to the previous images
#
# Environment: NAS_HOST (leosunnas), NAS_DEST (/volume1/docker/affilio/), NAS_DOCKER
# (sudo /usr/local/bin/docker), NAS_SSH_TTY=0 to run without a pseudo-terminal (no sudo prompt),
# AFFILIO_DEPLOY_YES=1 to skip the confirmation on repeat deployments (never on the first one).
set -Eeuo pipefail

ACTION="${1:-}"
NAS_HOST="${NAS_HOST:-leosunnas}"
NAS_DEST="${NAS_DEST:-/volume1/docker/affilio/}"
NAS_DEST="${NAS_DEST%/}/"
NAS_SSH_TTY="${NAS_SSH_TTY:-1}"
REMOTE_SCRIPT="${NAS_DEST}scripts/nas-deploy-remote.sh"

usage() {
  cat >&2 <<USAGE
Usage: $0 {check|deploy|rollback}
Deploys the source already present on the NAS at $NAS_DEST. Run 'pnpm nas:sync' first when
the NAS copy is older than what you want to deploy; the two steps are independent.
USAGE
}

case "$ACTION" in
  check|deploy|rollback) ;;
  *) usage; exit 1 ;;
esac
case "$NAS_DEST" in
  /volume1/docker/*/) ;;
  *) echo "ERROR: NAS_DEST must be a project directory under /volume1/docker/: $NAS_DEST" >&2; exit 1 ;;
esac
[[ "$NAS_DEST" != *[[:space:]\$\`\;\|\&\<\>\(\)\'\"\\]* ]] || { echo "ERROR: unsafe NAS_DEST" >&2; exit 1; }

if [[ "$ACTION" == "deploy" ]]; then
  cat <<NOTE
About to deploy AFFILIO on $NAS_HOST from $NAS_DEST (no files are copied by this command).
It rebuilds and restarts ONLY the 'affilio' compose project (postgres, api, worker, publisher, web),
takes a database backup first when the database is running, applies migrations, and checks health.
Meta live publishing stays disabled. You will be asked for the NAS sudo password.
NOTE
fi

ssh_args=(-o BatchMode=no -o ConnectTimeout=15)
[[ "$NAS_SSH_TTY" == "1" ]] && ssh_args+=(-t)
# NAS_DEST/ACTION are validated above; AFFILIO_DEPLOY_YES and NAS_DOCKER are passed through.
# shellcheck disable=SC2029
exec ssh "${ssh_args[@]}" "$NAS_HOST" \
  "if [ ! -f '$REMOTE_SCRIPT' ]; then echo 'ERROR: $REMOTE_SCRIPT is missing on the NAS; run pnpm nas:sync first' >&2; exit 3; fi; \
   AFFILIO_DEPLOY_YES='${AFFILIO_DEPLOY_YES:-0}' NAS_DOCKER='${NAS_DOCKER:-sudo /usr/local/bin/docker}' bash '$REMOTE_SCRIPT' '$ACTION'"
