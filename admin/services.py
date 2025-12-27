import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List, Sequence

logger = logging.getLogger("admin_panel")


class AdminDatabase:
    def __init__(self, data_dir: str):
        self.local_db = os.path.join(data_dir, "local_data.db")
        self.main_db = os.path.join(data_dir, "besthome.db")
        self._indexes_created = False

    def _connect(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def local_conn(self) -> sqlite3.Connection:
        return self._connect(self.local_db)

    def main_conn(self) -> sqlite3.Connection:
        return self._connect(self.main_db)

    def ensure_user_indexes(self):
        """Ensure lookup-heavy columns are indexed for faster admin queries."""
        if self._indexes_created:
            return
        conn = self.local_conn()
        try:
            cur = conn.cursor()
            # Indexes used by admin filters/status calculations
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_approved ON users(approved)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(blocked)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_paid_until ON users(paid_until)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_demo_end_at ON users(demo_end_at)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_demo_expires_at ON users(demo_expires_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_chat_id ON subscriptions(chat_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_expires_at ON subscriptions(expires_at)"
            )
            conn.commit()
            self._indexes_created = True
        finally:
            conn.close()

    def table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return cur.fetchone() is not None


def parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except Exception:
        return None


def compute_dashboard_counts(db: AdminDatabase) -> Dict[str, int]:
    counts = {"total": 0, "active": 0, "expired": 0, "demo": 0}
    conn = db.local_conn()
    try:
        if db.table_exists(conn, "users_with_status"):
            cur = conn.execute(
                "SELECT COUNT(*) as total,"
                " SUM(CASE WHEN computed_status='ACTIVE' THEN 1 ELSE 0 END) as active,"
                " SUM(CASE WHEN computed_status='EXPIRED' THEN 1 ELSE 0 END) as expired,"
                " SUM(CASE WHEN computed_status='DEMO' THEN 1 ELSE 0 END) as demo"
                " FROM users_with_status"
            )
            row = cur.fetchone() or {}
            counts = {
                "total": int(row.get("total") or 0),
                "active": int(row.get("active") or 0),
                "expired": int(row.get("expired") or 0),
                "demo": int(row.get("demo") or 0),
            }
            return counts

        cur = conn.execute("SELECT COUNT(*) as total FROM users")
        counts["total"] = int((cur.fetchone() or {}).get("total") or 0)

        now = datetime.utcnow()
        cur = conn.execute(
            "SELECT paid_until, demo_end_at, demo_expires_at, blocked FROM users"
        )
        for row in cur.fetchall():
            if row.get("blocked"):
                continue
            demo_dt = parse_dt(row.get("demo_end_at")) or parse_dt(row.get("demo_expires_at"))
            paid_dt = parse_dt(row.get("paid_until"))
            effective = max(filter(None, [demo_dt, paid_dt]), default=None)
            if demo_dt and demo_dt > now:
                counts["demo"] += 1
            if effective and effective > now:
                counts["active"] += 1
            elif effective:
                counts["expired"] += 1
        return counts
    finally:
        conn.close()


def fetch_user(db: AdminDatabase, chat_id: int) -> Optional[sqlite3.Row]:
    conn = db.local_conn()
    try:
        cur = conn.execute(
            """
            SELECT
                chat_id,
                username,
                full_name,
                approved,
                blocked,
                paid_until,
                demo_end_at,
                demo_expires_at
            FROM users
            WHERE chat_id=?
            """,
            (chat_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def fetch_subscription(db: AdminDatabase, chat_id: int) -> Optional[sqlite3.Row]:
    conn = db.local_conn()
    try:
        cur = conn.execute(
            """
            SELECT chat_id, expires_at, is_active, is_demo, plan, last_payment_note
            FROM subscriptions
            WHERE chat_id=?
            """,
            (chat_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def compute_user_status(user_row: sqlite3.Row, sub_row: Optional[sqlite3.Row]) -> Tuple[str, Optional[datetime]]:
    now = datetime.utcnow()
    demo_dt = parse_dt(user_row.get("demo_end_at")) or parse_dt(user_row.get("demo_expires_at"))
    paid_dt = parse_dt(user_row.get("paid_until"))
    sub_dt = parse_dt(sub_row.get("expires_at")) if sub_row else None

    candidates: List[datetime] = [dt for dt in [demo_dt, paid_dt, sub_dt] if dt]
    effective = max(candidates) if candidates else None

    if user_row.get("blocked"):
        return "blocked", effective
    if demo_dt and demo_dt > now:
        return "demo", demo_dt
    if effective and effective > now:
        return "active", effective
    return "expired", effective


def log_admin_action(db: AdminDatabase, username: str, action: str, count: int) -> None:
    """Persist admin operations for auditing."""

    conn = db.local_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                affected_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO admin_action_log (username, action, affected_count) VALUES (?, ?, ?)",
            (username, action, count),
        )
        conn.commit()
    finally:
        conn.close()


def extend_user(db: AdminDatabase, chat_id: int, days: int) -> Optional[datetime]:
    if days <= 0:
        return None
    user_row = fetch_user(db, chat_id)
    if not user_row:
        return None
    sub_row = fetch_subscription(db, chat_id)

    _, effective = compute_user_status(user_row, sub_row)
    base = effective if effective and effective > datetime.utcnow() else datetime.utcnow()
    new_expiry = base + timedelta(days=days)

    conn = db.local_conn()
    try:
        conn.execute(
            """
            INSERT INTO subscriptions (chat_id, expires_at, is_active, is_demo, plan, last_payment_note)
            VALUES (?, ?, 1, 0, 'admin_panel', 'manual_extend')
            ON CONFLICT(chat_id) DO UPDATE SET
                expires_at=excluded.expires_at,
                is_active=1,
                is_demo=0,
                last_payment_note=excluded.last_payment_note
            """,
            (chat_id, new_expiry.isoformat()),
        )
        conn.execute(
            "UPDATE users SET paid_until=?, blocked=0 WHERE chat_id=?",
            (new_expiry.isoformat(), chat_id),
        )
        conn.commit()
        return new_expiry
    finally:
        conn.close()


def extend_users_bulk(db: AdminDatabase, chat_ids: Sequence[int], days: int) -> int:
    """Extend multiple users at once using set-based SQL operations."""

    normalized_ids = [uid for uid in chat_ids if isinstance(uid, int) and uid > 0]
    if days <= 0 or not normalized_ids:
        return 0

    placeholders = ",".join(["?"] * len(normalized_ids))
    extension = f"+{days} days"
    base_expr = (
        "MAX(datetime(users.paid_until), datetime(users.demo_end_at),"
        " datetime(users.demo_expires_at),"
        " (SELECT datetime(MAX(expires_at)) FROM subscriptions WHERE chat_id = users.chat_id),"
        " datetime('now'))"
    )
    conn = db.local_conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            f"""
            UPDATE users
            SET paid_until = datetime({base_expr}, ?), blocked=0
            WHERE chat_id IN ({placeholders})
            """,
            [extension, *normalized_ids],
        )

        # Keep subscriptions table consistent for the same users
        conn.execute(
            f"""
            INSERT INTO subscriptions (chat_id, expires_at, is_active, is_demo, plan, last_payment_note)
            SELECT users.chat_id, datetime({base_expr}, ?), 1, 0, 'admin_panel', 'bulk_extend'
            FROM users
            LEFT JOIN (SELECT chat_id, MAX(expires_at) as expires_at FROM subscriptions GROUP BY chat_id) s
                ON s.chat_id = users.chat_id
            WHERE users.chat_id IN ({placeholders})
            ON CONFLICT(chat_id) DO UPDATE SET
                expires_at=excluded.expires_at,
                is_active=1,
                is_demo=0,
                last_payment_note=excluded.last_payment_note
            """,
            [extension, *normalized_ids],
        )
        conn.commit()
        return len(normalized_ids)
    finally:
        conn.close()


def block_user(db: AdminDatabase, chat_id: int, blocked: bool) -> bool:
    conn = db.local_conn()
    try:
        cur = conn.execute(
            "UPDATE users SET blocked=? WHERE chat_id=?", (1 if blocked else 0, chat_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def approve_user(db: AdminDatabase, chat_id: int) -> bool:
    conn = db.local_conn()
    try:
        cur = conn.execute("UPDATE users SET approved=1 WHERE chat_id=?", (chat_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_keyword_alerts(db: AdminDatabase, user_filter: Optional[int] = None) -> list:
    conn = db.local_conn()
    try:
        query = (
            "SELECT id, user_id, keywords, regions, is_active, created_at FROM keyword_alerts"
        )
        params: Tuple = ()
        if user_filter is not None:
            query += " WHERE user_id=?"
            params = (user_filter,)
        query += " ORDER BY created_at DESC LIMIT 500"
        cur = conn.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def toggle_keyword(db: AdminDatabase, alert_id: int, enabled: bool) -> bool:
    conn = db.local_conn()
    try:
        cur = conn.execute(
            "UPDATE keyword_alerts SET is_active=? WHERE id=?",
            (1 if enabled else 0, alert_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_keyword(db: AdminDatabase, alert_id: int) -> bool:
    conn = db.local_conn()
    try:
        conn.execute(
            "DELETE FROM keyword_alert_state WHERE key LIKE ?", (f"%{alert_id}%",)
        )
        cur = conn.execute("DELETE FROM keyword_alerts WHERE id=?", (alert_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_users_paginated(
    db: AdminDatabase,
    page: int,
    page_size: int = 50,
    status: str = "all",
    search: str = "",
    expiry_window: Optional[str] = None,
) -> list:
    """Fetch paginated users using SQL-side filtering only."""

    page = max(page, 1)
    page_size = max(page_size, 1)
    offset = (page - 1) * page_size
    db.ensure_user_indexes()

    now_expr = "datetime('now')"
    effective_expr = (
        "MAX(datetime(u.paid_until), datetime(u.demo_end_at), datetime(u.demo_expires_at),"
        " datetime(s.expires_at))"
    )
    status_expr = (
        "CASE "
        " WHEN u.blocked=1 THEN 'blocked'"
        " WHEN {eff} IS NULL THEN 'expired'"
        " WHEN datetime(u.demo_end_at) > {now} OR datetime(u.demo_expires_at) > {now} THEN 'demo'"
        " WHEN datetime({eff}) > {now} THEN 'active'"
        " ELSE 'expired' END"
    ).format(eff=effective_expr, now=now_expr)

    clauses = ["1=1"]
    params: List = []

    if search:
        search_term = search.strip()
        if search_term.isdigit():
            clauses.append("u.chat_id = ?")
            params.append(int(search_term))
        else:
            like = f"%{search_term}%"
            clauses.append("(LOWER(u.username) LIKE LOWER(?) OR LOWER(u.full_name) LIKE LOWER(?))")
            params.extend([like, like])

    if status == "active":
        clauses.append(f"u.blocked=0 AND datetime({effective_expr}) > {now_expr}")
    elif status == "expired":
        clauses.append(f"u.blocked=0 AND (datetime({effective_expr}) <= {now_expr} OR {effective_expr} IS NULL)")
    elif status == "demo":
        clauses.append(
            f"u.blocked=0 AND (datetime(u.demo_end_at) > {now_expr} OR datetime(u.demo_expires_at) > {now_expr})"
        )
    elif status == "blocked":
        clauses.append("u.blocked=1")

    if expiry_window:
        # expiry_window values: today,1d,3d,7d,30d
        ranges = {
            "today": ("datetime('now', 'start of day')", "datetime('now', 'start of day', '+1 day')"),
            "1d": (now_expr, "datetime('now', '+1 day')"),
            "3d": (now_expr, "datetime('now', '+3 day')"),
            "7d": (now_expr, "datetime('now', '+7 day')"),
            "30d": (now_expr, "datetime('now', '+30 day')"),
        }
        if expiry_window in ranges:
            start, end = ranges[expiry_window]
            clauses.append(
                f"{effective_expr} IS NOT NULL AND datetime({effective_expr}) >= {start} AND datetime({effective_expr}) <= {end}"
            )

    where_sql = " AND ".join(clauses)

    conn = db.local_conn()
    try:
        select_sql = f"""
            SELECT
                u.chat_id, u.username, u.full_name, u.approved, u.blocked,
                {effective_expr} AS effective_until,
                {status_expr} AS computed_status
            FROM users u
            LEFT JOIN (SELECT chat_id, MAX(expires_at) as expires_at FROM subscriptions GROUP BY chat_id) s
                ON s.chat_id = u.chat_id
            WHERE {where_sql}
            ORDER BY datetime(effective_until) DESC, u.chat_id DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(select_sql, params + [page_size, offset]).fetchall()
        return rows
    finally:
        conn.close()


def count_users_filtered(
    db: AdminDatabase,
    status: str = "all",
    search: str = "",
    expiry_window: Optional[str] = None,
) -> int:
    db.ensure_user_indexes()
    now_expr = "datetime('now')"
    effective_expr = (
        "MAX(datetime(u.paid_until), datetime(u.demo_end_at), datetime(u.demo_expires_at),"
        " datetime(s.expires_at))"
    )

    clauses = ["1=1"]
    params: List = []

    if search:
        search_term = search.strip()
        if search_term.isdigit():
            clauses.append("u.chat_id = ?")
            params.append(int(search_term))
        else:
            like = f"%{search_term}%"
            clauses.append("(LOWER(u.username) LIKE LOWER(?) OR LOWER(u.full_name) LIKE LOWER(?))")
            params.extend([like, like])

    if status == "active":
        clauses.append(f"u.blocked=0 AND datetime({effective_expr}) > {now_expr}")
    elif status == "expired":
        clauses.append(f"u.blocked=0 AND (datetime({effective_expr}) <= {now_expr} OR {effective_expr} IS NULL)")
    elif status == "demo":
        clauses.append(
            f"u.blocked=0 AND (datetime(u.demo_end_at) > {now_expr} OR datetime(u.demo_expires_at) > {now_expr})"
        )
    elif status == "blocked":
        clauses.append("u.blocked=1")

    if expiry_window:
        ranges = {
            "today": ("datetime('now', 'start of day')", "datetime('now', 'start of day', '+1 day')"),
            "1d": (now_expr, "datetime('now', '+1 day')"),
            "3d": (now_expr, "datetime('now', '+3 day')"),
            "7d": (now_expr, "datetime('now', '+7 day')"),
            "30d": (now_expr, "datetime('now', '+30 day')"),
        }
        if expiry_window in ranges:
            start, end = ranges[expiry_window]
            clauses.append(
                f"{effective_expr} IS NOT NULL AND datetime({effective_expr}) >= {start} AND datetime({effective_expr}) <= {end}"
            )

    where_sql = " AND ".join(clauses)
    conn = db.local_conn()
    try:
        count_sql = f"""
            SELECT COUNT(*) as cnt
            FROM users u
            LEFT JOIN (SELECT chat_id, MAX(expires_at) as expires_at FROM subscriptions GROUP BY chat_id) s
                ON s.chat_id = u.chat_id
            WHERE {where_sql}
        """
        row = conn.execute(count_sql, params).fetchone() or {}
        return int(row.get("cnt") or 0)
    finally:
        conn.close()


def update_block_state(db: AdminDatabase, chat_ids: Sequence[int], blocked: bool) -> int:
    normalized_ids = [uid for uid in chat_ids if isinstance(uid, int) and uid > 0]
    if not normalized_ids:
        return 0
    placeholders = ",".join(["?"] * len(normalized_ids))
    conn = db.local_conn()
    try:
        cur = conn.execute(
            f"UPDATE users SET blocked=? WHERE chat_id IN ({placeholders})",
            [1 if blocked else 0, *normalized_ids],
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def approve_users(db: AdminDatabase, chat_ids: Sequence[int]) -> int:
    normalized_ids = [uid for uid in chat_ids if isinstance(uid, int) and uid > 0]
    if not normalized_ids:
        return 0
    placeholders = ",".join(["?"] * len(normalized_ids))
    conn = db.local_conn()
    try:
        cur = conn.execute(
            f"UPDATE users SET approved=1 WHERE chat_id IN ({placeholders})",
            normalized_ids,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
