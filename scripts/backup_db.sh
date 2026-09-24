#!/bin/bash
#
# Бэкап PostgreSQL для проекта sklad.
# Запускается по cron на выходных.
# Хранит 8 последних архивов, старые удаляет.
#

set -euo pipefail

BACKUP_DIR="/opt/sklad/backups"
ENV_FILE="/opt/sklad/.env"
KEEP=8

if [ ! -f "$ENV_FILE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: .env не найден: $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

mkdir -p "$BACKUP_DIR"

DATE=$(date '+%Y-%m-%d_%H%M%S')
FILE="$BACKUP_DIR/sklad_db_${DATE}.sql.gz"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Старт бэкапа: $FILE"

PGPASSWORD="$DB_PASSWORD" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-owner \
    --no-privileges \
    | gzip > "$FILE"

if [ ! -s "$FILE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: архив пустой, удаляем" >&2
    rm -f "$FILE"
    exit 1
fi

SIZE=$(du -h "$FILE" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Готово: $FILE ($SIZE)"

mapfile -t OLD < <(ls -1t "$BACKUP_DIR"/sklad_db_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)))
for f in "${OLD[@]:-}"; do
    [ -n "$f" ] && rm -f "$f" && echo "[$(date '+%Y-%m-%d %H:%M:%S')] Удалён старый: $f"
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Завершено"
exit 0
