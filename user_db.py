"""Supabase access layer for BestHome user data.

Only user-owned tables live here. Listing search and region filtering remain in
besthome.db through listing_db.py.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from supabase_client import supabase

logger = logging.getLogger("besthome_user_db")

USER_TABLES = {
    "users",
    "subscriptions",
    "favorites",
    "user_notifications",
    "search_history",
    "saved_searches",
    "payments",
    "referrals",
    "promo_codes",
    "promo_usages",
    "user_activity",
    "search_logs",
    "keyword_alerts",
    "keyword_alert_hits",
    "customer_requests",
    "customer_request_rules",
    "customer_request_alerts",
    "customer_request_favorites",
    "customer_request_archives",
    "agents",
    "agent_activity",
    "agent_notifications",
    "agent_interests",
    "customer_requests_access",
    "support_threads",
    "support_messages",
    "feature_flags",
    "feature_overrides",
    "manual_payments",
    "bonus_probabilities",
}

_CACHE_TTL_SECONDS = 60
_cache: Dict[str, Dict[str, Any]] = {}
_DEFAULT_ON_CONFLICT_BY_TABLE: Dict[str, str] = {
    "users": "chat_id",
    "subscriptions": "chat_id",
    "favorites": "chat_id,listing_id,source",
    "user_notifications": "chat_id,criteria_id,listing_id",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_supabase_error(table: str, action: str, exc: BaseException) -> None:
    logger.error(
        "[SUPABASE ERROR]\ntable=%s\naction=%s\ndetails=%s",
        table,
        action,
        exc,
        exc_info=True,
    )


_UNAVAILABLE_USER_COLUMNS = set()


def _missing_schema_column_from_error(exc: BaseException) -> Optional[str]:
    """Return a missing PostgREST schema-cache column name when available."""
    text = str(exc)
    patterns = (
        r"Could not find the ['\"](?P<column>[^'\"]+)['\"] column",
        r"column ['\"](?P<column>[^'\"]+)['\"] of relation",
        r"record ['\"](?P<column>[^'\"]+)['\"] has no field",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("column")
    return None


def _without_unavailable_user_columns(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if table != "users" or not _UNAVAILABLE_USER_COLUMNS:
        return payload
    return {k: v for k, v in payload.items() if k not in _UNAVAILABLE_USER_COLUMNS}


def _execute_user_write_with_schema_fallback(
    action: str,
    payload: Dict[str, Any],
    execute,
) -> Optional[Any]:
    """Write users rows while tolerating optional columns absent from deployments.

    Some BestHome deployments have a minimal `users` table.  The /start flow
    should still save the Telegram user instead of failing the whole write when
    optional profile or timestamp columns are not present in Supabase.
    """
    current_payload = _without_unavailable_user_columns("users", dict(payload))
    for _ in range(len(payload) + 1):
        try:
            return execute(current_payload)
        except Exception as exc:
            missing_column = _missing_schema_column_from_error(exc)
            if (
                missing_column
                and missing_column in current_payload
                and missing_column != "chat_id"
            ):
                logger.warning(
                    "[SUPABASE WARN] table=users action=%s missing_optional_column=%s; retrying without it",
                    action,
                    missing_column,
                )
                _UNAVAILABLE_USER_COLUMNS.add(missing_column)
                current_payload.pop(missing_column, None)
                continue
            _log_supabase_error("users", action, exc)
            return None
    return None


def _ttl_get(key: str) -> Any:
    item = _cache.get(key)
    if not item:
        return None
    if item["expires_at"] < time.time():
        _cache.pop(key, None)
        return None
    return item["value"]


def _ttl_set(key: str, value: Any, ttl: int = _CACHE_TTL_SECONDS) -> Any:
    _cache[key] = {"value": value, "expires_at": time.time() + ttl}
    return value


def invalidate_cache(*prefixes: str) -> None:
    if not prefixes:
        _cache.clear()
        return
    for key in list(_cache.keys()):
        if any(key.startswith(prefix) for prefix in prefixes):
            _cache.pop(key, None)


def select_one(table: str, **equals: Any) -> Optional[Dict[str, Any]]:
    try:
        query = supabase.table(table).select("*")
        for key, value in equals.items():
            query = query.eq(key, value)
        response = query.limit(1).execute()
        rows = response.data or []
        return rows[0] if rows else None
    except Exception as exc:
        _log_supabase_error(table, "select", exc)
        return None


def select_many(
    table: str,
    *,
    order: Optional[str] = None,
    desc: bool = False,
    limit: Optional[int] = None,
    **equals: Any,
) -> List[Dict[str, Any]]:
    try:
        query = supabase.table(table).select("*")
        for key, value in equals.items():
            query = query.eq(key, value)
        if order:
            query = query.order(order, desc=desc)
        if limit:
            query = query.limit(limit)
        response = query.execute()
        return response.data or []
    except Exception as exc:
        _log_supabase_error(table, "select", exc)
        return []


def upsert(table: str, payload: Dict[str, Any], on_conflict: Optional[str] = None) -> Optional[Dict[str, Any]]:
    def execute(write_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        conflict = on_conflict or _DEFAULT_ON_CONFLICT_BY_TABLE.get(table)
        query = (
            supabase.table(table).upsert(write_payload, on_conflict=conflict)
            if conflict
            else supabase.table(table).upsert(write_payload)
        )
        response = query.execute()
        rows = response.data or []
        invalidate_cache(table)
        if "chat_id" in write_payload:
            invalidate_cache(f"{table}:{write_payload.get('chat_id')}")
        return rows[0] if rows else write_payload

    if table == "users":
        return _execute_user_write_with_schema_fallback("upsert", payload, execute)

    try:
        return execute(payload)
    except Exception as exc:
        _log_supabase_error(table, "upsert", exc)
        return None


def insert(table: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    def execute(write_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        response = supabase.table(table).insert(write_payload).execute()
        rows = response.data or []
        invalidate_cache(table)
        if "chat_id" in write_payload:
            invalidate_cache(f"{table}:{write_payload.get('chat_id')}")
        return rows[0] if rows else write_payload

    if table == "users":
        return _execute_user_write_with_schema_fallback("insert", payload, execute)

    try:
        return execute(payload)
    except Exception as exc:
        _log_supabase_error(table, "insert", exc)
        return None


def update(table: str, payload: Dict[str, Any], **equals: Any) -> bool:
    def execute(write_payload: Dict[str, Any]) -> bool:
        if not write_payload:
            logger.warning("[SUPABASE WARN] table=%s action=update skipped empty payload", table)
            return True
        query = supabase.table(table).update(write_payload)
        for key, value in equals.items():
            query = query.eq(key, value)
        query.execute()
        invalidate_cache(table)
        if "chat_id" in equals:
            invalidate_cache(f"{table}:{equals.get('chat_id')}")
        return True

    if table == "users":
        return bool(_execute_user_write_with_schema_fallback("update", payload, execute))

    try:
        return execute(payload)
    except Exception as exc:
        _log_supabase_error(table, "update", exc)
        return False


def delete(table: str, **equals: Any) -> bool:
    try:
        query = supabase.table(table).delete()
        for key, value in equals.items():
            query = query.eq(key, value)
        query.execute()
        invalidate_cache(table)
        return True
    except Exception as exc:
        _log_supabase_error(table, "delete", exc)
        return False


def create_user(
    chat_id: int,
    *,
    username: str = "",
    full_name: str = "",
    first_name: str = "",
    is_admin: bool = False,
    start_source: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        record, _ = ensure_user(
            chat_id,
            username=username,
            full_name=full_name,
            first_name=first_name,
            is_admin=is_admin,
            start_source=start_source,
        )
        return record
    except Exception as exc:
        _log_supabase_error("users", "create", exc)
        return None


def update_last_seen(chat_id: int, *, username: str = "", full_name: str = "") -> bool:
    payload = {"last_seen": _now_iso(), "last_seen_at": _now_iso()}
    if username:
        payload["username"] = username
    if full_name:
        payload["full_name"] = full_name
    return update_user(chat_id, payload)


def activate_subscription(
    chat_id: int,
    *,
    plan: str,
    expires_at: Any,
    is_demo: int = 0,
    note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    expires_iso = expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at
    now_iso = _now_iso()
    sub = upsert_subscription(
        {
            "chat_id": chat_id,
            "plan": plan,
            "expires_at": expires_iso,
            "is_active": 1,
            "is_demo": int(is_demo),
            "last_payment_note": note,
        }
    )
    update_user(
        chat_id,
        {
            "paid_until": expires_iso if not is_demo else None,
            "demo_end_at": expires_iso if is_demo else None,
            "demo_expires_at": expires_iso if is_demo else None,
            "approved": 1,
            "blocked": 0,
            "is_blocked": 0,
            "is_active": 1,
            "last_payment_at": now_iso if not is_demo else None,
        },
    )
    invalidate_cache(f"users:{chat_id}", f"subscriptions:{chat_id}", "premium_users", "blocked_users")
    return sub


def ensure_user(
    chat_id: int,
    *,
    username: str = "",
    full_name: str = "",
    first_name: str = "",
    is_admin: bool = False,
    start_source: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], bool]:
    now_iso = _now_iso()
    existing = get_user(chat_id, use_cache=False)
    if existing:
        payload = {
            "last_seen": now_iso,
            "last_seen_at": now_iso,
            "username": username or existing.get("username"),
            "full_name": full_name or existing.get("full_name"),
            "first_name": first_name or existing.get("first_name"),
            "last_version": start_source.get("last_version") if start_source else existing.get("last_version"),
        }
        update("users", payload, chat_id=chat_id)
        merged = {**existing, **{k: v for k, v in payload.items() if v is not None}}
        _ttl_set(f"users:{chat_id}", merged)
        return merged, False

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "username": username or "",
        "full_name": full_name or "",
        "first_name": first_name or "",
        "first_seen": now_iso,
        "first_seen_at": now_iso,
        "last_seen": now_iso,
        "last_seen_at": now_iso,
        "approved": 1 if is_admin else 0,
        "blocked": 0,
        "is_blocked": 0,
        "is_admin": 1 if is_admin else 0,
        "is_active": 1,
        "is_first_start": 1,
        "created_at": now_iso,
        # joined_at is intentionally omitted so the Supabase/PostgreSQL default NOW() owns it.
    }
    if start_source:
        payload.update({k: v for k, v in start_source.items() if v is not None})
    created = upsert("users", payload, on_conflict="chat_id") or payload
    _ttl_set(f"users:{chat_id}", created)
    return created, True


def get_user(chat_id: int, *, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    key = f"users:{chat_id}"
    if use_cache:
        cached = _ttl_get(key)
        if cached is not None:
            return cached
    row = select_one("users", chat_id=chat_id)
    if row:
        _ttl_set(key, row)
    return row


def update_user(chat_id: int, payload: Dict[str, Any]) -> bool:
    ok = update("users", payload, chat_id=chat_id)
    invalidate_cache(f"users:{chat_id}", "premium_users", "blocked_users", "admin_users")
    return ok


def get_subscription(chat_id: int) -> Optional[Dict[str, Any]]:
    cached = _ttl_get(f"subscriptions:{chat_id}")
    if cached is not None:
        return cached
    row = select_one("subscriptions", chat_id=chat_id)
    return _ttl_set(f"subscriptions:{chat_id}", row)


def upsert_subscription(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    result = upsert("subscriptions", payload, on_conflict="chat_id")
    invalidate_cache(f"subscriptions:{payload.get('chat_id')}", "premium_users")
    return result


def add_favorite(chat_id: int, listing_id: int, source: str = "main") -> bool:
    payload = {
        "chat_id": chat_id,
        "listing_id": listing_id,
        "source": source,
        "created_at": _now_iso(),
    }
    result = upsert("favorites", payload, on_conflict="chat_id,listing_id,source")
    return bool(result)


def remove_favorite(chat_id: int, listing_id: int, source: str = "main") -> bool:
    return delete("favorites", chat_id=chat_id, listing_id=listing_id, source=source)


def is_favorite(chat_id: int, listing_id: int, source: str = "main") -> bool:
    return bool(select_one("favorites", chat_id=chat_id, listing_id=listing_id, source=source))


def get_user_favorites(chat_id: int, source: Optional[str] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"chat_id": chat_id}
    if source:
        params["source"] = source
    rows = select_many("favorites", order="created_at", desc=True, **params)
    return rows or []


def list_favorites(chat_id: int, source: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_user_favorites(chat_id, source=source)


def toggle_favorite(chat_id: int, listing_id: int, source: str = "main") -> bool:
    if is_favorite(chat_id, listing_id, source=source):
        remove_favorite(chat_id, listing_id, source=source)
        return False
    add_favorite(chat_id, listing_id, source=source)
    return True


def ensure_notification_records(chat_id: int, criteria_id: Optional[int], listing_ids: Sequence[int]) -> int:
    inserted = 0
    for lid in dict.fromkeys(listing_ids):
        try:
            listing_id = int(lid)
        except Exception:
            continue
        payload = {
            "chat_id": chat_id,
            "criteria_id": criteria_id,
            "listing_id": listing_id,
            "created_at": _now_iso(),
            "status": "new",
        }
        if upsert("user_notifications", payload, on_conflict="chat_id,criteria_id,listing_id"):
            inserted += 1
    return inserted


def cached_admin_users() -> List[int]:
    cached = _ttl_get("admin_users")
    if cached is not None:
        return cached
    rows = select_many("users", is_admin=1)
    return _ttl_set("admin_users", [int(r["chat_id"]) for r in rows if r.get("chat_id")])


def cached_blocked_users() -> List[int]:
    cached = _ttl_get("blocked_users")
    if cached is not None:
        return cached
    rows = select_many("users", blocked=1)
    rows += select_many("users", is_blocked=1)
    ids = sorted({int(r["chat_id"]) for r in rows if r.get("chat_id")})
    return _ttl_set("blocked_users", ids)

def _split_sql_assignments(set_part: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    quote: Optional[str] = None
    for ch in set_part:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        if ch == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _effective_expires_at(row: Dict[str, Any]) -> Optional[str]:
    dates = []
    for key in ("paid_until", "demo_end_at", "demo_expires_at", "promo_expires_at"):
        if key == "promo_expires_at" and not row.get("promo_active"):
            continue
        dt = _parse_dt(row.get(key))
        if dt:
            dates.append(dt)
    return max(dates).isoformat() if dates else None


def _compute_user_status(row: Dict[str, Any]) -> str:
    if row.get("blocked") or row.get("is_blocked") or row.get("deleted_at") or row.get("is_active") == 0:
        return "BLOCKED"
    if row.get("approved") == 0 and not row.get("is_admin"):
        return "PENDING"
    raw_exp = _effective_expires_at(row)
    exp = _parse_dt(raw_exp)
    if exp and exp > datetime.utcnow():
        return "ACTIVE"
    if row.get("is_admin"):
        return "ACTIVE"
    return "EXPIRED"


class SupabaseCompatRow(dict):
    def __init__(self, data: Dict[str, Any], columns: Optional[Sequence[str]] = None):
        super().__init__(data)
        self._columns = list(columns or data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            if key < 0 or key >= len(self._columns):
                raise IndexError(key)
            return dict.get(self, self._columns[key])
        return dict.__getitem__(self, key)

    def keys(self):
        return dict.keys(self)


class SupabaseCompatCursor:
    """Small transitional cursor for legacy code paths while user tables move to Supabase.

    It intentionally does not touch SQLite. Supported simple statements are routed to
    Supabase; unsupported reporting joins return an empty result instead of creating a
    local database.
    """

    def __init__(self):
        self._rows: List[Dict[str, Any]] = []
        self.rowcount = 0
        self.lastrowid = None

    def execute(self, sql: str, params: Iterable[Any] = ()):  # noqa: C901 - transitional parser
        import re

        params = tuple(params or ())
        compact = " ".join((sql or "").strip().split())
        lower = compact.lower()
        self._rows = []
        self.rowcount = 0
        try:
            if lower.startswith("select 1"):
                self._rows = [{"1": 1}]
                return self
            if lower.startswith("select"):
                m = re.search(r"from\s+([a-zA-Z_][a-zA-Z0-9_]*)", lower)
                table = m.group(1) if m else ""
                if table not in USER_TABLES and table != "users_with_status":
                    return self
                table = "users" if table == "users_with_status" else table
                rows = select_many(table)
                if "users_with_status" in lower:
                    for row in rows:
                        if "computed_status" not in row:
                            row["computed_status"] = _compute_user_status(row)
                        if "effective_expires_at" not in row:
                            row["effective_expires_at"] = _effective_expires_at(row)
                # Apply very common single-column equality predicates in parameter order.
                columns = re.findall(r"([a-zA-Z_][a-zA-Z0-9_\.]*?)\s*=\s*\?", compact)
                for col, value in zip(columns, params):
                    key = col.split(".")[-1]
                    rows = [r for r in rows if str(r.get(key)) == str(value)]
                select_match = re.match(r"select\s+(.+?)\s+from\s+", compact, re.I)
                raw_cols = select_match.group(1).strip() if select_match else "*"
                if raw_cols.lower().startswith("count("):
                    alias_match = re.search(r"\bas\s+([a-zA-Z_][a-zA-Z0-9_]*)", raw_cols, re.I)
                    count_col = alias_match.group(1) if alias_match else "COUNT(*)"
                    self._rows = [SupabaseCompatRow({count_col: len(rows)}, [count_col])]
                    self.rowcount = 1
                    return self
                result_columns = None
                if raw_cols and raw_cols != "*" and "," in raw_cols:
                    result_columns = [c.strip().split()[-1].split(".")[-1] for c in raw_cols.split(",")]
                elif raw_cols and raw_cols != "*" and "(" not in raw_cols:
                    result_columns = [raw_cols.strip().split()[-1].split(".")[-1]]
                self._rows = [SupabaseCompatRow(r, result_columns or list(r.keys())) for r in rows]
                self.rowcount = len(rows)
                return self
            if lower.startswith("update"):
                m = re.match(r"update\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+set\s+(.+?)\s+where\s+(.+)", compact, re.I)
                if not m:
                    return self
                table, set_part, where_part = m.groups()
                if table not in USER_TABLES:
                    return self
                payload: Dict[str, Any] = {}
                set_param_count = 0
                for part in _split_sql_assignments(set_part):
                    if "=" not in part:
                        continue
                    col, expr = part.split("=", 1)
                    col = col.strip()
                    expr_clean = expr.strip()
                    expr_lower = expr_clean.lower()
                    if "?" in expr_clean:
                        if set_param_count < len(params):
                            payload[col] = params[set_param_count]
                        set_param_count += expr_clean.count("?")
                    elif expr_lower == "null":
                        payload[col] = None
                    elif expr_clean in ("0", "1"):
                        payload[col] = int(expr_clean)
                    elif (expr_clean.startswith("'") and expr_clean.endswith("'")) or (expr_clean.startswith('"') and expr_clean.endswith('"')):
                        payload[col] = expr_clean[1:-1]
                where_cols = re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*\?", where_part)
                equals = {}
                for idx, col in enumerate(where_cols, start=set_param_count):
                    if idx < len(params):
                        equals[col] = params[idx]
                if equals:
                    self.rowcount = 1 if update(table, payload, **equals) else 0
                return self
            if lower.startswith("insert"):
                m = re.search(r"into\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]+)\)", compact, re.I)
                if not m:
                    return self
                table, cols_raw = m.groups()
                if table not in USER_TABLES:
                    return self
                cols = [c.strip() for c in cols_raw.split(",")]
                payload = {col: params[i] for i, col in enumerate(cols) if i < len(params)}
                if "or ignore" in lower and select_one(table, **{k: payload[k] for k in ("chat_id",) if k in payload}):
                    result = payload
                elif table == "manual_payments":
                    result = insert(table, payload)
                else:
                    result = upsert(table, payload)
                self.lastrowid = result.get("id") if isinstance(result, dict) else None
                self.rowcount = 1 if result else 0
                return self
            if lower.startswith("delete"):
                m = re.match(r"delete\s+from\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+where\s+(.+)", compact, re.I)
                if not m:
                    return self
                table, where_part = m.groups()
                if table not in USER_TABLES:
                    return self
                where_cols = re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*\?", where_part)
                equals = {col: params[i] for i, col in enumerate(where_cols) if i < len(params)}
                self.rowcount = 1 if delete(table, **equals) else 0
                return self
        except Exception as exc:
            _log_supabase_error("legacy_sql", compact[:80], exc)
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        count = 0
        for params in seq_of_params:
            self.execute(sql, params)
            count += self.rowcount or 0
        self.rowcount = count
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class SupabaseCompatConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def cursor(self):
        return SupabaseCompatCursor()

    def execute(self, sql: str, params: Iterable[Any] = ()): 
        cur = self.cursor()
        return cur.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        cur = self.cursor()
        return cur.executemany(sql, seq_of_params)

    def executescript(self, sql: str):
        return self.cursor()

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None
