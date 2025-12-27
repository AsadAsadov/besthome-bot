import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger("admin_panel")


class AdminDatabase:
    def __init__(self, data_dir: str):
        self.local_db = os.path.join(data_dir, "local_data.db")
        self.main_db = os.path.join(data_dir, "besthome.db")

    def _connect(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def local_conn(self) -> sqlite3.Connection:
        return self._connect(self.local_db)

    def main_conn(self) -> sqlite3.Connection:
        return self._connect(self.main_db)

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
    db: AdminDatabase, page: int, page_size: int = 50
) -> Tuple[list, int]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    offset = (page - 1) * page_size
    conn = db.local_conn()
    try:
        rows = conn.execute(
            """
            SELECT chat_id, username, full_name, approved, blocked,
                   paid_until, demo_end_at, demo_expires_at
            FROM users
            ORDER BY chat_id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
        total_row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone() or {}
        total = int(total_row.get("cnt") or 0)
        return rows, total
    finally:
        conn.close()
