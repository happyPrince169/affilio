#!/usr/bin/env bash
# scripts/nas-tunnel.sh — operate the AFFILIO Cloudflare Tunnel connector on the NAS (compose
# project "affilio-tunnel", directory infrastructure/docker/synology/cloudflared). Interactive
# SSH: Docker on the NAS needs the sudo password. Never prints the token.
#
#   scripts/nas-tunnel.sh config    validate the compose file + .env (no daemon needed)
#   scripts/nas-tunnel.sh up        start (or update) the connector
#   scripts/nas-tunnel.sh status    container state + last log lines
#   scripts/nas-tunnel.sh logs      follow the connector log
#   scripts/nas-tunnel.sh restart   restart the connector
#   scripts/nas-tunnel.sh down      stop and remove the connector container (no data involved)
#
# Only these verbs are accepted; nothing here touches the AFFILIO stack or other projects.
set -Eeuo pipefail

VERB="${1:-}"
NAS_HOST="${NAS_HOST:-leosunnas}"
NAS_DEST="${NAS_DEST:-/volume1/docker/affilio/}"
NAS_DEST="${NAS_DEST%/}/"
NAS_DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
NAS_SSH_TTY="${NAS_SSH_TTY:-1}"
DIR="${NAS_DEST}infrastructure/docker/synology/cloudflared"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2; exit "${1:-1}"; }

case "$NAS_DEST" in
  /volume1/docker/*/) ;;
  *) echo "ERROR: NAS_DEST must be a project directory under /volume1/docker/: $NAS_DEST" >&2; exit 1 ;;
esac
[[ "$NAS_DEST" != *[[:space:]\$\`\;\|\&\<\>\(\)\'\"\\]* ]] || { echo "ERROR: unsafe NAS_DEST" >&2; exit 1; }

COMPOSE="$NAS_DOCKER compose -p affilio-tunnel --env-file .env -f docker-compose.yml"
case "$VERB" in
  config)  REMOTE="/usr/local/bin/docker compose -p affilio-tunnel --env-file .env -f docker-compose.yml config -q && echo 'tunnel compose config: OK'" ;;
  up)      REMOTE="$COMPOSE up -d" ;;
  status)  REMOTE="$COMPOSE ps && $COMPOSE logs --tail=20 --no-log-prefix" ;;
  logs)    REMOTE="$COMPOSE logs -f --tail=100" ;;
  restart) REMOTE="$COMPOSE restart" ;;
  down)    REMOTE="$COMPOSE down" ;;
  -h|--help|help|"") usage 0 ;;
  *) echo "Refusing unknown verb: $VERB" >&2; usage ;;
esac

ssh_args=(-o ConnectTimeout=15)
[[ "$NAS_SSH_TTY" == "1" ]] && ssh_args+=(-t)
echo "==> $NAS_HOST:$DIR\$ $REMOTE" >&2
# DIR is validated above (absolute, no metacharacters); REMOTE is built from fixed verbs.
# shellcheck disable=SC2029
exec ssh "${ssh_args[@]}" "$NAS_HOST" \
  "if [ ! -f '$DIR/docker-compose.yml' ]; then echo 'ERROR: $DIR is missing on the NAS; run pnpm nas:sync first' >&2; exit 3; fi; \
   if [ ! -f '$DIR/.env' ]; then echo 'ERROR: $DIR/.env is missing; create it with the connector token (see docs/deployment/nas-sync.md)' >&2; exit 3; fi; \
   cd '$DIR' && $REMOTE"
