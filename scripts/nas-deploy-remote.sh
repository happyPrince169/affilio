#!/usr/bin/env bash
# scripts/nas-deploy-remote.sh — runs ON THE NAS (invoked by scripts/nas-deploy.sh). Deploys the
# AFFILIO compose project from the source tree this script lives in. Touches only the 'affilio'
# compose project and the AFFILIO folders; never other NAS projects, never `down -v`, never prune.
#
#   bash scripts/nas-deploy-remote.sh check|deploy|rollback
set -Eeuo pipefail

ACTION="${1:-check}"
# Normally this file lives in <project>/scripts; AFFILIO_PROJECT_DIR overrides that (e.g. when
# the script is streamed over ssh for a preflight without a sync).
if [[ -n "${AFFILIO_PROJECT_DIR:-}" ]]; then
  PROJECT_DIR="${AFFILIO_PROJECT_DIR%/}"
else
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
fi
COMPOSE_DIR="$PROJECT_DIR/infrastructure/docker/synology"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
ENV_FILE="$COMPOSE_DIR/.env"
DEPLOY_DIR="$PROJECT_DIR/.deploy"
BACKUP_DIR="$PROJECT_DIR/backups"
DOCKER="${NAS_DOCKER:-sudo /usr/local/bin/docker}"
# The docker CLI binary without sudo: `compose config` only parses files and needs no daemon,
# so preflight can run without a password.
DOCKER_BIN="${DOCKER##* }"
PROJECT=affilio
IMAGES=(affilio-api affilio-web)
SERVICES=(postgres api worker publisher web)
# Opt-in export worker (Export pipeline E1/E2): runs only with COMPOSE_PROFILES=exports in .env.
# Images older than E1 have no `exporter` entrypoint, so deploy and rollback check the image
# before starting the service and never leave an unsupported exporter in a restart loop.
EXPORTER_SERVICE=exporter
EXPORTER_CONTAINER="affilio-$EXPORTER_SERVICE"
EXPORTER_PROBE="import affilio.worker.exporter"
# Rollback guard (P4B): run inside the rollback image with no network, prints the image's
# migration head(s) and exits 0 when the given revision exists in its script directory, 2 when
# it does not. It never opens a database connection.
ALEMBIC_PROBE='import sys
from alembic.config import Config
from alembic.script import ScriptDirectory
s = ScriptDirectory.from_config(Config("alembic.ini"))
print("heads=" + ",".join(s.get_heads()))
try:
    s.get_revision(sys.argv[1])
except Exception:
    sys.exit(2)'
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"
AFFILIO_DEPLOY_YES="${AFFILIO_DEPLOY_YES:-0}"
STEP="start"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }
on_error() {
  local rc=$?
  trap - ERR
  log "FAILED during step '$STEP' (exit $rc)."
  case "$STEP" in
    build) log "Nothing was changed: the running containers (if any) are untouched. Fix the build and re-run 'pnpm nas:deploy deploy'." ;;
    migrate) log "The database schema may be partially upgraded. Check 'docker compose logs api', fix, and re-run; to go back: restore the pre-deploy dump from $BACKUP_DIR or 'alembic downgrade <revision>'." ;;
    up|health) log "Containers may be in a mixed state. Inspect with 'pnpm nas:status'; to return to the previous images: 'pnpm nas:deploy rollback'." ;;
    rollback-schema) log "Nothing was changed: no image tag, container or database write happened." ;;
    *) log "See the messages above; nothing destructive was done automatically." ;;
  esac
  exit "$rc"
}
trap on_error ERR

compose() { ${DOCKER} compose --project-name "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }
compose_nosudo() { "$DOCKER_BIN" compose --project-name "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

# Never fails (a missing key yields an empty value) so command substitutions do not trip ERR.
env_value() { { grep -E "^$1=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

# COMPOSE_PROFILES in .env lists the opt-in profiles (comma separated).
exports_profile_active() {
  case ",$(env_value COMPOSE_PROFILES | tr -d ' '),"
  in *,exports,*) return 0 ;; *) return 1 ;; esac
}

# Does this image know the exporter? (an import, no database, no media; the image's own user)
image_supports_exporter() {
  ${DOCKER} run --rm --entrypoint python "$1" -c "$EXPORTER_PROBE" >/dev/null 2>&1
}

# Live schema revision, read-only (`select` on alembic_version). Empty when postgres is not
# running, the query fails or the table is missing; the caller fails closed on empty.
live_db_revision() {
  { ${DOCKER} exec affilio-postgres psql -U "$(env_value POSTGRES_USER || echo affilio)" -d "$(env_value POSTGRES_DB || echo affilio)" -Atc "select version_num from alembic_version" 2>/dev/null || true; } | tr -d '\r'
}

# $1 image, $2 revision. Prints "heads=<a,b>"; exit 0 = revision known to the image, 2 = unknown,
# anything else = the probe itself failed (image without alembic, docker error): unknown.
image_alembic_heads() {
  ${DOCKER} run --rm --network none --entrypoint python "$1" -c "$ALEMBIC_PROBE" "$2" 2>/dev/null
}

# Refuses a rollback unless the database revision is exactly the single migration head of the
# image that would be started. Runs BEFORE any tag, container or database change, and never
# downgrades or restores anything itself: the old image runs `alembic upgrade head` on start,
# so a revision it does not know crashes it ("Can't locate revision"), and a revision behind
# its head would be migrated forward silently. Both are refused; so is "cannot tell".
check_rollback_schema() {
  STEP="rollback-schema"
  local image="affilio-api:previous" db_rev probe_out probe_rc heads manual
  db_rev="$(live_db_revision)"
  [[ -n "$db_rev" ]] || fail "cannot read the database revision (is affilio-postgres running? does alembic_version exist?): rollback refused, nothing was changed"
  [[ "$(printf '%s\n' "$db_rev" | wc -l)" -eq 1 ]] || fail "alembic_version holds more than one row ($(printf '%s' "$db_rev" | tr '\n' ' ')): rollback refused, nothing was changed"
  if probe_out="$(image_alembic_heads "$image" "$db_rev")"; then probe_rc=0; else probe_rc=$?; fi
  heads="${probe_out#heads=}"
  manual="To proceed: take a fresh dump, bring the schema to that revision with the CURRENT image (compose run --rm --no-deps api alembic downgrade <revision>), confirm alembic_version, then run rollback again. This script never downgrades or restores the database."
  case "$probe_rc" in
    0) ;;
    2) fail "database is at '$db_rev', which $image does not know (its head: ${heads:-unknown}); starting it would crash on 'Can't locate revision'. Rollback refused, nothing was changed. $manual" ;;
    *) fail "cannot determine which migrations $image supports (probe exit $probe_rc): rollback refused, nothing was changed" ;;
  esac
  [[ -n "$heads" && "$heads" != *,* ]] || fail "$image reports no single migration head ('${heads}'): rollback refused, nothing was changed"
  [[ "$heads" == "$db_rev" ]] || fail "database is at '$db_rev' but the head of $image is '$heads': starting it would migrate the database on start-up (RUN_MIGRATIONS_ON_START). Rollback refused, nothing was changed. $manual"
  log "schema check: database at '$db_rev' = head of $image, compatible"
}

exporter_container_exists() {
  { ${DOCKER} ps -a --filter "name=^${EXPORTER_CONTAINER}\$" --format '{{.Names}}' 2>/dev/null || true; } | grep -qx "$EXPORTER_CONTAINER"
}

# Stop and remove the exporter container only (its queue rows and files are untouched).
remove_exporter_container() {
  if exporter_container_exists; then
    ${DOCKER} stop "$EXPORTER_CONTAINER" >/dev/null
    ${DOCKER} rm "$EXPORTER_CONTAINER" >/dev/null
    log "stopped and removed $EXPORTER_CONTAINER"
  fi
}

# `compose` with the exports profile forced off for this one invocation. The override is
# passed through `env` so it survives sudo's env_reset; .env itself is never modified.
compose_without_exports_profile() {
  local prefix="${DOCKER%"$DOCKER_BIN"}"
  ${prefix} env COMPOSE_PROFILES= "$DOCKER_BIN" compose --project-name "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

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
  for var in POSTGRES_PASSWORD MEDIA_ADMIN_TOKEN AFFILIO_ORIGINALS_HOST_PATH AFFILIO_PROXIES_HOST_PATH AFFILIO_FINALS_HOST_PATH AFFILIO_UPLOADS_HOST_PATH AFFILIO_THUMBNAILS_HOST_PATH AFFILIO_EXPORTS_HOST_PATH AFFILIO_DATA_HOST_PATH; do
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
  # MeoScout ingest (P4B): the api joins an external network the operator creates, and trusts
  # X-Forwarded-* only from the web proxy's subnet. Both are explicit; a wildcard is refused.
  [[ -n "$(env_value AFFILIO_INGEST_NETWORK)" ]] || fail "AFFILIO_INGEST_NETWORK is unset in .env: name the dedicated ingest network (sudo docker network create --internal affilio-ingest; docker network ls)"
  case "$(env_value TRUSTED_PROXY_IPS | tr -d ' ')" in
    "") fail "TRUSTED_PROXY_IPS is unset in .env: set it to the affilio_default subnet (sudo docker network inspect affilio_default -f '{{(index .IPAM.Config 0).Subnet}}')" ;;
    *"*"*) fail "TRUSTED_PROXY_IPS must not contain '*': the api would trust X-Forwarded-* from every peer, including the MeoScout bridge" ;;
  esac
  for var in AFFILIO_ORIGINALS_HOST_PATH AFFILIO_PROXIES_HOST_PATH AFFILIO_FINALS_HOST_PATH; do
    [[ -d "$(env_value "$var")" ]] || fail "$var points to a missing folder: $(env_value "$var")"
  done
  for var in AFFILIO_THUMBNAILS_HOST_PATH AFFILIO_EXPORTS_HOST_PATH AFFILIO_DATA_HOST_PATH; do
    [[ -d "$(env_value "$var")" ]] || fail "$var points to a missing folder: $(env_value "$var") (create it: sudo mkdir -p ... && sudo chown 1026:100 ...)"
  done
  # Uploads root (ADR 0012 amendment 2): the root and both subfolders must exist before the
  # nested bind mounts can be created; the api binds staging/ directly.
  local uploads sub
  uploads="$(env_value AFFILIO_UPLOADS_HOST_PATH)"
  for sub in "" /staging /ready; do
    [[ -d "$uploads$sub" && ! -L "$uploads$sub" ]] || fail "AFFILIO_UPLOADS_HOST_PATH$sub is missing or a symlink: $uploads$sub (create it: sudo mkdir -p $uploads/staging $uploads/ready && sudo chown -R 1026:100 $uploads && sudo chmod 750 $uploads $uploads/staging $uploads/ready)"
  done
  case "$uploads" in "$(env_value AFFILIO_FINALS_HOST_PATH)"|"$(env_value AFFILIO_FINALS_HOST_PATH)"/*) fail "AFFILIO_UPLOADS_HOST_PATH must not be inside the legacy FINAL tree" ;; esac
  STEP="compose-config"
  if [[ -x "$DOCKER_BIN" ]]; then
    compose_nosudo config -q || fail "docker compose config rejected the compose file / .env"
  else
    compose config -q || fail "docker compose config rejected the compose file / .env"
  fi
  if exports_profile_active; then
    log "exports profile: active (COMPOSE_PROFILES=exports) — the exporter service is deployed and health-checked"
  else
    log "exports profile: inactive — the exporter service is not started"
  fi
  log "preflight OK (compose project '$PROJECT', live publishing disabled)"
}

first_deployment() {
  local existing
  existing="$({ ${DOCKER} ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}' 2>/dev/null || true; } | head -1)"
  [[ ! -f "$DEPLOY_DIR/last-deploy" && -z "$existing" ]]
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

# Health by container name through `docker inspect`: works on every Compose version (the NAS
# runs Compose v2.20, whose `ps --format` accepts only table|json), and the substitution never
# fails, so the ERR trap cannot fire inside it.
wait_healthy() {
  local service="$1" waited=0 state
  while :; do
    state="$({ ${DOCKER} inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "affilio-$service" 2>/dev/null || true; } | tr -d '\r')"
    case "$state" in
      running:healthy|running:none) return 0 ;;
      exited:*|dead:*|running:unhealthy) fail "service $service is $state" ;;
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
  STEP="ingest-network"
  local ingest
  ingest="$(env_value AFFILIO_INGEST_NETWORK)"
  ${DOCKER} network inspect "$ingest" >/dev/null 2>&1 || fail "the ingest network '$ingest' does not exist; create it first (sudo docker network create --internal $ingest) — compose would otherwise refuse to start the api"
  log "ingest network '$ingest' present"
  backup_db
  tag_previous
  STEP="build"
  log "building AFFILIO images (api, web) from $PROJECT_DIR"
  compose build --pull api web
  if exports_profile_active; then
    image_supports_exporter "affilio-api:latest" || fail "COMPOSE_PROFILES=exports is set but the built affilio-api image has no exporter (python -c '$EXPORTER_PROBE' failed); remove the profile from .env or deploy a release that includes it"
    log "exporter supported by the built image"
  fi
  STEP="up"
  compose up -d postgres
  wait_healthy postgres
  STEP="migrate"
  log "applying database migrations"
  compose run --rm --no-deps api migrate
  STEP="up"
  log "starting / updating the AFFILIO services"
  if exports_profile_active; then
    compose up -d --remove-orphans
  else
    # A container left from an earlier profile-enabled deploy would keep running old code.
    remove_exporter_container
    compose_without_exports_profile up -d --remove-orphans
  fi
  STEP="health"
  local service
  for service in "${SERVICES[@]}"; do wait_healthy "$service"; log "$service healthy"; done
  if exports_profile_active; then
    wait_healthy "$EXPORTER_SERVICE"; log "$EXPORTER_SERVICE healthy"
  fi
  http_checks
  STEP="record"
  mkdir -p "$DEPLOY_DIR"
  {
    printf 'deployed_at=%s\n' "$(date -u '+%FT%TZ')"
    [[ -f "$DEPLOY_DIR/source-revision" ]] && cat "$DEPLOY_DIR/source-revision"
    local image ids=""
    for image in "${IMAGES[@]}"; do  # `docker images` takes one repository at a time
      ids+="$({ ${DOCKER} images --format '{{.Repository}}:{{.Tag}}@{{.ID}}' "$image:latest" 2>/dev/null || true; } | head -1) "
    done
    printf 'images=%s\n' "$ids"
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
  # Schema guard first: nothing below runs unless the database revision matches the image.
  check_rollback_schema
  # Profile-aware: an exporter is kept only when the previous image can run it.
  local exporter_plan="absent"
  if exports_profile_active; then
    if image_supports_exporter "affilio-api:previous"; then
      exporter_plan="keep"
      log "exports profile active and affilio-api:previous supports the exporter: it is kept and health-checked"
    else
      exporter_plan="remove"
      log "exports profile active but affilio-api:previous has no exporter: $EXPORTER_CONTAINER will be stopped and removed; COMPOSE_PROFILES=exports stays in .env for the next deploy of a supporting release"
    fi
  fi
  read -r -p "Switch AFFILIO back to the previous images and restart its containers? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || fail "aborted"
  for image in "${IMAGES[@]}"; do ${DOCKER} tag "$image:previous" "$image:latest"; done
  if [[ "$exporter_plan" == "remove" ]]; then
    remove_exporter_container
    compose_without_exports_profile up -d --no-build
  else
    compose up -d --no-build
  fi
  local service
  for service in "${SERVICES[@]}"; do wait_healthy "$service"; done
  [[ "$exporter_plan" != "keep" ]] || wait_healthy "$EXPORTER_SERVICE"
  log "containers run the previous images again (schema revision verified compatible before the switch)."
}

case "$ACTION" in
  check) preflight ;;
  deploy) deploy ;;
  rollback) rollback ;;
  *) fail "usage: $0 {check|deploy|rollback}" ;;
esac
