#!/usr/bin/env sh
# Consistent database + private-media backup; briefly stops only already-running writers.
set -eu
umask 077
PROJECT_DIR="${PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
cd "$PROJECT_DIR"
mkdir -p "$BACKUP_DIR"
target="$BACKUP_DIR/offers_$(date +%F_%H-%M-%S)"
mkdir "$target"
running="$(docker compose --profile bot --profile sync ps --services --status running | sed -n '/^backend$/p;/^bot$/p;/^billing$/p;/^offers$/p;/^recognition$/p;/^sync$/p')"
restore_writers() {
    if [ -n "$running" ]; then docker compose --profile bot --profile sync start $running >/dev/null; fi
}
trap restore_writers EXIT HUP INT TERM
if [ -n "$running" ]; then docker compose --profile bot --profile sync stop $running; fi
# No pipeline: a failed pg_dump must not be hidden by a successful gzip.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$target/database.sql"
gzip "$target/database.sql"
docker compose run --rm --no-deps -T backend tar -C /app/offer_media -czf - . > "$target/offer_media.tar.gz"
docker compose images > "$target/images.txt"
(cd "$target" && sha256sum database.sql.gz offer_media.tar.gz > SHA256SUMS)
printf 'Backup complete: %s\n' "$target"
