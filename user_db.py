"""SQLite access layer for BestHome user-owned data.

Listing data is intentionally kept separate in besthome.db.  Every function in
this module uses LOCAL_DB_PATH only and opens a short-lived SQLite connection per
operation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent
LOCAL_DB_PATH = Path(os.getenv("LOCAL_DB_PATH", BASE_DIR / "local_data.db")).resolve()
LISTINGS_DB_PATH = Path(os.getenv("LISTINGS_DB_PATH", BASE_DIR / "data" / "besthome.db")).resolve()

logger = logging.getLogger("besthome_user_db")

USER_TABLES = {
    "users", "subscriptions", "favorites", "user_notifications", "search_history",
    "saved_searches", "payments", "referrals", "referral_logs", "promo_codes",
    "promo_usages", "user_activity", "search_limits", "search_logs",
    "keyword_alerts", "keyword_alert_hits", "keyword_alert_state",
    "customer_requests", "customer_request_rules", "customer_request_alerts",
    "customer_request_favorites", "customer_request_archives", "customer_requests_access",
    "agents", "agent_activity", "agent_notifications", "agent_interests",
    "listing_views", "listing_status", "listing_stats", "favorite_price_history",
    "support_threads", "support_messages", "feature_flags", "feature_overrides",
    "manual_payments", "bonus_probabilities",
}

_DEFAULT_ON_CONFLICT_BY_TABLE = {
    "users": "chat_id",
    "subscriptions": "chat_id",
    "favorites": "chat_id,listing_id,source",
    "user_notifications": "chat_id,criteria_id,listing_id",
    "referrals": "referred_chat_id",
}
_CACHE_TTL_SECONDS = 60
_cache: Dict[str, Dict[str, Any]] = {}
_WRITE_LOCK = threading.RLock()
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_ident(name: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def get_local_connection() -> sqlite3.Connection:
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOCAL_DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def local_connection() -> Iterator[sqlite3.Connection]:
    conn = get_local_connection()
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row) if isinstance(row, sqlite3.Row) else dict(row)


def _rows_to_dicts(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    return [dict(r) if isinstance(r, sqlite3.Row) else dict(r) for r in rows]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({_safe_ident(table)})")}


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    table = _safe_ident(table); column = _safe_ident(column)
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_local_database() -> None:
    with local_connection() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY, first_seen TEXT, username TEXT,
                is_premium INTEGER DEFAULT 0, approved INTEGER DEFAULT 0,
                blocked INTEGER DEFAULT 0, full_name TEXT, date_joined TEXT,
                last_version TEXT DEFAULT 'v1', is_admin INTEGER DEFAULT 0,
                last_seen TEXT, promo_active INTEGER DEFAULT 0, promo_expires_at TEXT,
                referred_by INTEGER, referral_bonus_used INTEGER DEFAULT 0,
                referral_milestone_used INTEGER DEFAULT 0, demo_used INTEGER DEFAULT 0,
                demo_expires_at TEXT, blocked_at TEXT, status TEXT, joined_at TEXT,
                demo_start_at TEXT, demo_end_at TEXT, paid_until TEXT,
                last_status_change_at TEXT, role TEXT, is_first_start INTEGER DEFAULT 0,
                customer_requests_enabled INTEGER DEFAULT 0)""")
            for col, defi in {
                "first_seen":"TEXT", "username":"TEXT", "is_premium":"INTEGER DEFAULT 0",
                "approved":"INTEGER DEFAULT 0", "blocked":"INTEGER DEFAULT 0", "full_name":"TEXT",
                "date_joined":"TEXT", "last_version":"TEXT DEFAULT 'v1'", "is_admin":"INTEGER DEFAULT 0",
                "last_seen":"TEXT", "promo_active":"INTEGER DEFAULT 0", "promo_expires_at":"TEXT",
                "referred_by":"INTEGER", "referral_bonus_used":"INTEGER DEFAULT 0",
                "referral_milestone_used":"INTEGER DEFAULT 0", "demo_used":"INTEGER DEFAULT 0",
                "demo_expires_at":"TEXT", "blocked_at":"TEXT", "status":"TEXT", "joined_at":"TEXT",
                "demo_start_at":"TEXT", "demo_end_at":"TEXT", "paid_until":"TEXT",
                "last_status_change_at":"TEXT", "role":"TEXT", "is_first_start":"INTEGER DEFAULT 0",
                "customer_requests_enabled":"INTEGER DEFAULT 0", "first_name":"TEXT",
                "first_seen_at":"TEXT", "last_seen_at":"TEXT", "created_at":"TEXT",
                "is_active":"INTEGER DEFAULT 1", "is_blocked":"INTEGER DEFAULT 0", "payment_type":"TEXT",
                "last_payment_at":"TEXT", "user_support_active":"INTEGER DEFAULT 0",
                "user_support_inbox_unread":"INTEGER DEFAULT 0", "deleted_at":"TEXT", "phone":"TEXT", "is_verified":"INTEGER DEFAULT 0",
            }.items(): ensure_column(conn, "users", col, defi)
            conn.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
                chat_id INTEGER PRIMARY KEY, plan TEXT, expires_at TEXT,
                is_active INTEGER DEFAULT 0, is_demo INTEGER DEFAULT 0, last_payment_note TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, plan TEXT, amount INTEGER,
                approved_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, listing_id INTEGER, source TEXT, added_at TEXT)""")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_favorites_user_listing_source ON favorites(chat_id, listing_id, source)")
            conn.execute("""CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, search_type TEXT, query TEXT, filters TEXT, created_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, search_type TEXT, operation TEXT, rayon TEXT, query_text TEXT, created_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS user_activity (
                chat_id INTEGER PRIMARY KEY, last_seen TEXT, total_searches INTEGER DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS search_limits (
                chat_id INTEGER, date TEXT, key_type TEXT, used INTEGER DEFAULT 0,
                PRIMARY KEY(chat_id, date, key_type))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, days INTEGER, is_active INTEGER DEFAULT 1, created_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS promo_usages (code TEXT, chat_id INTEGER, used_at TEXT, expires_at TEXT, PRIMARY KEY(code, chat_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS referrals (referrer_chat_id INTEGER, referred_chat_id INTEGER PRIMARY KEY, created_at TEXT, reward_given INTEGER DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS referral_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referred_user_id INTEGER, bonus_days INTEGER, created_at TEXT)""")
            for table in USER_TABLES - {"users","subscriptions","payments","favorites","search_history","search_logs","user_activity","search_limits","promo_codes","promo_usages","referrals","referral_logs"}:
                conn.execute(f"CREATE TABLE IF NOT EXISTS {_safe_ident(table)} (id INTEGER PRIMARY KEY AUTOINCREMENT)")
            conn.commit()
        except Exception:
            conn.rollback(); raise
    log_startup_status()


def log_startup_status() -> None:
    logger.info("[BOOT] LOCAL_DB_PATH=%s", LOCAL_DB_PATH)
    logger.info("[BOOT] LOCAL_DB_REALPATH=%s", LOCAL_DB_PATH.resolve())
    logger.info("[BOOT] LISTINGS_DB_PATH=%s", LISTINGS_DB_PATH)
    logger.info("[BOOT] LISTINGS_DB_REALPATH=%s", LISTINGS_DB_PATH.resolve())
    try:
        exists = LOCAL_DB_PATH.exists(); size = LOCAL_DB_PATH.stat().st_size if exists else 0
        with local_connection() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            users_ok = _table_exists(conn, "users"); subs_ok = _table_exists(conn, "subscriptions")
        logger.info("[BOOT] local_data exists=%s size=%s integrity=%s users=%s subscriptions=%s", exists, size, integrity, users_ok, subs_ok)
    except Exception:
        logger.exception("[LOCAL DB ERROR] operation=startup_status db=%s", LOCAL_DB_PATH)


def _ttl_get(key: str) -> Any:
    item = _cache.get(key)
    if not item or item["expires_at"] < time.time():
        _cache.pop(key, None); return None
    return item["value"]


def _ttl_set(key: str, value: Any, ttl: int = _CACHE_TTL_SECONDS) -> Any:
    _cache[key] = {"value": value, "expires_at": time.time() + ttl}; return value


def invalidate_cache(*prefixes: str) -> None:
    if not prefixes: _cache.clear(); return
    for key in list(_cache):
        if any(key.startswith(p) for p in prefixes): _cache.pop(key, None)


def _log_db_error(operation: str, chat_id: Any = None, exc: BaseException | None = None) -> None:
    logger.exception("[LOCAL DB ERROR] operation=%s chat_id=%s db=%s", operation, chat_id, LOCAL_DB_PATH)


def _filter_payload(conn: sqlite3.Connection, table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    cols = _columns(conn, table)
    return {k: v for k, v in payload.items() if k in cols}


def select_one(table: str, **equals: Any) -> Optional[Dict[str, Any]]:
    table = _safe_ident(table)
    try:
        with local_connection() as conn:
            if not _table_exists(conn, table): return None
            clauses=[]; params=[]
            cols=_columns(conn, table)
            for k,v in equals.items():
                if k in cols: clauses.append(f"{_safe_ident(k)}=?"); params.append(v)
            sql=f"SELECT * FROM {table}" + (" WHERE "+" AND ".join(clauses) if clauses else "") + " LIMIT 1"
            return _row_to_dict(conn.execute(sql, params).fetchone())
    except Exception as exc:
        _log_db_error(f"select:{table}", equals.get("chat_id"), exc); return None


def select_many(table: str, *, order: Optional[str]=None, desc: bool=False, limit: Optional[int]=None, **equals: Any) -> List[Dict[str, Any]]:
    table = _safe_ident(table)
    try:
        with local_connection() as conn:
            if not _table_exists(conn, table): return []
            cols=_columns(conn, table); clauses=[]; params=[]
            for k,v in equals.items():
                if k in cols: clauses.append(f"{_safe_ident(k)}=?"); params.append(v)
            sql=f"SELECT * FROM {table}" + (" WHERE "+" AND ".join(clauses) if clauses else "")
            if order and order in cols: sql += f" ORDER BY {_safe_ident(order)} {'DESC' if desc else 'ASC'}"
            if limit: sql += " LIMIT ?"; params.append(int(limit))
            return _rows_to_dicts(conn.execute(sql, params).fetchall())
    except Exception as exc:
        _log_db_error(f"select_many:{table}", equals.get("chat_id"), exc); return []


def upsert(table: str, payload: Dict[str, Any], on_conflict: Optional[str]=None) -> Optional[Dict[str, Any]]:
    table = _safe_ident(table)
    chat_id = payload.get("chat_id") or payload.get("user_id")
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN")
                p = _filter_payload(conn, table, dict(payload))
                if not p:
                    conn.rollback(); return None
                cols = list(p); vals = [p[c] for c in cols]
                conflict = on_conflict or _DEFAULT_ON_CONFLICT_BY_TABLE.get(table)
                if conflict:
                    ccols = [_safe_ident(c.strip()) for c in conflict.split(",")]
                    updates = [c for c in cols if c not in ccols]
                    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) ON CONFLICT({','.join(ccols)}) DO UPDATE SET "
                    sql += ",".join(f"{c}=excluded.{c}" for c in updates) if updates else f"{ccols[0]}={ccols[0]}"
                else:
                    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
                conn.execute(sql, vals); conn.commit()
        invalidate_cache(table, f"{table}:{chat_id}"); return dict(payload)
    except Exception as exc:
        _log_db_error(f"upsert:{table}", chat_id, exc); return None


def insert(table: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    table = _safe_ident(table); chat_id = payload.get("chat_id") or payload.get("user_id")
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN"); p = _filter_payload(conn, table, dict(payload))
                cols=list(p); conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [p[c] for c in cols])
                last_id=conn.execute("SELECT last_insert_rowid()").fetchone()[0]; conn.commit()
        invalidate_cache(table, f"{table}:{chat_id}"); out=dict(payload); out.setdefault("id", last_id); return out
    except Exception as exc:
        _log_db_error(f"insert:{table}", chat_id, exc); return None


def update(table: str, payload: Dict[str, Any], **equals: Any) -> bool:
    table = _safe_ident(table); chat_id = equals.get("chat_id") or equals.get("user_id")
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                p=_filter_payload(conn, table, dict(payload)); cols=_columns(conn, table)
                wh={k:v for k,v in equals.items() if k in cols}
                if not p or not wh: return False
                conn.execute("BEGIN")
                sql=f"UPDATE {table} SET "+", ".join(f"{_safe_ident(k)}=?" for k in p)+" WHERE "+" AND ".join(f"{_safe_ident(k)}=?" for k in wh)
                cur=conn.execute(sql, list(p.values())+list(wh.values())); conn.commit()
        invalidate_cache(table, f"{table}:{chat_id}"); return cur.rowcount >= 0
    except Exception as exc:
        _log_db_error(f"update:{table}", chat_id, exc); return False


def delete(table: str, **equals: Any) -> bool:
    table = _safe_ident(table); chat_id = equals.get("chat_id") or equals.get("user_id")
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                cols=_columns(conn, table); wh={k:v for k,v in equals.items() if k in cols}
                if not wh: return False
                conn.execute("BEGIN")
                cur=conn.execute(f"DELETE FROM {table} WHERE "+" AND ".join(f"{_safe_ident(k)}=?" for k in wh), list(wh.values()))
                conn.commit()
        invalidate_cache(table, f"{table}:{chat_id}"); return cur.rowcount > 0
    except Exception as exc:
        _log_db_error(f"delete:{table}", chat_id, exc); return False


def ensure_user(chat_id: int, *, username: str="", full_name: str="", first_name: str="", is_admin: bool=False, start_source: Optional[Dict[str, Any]]=None) -> Tuple[Dict[str, Any], bool]:
    now = _now_iso(); role = "admin" if is_admin else "user"; version = (start_source or {}).get("last_version") or "v1"
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN")
                existing = _row_to_dict(conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone())
                conn.execute("""INSERT INTO users (chat_id, username, full_name, first_seen, date_joined, joined_at, last_seen, last_version, role, is_first_start, is_admin, approved, first_name, first_seen_at, last_seen_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name,
                        first_name=COALESCE(excluded.first_name, users.first_name), last_seen=excluded.last_seen,
                        last_seen_at=excluded.last_seen_at, last_version=excluded.last_version,
                        role=COALESCE(users.role, excluded.role)""",
                    (chat_id, username or "", full_name or "", now, now, now, now, version, role, 1 if not existing else 0, 1 if is_admin else 0, 1 if is_admin else 0, first_name or "", now, now, now))
                row = _row_to_dict(conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()) or {}
                conn.commit()
        invalidate_cache(f"users:{chat_id}"); _ttl_set(f"users:{chat_id}", row); return row, existing is None
    except Exception as exc:
        _log_db_error("ensure_user", chat_id, exc); raise


def create_user(chat_id: int, **kwargs: Any) -> Optional[Dict[str, Any]]:
    try: return ensure_user(chat_id, **kwargs)[0]
    except Exception: return None


def get_user(chat_id: int, *, use_cache: bool=True) -> Optional[Dict[str, Any]]:
    key=f"users:{chat_id}"
    if use_cache and (cached:=_ttl_get(key)) is not None: return cached
    row=select_one("users", chat_id=chat_id)
    if row: _ttl_set(key, row)
    return row


def update_user(chat_id: int, payload: Dict[str, Any]) -> bool:
    ok=update("users", payload, chat_id=chat_id); invalidate_cache(f"users:{chat_id}", "premium_users", "blocked_users", "admin_users"); return ok


def update_last_seen(chat_id: int, *, username: str="", full_name: str="") -> bool:
    payload={"last_seen": _now_iso(), "last_seen_at": _now_iso()}
    if username: payload["username"] = username
    if full_name: payload["full_name"] = full_name
    return update_user(chat_id, payload)


def get_subscription(chat_id: int) -> Optional[Dict[str, Any]]:
    if (c:=_ttl_get(f"subscriptions:{chat_id}")) is not None: return c
    return _ttl_set(f"subscriptions:{chat_id}", select_one("subscriptions", chat_id=chat_id))


def upsert_subscription(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res=upsert("subscriptions", payload, on_conflict="chat_id"); invalidate_cache(f"subscriptions:{payload.get('chat_id')}", "premium_users"); return res


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value: return None
    if isinstance(value, datetime): return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text=str(value).strip().replace("Z", "+00:00")
        dt=datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        logger.warning("[LOCAL DB WARN] invalid datetime value=%r", value); return None


def activate_subscription(chat_id: int, *, plan: str, expires_at: Any, is_demo: int=0, note: Optional[str]=None) -> Optional[Dict[str, Any]]:
    exp = expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at)
    now = _now_iso()
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN")
                if is_demo:
                    row=conn.execute("SELECT demo_used FROM users WHERE chat_id=?", (chat_id,)).fetchone()
                    if row and int(row["demo_used"] or 0) == 1:
                        conn.rollback(); return None
                    user_payload={"demo_used":1,"demo_start_at":now,"demo_end_at":exp,"demo_expires_at":exp,"status":"demo","last_status_change_at":now,"approved":1,"blocked":0,"is_blocked":0,"is_active":1}
                else:
                    user_payload={"is_premium":1,"approved":1,"status":"active","paid_until":exp,"last_status_change_at":now,"blocked":0,"is_blocked":0,"is_active":1,"last_payment_at":now}
                if not conn.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)).fetchone():
                    conn.execute("INSERT INTO users (chat_id, first_seen, date_joined, joined_at, last_seen) VALUES (?, ?, ?, ?, ?)", (chat_id, now, now, now, now))
                conn.execute("UPDATE users SET "+", ".join(f"{k}=?" for k in user_payload)+" WHERE chat_id=?", list(user_payload.values())+[chat_id])
                conn.execute("""INSERT INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note)
                    VALUES (?, ?, ?, 1, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, is_active=1, is_demo=excluded.is_demo, last_payment_note=excluded.last_payment_note""", (chat_id, plan, exp, int(is_demo), note))
                conn.commit()
        invalidate_cache(f"users:{chat_id}", f"subscriptions:{chat_id}", "premium_users", "blocked_users"); return get_subscription(chat_id)
    except Exception as exc:
        _log_db_error("activate_subscription", chat_id, exc); return None


def approve_payment(chat_id: int, plan: str, amount: int, days: int, note: Optional[str]=None) -> Optional[str]:
    now_dt=datetime.now(timezone.utc); user=get_user(chat_id, use_cache=False) or {}; base=_parse_dt(user.get("paid_until")) or now_dt
    if base < now_dt: base = now_dt
    exp=(base + timedelta(days=int(days))).isoformat(); now=now_dt.isoformat()
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN")
                if not conn.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)).fetchone():
                    conn.execute("INSERT INTO users (chat_id, first_seen, date_joined, joined_at, last_seen) VALUES (?, ?, ?, ?, ?)", (chat_id, now, now, now, now))
                conn.execute("UPDATE users SET is_premium=1, approved=1, status='active', paid_until=?, last_status_change_at=? WHERE chat_id=?", (exp, now, chat_id))
                conn.execute("""INSERT INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note) VALUES (?, ?, ?, 1, 0, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, is_active=1, is_demo=0, last_payment_note=excluded.last_payment_note""", (chat_id, plan, exp, note))
                conn.execute("INSERT INTO payments (chat_id, plan, amount, approved_at) VALUES (?, ?, ?, ?)", (chat_id, plan, amount, now))
                conn.commit()
        invalidate_cache(f"users:{chat_id}", f"subscriptions:{chat_id}"); return exp
    except Exception as exc:
        _log_db_error("approve_payment", chat_id, exc); return None


def cleanup_expired_access(chat_id: int) -> None:
    user=get_user(chat_id, use_cache=False) or {}; sub=get_subscription(chat_id) or {}; now=datetime.now(timezone.utc); updates={}
    if user.get("paid_until") and not ((_parse_dt(user.get("paid_until")) or datetime.min.replace(tzinfo=timezone.utc)) > now): updates.update({"is_premium":0,"status":"expired"})
    if user.get("promo_active") and not ((_parse_dt(user.get("promo_expires_at")) or datetime.min.replace(tzinfo=timezone.utc)) > now): updates["promo_active"]=0
    if user.get("demo_end_at") and not ((_parse_dt(user.get("demo_end_at")) or datetime.min.replace(tzinfo=timezone.utc)) > now): updates.setdefault("status", "expired")
    if updates: update_user(chat_id, updates)
    if sub.get("is_active") and not ((_parse_dt(sub.get("expires_at")) or datetime.min.replace(tzinfo=timezone.utc)) > now): update("subscriptions", {"is_active":0}, chat_id=chat_id)


def is_user_active(chat_id: int) -> bool:
    user=get_user(chat_id, use_cache=False) or {}; now=datetime.now(timezone.utc)
    if user.get("is_admin"): return True
    if user.get("blocked") or user.get("is_blocked"): return False
    active = any((_parse_dt(user.get(k)) or datetime.min.replace(tzinfo=timezone.utc)) > now for k in ("paid_until","demo_end_at"))
    active = active or (bool(user.get("promo_active")) and ((_parse_dt(user.get("promo_expires_at")) or datetime.min.replace(tzinfo=timezone.utc)) > now))
    sub=get_subscription(chat_id) or {}
    if bool(sub.get("is_active")):
        sub_exp = (_parse_dt(sub.get("expires_at")) or datetime.min.replace(tzinfo=timezone.utc))
        if int(sub.get("is_demo") or 0) == 1:
            demo_exp = (_parse_dt(user.get("demo_end_at") or user.get("demo_expires_at")) or datetime.min.replace(tzinfo=timezone.utc))
            active = active or (sub_exp > now and demo_exp > now)
        else:
            active = active or (sub_exp > now)
    if not active: cleanup_expired_access(chat_id)
    return active


def add_favorite(chat_id:int, listing_id:int, source:str="main") -> bool:
    return bool(upsert("favorites", {"chat_id":chat_id,"listing_id":listing_id,"source":source,"added_at":_now_iso()}, on_conflict="chat_id,listing_id,source"))

def remove_favorite(chat_id:int, listing_id:int, source:str="main") -> bool: return delete("favorites", chat_id=chat_id, listing_id=listing_id, source=source)
def is_favorite(chat_id:int, listing_id:int, source:str="main") -> bool: return bool(select_one("favorites", chat_id=chat_id, listing_id=listing_id, source=source))
def get_user_favorites(chat_id:int, source:Optional[str]=None) -> List[Dict[str, Any]]:
    p={"chat_id":chat_id};
    if source: p["source"]=source
    return select_many("favorites", order="added_at", desc=True, **p)
def list_favorites(chat_id:int, source:Optional[str]=None) -> List[Dict[str, Any]]: return get_user_favorites(chat_id, source)
def toggle_favorite(chat_id:int, listing_id:int, source:str="main") -> bool:
    if is_favorite(chat_id, listing_id, source): remove_favorite(chat_id, listing_id, source); return False
    add_favorite(chat_id, listing_id, source); return True


def record_search_activity(chat_id:int, search_type:str, query:Optional[str]=None, filters:Any=None, operation:Optional[str]=None, rayon:Optional[str]=None, key_type:Optional[str]=None) -> bool:
    now=_now_iso(); day=now[:10]; filters_text=json.dumps(filters or {}, ensure_ascii=False) if not isinstance(filters, str) else filters
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN")
                conn.execute("INSERT INTO search_history (chat_id, search_type, query, filters, created_at) VALUES (?, ?, ?, ?, ?)", (chat_id, search_type, query, filters_text, now))
                conn.execute("INSERT INTO search_logs (chat_id, search_type, operation, rayon, query_text, created_at) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, search_type, operation, rayon, query, now))
                conn.execute("""INSERT INTO user_activity (chat_id, last_seen, total_searches) VALUES (?, ?, 1)
                    ON CONFLICT(chat_id) DO UPDATE SET last_seen=excluded.last_seen, total_searches=COALESCE(user_activity.total_searches,0)+1""", (chat_id, now))
                conn.execute("""INSERT INTO search_limits (chat_id, date, key_type, used) VALUES (?, ?, ?, 1)
                    ON CONFLICT(chat_id, date, key_type) DO UPDATE SET used=COALESCE(search_limits.used,0)+1""", (chat_id, day, key_type or search_type))
                conn.commit(); return True
    except Exception as exc:
        _log_db_error("record_search_activity", chat_id, exc); return False


def use_promo_code(chat_id:int, code:str) -> Optional[str]:
    now_dt=datetime.now(timezone.utc); now=now_dt.isoformat(); code=code.strip()
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                promo=conn.execute("SELECT * FROM promo_codes WHERE code=? AND COALESCE(is_active,1)=1", (code,)).fetchone()
                if not promo or conn.execute("SELECT 1 FROM promo_usages WHERE code=? AND chat_id=?", (code, chat_id)).fetchone():
                    return None
                exp=(now_dt+timedelta(days=int(promo["days"] or 0))).isoformat()
                conn.execute("BEGIN")
                conn.execute("UPDATE users SET promo_active=1, promo_expires_at=?, status='promo' WHERE chat_id=?", (exp, chat_id))
                conn.execute("INSERT INTO promo_usages (code, chat_id, used_at, expires_at) VALUES (?, ?, ?, ?)", (code, chat_id, now, exp))
                conn.commit()
        invalidate_cache(f"users:{chat_id}"); return exp
    except Exception as exc:
        _log_db_error("use_promo_code", chat_id, exc); return None


def create_referral(referrer_chat_id:int, referred_chat_id:int, bonus_days:int=0) -> bool:
    now=_now_iso()
    try:
        with _WRITE_LOCK:
            with local_connection() as conn:
                conn.execute("BEGIN")
                conn.execute("INSERT OR IGNORE INTO referrals (referrer_chat_id, referred_chat_id, created_at, reward_given) VALUES (?, ?, ?, 0)", (referrer_chat_id, referred_chat_id, now))
                if bonus_days:
                    conn.execute("INSERT INTO referral_logs (referrer_id, referred_user_id, bonus_days, created_at) VALUES (?, ?, ?, ?)", (referrer_chat_id, referred_chat_id, bonus_days, now))
                conn.commit(); return True
    except Exception as exc:
        _log_db_error("create_referral", referred_chat_id, exc); return False


def ensure_notification_records(chat_id:int, criteria_id:Optional[int], listing_ids:Sequence[int]) -> int:
    inserted=0
    for lid in dict.fromkeys(listing_ids):
        if upsert("user_notifications", {"chat_id":chat_id,"criteria_id":criteria_id,"listing_id":int(lid),"created_at":_now_iso(),"status":"new"}, on_conflict="chat_id,criteria_id,listing_id"):
            inserted += 1
    return inserted


def cached_admin_users() -> List[int]:
    if (c:=_ttl_get("admin_users")) is not None: return c
    return _ttl_set("admin_users", [int(r["chat_id"]) for r in select_many("users", is_admin=1) if r.get("chat_id")])

def cached_blocked_users() -> List[int]:
    if (c:=_ttl_get("blocked_users")) is not None: return c
    ids={int(r["chat_id"]) for r in select_many("users", blocked=1) if r.get("chat_id")}
    ids.update(int(r["chat_id"]) for r in select_many("users", is_blocked=1) if r.get("chat_id"))
    return _ttl_set("blocked_users", sorted(ids))

# Legacy SQLite compatibility: bot code expects a DB-API connection for user data.
LocalCompatConnection = sqlite3.Connection
LocalCompatCursor = sqlite3.Cursor
SupabaseCompatConnection = sqlite3.Connection  # backwards-compatible alias; no Supabase runtime
SupabaseCompatCursor = sqlite3.Cursor

def connect() -> sqlite3.Connection: return get_local_connection()
def get_conn() -> sqlite3.Connection: return get_local_connection()
