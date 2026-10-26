#!/usr/bin/env bash
# Restore a dump produced by db-backup.sh into the running postgres container.
# DESTRUCTIVE: drops and recreates the schema. Stop api/worker first.
# Usage: scripts/db-restore.sh <dump-file> [container-name]
set -euo pipefail
DUMP="${1:?dump file required}"
CONTAINER="${2:-affilio-postgres}"
USER_NAME="${POSTGRES_USER:-affilio}"
DB_NAME="${POSTGRES_DB:-affilio}"
[ -f "$DUMP" ] || { echo "no such file: $DUMP"; exit 1; }
read -r -p "This will REPLACE database '$DB_NAME' in container '$CONTAINER'. Type 'restore' to continue: " answer
[ "$answer" = "restore" ] || { echo "aborted"; exit 1; }
docker exec -i "$CONTAINER" pg_restore -U "$USER_NAME" -d "$DB_NAME" --clean --if-exists --no-owner < "$DUMP"
echo "restored $DUMP into $DB_NAME"
