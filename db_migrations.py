"""Production-safe SQLite migrations for local_data.db.

All migrations are idempotent, copy legacy rows into canonical tables, and keep
legacy tables under a timestamped backup name instead of deleting data.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

FEATURE_KEY_COLUMNS = ("key", "feature_key", "name", "flag")
FEATURE_ENABLED_COLUMNS = ("is_enabled", "enabled", "value")
BONUS_DAYS_COLUMNS = ("days", "bonus_days", "reward_days", "day", "value")
BONUS_WEIGHT_COLUMNS = ("weight", "probability", "chance", "percent", "probability_percent")


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _columns(cur: sqlite3.Cursor, table: str) -> Dict[str, sqlite3.Row]:
    try:
        return {str(row[1]): row for row in cur.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except sqlite3.Error:
        return {}


def _pick(cols: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    colset = set(cols)
    for name in candidates:
        if name in colset:
            return name
    return None


def _backup_table(cur: sqlite3.Cursor, table: str) -> str:
    backup = f"{table}_legacy_{int(time.time())}"
    suffix = 0
    while _table_exists(cur, backup):
        suffix += 1
        backup = f"{table}_legacy_{int(time.time())}_{suffix}"
    cur.execute(f'ALTER TABLE "{table}" RENAME TO "{backup}"')
    return backup


def _is_feature_flags_canonical(cols: Mapping[str, sqlite3.Row]) -> bool:
    return "key" in cols and "is_enabled" in cols


def ensure_feature_flag_schema(conn: sqlite3.Connection, defaults: Mapping[str, int]) -> None:
    cur = conn.cursor()
    nested = conn.in_transaction
    cur.execute("SAVEPOINT feature_schema_migration" if nested else "BEGIN")
    try:
        if _table_exists(cur, "feature_flags"):
            cols = _columns(cur, "feature_flags")
            if not _is_feature_flags_canonical(cols):
                legacy = _backup_table(cur, "feature_flags")
                cur.execute('CREATE TABLE feature_flags (key TEXT PRIMARY KEY, is_enabled INTEGER NOT NULL DEFAULT 1)')
                lcols = _columns(cur, legacy)
                key_col = _pick(lcols, FEATURE_KEY_COLUMNS)
                enabled_col = _pick(lcols, FEATURE_ENABLED_COLUMNS)
                if key_col:
                    enabled_expr = f'COALESCE(CAST("{enabled_col}" AS INTEGER), 1)' if enabled_col else "1"
                    cur.execute(
                        f'INSERT OR IGNORE INTO feature_flags (key, is_enabled) '
                        f'SELECT CAST("{key_col}" AS TEXT), {enabled_expr} FROM "{legacy}" '
                        f'WHERE "{key_col}" IS NOT NULL'
                    )
                logger.info("FEATURE_FLAGS_MIGRATION backup=%s key_col=%s enabled_col=%s rows=%s", legacy, key_col, enabled_col, cur.rowcount)
        else:
            cur.execute('CREATE TABLE feature_flags (key TEXT PRIMARY KEY, is_enabled INTEGER NOT NULL DEFAULT 1)')

        if _table_exists(cur, "user_feature_overrides"):
            cols = _columns(cur, "user_feature_overrides")
            if not {"user_id", "key", "is_enabled"}.issubset(cols):
                legacy = _backup_table(cur, "user_feature_overrides")
                cur.execute('CREATE TABLE user_feature_overrides (user_id INTEGER NOT NULL, key TEXT NOT NULL, is_enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id, key))')
                _copy_overrides(cur, legacy)
                logger.info("USER_FEATURE_OVERRIDES_MIGRATION backup=%s", legacy)
        else:
            cur.execute('CREATE TABLE user_feature_overrides (user_id INTEGER NOT NULL, key TEXT NOT NULL, is_enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id, key))')

        if _table_exists(cur, "feature_overrides"):
            _copy_overrides(cur, "feature_overrides")

        cur.execute('CREATE INDEX IF NOT EXISTS idx_user_feature_overrides_user_id ON user_feature_overrides(user_id)')
        cur.executemany(
            'INSERT OR IGNORE INTO feature_flags (key, is_enabled) VALUES (?, ?)',
            [(str(k), int(v)) for k, v in defaults.items()],
        )
        cur.execute("RELEASE feature_schema_migration" if nested else "COMMIT")
    except Exception:
        cur.execute("ROLLBACK TO feature_schema_migration" if nested else "ROLLBACK")
        if nested:
            cur.execute("RELEASE feature_schema_migration")
        raise


def _copy_overrides(cur: sqlite3.Cursor, table: str) -> None:
    cols = _columns(cur, table)
    user_col = _pick(cols, ("user_id", "chat_id", "telegram_id"))
    key_col = _pick(cols, FEATURE_KEY_COLUMNS)
    enabled_col = _pick(cols, FEATURE_ENABLED_COLUMNS)
    if not user_col or not key_col:
        logger.warning("USER_FEATURE_OVERRIDES_MIGRATION skipped table=%s columns=%s", table, sorted(cols))
        return
    enabled_expr = f'COALESCE(CAST("{enabled_col}" AS INTEGER), 1)' if enabled_col else "1"
    cur.execute(
        f'INSERT OR IGNORE INTO user_feature_overrides (user_id, key, is_enabled) '
        f'SELECT CAST("{user_col}" AS INTEGER), CAST("{key_col}" AS TEXT), {enabled_expr} FROM "{table}" '
        f'WHERE "{user_col}" IS NOT NULL AND "{key_col}" IS NOT NULL'
    )


def ensure_bonus_schema(conn: sqlite3.Connection, defaults: Mapping[int, int]) -> None:
    cur = conn.cursor()
    nested = conn.in_transaction
    cur.execute("SAVEPOINT bonus_schema_migration" if nested else "BEGIN")
    try:
        if _table_exists(cur, "bonus_probabilities"):
            cols = _columns(cur, "bonus_probabilities")
            if not {"days", "weight"}.issubset(cols):
                legacy = _backup_table(cur, "bonus_probabilities")
                cur.execute('CREATE TABLE bonus_probabilities (days INTEGER PRIMARY KEY, weight INTEGER NOT NULL)')
                lcols = _columns(cur, legacy)
                days_col = _pick(lcols, BONUS_DAYS_COLUMNS)
                weight_col = _pick(lcols, BONUS_WEIGHT_COLUMNS)
                if days_col and weight_col:
                    cur.execute(
                        f'INSERT OR IGNORE INTO bonus_probabilities (days, weight) '
                        f'SELECT CAST("{days_col}" AS INTEGER), CAST("{weight_col}" AS INTEGER) FROM "{legacy}" '
                        f'WHERE "{days_col}" IS NOT NULL AND "{weight_col}" IS NOT NULL'
                    )
                logger.info("BONUS_PROBABILITIES_MIGRATION backup=%s days_col=%s weight_col=%s rows=%s", legacy, days_col, weight_col, cur.rowcount)
        else:
            cur.execute('CREATE TABLE bonus_probabilities (days INTEGER PRIMARY KEY, weight INTEGER NOT NULL)')
        cur.execute('CREATE TABLE IF NOT EXISTS chance_bonus_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, granted_days INTEGER, created_at TEXT)')
        cur.execute('SELECT COUNT(*) FROM bonus_probabilities')
        if int(cur.fetchone()[0] or 0) == 0:
            cur.executemany('INSERT OR IGNORE INTO bonus_probabilities (days, weight) VALUES (?, ?)', [(int(k), int(v)) for k, v in defaults.items()])
        cur.execute("RELEASE bonus_schema_migration" if nested else "COMMIT")
    except Exception:
        cur.execute("ROLLBACK TO bonus_schema_migration" if nested else "ROLLBACK")
        if nested:
            cur.execute("RELEASE bonus_schema_migration")
        raise


def ensure_local_schema(conn: sqlite3.Connection, feature_defaults: Mapping[str, int], bonus_defaults: Mapping[int, int]) -> None:
    ensure_feature_flag_schema(conn, feature_defaults)
    ensure_bonus_schema(conn, bonus_defaults)
