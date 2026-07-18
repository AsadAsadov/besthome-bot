#!/usr/bin/env bash
set -euo pipefail

ACTIVE_DB="/var/www/besthome-bot/data/besthome.db"
DATA_DIR="$(dirname "$ACTIVE_DB")"
BACKUP_DIR="$DATA_DIR/backups"
LOCK_FILE="$DATA_DIR/.besthome-db-deploy.lock"
VERSION_FILE="$DATA_DIR/besthome.db.version.json"
MIN_SIZE_BYTES="${BESTHOME_MIN_DB_SIZE_BYTES:-1048576}"
KEEP_BACKUPS="${BESTHOME_KEEP_BACKUPS:-10}"
UPLOAD_PATH="${1:-}"
EXPECTED_SHA256="${2:-}"

log() { printf '[activate_besthome_db] %s\n' "$*"; }
fail() { log "ERROR: $*"; [[ -n "${UPLOAD_PATH:-}" && -f "${UPLOAD_PATH:-}" ]] && rm -f -- "$UPLOAD_PATH" || true; exit 1; }

[[ -n "$UPLOAD_PATH" ]] || fail "uploaded DB path argument is required"
mkdir -p "$DATA_DIR" "$BACKUP_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "another deploy is already running"

case "$UPLOAD_PATH" in
  "$DATA_DIR"/.besthome.db.uploading-*) ;;
  *) fail "upload path is outside allowed temporary namespace" ;;
esac
[[ "$UPLOAD_PATH" != *".."* ]] || fail "path traversal is not allowed"
[[ -f "$UPLOAD_PATH" ]] || fail "uploaded file does not exist"
[[ ! -L "$UPLOAD_PATH" ]] || fail "uploaded file must not be a symlink"

size=$(stat -c '%s' -- "$UPLOAD_PATH")
[[ "$size" -ge "$MIN_SIZE_BYTES" ]] || fail "uploaded DB is too small: $size bytes"

log "validating sqlite integrity"
quick_check=$(sqlite3 "$UPLOAD_PATH" 'PRAGMA quick_check;')
[[ "$quick_check" == "ok" ]] || fail "quick_check failed: $quick_check"

has_listings=$(sqlite3 "$UPLOAD_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='listings';")
[[ "$has_listings" == "1" ]] || fail "listings table missing"
listings_count=$(sqlite3 "$UPLOAD_PATH" 'SELECT COUNT(*) FROM listings;')
[[ "$listings_count" -gt 0 ]] || fail "listings table is empty"

for col in id date_read operation price phone summary source_link; do
  exists=$(sqlite3 "$UPLOAD_PATH" "SELECT COUNT(*) FROM pragma_table_info('listings') WHERE name='$col';")
  [[ "$exists" == "1" ]] || fail "required listings column missing: $col"
done

sha256=$(sha256sum "$UPLOAD_PATH" | awk '{print $1}')
if [[ -n "$EXPECTED_SHA256" && "$sha256" != "$EXPECTED_SHA256" ]]; then
  fail "sha256 mismatch expected=$EXPECTED_SHA256 actual=$sha256"
fi

ts=$(date -u +%Y%m%d_%H%M%S)
if [[ -f "$ACTIVE_DB" ]]; then
  backup="$BACKUP_DIR/besthome_${ts}.db"
  log "creating backup $backup"
  cp -p -- "$ACTIVE_DB" "$backup"
fi

owner_group="$(stat -c '%U:%G' "$DATA_DIR")"
if [[ -f "$ACTIVE_DB" ]]; then
  owner_group="$(stat -c '%U:%G' "$ACTIVE_DB")"
fi
chown "$owner_group" "$UPLOAD_PATH" || true
chmod 0644 "$UPLOAD_PATH"

log "atomically activating DB"
mv -f -- "$UPLOAD_PATH" "$ACTIVE_DB"

python3 - "$VERSION_FILE" "$sha256" "$size" "$listings_count" <<'PY'
import json, sys
from datetime import datetime, timezone
path, sha256, size, count = sys.argv[1:]
payload = {
    "deployed_at": datetime.now(timezone.utc).isoformat(),
    "sha256": sha256,
    "size": int(size),
    "listings_count": int(count),
    "source": "direct-scp",
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY
chown "$owner_group" "$VERSION_FILE" || true
chmod 0644 "$VERSION_FILE"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'besthome_*.db' -printf '%T@ %p\n' | sort -nr | awk "NR>${KEEP_BACKUPS} {print \$2}" | xargs -r rm -f --
log "DEPLOY_OK sha256=$sha256 size=$size listings_count=$listings_count"
