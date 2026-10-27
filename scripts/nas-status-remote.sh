#!/usr/bin/env bash
# scripts/nas-status-remote.sh — runs ON THE NAS (invoked by scripts/nas-status.sh). Read-only.
# Expects P (project directory) and NAS_DOCKER (docker command, usually with sudo).
set -u
P="${P%/}"
echo "== AFFILIO on $(hostname) : $P"
if [ ! -d "$P" ]; then echo "project directory missing (run pnpm nas:sync)"; exit 3; fi
echo "-- source"; [ -f "$P/.deploy/source-revision" ] && sed 's/^/   /' "$P/.deploy/source-revision" || echo "   no .deploy/source-revision (never synced with the current script)"
echo "-- last deployment"; [ -f "$P/.deploy/last-deploy" ] && sed 's/^/   /' "$P/.deploy/last-deploy" || echo "   never deployed from this directory"
echo "-- configuration"
[ -f "$P/infrastructure/docker/synology/.env" ] && echo "   .env present (mode $(stat -c %a "$P/infrastructure/docker/synology/.env"))" || echo "   .env MISSING in infrastructure/docker/synology (deployment preflight will stop)"
for d in data/postgres backups thumbnails exports; do [ -d "$P/$d" ] && echo "   $d/ present" || echo "   $d/ missing"; done
echo "-- disk"; df -h /volume1 | tail -1 | awk '{print "   /volume1: " $3 " used, " $4 " free (" $5 ")"}'
echo "-- containers (compose project 'affilio')"
if $NAS_DOCKER info >/dev/null 2>&1; then
  out="$($NAS_DOCKER ps -a --filter label=com.docker.compose.project=affilio --format '   {{.Names}}\t{{.Status}}\t{{.Image}}')"
  [ -n "$out" ] && printf '%s\n' "$out" || echo "   no affilio containers (not deployed)"
  echo "-- images"; $NAS_DOCKER images --format '   {{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedSince}}' affilio-api affilio-web 2>/dev/null || true
  if [ -f "$P/infrastructure/docker/synology/.env" ]; then
    port="$(grep -E '^AFFILIO_WEB_PORT=' "$P/infrastructure/docker/synology/.env" | cut -d= -f2)"; port="${port:-3080}"
    bind="$(grep -E '^AFFILIO_BIND_ADDRESS=' "$P/infrastructure/docker/synology/.env" | cut -d= -f2)"; [ -z "$bind" ] || [ "$bind" = "0.0.0.0" ] && bind=127.0.0.1
    echo "-- health (http://$bind:$port)"
    curl -fsS --max-time 5 "http://$bind:$port/api/v1/health" >/dev/null 2>&1 && echo "   /api/v1/health: ok" || echo "   /api/v1/health: not answering"
    curl -fsS --max-time 5 "http://$bind:$port/api/v1/system/info" 2>/dev/null | tr -d '\n' | cut -c1-200 | sed 's/^/   /' || true
    echo
  fi
else
  echo "   Docker needs sudo on this NAS; run 'pnpm nas:status' from a terminal to enter the password (nothing is changed)."
fi
