#!/usr/bin/env bash
# Logical backup of the AFFILIO database from the running postgres container.
# Usage: scripts/db-backup.sh [backup-dir] [container-name]
set -euo pipefail
BACKUP_DIR="${1:-./backups}"
CONTAINER="${2:-affilio-postgres}"
USER_NAME="${POSTGRES_USER:-affilio}"
DB_NAME="${POSTGRES_DB:-affilio}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/affilio-$STAMP.dump"
docker exec "$CONTAINER" pg_dump -U "$USER_NAME" -d "$DB_NAME" --format=custom --compress=6 > "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
# Keep the last 14 dumps.
ls -1t "$BACKUP_DIR"/affilio-*.dump 2>/dev/null | tail -n +15 | xargs -r rm -f
