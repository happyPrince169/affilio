#!/usr/bin/env bash
# scripts/nas-deploy-remote.sh — runs ON THE NAS (invoked by scripts/nas-deploy.sh). Deploys the
# AFFILIO compose project from the source tree this script lives in. Touches only the 'affilio'
# compose project and the AFFILIO folders; never other NAS projects, never `down -v`, never prune.
#
#   bash scripts/nas-deploy-remote.sh check|deploy|rollback
set -Eeuo pipefail

ACTION="${1:-check}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="$PROJECT_DIR/infrastructure/docker/synology"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
ENV_FILE="$COMPOSE_DIR/.env"
DEPLOY_DIR="$PROJECT_DIR/.deploy"
BACKUP_DIR="$PROJECT_DIR/backups"
DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
PROJECT=affilio
IMAGES=(affilio-api affilio-web)
SERVICES=(postgres api worker publisher web)
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"
AFFILIO_DEPLOY_YES="${AFFILIO_DEPLOY_YES:-0}"
STEP="start"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }
on_error() {
  local rc=$?
  log "FAILED during step '$STEP' (exit $rc)."
  case "$STEP" in
    build) log "Nothing was changed: the running containers (if any) are untouched. Fix the build and re-run 'pnpm nas:deploy deploy'." ;;
    migrate) log "The database schema may be partially upgraded. Check 'docker compose logs api', fix, and re-run; to go back: restore the pre-deploy dump from $BACKUP_DIR or 'alembic downgrade <revision>'." ;;
    up|health) log "Containers may be in a mixed state. Inspect with 'pnpm nas:status'; to return to the previous images: 'pnpm nas:deploy rollback'." ;;
    *) log "See the messages above; nothing destructive was done automatically." ;;
  esac
  exit "$rc"
}
trap on_error ERR

compose() { ${DOCKER} compose --project-name "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

env_value() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

preflight() {
  STEP="preflight"
  [[ -f "$COMPOSE_FILE" ]] || fail "compose file missing: $COMPOSE_FILE (run pnpm nas:sync)"
  log "project: $PROJECT_DIR"
  if [[ -f "$DEPLOY_DIR/source-revision" ]]; then
    log "source on the NAS: $(tr '\n' ' ' < "$DEPLOY_DIR/source-revision")"
  else
    log "source revision unknown (no .deploy/source-revision; synced with an older script?)"
  fi
  if [[ -f "$DEPLOY_DIR/last-deploy" ]]; then
    log "last deployment: $(tr '\n' ' ' < "$DEPLOY_DIR/last-deploy")"
  else
    log "no previous deployment recorded in $DEPLOY_DIR"
  fi
  [[ -f "$ENV_FILE" ]] || fail "missing $ENV_FILE — copy $COMPOSE_DIR/.env.example to .env on the NAS and fill it in (never synced from the laptop)"
  local perms
  perms="$(stat -c %a "$ENV_FILE" 2>/dev/null || echo '?')"
  [[ "$perms" == "600" ]] || log "warning: $ENV_FILE has mode $perms (recommended 600)"
  local var missing=()
  for var in POSTGRES_PASSWORD MEDIA_ADMIN_TOKEN AFFILIO_ORIGINALS_HOST_PATH AFFILIO_PROXIES_HOST_PATH AFFILIO_FINALS_HOST_PATH AFFILIO_THUMBNAILS_HOST_PATH AFFILIO_EXPORTS_HOST_PATH AFFILIO_DATA_HOST_PATH; do
    [[ -n "$(env_value "$var")" ]] || missing+=("$var")
  done
  [[ ${#missing[@]} -eq 0 ]] || fail "unset in .env: ${missing[*]}"
  [[ "$(env_value POSTGRES_PASSWORD)" != "change-me-postgres" ]] || fail "POSTGRES_PASSWORD still has the example value"
  [[ "$(env_value MEDIA_ADMIN_TOKEN)" != "change-me-admin-token" ]] || fail "MEDIA_ADMIN_TOKEN still has the example value"
  case "$(env_value META_LIVE_PUBLISHING_ENABLED | tr '[:upper:]' '[:lower:]')" in
    ""|false|0|no) ;;
    *) fail "META_LIVE_PUBLISHING_ENABLED is enabled in .env; live Facebook publishing is not activated yet (docs/integrations/meta-live-publishing-activation.md)" ;;
  esac
  case "$(env_value META_PROVIDER_MODE | tr '[:upper:]' '[:lower:]')" in
    ""|live) ;;
    *) fail "META_PROVIDER_MODE must be 'live' (or unset) on the NAS; mock mode is development-only" ;;
  esac
  [[ -n "$(env_value CREDENTIAL_ENCRYPTION_KEYS)" ]] || log "warning: CREDENTIAL_ENCRYPTION_KEYS is empty; Facebook Page connections will be refused until it is set"
  for var in AFFILIO_ORIGINALS_HOST_PATH AFFILIO_PROXIES_HOST_PATH AFFILIO_FINALS_HOST_PATH; do
    [[ -d "$(env_value "$var")" ]] || fail "$var points to a missing folder: $(env_value "$var")"
  done
  for var in AFFILIO_THUMBNAILS_HOST_PATH AFFILIO_EXPORTS_HOST_PATH AFFILIO_DATA_HOST_PATH; do
    [[ -d "$(env_value "$var")" ]] || fail "$var points to a missing folder: $(env_value "$var") (create it: sudo mkdir -p ... && sudo chown 1026:100 ...)"
  done
  STEP="compose-config"
  compose config -q || fail "docker compose config rejected the compose file / .env"
  log "preflight OK (compose project '$PROJECT', live publishing disabled)"
}

first_deployment() {
  [[ ! -f "$DEPLOY_DIR/last-deploy" ]] && ! ${DOCKER} ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}' | grep -q .
}

confirm() {
  local answer
  if first_deployment; then
    log "This is the FIRST deployment of AFFILIO on this NAS."
    read -r -p "Type the project directory ($PROJECT_DIR) to confirm: " answer
    [[ "$answer" == "$PROJECT_DIR" ]] || fail "confirmation did not match; aborting"
  elif [[ "$AFFILIO_DEPLOY_YES" != "1" ]]; then
    read -r -p "Redeploy AFFILIO from the source on the NAS? [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || fail "aborted"
  fi
}

backup_db() {
  STEP="backup"
  if ${DOCKER} ps --format '{{.Names}}' | grep -qx "affilio-postgres"; then
    mkdir -p "$BACKUP_DIR"
    local out
    out="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S).dump"
    ${DOCKER} exec affilio-postgres pg_dump -U "$(env_value POSTGRES_USER || echo affilio)" -d "$(env_value POSTGRES_DB || echo affilio)" --format=custom --compress=6 > "$out"
    log "database backup: $out ($(du -h "$out" | cut -f1))"
  else
    log "database not running: no pre-deploy backup (first deployment or stack stopped)"
  fi
}

tag_previous() {
  STEP="tag-previous"
  local image
  for image in "${IMAGES[@]}"; do
    if ${DOCKER} image inspect "$image:latest" >/dev/null 2>&1; then
      ${DOCKER} tag "$image:latest" "$image:previous"
      log "kept $image:latest as $image:previous (rollback target)"
    fi
  done
}

wait_healthy() {
  local service="$1" waited=0 state
  while :; do
    state="$(compose ps --format '{{.Service}} {{.Health}} {{.State}}' 2>/dev/null | awk -v s="$service" '$1==s {print $2":"$3}')"
    case "$state" in
      healthy:*) return 0 ;;
      *:exited|*:dead|unhealthy:*) fail "service $service is $state" ;;
    esac
    [[ $waited -lt $HEALTH_TIMEOUT ]] || fail "service $service not healthy after ${HEALTH_TIMEOUT}s (state: ${state:-missing})"
    sleep 5; waited=$((waited + 5))
  done
}

http_checks() {
  STEP="health"
  local port bind base
  port="$(env_value AFFILIO_WEB_PORT)"; port="${port:-3080}"
  bind="$(env_value AFFILIO_BIND_ADDRESS)"; [[ -z "$bind" || "$bind" == "0.0.0.0" ]] && bind=127.0.0.1
  base="http://$bind:$port"
  curl -fsS --max-time 10 "$base/api/v1/health" >/dev/null || fail "$base/api/v1/health did not answer"
  local info
  info="$(curl -fsS --max-time 10 "$base/api/v1/system/info")"
  log "system info: $(printf '%s' "$info" | tr -d '\n' | cut -c1-220)"
  printf '%s' "$info" | grep -q '"status":"ok"' || fail "database status is not ok in /api/v1/system/info"
  printf '%s' "$info" | grep -q '"mode":"live"' || fail "Meta provider mode is not 'live'"
}

deploy() {
  preflight
  ${DOCKER} info >/dev/null 2>&1 || fail "cannot talk to Docker (sudo password or docker group needed): $DOCKER"
  confirm
  backup_db
  tag_previous
  STEP="build"
  log "building AFFILIO images (api, web) from $PROJECT_DIR"
  compose build --pull api web
  STEP="up"
  compose up -d postgres
  wait_healthy postgres
  STEP="migrate"
  log "applying database migrations"
  compose run --rm --no-deps api migrate
  STEP="up"
  log "starting / updating the AFFILIO services"
  compose up -d --remove-orphans
  STEP="health"
  local service
  for service in "${SERVICES[@]}"; do wait_healthy "$service"; log "$service healthy"; done
  http_checks
  STEP="record"
  mkdir -p "$DEPLOY_DIR"
  {
    printf 'deployed_at=%s\n' "$(date -u '+%FT%TZ')"
    [[ -f "$DEPLOY_DIR/source-revision" ]] && cat "$DEPLOY_DIR/source-revision"
    printf 'images=%s\n' "$(${DOCKER} images --format '{{.Repository}}:{{.Tag}}@{{.ID}}' affilio-api:latest affilio-web:latest | tr '\n' ' ')"
  } > "$DEPLOY_DIR/last-deploy"
  log "deployment complete: $(tr '\n' ' ' < "$DEPLOY_DIR/last-deploy")"
  log "post-deploy: open the UI, check Content → root chips, Distribution → Queue → engine card"
}

rollback() {
  preflight
  ${DOCKER} info >/dev/null 2>&1 || fail "cannot talk to Docker (sudo password or docker group needed): $DOCKER"
  STEP="rollback"
  local image
  for image in "${IMAGES[@]}"; do
    ${DOCKER} image inspect "$image:previous" >/dev/null 2>&1 || fail "no $image:previous image: nothing to roll back to"
  done
  read -r -p "Switch AFFILIO back to the previous images and restart its containers? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || fail "aborted"
  for image in "${IMAGES[@]}"; do ${DOCKER} tag "$image:previous" "$image:latest"; done
  compose up -d --no-build
  local service
  for service in "${SERVICES[@]}"; do wait_healthy "$service"; done
  log "containers run the previous images again."
  log "If the new release applied a migration the old code cannot use, also restore the pre-deploy dump:"
  log "  scripts/db-restore.sh $BACKUP_DIR/pre-deploy-<timestamp>.dump affilio-postgres   (stops api/worker/publisher first)"
  log "or downgrade the schema: compose run --rm --no-deps api alembic downgrade <previous revision>"
}

case "$ACTION" in
  check) preflight ;;
  deploy) deploy ;;
  rollback) rollback ;;
  *) fail "usage: $0 {check|deploy|rollback}" ;;
esac
