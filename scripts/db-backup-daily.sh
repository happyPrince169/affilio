#!/usr/bin/env bash
# scripts/db-backup-daily.sh — scheduled AFFILIO database backup for the NAS (P4B follow-up).
#
# Runs ON THE NAS from DSM Task Scheduler (user-defined script, root, daily). Each run:
#   1. dumps the database from the running postgres container (custom format, compressed)
#      to a temp file, refuses an implausibly small dump, verifies the archive with
#      `pg_restore --list`, then renames it into place and writes a .sha256 sidecar;
#   2. on the weekly day (RESTORE_TEST_DOW, Sunday by default) also keeps a weekly copy and
#      restores the new dump into a scratch database inside the container
#      (affilio_restore_test: created, restored with --exit-on-error, table count and
#      alembic revision read back, dropped) — the production database is only ever read;
#   3. prunes daily-*.dump beyond DAILY_KEEP and weekly-*.dump beyond WEEKLY_KEEP. Files the
#      deploy script writes (pre-deploy-*.dump, env-*) and anything else are never touched;
#   4. writes STATUS_FILE (status=ok|fail, finished_at, file, bytes, sha256, restore_test,
#      message) and exits non-zero on any failure, so Task Scheduler can mail the run log.
#
# Environment (all optional): BACKUP_DIR, CONTAINER, POSTGRES_USER, POSTGRES_DB, DAILY_KEEP,
# WEEKLY_KEEP, RESTORE_TEST (1 forces the restore test), RESTORE_TEST_DOW (0=Sunday),
# MIN_BYTES, NAS_DOCKER (docker command; default "docker" because Task Scheduler runs as root),
# STATUS_FILE. Nothing here needs the AFFILIO .env: the dump runs inside the container.
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/volume1/docker/affilio/backups}"
CONTAINER="${CONTAINER:-affilio-postgres}"
USER_NAME="${POSTGRES_USER:-affilio}"
DB_NAME="${POSTGRES_DB:-affilio}"
DAILY_KEEP="${DAILY_KEEP:-14}"
WEEKLY_KEEP="${WEEKLY_KEEP:-8}"
RESTORE_TEST_DOW="${RESTORE_TEST_DOW:-0}"
MIN_BYTES="${MIN_BYTES:-4096}"
DOCKER="${NAS_DOCKER:-docker}"
STATUS_FILE="${STATUS_FILE:-$BACKUP_DIR/last-backup.status}"
# Scratch database for the restore test. Its name is fixed and distinctive; the script refuses
# to run the test when a database by that name already exists (a previous run that died
# mid-way, or a real database someone created with that name), so nothing unknown is ever
# dropped. It also refuses if the name matches the production database.
SCRATCH_DB="${SCRATCH_DB:-affilio_restore_test}"
STAMP="${BACKUP_STAMP:-$(date +%Y%m%d-%H%M%S)}"
DOW="${BACKUP_DOW:-$(date +%w)}"
OUT="$BACKUP_DIR/daily-$STAMP.dump"
TMP="$OUT.tmp"
RESTORE_RESULT="skipped"
STEP="start"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
write_status() {  # $1 status, $2 message
  local bytes="" sum=""
  if [[ -f "$OUT" ]]; then bytes="$(stat -c %s "$OUT")"; sum="$(cut -d' ' -f1 "$OUT.sha256" 2>/dev/null || true)"; fi
  {
    printf 'status=%s\nfinished_at=%s\nstep=%s\nfile=%s\nbytes=%s\nsha256=%s\nrestore_test=%s\nmessage=%s\n' \
      "$1" "$(date -u '+%FT%TZ')" "$STEP" "$([[ -f "$OUT" ]] && echo "$OUT" || echo "")" "$bytes" "$sum" "$RESTORE_RESULT" "$2"
  } > "$STATUS_FILE.tmp" && mv "$STATUS_FILE.tmp" "$STATUS_FILE"
}
fail() {
  log "ERROR: $*"
  rm -f "$TMP"
  write_status fail "$*"
  exit 1
}
trap 'fail "unexpected error during step $STEP"' ERR

[[ "$DAILY_KEEP" =~ ^[0-9]+$ && "$WEEKLY_KEEP" =~ ^[0-9]+$ ]] || fail "DAILY_KEEP / WEEKLY_KEEP must be numbers"
[[ "$DAILY_KEEP" -ge 1 ]] || fail "DAILY_KEEP must keep at least the dump just taken"
[[ "$SCRATCH_DB" != "$DB_NAME" && "$SCRATCH_DB" =~ ^affilio_restore_test ]] || fail "SCRATCH_DB must be a dedicated affilio_restore_test* name, never the production database"
mkdir -p "$BACKUP_DIR"
chmod 770 "$BACKUP_DIR" 2>/dev/null || true

STEP="precheck"
${DOCKER} ps --format '{{.Names}}' | grep -qx "$CONTAINER" || fail "container $CONTAINER is not running; no backup taken"
free_kb="$(df -Pk "$BACKUP_DIR" | awk 'NR==2{print $4}')"
[[ "${free_kb:-0}" -gt 1048576 ]] || fail "less than 1 GiB free on $BACKUP_DIR; no backup taken"

STEP="dump"
log "dumping $DB_NAME from $CONTAINER to $OUT"
${DOCKER} exec "$CONTAINER" nice -n 10 pg_dump -U "$USER_NAME" -d "$DB_NAME" --format=custom --compress=6 > "$TMP" || fail "pg_dump failed"
bytes="$(stat -c %s "$TMP")"
[[ "$bytes" -ge "$MIN_BYTES" ]] || fail "dump is only $bytes bytes (minimum $MIN_BYTES); not kept"

STEP="verify"
# The archive must list cleanly and contain the schema version table.
listing="$(${DOCKER} exec -i "$CONTAINER" pg_restore --list < "$TMP")" || fail "pg_restore --list rejected the dump; not kept"
printf '%s\n' "$listing" | grep -q 'TABLE DATA public alembic_version' || fail "dump has no alembic_version table; not kept"
mv "$TMP" "$OUT"
chmod 600 "$OUT"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
log "kept $OUT ($bytes bytes, $(printf '%s\n' "$listing" | grep -c 'TABLE DATA') tables)"

if [[ "${RESTORE_TEST:-0}" == "1" || "$DOW" == "$RESTORE_TEST_DOW" ]]; then
  STEP="weekly-copy"
  cp -p "$OUT" "$BACKUP_DIR/weekly-$STAMP.dump"
  cp -p "$OUT.sha256" "$BACKUP_DIR/weekly-$STAMP.dump.sha256"
  STEP="restore-test"
  RESTORE_RESULT="fail"
  log "restore test into scratch database $SCRATCH_DB"
  existing="$(${DOCKER} exec "$CONTAINER" psql -U "$USER_NAME" -d postgres -Atc "select 1 from pg_database where datname = '$SCRATCH_DB'")" || fail "could not query pg_database"
  [[ -z "$existing" ]] || fail "database $SCRATCH_DB already exists; a previous run did not clean up or the name is in use. Inspect it and drop it by hand before the next run. The dump itself was kept."
  ${DOCKER} exec "$CONTAINER" psql -U "$USER_NAME" -d postgres -q -c "CREATE DATABASE $SCRATCH_DB" >/dev/null || fail "could not create $SCRATCH_DB"
  if ! ${DOCKER} exec -i "$CONTAINER" nice -n 10 pg_restore -U "$USER_NAME" -d "$SCRATCH_DB" --no-owner --exit-on-error < "$OUT" >/dev/null; then
    ${DOCKER} exec "$CONTAINER" psql -U "$USER_NAME" -d postgres -q -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null || true
    fail "restore test FAILED: the dump does not restore cleanly"
  fi
  tables="$(${DOCKER} exec "$CONTAINER" psql -U "$USER_NAME" -d "$SCRATCH_DB" -Atc "select count(*) from information_schema.tables where table_schema='public'")"
  revision="$(${DOCKER} exec "$CONTAINER" psql -U "$USER_NAME" -d "$SCRATCH_DB" -Atc "select version_num from alembic_version")"
  ${DOCKER} exec "$CONTAINER" psql -U "$USER_NAME" -d postgres -q -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null || log "warning: could not drop $SCRATCH_DB"
  [[ "${tables:-0}" -gt 0 && -n "$revision" ]] || fail "restore test FAILED: $tables tables, revision '$revision'"
  RESTORE_RESULT="ok"
  log "restore test ok: $tables tables, revision $revision (scratch database dropped)"
fi

STEP="prune"
prune() {  # $1 glob prefix, $2 keep
  local keep="$2" f
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    rm -f -- "$f" "$f.sha256"
    log "pruned $(basename "$f")"
  done < <(ls -1t "$BACKUP_DIR"/"$1"-*.dump 2>/dev/null | tail -n +"$((keep + 1))")
}
prune daily "$DAILY_KEEP"
prune weekly "$WEEKLY_KEEP"

STEP="done"
write_status ok "backup ok"
log "done: $(tr '\n' ' ' < "$STATUS_FILE")"
