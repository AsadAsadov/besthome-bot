#!/usr/bin/env bash
set -euo pipefail
ACTIVE_DB="/var/www/besthome-bot/data/besthome.db"
DATA_DIR="$(dirname "$ACTIVE_DB")"
BACKUP_DIR="$DATA_DIR/backups"
LOCK_FILE="$DATA_DIR/.besthome-db-deploy.lock"
VERSION_FILE="$DATA_DIR/besthome.db.version.json"
log() { printf '[rollback_besthome_db] %s\n' "$*"; }
fail() { log "ERROR: $*"; exit 1; }
mkdir -p "$DATA_DIR" "$BACKUP_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "another deploy/rollback is running"
backup="${1:-}"
if [[ -z "$backup" ]]; then
  backup=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'besthome_*.db' -printf '%T@ %p\n' | sort -nr | awk 'NR==1 {print $2}')
fi
[[ -n "$backup" && -f "$backup" ]] || fail "backup not found"
[[ ! -L "$backup" ]] || fail "backup must not be a symlink"
[[ "$(sqlite3 "$backup" 'PRAGMA quick_check;')" == "ok" ]] || fail "backup integrity failed"
[[ "$(sqlite3 "$backup" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='listings';")" == "1" ]] || fail "listings table missing"
count=$(sqlite3 "$backup" 'SELECT COUNT(*) FROM listings;')
[[ "$count" -gt 0 ]] || fail "listings table empty"
tmp="$DATA_DIR/.besthome.db.rollbacking-$(date -u +%Y%m%d_%H%M%S)-$$"
cp -p -- "$backup" "$tmp"
sha256=$(sha256sum "$tmp" | awk '{print $1}')
size=$(stat -c '%s' -- "$tmp")
owner_group="$(stat -c '%U:%G' "$DATA_DIR")"
[[ -f "$ACTIVE_DB" ]] && owner_group="$(stat -c '%U:%G' "$ACTIVE_DB")"
chown "$owner_group" "$tmp" || true
chmod 0644 "$tmp"
mv -f -- "$tmp" "$ACTIVE_DB"
python3 - "$VERSION_FILE" "$sha256" "$size" "$count" <<'PY'
import json, sys
from datetime import datetime, timezone
path, sha256, size, count = sys.argv[1:]
json.dump({"deployed_at": datetime.now(timezone.utc).isoformat(), "sha256": sha256, "size": int(size), "listings_count": int(count), "source": "rollback"}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
chown "$owner_group" "$VERSION_FILE" || true
chmod 0644 "$VERSION_FILE"
log "ROLLBACK_OK backup=$backup sha256=$sha256 size=$size listings_count=$count"
