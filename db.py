import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

DB_PATH = os.getenv("DM_DB_PATH", "dm_events.db")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


@contextmanager
def get_conn() -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dm_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time INTEGER NOT NULL,
                ig_business_id TEXT,
                message_id TEXT,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                text TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dm_events_thread_time
            ON dm_events(thread_id, event_time DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_dm_events_time
            ON dm_events(event_time DESC, id DESC);

            CREATE TABLE IF NOT EXISTS dm_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                trigger_type TEXT NOT NULL CHECK (trigger_type IN ('exact', 'contains', 'regex', 'any')),
                trigger_value TEXT,
                reply_text TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dm_templates_active
            ON dm_templates(is_active);
            """
        )


def insert_dm_event(
    *,
    event_time: int,
    ig_business_id: Optional[str],
    message_id: Optional[str],
    sender_id: str,
    recipient_id: str,
    thread_id: str,
    direction: str,
    text: str,
    payload_json: Optional[str],
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO dm_events(
                event_time, ig_business_id, message_id, sender_id, recipient_id,
                thread_id, direction, text, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_time,
                ig_business_id,
                message_id,
                sender_id,
                recipient_id,
                thread_id,
                direction,
                text,
                payload_json,
                now,
            ),
        )
        return int(cur.lastrowid)


def list_threads(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.thread_id,
                   MAX(e.event_time) AS last_event_time,
                   (
                     SELECT text FROM dm_events x
                     WHERE x.thread_id = e.thread_id
                     ORDER BY x.event_time DESC, x.id DESC
                     LIMIT 1
                   ) AS last_text,
                   (
                     SELECT direction FROM dm_events x
                     WHERE x.thread_id = e.thread_id
                     ORDER BY x.event_time DESC, x.id DESC
                     LIMIT 1
                   ) AS last_direction,
                   SUM(CASE WHEN e.direction = 'incoming' THEN 1 ELSE 0 END) AS incoming_count,
                   SUM(CASE WHEN e.direction = 'outgoing' THEN 1 ELSE 0 END) AS outgoing_count
            FROM dm_events e
            GROUP BY e.thread_id
            ORDER BY last_event_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_thread_messages(thread_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, event_time, sender_id, recipient_id, direction, text, message_id
            FROM dm_events
            WHERE thread_id = ?
            ORDER BY event_time ASC, id ASC
            LIMIT ?
            """,
            (thread_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_templates() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, trigger_type, trigger_value, reply_text, is_active, created_at, updated_at
            FROM dm_templates
            ORDER BY id DESC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def create_template(name: str, trigger_type: str, trigger_value: str, reply_text: str, is_active: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO dm_templates(name, trigger_type, trigger_value, reply_text, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), trigger_type, trigger_value.strip(), reply_text.strip(), is_active, now, now),
        )


def delete_template(template_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM dm_templates WHERE id = ?", (template_id,))


def find_matching_template(incoming_text: str) -> Optional[Dict[str, Any]]:
    import re

    normalized = (incoming_text or "").strip()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, trigger_type, trigger_value, reply_text
            FROM dm_templates
            WHERE is_active = 1
            ORDER BY id ASC
            """
        ).fetchall()

    for row in rows:
        template = _row_to_dict(row)
        trigger_type = template["trigger_type"]
        trigger_value = (template["trigger_value"] or "").strip()

        if trigger_type == "any":
            return template
        if trigger_type == "exact" and normalized.lower() == trigger_value.lower():
            return template
        if trigger_type == "contains" and trigger_value and trigger_value.lower() in normalized.lower():
            return template
        if trigger_type == "regex" and trigger_value:
            try:
                if re.search(trigger_value, normalized, flags=re.IGNORECASE):
                    return template
            except re.error:
                continue
    return None
