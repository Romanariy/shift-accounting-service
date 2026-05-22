#!/usr/bin/env sh
set -eu

PROJECT_DIR="${PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

timestamp="$(date +%F_%H-%M-%S)"
target="$BACKUP_DIR/shift_accounting_$timestamp.sql.gz"

docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip -9 > "$target"
find "$BACKUP_DIR" -type f -name "shift_accounting_*.sql.gz" -mtime +"$KEEP_DAYS" -delete

echo "Backup saved to $target"
