#!/usr/bin/env bash
# scripts/nas-status.sh — read-only view of AFFILIO on the NAS: synced source revision, last
# deployment, .env presence, container health, images, disk, health endpoints. Changes nothing.
# Docker on the NAS is root-only, so the container part needs sudo (interactive password);
# everything else is shown even when sudo is unavailable. NAS_SSH_TTY=0 disables the tty.
set -Eeuo pipefail

NAS_HOST="${NAS_HOST:-leosunnas}"
NAS_DEST="${NAS_DEST:-/volume1/docker/affilio/}"
NAS_DEST="${NAS_DEST%/}/"
NAS_DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
NAS_SSH_TTY="${NAS_SSH_TTY:-1}"

case "$NAS_DEST" in
  /volume1/docker/*/) ;;
  *) echo "ERROR: NAS_DEST must be a project directory under /volume1/docker/: $NAS_DEST" >&2; exit 1 ;;
esac
[[ "$NAS_DEST" != *[[:space:]\$\`\;\|\&\<\>\(\)\'\"\\]* ]] || { echo "ERROR: unsafe NAS_DEST" >&2; exit 1; }

ssh_args=(-o ConnectTimeout=15)
[[ "$NAS_SSH_TTY" == "1" ]] && ssh_args+=(-t)

REMOTE_SCRIPT="${NAS_DEST}scripts/nas-status-remote.sh"
# The remote script is executed from the synced copy on the NAS (not streamed over stdin) so
# that `ssh -t` keeps a real terminal and sudo can prompt for the password.
# shellcheck disable=SC2029
exec ssh "${ssh_args[@]}" "$NAS_HOST" \
  "if [ ! -f '$REMOTE_SCRIPT' ]; then echo 'ERROR: $REMOTE_SCRIPT is missing on the NAS; run pnpm nas:sync first' >&2; exit 3; fi; \
   NAS_DOCKER='$NAS_DOCKER' P='$NAS_DEST' bash '$REMOTE_SCRIPT'"
