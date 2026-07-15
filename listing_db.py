"""SQLite access layer for listing-only data stored in besthome.db.

This module intentionally owns only the immutable listing database path and
helpers. User data must be read/written through user_db.py (local_data.db).
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

BASE_DATA_DIR = os.getenv("BASE_DATA_DIR") or os.getenv("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MAIN_DB_DEFAULT_PATH = os.path.join(BASE_DATA_DIR, "besthome.db")
MAIN_DB = os.path.realpath(os.getenv("LISTINGS_DB_PATH") or os.getenv("BESTHOME_DB_PATH", MAIN_DB_DEFAULT_PATH))

LISTING_SQLITE_TABLES = {
    "listings",
    "regions",
    "sold",
    "listing_views",
    "listing_stats",
    "favorite_price_history",
}


def ensure_parent_dir(path: str) -> bool:
    parent_dir = os.path.dirname(path)
    if not parent_dir:
        return True
    os.makedirs(parent_dir, exist_ok=True)
    return True


def connect() -> sqlite3.Connection:
    ensure_parent_dir(MAIN_DB)
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
