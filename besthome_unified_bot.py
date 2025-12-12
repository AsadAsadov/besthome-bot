# ============================================
# 🏠 BestHome Unified Bot — FULL v9
# Elan əlavə • Filtrlə axtarış • Açar sözlə axtarış • Nömrə ilə axtarış
# Favorilər • Admin Panel • Vasitəçi bazası • İstifadəçi təsdiqi
# besthome.db + local_data.db + agents.db
# ©️ 2025 Əsəd Əsədov (@esedovesed)
# ============================================

CURRENT_VERSION = "v9.1"
# ==============================
# 🧩 Admin konfiqurasiyası
# ==============================
ADMIN_ID = 6736526711

import os
import io
import time
import zipfile
import sqlite3
import threading
import math
import re
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import quote

import requests
from flask import Flask
import telebot
from telebot import types

# ==============================
# 🔐 BOT KONFİQURASİYASI
# ==============================
BOT_TOKEN = "7938311608:AAHmzsTqnVJ7cVtStp2lmzGe2-1oj9LN1JM"
ADMIN_ID = 1311851277
CHANNEL_ID = -1001878623087  # Bot bu kanalda admin olmalıdır

bot = telebot.TeleBot(BOT_TOKEN)

# ==============================
# 💾 DATABASE KONFİQURASİYASI
# ==============================
DATA_DIR = "/data"

MAIN_DB = os.path.join(DATA_DIR, "besthome.db")  # Əsas elan bazası (daily update)
LOCAL_DB = os.path.join(
    DATA_DIR, "local_data.db"
)  # Favorilər, qara siyahı, statuslar, last_seen
AGENTS_DB = os.path.join(DATA_DIR, "agents.db")  # Vasitəçi elanları

# ==============================
# 🛡️ DB TƏHLÜKƏSİZLİK YOXLAMASI
# ==============================
for db_path in (MAIN_DB, LOCAL_DB, AGENTS_DB):
    if not os.path.exists(db_path):
        raise RuntimeError(f"❌ DB tapılmadı: {db_path}")

print("✅ Bütün DB-lər tapıldı və hazırdır")

# ==============================
# 🧠 STATE-LƏR
# ==============================
user_state = {}  # Yeni elan prosesi
search_state = {}  # Axtarış paging və filter state

bot = telebot.TeleBot(BOT_TOKEN)
user_state = {}  # Yeni elan proses state
search_state = {}  # Açar sözlə axtarış paging state
search_reminder_shown = set()  # Session-level reminder flag

# Pagination
PAGE_SIZE = 20
NEW_LISTING_WINDOW_HOURS = 24
HOT_VIEWS_THRESHOLD = 50


def get_main_conn():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB, check_same_thread=False)  # 🔥 ÇOX VACİB
    conn.row_factory = sqlite3.Row
    return conn


def get_agents_conn():
    conn = sqlite3.connect(AGENTS_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_local_db():
    conn = get_local_conn()
    cur = conn.cursor()

    # Yeni elanlar
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listings_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_added TEXT,
            chat_id INTEGER,
            role TEXT,
            prop_type TEXT,
            operation TEXT,
            rayon TEXT,
            metro TEXT,
            rooms TEXT,
            area_kvm TEXT,
            price TEXT,
            currency TEXT,
            phone TEXT,
            contact_name TEXT,
            summary TEXT,
            link TEXT,
            approved INTEGER DEFAULT 0
        )
    """
    )

    # Təsdiqlənmiş elanlar (lokal)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listings_approved (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_added TEXT,
            chat_id INTEGER,
            role TEXT,
            prop_type TEXT,
            operation TEXT,
            rayon TEXT,
            metro TEXT,
            rooms TEXT,
            area_kvm TEXT,
            price TEXT,
            currency TEXT,
            phone TEXT,
            contact_name TEXT,
            summary TEXT,
            link TEXT
        )
    """
    )

    # Vasitəçilər (özünü vasitəçi kimi qeyd edən userlər üçün)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            chat_id INTEGER PRIMARY KEY,
            role TEXT,
            phone TEXT,
            name TEXT
        )
    """
    )

    # Favorilər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            listing_id INTEGER,
            source TEXT,
            added_at TEXT,
            UNIQUE(chat_id, listing_id, source)
        )
    """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorite_price_history (
            source TEXT,
            listing_id INTEGER,
            last_price INTEGER,
            updated_at TEXT,
            PRIMARY KEY (source, listing_id)
        )
    """
    )

    # İstifadəçilər (access control)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            date_joined TEXT,
            approved INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0
        )
    """
    )

    # Limitlər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS search_limits (
            chat_id INTEGER,
            date TEXT,
            key_type TEXT,    -- 'phone', 'keyword', 'structured'
            used INTEGER,
            PRIMARY KEY (chat_id, date, key_type)
        )
    """
    )

    # Elan statusları
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listing_status (
            source TEXT,
            listing_id INTEGER,
            status TEXT,
            updated_at TEXT,
            PRIMARY KEY (source, listing_id)
        )
    """
    )

    # Elan baxışları
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listing_views (
            source TEXT,
            listing_id INTEGER,
            views INTEGER DEFAULT 0,
            last_viewed_at TEXT,
            PRIMARY KEY (source, listing_id)
        )
    """
    )

    # Saxlanılan axtarışlar
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            operation TEXT,
            rooms INTEGER,
            price_min INTEGER,
            price_max INTEGER,
            rayon TEXT,
            prop_type TEXT,
            created_at TEXT,
            last_notified_at TEXT
        )
    """
    )

    # Axtarış logları
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            search_type TEXT,
            operation TEXT,
            rayon TEXT,
            query_text TEXT,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
        )
    """
    )

    # İstifadəçi aktivliyi
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_activity (
            chat_id INTEGER PRIMARY KEY,
            last_seen TEXT,
            total_searches INTEGER DEFAULT 0
        )
    """
    )

    # Status cədvəli sütun yoxlaması (avtomatik migrasiya)
    cur.execute("PRAGMA table_info(listing_status)")
    cols = {row[1] for row in cur.fetchall()}
    if "status" not in cols:
        cur.execute(
            "ALTER TABLE listing_status ADD COLUMN status TEXT DEFAULT 'active'"
        )
    if "updated_at" not in cols:
        cur.execute(
            "ALTER TABLE listing_status ADD COLUMN updated_at TEXT"
        )

    # saved_searches cədvəli üçün sütun yoxlaması
    cur.execute("PRAGMA table_info(saved_searches)")
    saved_cols = {row[1] for row in cur.fetchall()}
    if "last_notified_at" not in saved_cols:
        cur.execute(
            "ALTER TABLE saved_searches ADD COLUMN last_notified_at TEXT"
        )
    if "created_at" not in saved_cols:
        cur.execute(
            "ALTER TABLE saved_searches ADD COLUMN created_at TEXT"
        )

    conn.commit()
    conn.close()
    print("✅ local_data.db hazırdır.")


def init_agents_db():
    """Vasitəçi elanları üçün ayrıca baza."""
    conn = get_agents_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS arenda_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Emeliyyat TEXT,
            Emlakin_novu TEXT,
            Rayon_Qesebe TEXT,
            Unvan TEXT,
            Metro TEXT,
            Otaq_sayi TEXT,
            Mertebe TEXT,
            Sahe_sot TEXT,
            Sahe_kvm TEXT,
            Qiymet TEXT,
            Elaqe_nomresi TEXT,
            Ad TEXT,
            Sened TEXT,
            Menbe TEXT,
            Elani_veren TEXT,
            Elanin_tarixi TEXT,
            Elan_kodu TEXT,
            Umumi_melumat TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.commit()
    conn.close()
    print("✅ agents.db hazırdır.")


def init_main_db_indices():
    """Əsas bazada indekslər."""
    if not os.path.exists(MAIN_DB):
        print("ℹ️ besthome.db tapılmadı, indeks mərhələsi keçildi.")
        return
    try:
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_main_operation ON listings(operation)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_main_price ON listings(price)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_main_date ON listings(date_read)")
        conn.commit()
        conn.close()
        print("✅ besthome.db indeksləri hazırdır.")
    except Exception as e:
        print("⚠️ İndeks yaradarkən xəta:", e)


def ensure_fts_tables():
    """FTS5 cədvəllərini yalnız boş olduqda qur."""

    def build_fts(conn, base_table: str, fts_name: str):
        cur = conn.cursor()
        cur.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS "
            f"{fts_name} USING fts5(summary, address, metro, rayon, contact_name, operation, "
            f"content='{base_table}', content_rowid='id')"
        )
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (fts_name,))
        if cur.fetchone()[0] == 0:
            return
        cur.execute(f"SELECT COUNT(*) FROM {fts_name}")
        existing = cur.fetchone()[0] or 0
        if existing > 0:
            return

        cur.execute(f"SELECT name FROM pragma_table_info('{base_table}')")
        cols = {row[0] for row in cur.fetchall()}

        def col_expr(name: str):
            return name if name in cols else "''"

        select_sql = (
            "SELECT id, "
            + ", ".join(
                [
                    col_expr("summary"),
                    col_expr("address"),
                    col_expr("metro"),
                    col_expr("rayon"),
                    col_expr("contact_name"),
                    col_expr("operation"),
                ]
            )
            + f" FROM {base_table}"
        )
        cur.execute(
            f"INSERT INTO {fts_name}(rowid, summary, address, metro, rayon, contact_name, operation) "
            + select_sql
        )
        conn.commit()

    try:
        if os.path.exists(MAIN_DB):
            conn = get_main_conn()
            build_fts(conn, "listings", "listings_fts")
            conn.close()
    except Exception as e:
        print("⚠️ FTS (main) yaradarkən xəta:", e)

    try:
        conn = get_local_conn()
        build_fts(conn, "listings_approved", "local_listings_fts")
        conn.close()
    except Exception as e:
        print("⚠️ FTS (local) yaradarkən xəta:", e)


# =============== ÜMUMİ UTIL FUNKSİYALAR ===============


def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_ID


def format_price(v) -> str:
    if v is None:
        return "-"
    s = str(v).strip()
    if not s:
        return "-"
    try:
        val = int(float(s.replace(" ", "").replace(",", "")))
        return f"{val:,}".replace(",", " ")
    except:
        return s


def parse_price_value(raw) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def safe_date(row: dict):
    for key in ("date_read", "date_added", "Elanin_tarixi", "added_at", "created_at"):
        v = row.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace(" ", "T"))
            except:
                pass
    return datetime.min


def register_user(message):
    chat = message.chat
    uid = chat.id
    full_name = (chat.first_name or "") + (
        (" " + chat.last_name) if chat.last_name else ""
    )
    username = message.from_user.username if message.from_user else None

    conn = get_local_conn()
    cur = conn.cursor()

    # Admin avtomatik approved
    if is_admin(uid):
        cur.execute(
            """
            INSERT INTO users (chat_id, full_name, username, date_joined, approved, blocked)
            VALUES (?, ?, ?, ?, 1, 0)
            ON CONFLICT(chat_id) DO UPDATE SET
                full_name=excluded.full_name,
                username=excluded.username
        """,
            (uid, full_name, username or "", datetime.utcnow().isoformat()),
        )
    else:
        cur.execute(
            """
            INSERT INTO users (chat_id, full_name, username, date_joined)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                full_name=excluded.full_name,
                username=excluded.username
        """,
            (uid, full_name, username or "", datetime.utcnow().isoformat()),
        )
    conn.commit()
    conn.close()


def is_user_allowed(chat_id: int) -> bool:
    if is_admin(chat_id):
        return True
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT approved, blocked FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    approved, blocked = row
    return approved == 1 and blocked == 0


def ensure_allowed(message) -> bool:
    chat_id = message.chat.id
    if is_admin(chat_id):
        return True
    if not is_user_allowed(chat_id):
        bot.send_message(
            chat_id,
            "🛑 Botdan istifadə üçün admin təsdiqi tələb olunur.\n"
            "Zəhmət olmasa icazə verilməsini gözləyin.",
        )

        return False
    return True


def ensure_allowed_cb(c) -> bool:
    chat_id = c.message.chat.id
    if is_admin(chat_id):
        return True
    if not is_user_allowed(chat_id):
        try:
            bot.answer_callback_query(
                c.id,
                "🛑 Botdan istifadə üçün admin təsdiqi tələb olunur.",
                show_alert=True,
            )
        except:
            pass
        return False
    return True


def check_limit(chat_id: int, key_type: str, daily_limit: int) -> bool:
    if daily_limit <= 0:
        return True
    today = date.today().isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT used FROM search_limits WHERE chat_id=? AND date=? AND key_type=?",
        (chat_id, today, key_type),
    )
    row = cur.fetchone()
    conn.close()
    used = row[0] if row else 0
    return used < daily_limit


def inc_limit(chat_id: int, key_type: str, inc: int = 1):
    today = date.today().isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO search_limits (chat_id, date, key_type, used)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id, date, key_type)
        DO UPDATE SET used = used + ?
        """,
        (chat_id, today, key_type, inc, inc),
    )
    conn.commit()
    conn.close()


def reset_user_state(chat_id: int):
    user_state.pop(chat_id, None)


def reset_search_state(chat_id: int):
    state = search_state.get(chat_id)
    if state and state.get("mode") == "structured" and state.get("awaiting_floor_range"):
        try:
            bot.send_message(chat_id, "↩️ Filter mərhələsi ləğv edildi.")
        except:
            pass
    search_state.pop(chat_id, None)


def compute_total_pages(total_count: int) -> int:
    return max(1, math.ceil(total_count / PAGE_SIZE))


def build_pagination_keyboard(page: int, total_pages: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("⏮ İlk", callback_data="pg:first"),
        types.InlineKeyboardButton("◀️ Geri", callback_data="pg:prev"),
        types.InlineKeyboardButton(
            f"📄 {page} / {total_pages}", callback_data="pg:noop"
        ),
        types.InlineKeyboardButton("▶️ İrəli", callback_data="pg:next"),
        types.InlineKeyboardButton("⏭ Son", callback_data="pg:last"),
    )
    return mk


def set_pagination_state(chat_id: int, mode: str, params: dict, page: int, total_pages: int):
    search_state[chat_id] = {
        "mode": mode,
        "params": params or {},
        "page": page,
        "total_pages": total_pages,
    }


def offer_save_search(chat_id: int, params: dict):
    if not params:
        return

    st = search_state.setdefault(chat_id, {})

    if chat_id in search_reminder_shown:
        st.pop("pending_save", None)
        return

    st["pending_save"] = params
    search_reminder_shown.add(chat_id)

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("✅ Bəli", callback_data="save_search|yes"),
        types.InlineKeyboardButton("❌ Xeyr", callback_data="save_search|no"),
    )
    bot.send_message(
        chat_id,
        "💡 Bu axtarışı tez-tez edirsiniz?\nBildiriş aktiv edim?",
        reply_markup=mk,
    )


def build_saved_search_from_structured(filters: dict):
    op_code = filters.get("op")
    op_norm = None
    if op_code == "sat":
        op_norm = "sale"
    elif op_code == "kir":
        op_norm = "rent"
    elif op_code:
        op_norm = normalize_operation_value(op_code)

    price_code = filters.get("price", "s0")
    price_min, price_max = decode_price_range(price_code)

    room_code = filters.get("rooms")
    rooms_val = None
    if room_code and room_code.startswith("r"):
        try:
            rooms_val = int(room_code.replace("r", ""))
        except Exception:
            rooms_val = None

    prop_code = filters.get("prop")
    prop_type = None
    if prop_code:
        prop_type = PROP_TYPES.get(prop_code)

    rayon = filters.get("rayon")
    if rayon == "all":
        rayon = None

    return {
        "operation": op_norm,
        "rooms": rooms_val,
        "price_min": price_min,
        "price_max": price_max,
        "rayon": rayon,
        "prop_type": prop_type,
    }


def build_saved_search_from_keyword(operation: str):
    op_norm = normalize_operation_value(operation) if operation else None
    return {
        "operation": op_norm,
        "rooms": None,
        "price_min": None,
        "price_max": None,
        "rayon": None,
        "prop_type": None,
    }


def build_saved_search_from_smart(criteria: dict):
    op_norm = normalize_operation_value(criteria.get("operation"))
    return {
        "operation": op_norm,
        "rooms": criteria.get("rooms"),
        "price_min": criteria.get("price_min"),
        "price_max": criteria.get("price_max"),
        "rayon": criteria.get("rayon"),
        "prop_type": criteria.get("prop_type"),
    }


def save_search(chat_id: int, params: dict):
    if not params:
        return False
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO saved_searches (chat_id, operation, rooms, price_min, price_max, rayon, prop_type, created_at, last_notified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            params.get("operation"),
            params.get("rooms"),
            params.get("price_min"),
            params.get("price_max"),
            params.get("rayon"),
            params.get("prop_type"),
            datetime.utcnow().isoformat(),
            None,
        ),
    )
    conn.commit()
    conn.close()
    return True


def get_status_map():
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT source, listing_id, status FROM listing_status")
    rows = cur.fetchall()
    conn.close()
    return {(r["source"], r["listing_id"]): r["status"] for r in rows}


def get_listing_status(source: str, listing_id: int) -> str:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT status FROM listing_status WHERE source=? AND listing_id=?",
        (source, listing_id),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "active"


def update_listing_status(source: str, listing_id: int, status: str):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO listing_status (source, listing_id, status, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source, listing_id) DO UPDATE SET
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (source, listing_id, status, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


OPERATION_VARIANTS = {
    "sale": {"sale", "satılır", "satilir", "satış"},
    "rent": {"rent", "kirayə verilir", "kirayə", "kiraye", "icarə", "icare"},
}

OPERATION_DB_MAP = {
    "sale": "Satılır",
    "rent": "Kirayə verilir",
}

_operation_cache = {}


def normalize_operation_value(val: str):
    if not val:
        return None
    v = str(val).strip().lower()
    for norm, variants in OPERATION_VARIANTS.items():
        if v == norm or v in variants:
            return norm
    return None


def detect_db_operation_value(op_norm: str, source: str):
    if not op_norm:
        return None
    if op_norm in OPERATION_DB_MAP:
        return OPERATION_DB_MAP[op_norm]
    if op_norm not in OPERATION_VARIANTS:
        return op_norm
    key = (source, op_norm)
    if key in _operation_cache:
        return _operation_cache[key]

    values = set()
    try:
        if source == "main" and os.path.exists(MAIN_DB):
            conn = get_main_conn()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT operation FROM listings LIMIT 200")
        else:
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT operation FROM listings_approved LIMIT 200")
        values = {str(r[0]).strip().lower() for r in cur.fetchall() if r[0]}
        conn.close()
    except Exception:
        values = set()

    for candidate in OPERATION_VARIANTS[op_norm]:
        if candidate.lower() in values:
            _operation_cache[key] = candidate
            return candidate

    fallback = OPERATION_DB_MAP.get(op_norm, op_norm)
    _operation_cache[key] = fallback
    return fallback


def show_loading_message(chat_id: int, edit_target=None):
    text = "🔎 Elanlar axtarılır... zəhmət olmasa gözləyin."
    if edit_target:
        try:
            bot.edit_message_text(text, chat_id=edit_target[0], message_id=edit_target[1])
            return edit_target
        except Exception:
            pass
    try:
        msg = bot.send_message(chat_id, text)
        return (msg.chat.id, msg.message_id)
    except Exception:
        return None


def replace_loading_message(ref, text):
    if not ref:
        return False
    try:
        bot.edit_message_text(text, chat_id=ref[0], message_id=ref[1])
        return True
    except Exception:
        return False


def log_search_event(chat_id: int, search_type: str, operation=None, rayon=None, query_text=None):
    conn = None
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO search_logs (chat_id, search_type, operation, rayon, query_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, search_type, operation, rayon, query_text),
        )
        cur.execute(
            """
            INSERT INTO user_activity (chat_id, last_seen, total_searches)
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_seen=excluded.last_seen,
                total_searches=user_activity.total_searches + 1
            """,
            (chat_id, datetime.utcnow().isoformat(),),
        )
        conn.commit()
    except Exception as e:
        print("⚠️ Search log error:", e)
    finally:
        if conn:
            conn.close()

def fetch_listing_by_source(source: str, listing_id: int):
    if source == "main" and os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM listings WHERE id=?", (listing_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["__source"] = "main"
            return d
    if source == "local":
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM listings_approved WHERE id=?", (listing_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["__source"] = "local"
            return d
    if source == "agents":
        conn = get_agents_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM arenda_data WHERE id=?", (listing_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["__source"] = "agents"
            return d
    return None


def record_listing_view(source: str, listing_id: Optional[int]):
    if not listing_id:
        return
    conn = None
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO listing_views (source, listing_id, views, last_viewed_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(source, listing_id) DO UPDATE SET
                views=listing_views.views + 1,
                last_viewed_at=excluded.last_viewed_at
            """,
            (source, listing_id, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception as e:
        print("⚠️ View track error:", e)
    finally:
        if conn:
            conn.close()


def query_top_viewed_listings(days: int = 7, offset: int = 0, limit: int = None):
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source, listing_id, views, last_viewed_at
        FROM listing_views
        WHERE datetime(last_viewed_at) >= datetime(?)
        ORDER BY views DESC, last_viewed_at DESC
        """,
        (cutoff,),
    )
    rows = cur.fetchall()
    conn.close()

    status_map = get_status_map()
    enriched = []
    for r in rows:
        ev = fetch_listing_by_source(r["source"], r["listing_id"])
        if not ev:
            continue
        if not is_listing_active(ev, status_map):
            continue
        ev["__views"] = r["views"]
        ev["__last_viewed_at"] = r["last_viewed_at"]
        enriched.append(ev)

    total = len(enriched)
    if limit is not None:
        enriched = enriched[offset : offset + limit]
    else:
        enriched = enriched[offset:]
    return enriched, total


def get_listing_price(ev: dict) -> Optional[int]:
    return parse_price_value(ev.get("price") or ev.get("Qiymet"))


def upsert_favorite_price(source: str, listing_id: int, price_val: Optional[int]):
    if price_val is None:
        return
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO favorite_price_history (source, listing_id, last_price, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source, listing_id) DO UPDATE SET
            last_price=excluded.last_price,
            updated_at=excluded.updated_at
        """,
        (source, listing_id, price_val, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def record_favorite_price(source: str, listing_id: int):
    ev = fetch_listing_by_source(source, listing_id)
    if not ev:
        return
    price_val = get_listing_price(ev)
    upsert_favorite_price(source, listing_id, price_val)


def send_logo_if_exists(chat_id: int):
    try:
        if os.path.exists("besthomelogo.jpeg"):
            with open("besthomelogo.jpeg", "rb") as f:
                bot.send_photo(chat_id, f)
    except:
        pass


def send_main_menu(chat_id: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📝 Yeni elan əlavə et")
    kb.row("🔎 Axtarış sistemi")
    kb.row("🔥 Ən çox baxılan elanlar")
    kb.row("📂 Elan statusları")
    kb.row("⭐ Favorilərim", "📋 Elanlarım")
    kb.row("ℹ️ Haqqında")
    if is_admin(chat_id):
        kb.row("📊 Admin Panel")
    bot.send_message(chat_id, "🏠 Əsas menyu:", reply_markup=kb)


# =============== ELAN KARTI (WhatsApp ilə) ===============


def make_whatsapp_url(
    phone: str, text: str = "Salam, elanınız haqqında maraqlanmaq istəyirəm."
):
    if not phone:
        return None
    p = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    p = p.replace("+", "")
    if p.startswith("0"):
        p = "994" + p[1:]
    if p.startswith("8"):
        p = "994" + p[1:]
    if len(p) < 9:
        return None
    return f"https://wa.me/{p}?text={quote(text)}"


def send_listing_card(
    chat_id: int,
    ev: dict,
    source: str = "main",
    with_fav_button: bool = True,
    status_controls: bool = True,
    extra_buttons=None,
):
    date_val = (
        ev.get("date_read") or ev.get("date_added") or ev.get("created_at") or "-"
    )
    title = ev.get("prop_type") or ev.get("Emlakin_novu") or "-"
    rooms = ev.get("rooms") or ev.get("Otaq_sayi") or "-"
    op = ev.get("operation") or ev.get("Emeliyyat") or "-"
    price = format_price(ev.get("price") or ev.get("Qiymet"))
    cur = ev.get("currency") or "AZN"
    rayon = ev.get("rayon") or ev.get("Rayon_Qesebe") or ""
    metro = ev.get("metro") or ev.get("Metro") or ""
    addr = ev.get("address") or ev.get("Unvan") or ""
    phone = ev.get("phone") or ev.get("Elaqe_nomresi") or "-"
    cname = ev.get("contact_name") or ev.get("Ad") or "-"
    summary = ev.get("summary") or ev.get("Umumi_melumat") or ""

    location = addr or rayon
    if metro:
        if location:
            location += f" — {metro}"
        else:
            location = metro

    listing_id = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
    try:
        listing_pk = int(str(listing_id)) if listing_id is not None else None
    except (TypeError, ValueError):
        listing_pk = None
    status = get_listing_status(source, listing_pk) if listing_pk else "active"

    if listing_pk:
        record_listing_view(source, listing_pk)

    badges = []
    try:
        if datetime.utcnow() - safe_date(ev) <= timedelta(hours=NEW_LISTING_WINDOW_HOURS):
            badges.append("🆕")
    except Exception:
        pass

    try:
        if ev.get("__views") is not None and ev.get("__views") >= HOT_VIEWS_THRESHOLD:
            badges.append("🔥")
    except Exception:
        pass

    if ev.get("__price_drop"):
        badges.append("📉")

    badge_txt = (" ".join(badges) + " ") if badges else ""

    text = (
        f"📅 {date_val}\n"
        f"🏠 {badge_txt}{title} | {rooms}\n"
        f"💸 {op} | 💰 {price} {cur}\n"
        f"📍 {location or '-'}\n"
        f"📞 {phone} ({cname})\n"
        f"🧾 {summary}"
    )

    if status != "active":
        status_txt = {
            "sold": "✅ Satılıb",
            "rented": "✅ Kirayə verilib",
            "blacklisted": "⛔ Qara siyahıda",
        }.get(status, status)
        text += f"\n📌 Status: {status_txt}"

    link = ev.get("link") or ev.get("source_link")
    if link:
        text += f"\n🔗 {link}"

    if ev.get("__views") is not None:
        text += f"\n👁️ Baxış: {ev['__views']}"

    mk = types.InlineKeyboardMarkup()

    if with_fav_button and ev.get("id"):
        mk.add(
            types.InlineKeyboardButton(
                "⭐ Favoriyə əlavə et",
                callback_data=f"fav|{source}|{ev['id']}",
            )
        )

    if status_controls and listing_pk:
        mk.add(
            types.InlineKeyboardButton(
                "✅ Satılıb", callback_data=f"st|sold|{source}|{listing_pk}"
            ),
            types.InlineKeyboardButton(
                "✅ Kirayə verilib", callback_data=f"st|rent|{source}|{listing_pk}"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "⛔ Qara siyahıya", callback_data=f"st|blk|{source}|{listing_pk}"
            )
        )

    wa_url = make_whatsapp_url(phone)
    if wa_url:
        mk.add(types.InlineKeyboardButton("💬 WhatsApp-da yaz", url=wa_url))

    if extra_buttons:
        for btn in extra_buttons:
            mk.add(btn)

    if link:
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))

    bot.send_message(chat_id, text, reply_markup=mk)


@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_id = message.chat.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""
    first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    search_reminder_shown.discard(chat_id)

    onboarding_text = (
        "👋 Xoş gəlmisiniz!\n"
        "Axtarışa başlamaq üçün **Axtarış sistemi** düyməsinə toxunun."
    )

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT chat_id, approved, is_admin, last_version FROM users WHERE chat_id=?",
        (chat_id,),
    )
    row = cur.fetchone()
    is_first_time = False

    # 🧩 Əgər user bazada yoxdursa, əlavə et
    if not row:
        is_first_time = True
        cur.execute(
            "INSERT INTO users (chat_id, username, full_name, first_seen, approved, is_admin, last_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, username, full_name, first_seen, 0, 0, CURRENT_VERSION),
        )
        conn.commit()

    # 🧩 Admin üçün avtomatik təsdiq
    if chat_id == ADMIN_ID:
        cur.execute(
            "UPDATE users SET approved=1, is_admin=1 WHERE chat_id=?", (chat_id,)
        )
        conn.commit()
        conn.close()
        main_menu(chat_id)
        if is_first_time:
            bot.send_message(chat_id, onboarding_text, parse_mode="Markdown")
        bot.send_message(chat_id, "✅ Admin kimi daxil oldun.")
        return

    # 🧩 İstifadəçi təsdiqlənməyibsə
    cur.execute("SELECT approved FROM users WHERE chat_id=?", (chat_id,))
    approved = cur.fetchone()[0]
    conn.close()

    if not approved:
        if is_first_time:
            bot.send_message(chat_id, onboarding_text, parse_mode="Markdown")
        bot.send_message(
            chat_id, "❌ Admin icazə verməyib. Zəhmət olmasa təsdiq gözləyin."
        )
        return

    # 🧩 Təsdiqlənmiş istifadəçi üçün menyunu aç
    main_menu(chat_id)
    if is_first_time:
        bot.send_message(chat_id, onboarding_text, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "👋 Xoş gəlmisiniz! Menyudan seçim edin:")


# =============== ℹ️ Haqqında ===============


@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    text = (
        "🏠 BestHome Əmlak Botu\n\n"
        "BestHome — Azərbaycanda satılan və kirayə verilən daşınmaz əmlak elanlarını rahat və sürətli tapmaq üçün hazırlanmış ağıllı Telegram botudur.\n\n"
        "🔎 Axtarış imkanları\n"
        "• Filtrlə axtarış (satılır / kirayə verilir, otaq, qiymət və s.)\n"
        "• Açar sözlə axtarış (mətn yazmaq kifayətdir)\n"
        "• Telefon nömrəsi ilə axtarış\n"
        "• Ağıllı axtarış — yazdığınız mətni avtomatik analiz edir\n\n"
        "📄 Elanlarla işləmə\n"
        "• Elanlara baxış və səhifələmə\n"
        "• Elanları ⭐ favorilərə əlavə etmə\n"
        "• Favorilərdən çıxarma\n"
        "• Satılıb / Kirayə verilib kimi işarələmə\n"
        "• Qara siyahı ilə idarəetmə\n\n"
        "🔔 Bildirişlər\n"
        "• Yeni uyğun elan olduqda xəbərdarlıq\n"
        "• Favori elanların qiyməti düşdükdə bildiriş\n\n"
        "👥 Təhlükəsizlik\n"
        "• Bot yalnız admin tərəfindən təsdiqlənmiş istifadəçilər üçün aktivdir\n"
        "• Elanlar və istifadəçilər admin nəzarətindədir\n\n"
        "📞 Əlaqə\n"
        "Admin: @esedovesed"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


# =============== 📝 YENİ ELAN ƏLAVƏ ET ===============

CANCEL_CMDS = ["❌ Ləğv et", "🏠 Əsas menyu"]


def new_listing_keyboard(extra=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("❌ Ləğv et", "🏠 Əsas menyu")
    if extra:
        for row in extra:
            kb.row(*row)
    return kb


def handle_common_nav(message):
    chat_id = message.chat.id
    txt = message.text
    if txt in CANCEL_CMDS:
        reset_user_state(chat_id)
        bot.send_message(chat_id, "❌ Əməliyyat ləğv edildi.")
        send_main_menu(chat_id)
        return True
    return False


@bot.message_handler(func=lambda m: m.text == "📝 Yeni elan əlavə et")
def start_new_listing(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    reset_user_state(chat_id)

    instr = (
        "📝 *Yeni elan əlavə etmə qaydası:*\n"
        "1️⃣ Rol (Vasitəçi / Əmlak sahibi)\n"
        "2️⃣ Əməliyyat (Satılır / Kirayə verilir)\n"
        "3️⃣ Əmlak tipi (Mənzil / Fərdi yaşayış evi / Qeyri-yaşayış sahəsi / Bağ evi / Torpaq)\n"
        "4️⃣ Otaq sayı, ərazi, metro, sahə, qiymət, əlaqə\n"
        "5️⃣ Elan admin təsdiqindən sonra sistemə düşəcək."
    )
    bot.send_message(chat_id, instr, parse_mode="Markdown")

    kb = new_listing_keyboard(extra=[["Vasitəçi", "Əmlak sahibi"]])
    user_state[chat_id] = {"step": "role", "chat_id": chat_id}
    bot.send_message(chat_id, "👤 Rolunuzu seçin:", reply_markup=kb)


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "role")
def step_role(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    choice = message.text.strip()
    if choice not in ["Vasitəçi", "Əmlak sahibi"]:
        bot.send_message(
            chat_id, "Zəhmət olmasa 'Vasitəçi' və ya 'Əmlak sahibi' seçin."
        )
        return
    st = user_state[chat_id]
    st["role"] = choice

    kb = new_listing_keyboard(extra=[["Satılır", "Kirayə verilir"]])
    st["step"] = "operation"
    bot.send_message(chat_id, "💸 Əməliyyat növünü seçin:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "operation"
)
def step_operation(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    choice = message.text.strip()
    if choice not in ["Satılır", "Kirayə verilir"]:
        bot.send_message(chat_id, "Satılır və ya Kirayə verilir seçin.")
        return
    st = user_state[chat_id]
    st["operation"] = choice

    extra = [
        ["Mənzil", "Fərdi yaşayış evi"],
        ["Qeyri-yaşayış sahəsi", "Bağ evi"],
        ["Torpaq"],
    ]
    kb = new_listing_keyboard(extra=extra)
    st["step"] = "prop_type"
    bot.send_message(chat_id, "🏠 Əmlak tipini seçin:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "prop_type"
)
def step_prop_type(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    choice = message.text.strip()
    valid = ["Mənzil", "Fərdi yaşayış evi", "Qeyri-yaşayış sahəsi", "Bağ evi", "Torpaq"]
    if choice not in valid:
        bot.send_message(chat_id, "Verilən siyahıdan əmlak tipini seçin.")
        return
    st = user_state[chat_id]
    st["prop_type"] = choice

    kb = new_listing_keyboard(
        extra=[
            ["1 otaqlı", "2 otaqlı", "3 otaqlı"],
            ["4 otaqlı", "5 otaqlı", "6 otaqlı"],
            ["7 otaqlı", "8 otaqlı", "9 otaqlı"],
            ["10+ otaqlı"],
        ]
    )
    st["step"] = "rooms"
    bot.send_message(chat_id, "🔢 Otaq sayını seçin:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "rooms"
)
def step_rooms(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["rooms"] = message.text.strip()

    rayonlar = [
        "Binəqədi",
        "Qaradağ",
        "Xəzər",
        "Səbail",
        "Sabunçu",
        "Suraxanı",
        "Nərimanov",
        "Nəsimi",
        "Nizami",
        "Pirallahı",
        "Xətai",
        "Yasamal",
        "Xırdalan",
        "Abşeron",
        "Sumqayıt",
        "Digər",
    ]
    rows, row = [], []
    for r in rayonlar:
        row.append(r)
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    kb = new_listing_keyboard(extra=rows)
    st["step"] = "rayon"
    bot.send_message(chat_id, "📍 Rayon / ərazi seçin:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "rayon"
)
def step_rayon(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["rayon"] = message.text.strip()

    metros = [
        "İçərişəhər",
        "Sahil",
        "28 May",
        "Gənclik",
        "Nəriman Nərimanov",
        "Bakmil",
        "Ulduz",
        "Koroğlu",
        "Qara Qarayev",
        "Neftçilər",
        "Xalqlar Dostluğu",
        "Əhmədli",
        "Həzi Aslanov",
        "Nizami",
        "Elmlər Akademiyası",
        "İnşaatçılar",
        "20 Yanvar",
        "Memar Əcəmi",
        "Azadlıq Prospekti",
        "Dərnəgül",
        "Xocəsən",
        "Metro yoxdur",
    ]
    rows, row = [], []
    for mname in metros:
        row.append(mname)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    kb = new_listing_keyboard(extra=rows)
    st["step"] = "metro"
    bot.send_message(
        chat_id, "🚇 Metro seçin (yoxdursa 'Metro yoxdur'):", reply_markup=kb
    )


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "metro"
)
def step_metro(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["metro"] = message.text.strip()
    st["step"] = "area"
    kb = new_listing_keyboard()
    bot.send_message(chat_id, "📏 Sahə (m²) yazın:", reply_markup=kb)


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "area")
def step_area(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["area_kvm"] = message.text.strip()
    st["step"] = "price"
    kb = new_listing_keyboard()
    bot.send_message(chat_id, "💰 Qiyməti yazın:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "price"
)
def step_price(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["price"] = message.text.strip()
    st["step"] = "currency"
    kb = new_listing_keyboard(extra=[["AZN", "USD"]])
    bot.send_message(chat_id, "💱 Valyuta seçin:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "currency"
)
def step_currency(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    val = message.text.strip().upper()
    if val not in ["AZN", "USD"]:
        bot.send_message(chat_id, "AZN və ya USD seçin.")
        return
    st = user_state[chat_id]
    st["currency"] = val
    st["step"] = "phone"
    kb = new_listing_keyboard()
    bot.send_message(chat_id, "📞 Əlaqə nömrəsini yazın:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "phone"
)
def step_phone(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["phone"] = message.text.strip()
    st["step"] = "contact_name"
    kb = new_listing_keyboard()
    bot.send_message(chat_id, "👤 Əlaqədar şəxsin adını yazın:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "contact_name"
)
def step_contact_name(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["contact_name"] = message.text.strip()
    st["step"] = "summary"
    kb = new_listing_keyboard()
    bot.send_message(chat_id, "🧾 Elan haqqında qısa təsvir yazın:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "summary"
)
def step_summary(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    st["summary"] = message.text.strip()
    st["step"] = "link"
    kb = new_listing_keyboard(extra=[["Link yoxdur, elanı göndər ✅"]])
    bot.send_message(
        chat_id,
        "🔗 Əgər elan linki varsa yazın.\n"
        "Yoxdursa 'Link yoxdur, elanı göndər ✅' seçin.",
        reply_markup=kb,
    )


def save_agent_if_needed(data: dict):
    if data.get("role") != "Vasitəçi":
        return
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO agents (chat_id, role, phone, name)
            VALUES (?, ?, ?, ?)
        """,
            (
                data.get("chat_id"),
                data.get("role"),
                data.get("phone"),
                data.get("contact_name"),
            ),
        )
        conn.commit()
        conn.close()
    except:
        pass


def add_listing_new(data: dict) -> int:
    conn = get_local_conn()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO listings_new (
            date_added, chat_id, role, prop_type, operation,
            rayon, metro, rooms, area_kvm, price, currency,
            phone, contact_name, summary, link, approved
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """,
        (
            datetime.utcnow().date().isoformat(),
            data.get("chat_id"),
            data.get("role"),
            data.get("prop_type"),
            data.get("operation"),
            data.get("rayon"),
            data.get("metro"),
            data.get("rooms"),
            data.get("area_kvm"),
            data.get("price"),
            data.get("currency"),
            data.get("phone"),
            data.get("contact_name"),
            data.get("summary"),
            data.get("link", ""),
        ),
    )

    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 🔥 Pylance üçün 100% ziplənmiş fix
    if new_id is None:
        return 0  # və ya raise Exception, amma 0 normaldır

    return new_id


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "link")
def step_link(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    st = user_state[chat_id]
    txt = message.text.strip()

    if txt.startswith("http"):
        st["link"] = txt
    elif txt != "Link yoxdur, elanı göndər ✅":
        bot.send_message(chat_id, "Düzgün link yazın və ya uyğun düyməni seçin.")
        return

    st["chat_id"] = chat_id
    save_agent_if_needed(st)
    new_id = add_listing_new(st)

    # Adminə preview
    preview = (
        f"📢 *Yeni elan (gözləmədə)*\n\n"
        f"👤 {st['role']}\n"
        f"🏠 {st['prop_type']} | {st['rooms']}\n"
        f"💸 {st['operation']} | 💰 {format_price(st['price'])} {st['currency']}\n"
        f"📍 {st['rayon']} — {st['metro']}\n"
        f"📞 {st['phone']} ({st['contact_name']})\n"
        f"🧾 {st['summary']}"
    )
    if st.get("link"):
        preview += f"\n🔗 {st['link']}"

    try:
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"aprv|{new_id}"),
            types.InlineKeyboardButton("❌ Sil", callback_data=f"del|{new_id}"),
        )
        bot.send_message(ADMIN_ID, preview, parse_mode="Markdown", reply_markup=mk)
    except:
        pass

    bot.send_message(
        chat_id,
        "✅ Elanınız əlavə olundu və admin təsdiqini gözləyir.",
    )
    reset_user_state(chat_id)


# =============== 📋 ELANLARIM ===============


@bot.message_handler(func=lambda m: m.text == "📋 Elanlarım")
def my_listings(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM listings_new
        WHERE chat_id=?
        ORDER BY id DESC
    """,
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "Sizin göndərdiyiniz elan yoxdur.")
        return

    bot.send_message(chat_id, "📋 Sizin elanlarınız:")
    for r in rows:
        ev = dict(r)
        status = "✅ Təsdiqlənib" if ev.get("approved") else "⏳ Gözləmədə"
        txt = (
            f"{status}\n"
            f"🏠 {ev.get('prop_type','-')} | {ev.get('rooms','-')}\n"
            f"💸 {ev.get('operation','-')} | 💰 {format_price(ev.get('price'))} {ev.get('currency','AZN')}\n"
            f"📍 {ev.get('rayon','-')} — {ev.get('metro','')}\n"
            f"🧾 {ev.get('summary','-')}"
        )
        if ev.get("link"):
            txt += f"\n🔗 {ev['link']}"
        bot.send_message(chat_id, txt)


# =============== ⭐ FAVORİLƏRİM ===============


@bot.message_handler(func=lambda m: m.text == "⭐ Favorilərim")
def show_favorites(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    reset_search_state(chat_id)
    send_paginated_results(chat_id, "favorites", params={}, page=1)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav|"))
def cb_add_favorite(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    _, src, sid = c.data.split("|")
    lid = int(sid)
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO favorites (chat_id, listing_id, source, added_at)
        VALUES (?, ?, ?, ?)
    """,
        (chat_id, lid, src, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    record_favorite_price(src, lid)
    bot.answer_callback_query(c.id, "⭐ Favoriyə əlavə olundu.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("favdel|"))
def cb_remove_favorite(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    try:
        _, src, sid = c.data.split("|")
    except ValueError:
        return
    try:
        lid = int(sid)
    except Exception:
        lid = sid

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM favorites WHERE chat_id=? AND listing_id=? AND source=?",
        (chat_id, lid, src),
    )
    conn.commit()
    conn.close()

    try:
        bot.answer_callback_query(c.id, "⭐ Elan favoritlərdən çıxarıldı.")
    except Exception:
        pass

    st = search_state.get(chat_id, {})
    if st.get("mode") == "favorites":
        page = st.get("page", 1)
        send_paginated_results(
            chat_id,
            mode="favorites",
            params={},
            page=page,
            show_summary=False,
        )




# =============== 📌 ELAN STATUSLARI ===============


def status_label(code: str) -> str:
    return {
        "sold": "✅ Satılıb",
        "rented": "✅ Kirayə verilib",
        "blacklisted": "⛔ Qara siyahıda",
        "active": "Aktiv",
    }.get(code, code)


@bot.callback_query_handler(func=lambda c: c.data.startswith("st|"))
def cb_listing_status(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split("|")
    if len(parts) < 4:
        return
    action, source, lid = parts[1], parts[2], parts[3]
    try:
        lid_int = int(lid)
    except Exception:
        return

    new_status = {
        "sold": "sold",
        "rent": "rented",
        "blk": "blacklisted",
        "undo": "active",
    }.get(action)

    if not new_status:
        return

    update_listing_status(source, lid_int, new_status)
    try:
        bot.answer_callback_query(c.id, f"Status: {status_label(new_status)}")
    except Exception:
        pass
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id)
    except Exception:
        pass


def show_status_bucket(chat_id, status_code: str, title: str, undo_label: str):
    reset_search_state(chat_id)
    params = {"status": status_code, "undo_label": undo_label, "title": title}
    send_paginated_results(
        chat_id,
        mode="statuslist",
        params=params,
        page=1,
    )


def status_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Satılan elanlar", "🏢 Kirayə verilən elanlar")
    kb.row("⛔ Qara siyahı")
    kb.row("⬅️ Geri")
    return kb


@bot.message_handler(func=lambda m: m.text == "📂 Elan statusları")
def open_status_menu(message):
    if not ensure_allowed(message):
        return
    bot.send_message(
        message.chat.id,
        "📂 Elan statuslarını seçin:",
        reply_markup=status_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "🏠 Satılan elanlar")
def show_sold_list(message):
    if not ensure_allowed(message):
        return
    show_status_bucket(message.chat.id, "sold", "✅ Satılan elanlar:", "🔄 Geri qaytar")


@bot.message_handler(func=lambda m: m.text == "🏢 Kirayə verilən elanlar")
def show_rented_list(message):
    if not ensure_allowed(message):
        return
    show_status_bucket(
        message.chat.id,
        "rented",
        "✅ Kirayə verilən elanlar:",
        "🔄 Geri qaytar",
    )


@bot.message_handler(func=lambda m: m.text == "⛔ Qara siyahı")
def show_blacklist(message):
    if not ensure_allowed(message):
        return
    show_status_bucket(
        message.chat.id,
        "blacklisted",
        "⛔ Qara siyahıdakı elanlar:",
        "🔄 Geri qaytar",
    )


@bot.message_handler(func=lambda m: m.text == "⬅️ Geri")
def status_back_to_main(message):
    if not ensure_allowed(message):
        return
    send_main_menu(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "🔥 Ən çox baxılan elanlar")
def show_top_viewed(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    reset_search_state(chat_id)
    send_paginated_results(chat_id, "topviews", params={"days": 7}, page=1)

# =============== 🔎 AXTARIŞ SİSTEMİ ===============


@bot.message_handler(func=lambda m: m.text == "🔎 Axtarış sistemi")
def search_system_menu(message):
    if not ensure_allowed(message):
        return
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("📋 Filtrlə axtar", callback_data="ss|structured")
    )
    mk.add(
        types.InlineKeyboardButton("🔍 Açar sözlə axtar", callback_data="ss|keyword")
    )
    mk.add(types.InlineKeyboardButton("☎️ Nömrə ilə axtar", callback_data="ss|phone"))
    bot.send_message(
        message.chat.id,
        "🔎 Axtarış metodunu seçin:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("ss|"))
def cb_search_select(c):
    if not ensure_allowed_cb(c):
        return
    mode = c.data.split("|")[1]
    chat_id = c.message.chat.id

    if mode == "structured":
        if not check_limit(chat_id, "structured", 200):
            bot.answer_callback_query(
                c.id, "Günlük filtrli axtarış limitiniz bitib.", show_alert=True
            )
            return
        send_structured_start(chat_id, c.message)

    elif mode == "keyword":
        if not check_limit(chat_id, "keyword", 30):
            bot.answer_callback_query(
                c.id, "Günlük açar sözlə axtarış limitiniz bitib.", show_alert=True
            )
            return
        search_state[chat_id] = {"mode": "keyword", "operation": None}
        send_keyword_operation_prompt(chat_id)

    elif mode == "smart":
        if not check_limit(chat_id, "smart", 30):
            bot.answer_callback_query(
                c.id, "Günlük ağıllı axtarış limitiniz bitib.", show_alert=True
            )
            return
        search_state[chat_id] = {"mode": "smart"}
        msg = bot.send_message(
            chat_id,
            "🔥 Sorğunu yazın (məs: *3 otaq yasamal kirayə 800-1200*):",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, smart_search_handler)

    elif mode == "phone":
        if not check_limit(chat_id, "phone", 50):
            bot.answer_callback_query(
                c.id, "Günlük nömrə ilə axtarış limitiniz bitib.", show_alert=True
            )
            return
        msg = bot.send_message(chat_id, "☎️ Axtarmaq istədiyiniz nömrəni yazın:")
        bot.register_next_step_handler(msg, phone_search_handler)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("save_search|"))
def cb_save_search(c):
    if not ensure_allowed_cb(c):
        return
    action = c.data.split("|", 1)[1]
    chat_id = c.message.chat.id
    st = search_state.get(chat_id, {})
    params = st.get("pending_save")

    if action == "yes" and params:
        if save_search(chat_id, params):
            bot.send_message(
                chat_id,
                "✅ Axtarış yadda saxlanıldı. Yeni elan olduqda xəbər veriləcək.",
            )
        st.pop("pending_save", None)
    else:
        st.pop("pending_save", None)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def send_keyword_operation_prompt(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("🏠 Satılır", callback_data="kwop|sale"),
        types.InlineKeyboardButton("🏢 Kirayə verilir", callback_data="kwop|rent"),
    )
    bot.send_message(
        chat_id,
        "Əməliyyat növünü seçin və sonra açar sözü yazın:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("kwop|"))
def cb_keyword_operation(c):
    if not ensure_allowed_cb(c):
        return
    action = c.data.split("|")[1]
    chat_id = c.message.chat.id

    if action == "back":
        search_state[chat_id] = {"mode": "keyword", "operation": None}
        send_keyword_operation_prompt(chat_id)
        try:
            bot.answer_callback_query(c.id)
        except:
            pass
        return

    if action not in ("sale", "rent"):
        try:
            bot.answer_callback_query(c.id, "Naməlum seçim")
        except:
            pass
        return

    st = search_state.get(chat_id, {})
    st.update({"mode": "keyword", "operation": normalize_operation_value(action) or action})
    search_state[chat_id] = st

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kwop|back"))

    msg = bot.send_message(
        chat_id,
        "🔍 Açar söz və ya bir neçə söz yazın (məs: *yasamal 3 otaqlı 600 azn*):",
        parse_mode="Markdown",
        reply_markup=mk,
    )
    bot.register_next_step_handler(msg, keyword_search_handler)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


# ===== FİLTRLİ AXTARIŞ (STRUCTURED) =====

OP_CODES = {
    "all": None,
    "sat": ["satılır", "satış"],
    "kir": ["kirayə verilir", "kirayə", "icarə"],
}

PROP_TYPES = {
    "all": None,
    "m": "mənzil",
    "f": "fərdi yaşayış evi",
    "q": "qeyri-yaşayış sahəsi",
    "b": "bağ evi",
    "t": "torpaq",
}

RAYON_GROUPS = {
    "all": None,
    "bak": [
        "baki",
        "bakı",
        "yasamal",
        "xetai",
        "xətai",
        "nizami",
        "sabail",
        "səbail",
        "bineqedi",
        "binəqədi",
        "nerimanov",
        "nərimanov",
        "nasimi",
        "nəsimi",
        "sabuncu",
        "sabunçu",
        "suraxani",
        "suraxanı",
        "xezər",
        "xəzər",
        "qaradag",
        "qaradağ",
        "pirallahi",
        "ehmedli",
        "əhmədli",
        "xalqlar",
        "28 may",
        "elmler",
        "elmlər",
        "memar əcəmi",
        "20 yanvar",
        "inşaatçılar",
        "bakixanov",
        "hövsan",
        "biləcəri",
        "bileceri",
        "buzovna",
        "mastaga",
        "maştağa",
        "ramana",
    ],
    "abs": [
        "abşeron",
        "absheron",
        "xırdalan",
        "xirdalan",
        "masazır",
        "masazir",
        "mehdiabad",
        "saray",
        "novxanı",
        "novxani",
        "fatmayı",
        "fatmayi",
        "hökməli",
        "hokmeli",
        "qobu",
        "güzdək",
        "guzdek",
        "ceyranbatan",
    ],
    "sum": [
        "sumqayıt",
        "sumqayit",
    ],
}


def decode_price_range(code: str):
    # Kirayə
    if code == "k1":
        return 0, 500
    if code == "k2":
        return 520, 1000
    if code == "k3":
        return 1050, 1500
    if code == "k4":
        return 1550, 2000
    if code == "k5":
        return 2000, None
    # Satılır
    if code == "s0":
        return None, None
    if code == "s1":
        return 0, 50000
    if code == "s2":
        return 50000, 100000
    if code == "s3":
        return 100000, 200000
    if code == "s4":
        return 200000, None
    return None, None


def build_filters_sql(
    op_code, prop_code, rayon_group, min_price=None, max_price=None, mode="main"
):
    sql = " WHERE 1=1"
    params = []

    # Əməliyyat
    op_kws = OP_CODES.get(op_code)
    if op_kws:
        conds = []
        for kw in op_kws:
            conds.append("LOWER(operation) LIKE ?")
            params.append(f"%{kw}%")
        sql += " AND (" + " OR ".join(conds) + ")"

    # Əmlak tipi
    prop_kw = PROP_TYPES.get(prop_code)
    if prop_kw:
        sql += " AND LOWER(prop_type) LIKE ?"
        params.append(f"%{prop_kw}%")

    # Rayon qrupu
    kws = RAYON_GROUPS.get(rayon_group)
    if kws:
        conds = []
        for kw in kws:
            like = f"%{kw}%"
            if mode == "main":
                conds.append("LOWER(COALESCE(address,'')) LIKE ?")
                conds.append("LOWER(COALESCE(summary,'')) LIKE ?")
            else:  # local
                conds.append("LOWER(COALESCE(rayon,'')) LIKE ?")
                conds.append("LOWER(COALESCE(summary,'')) LIKE ?")
            params.extend([like, like])
        sql += " AND (" + " OR ".join(conds) + ")"

    # Qiymət
    if min_price is not None:
        sql += " AND CAST(REPLACE(REPLACE(price, ',', ''), ' ', '') AS INTEGER) >= ?"
        params.append(min_price)
    if max_price is not None:
        sql += " AND CAST(REPLACE(REPLACE(price, ',', ''), ' ', '') AS INTEGER) <= ?"
        params.append(max_price)

    return sql, params



BAKU_RAYONS = [
    "Binəqədi",
    "Qaradağ",
    "Xəzər",
    "Səbail",
    "Sabunçu",
    "Suraxanı",
    "Nərimanov",
    "Nəsimi",
    "Nizami",
    "Pirallahı",
    "Xətai",
    "Yasamal",
]
ABS_RAYONS = ["Xırdalan", "Abşeron", "Masazır", "Digər"]
SUM_RAYONS = ["Sumqayıt"]
ALL_RAYONS = sorted(set(BAKU_RAYONS + ABS_RAYONS + SUM_RAYONS + ["Digər"]))
REGION_OPTIONS = {
    "bak": {"title": "Bakı rayonları", "rayons": BAKU_RAYONS},
    "abs": {"title": "Abşeron", "rayons": ABS_RAYONS},
    "sum": {"title": "Sumqayıt", "rayons": SUM_RAYONS},
    "all": {"title": "Bütün ərazilər", "rayons": ALL_RAYONS},
}
ROOM_CODES = [("1", "r1"), ("2", "r2"), ("3", "r3"), ("4", "r4"), ("5+", "r5"), ("Hamısı", "r0")]
FLOOR_PRESETS = {"f13": (1, 3), "f49": (4, 9), "f10": (10, None), "fall": None}


def structured_send(chat_id, message, text, markup):
    if message:
        try:
            bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=message.message_id, reply_markup=markup
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=markup)


def structured_push_history(chat_id):
    st = search_state.setdefault(chat_id, {})
    hist = st.setdefault("history", [])
    hist.append({"step": st.get("step"), "filters": dict(st.get("filters", {}))})


def parse_number(val):
    try:
        return int(str(val))
    except Exception:
        pass
    import re as _re

    nums = _re.findall(r"\d+", str(val or ""))
    return int(nums[0]) if nums else None


def parse_floor_value(ev: dict):
    for k in ("floor", "Floor", "Mertebe", "mertebe"):
        if ev.get(k):
            num = parse_number(ev.get(k))
            if num is not None:
                return num
    text = (ev.get("summary") or ev.get("Umumi_melumat") or "")
    num = parse_number(text)
    return num


def matches_region_rayon(ev: dict, filters: dict) -> bool:
    region = filters.get("region") or "all"
    rayon = filters.get("rayon")
    text_block = " ".join(
        [
            str(ev.get("rayon") or ""),
            str(ev.get("Rayon_Qesebe") or ""),
            str(ev.get("address") or ""),
            str(ev.get("Unvan") or ""),
            str(ev.get("summary") or ""),
        ]
    ).lower()

    if rayon and rayon != "all":
        return rayon.lower() in text_block

    region_rayons = REGION_OPTIONS.get(region, {}).get("rayons", [])
    if region != "all":
        return any(r.lower() in text_block for r in region_rayons)
    return True


def matches_rooms(ev: dict, room_code: str) -> bool:
    if not room_code or room_code == "r0":
        return True
    room_val = parse_number(ev.get("rooms") or ev.get("Otaq_sayi"))
    if room_val is None:
        return True
    if room_code == "r5":
        return room_val >= 5
    try:
        desired = int(room_code.replace("r", ""))
        return room_val == desired
    except Exception:
        return True


def matches_floor(ev: dict, floor_range):
    if not floor_range:
        return True
    floor_val = parse_floor_value(ev)
    if floor_val is None:
        return True
    mn, mx = floor_range
    if mn is not None and floor_val < mn:
        return False
    if mx is not None and floor_val > mx:
        return False
    return True


def is_listing_active(ev: dict, status_map: dict) -> bool:
    src = ev.get("__source", "main")
    lid = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
    try:
        lid = int(str(lid))
    except Exception:
        return True
    status = status_map.get((src, lid), "active")
    return status not in {"sold", "rented", "blacklisted"}


def _listing_price_value(ev: dict):
    return parse_number(ev.get("price") or ev.get("Qiymet"))


def matches_saved_search(ev: dict, saved: dict, status_map: dict) -> bool:
    if not is_listing_active(ev, status_map):
        return False

    op_filter = normalize_operation_value(saved.get("operation"))
    ev_op = normalize_operation_value(ev.get("operation") or ev.get("Emeliyyat"))
    if op_filter and op_filter != ev_op:
        return False

    rooms_filter = saved.get("rooms")
    if rooms_filter:
        ev_room = parse_number(ev.get("rooms") or ev.get("Otaq_sayi"))
        if ev_room is None or ev_room != rooms_filter:
            return False

    price_val = _listing_price_value(ev)
    if saved.get("price_min") is not None:
        if price_val is None or price_val < saved.get("price_min"):
            return False
    if saved.get("price_max") is not None:
        if price_val is None or price_val > saved.get("price_max"):
            return False

    rayon_filter = saved.get("rayon")
    if rayon_filter:
        text_block = " ".join(
            [
                str(ev.get("rayon") or ""),
                str(ev.get("Rayon_Qesebe") or ""),
                str(ev.get("address") or ""),
                str(ev.get("Unvan") or ""),
                str(ev.get("summary") or ""),
            ]
        ).lower()
        if rayon_filter.lower() not in text_block:
            return False

    prop_filter = saved.get("prop_type")
    if prop_filter:
        prop_text = str(ev.get("prop_type") or ev.get("Emlakin_novu") or "").lower()
        if prop_filter.lower() not in prop_text:
            return False

    return True


def load_recent_listings(since_dt: datetime):
    results = []
    since_dt = since_dt or datetime.min

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM listings ORDER BY date_read DESC, id DESC LIMIT 800")
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            if safe_date(d) > since_dt:
                results.append(d)
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM listings_approved ORDER BY date_added DESC, id DESC LIMIT 300"
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        if safe_date(d) > since_dt:
            results.append(d)
    conn.close()

    return results


def process_saved_search_notifications():
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM saved_searches")
    searches = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not searches:
        return

    status_map = get_status_map()
    now_iso = datetime.utcnow().isoformat()

    for s in searches:
        since_raw = s.get("last_notified_at") or s.get("created_at")
        try:
            since_dt = datetime.fromisoformat(str(since_raw)) if since_raw else datetime.min
        except Exception:
            since_dt = datetime.min

        candidates = load_recent_listings(since_dt)
        matches = [ev for ev in candidates if matches_saved_search(ev, s, status_map)]

        if not matches:
            continue

        try:
            bot.send_message(
                s["chat_id"],
                "🔔 Axtardığınız kriteriyalara uyğun YENİ ELAN tapıldı",
            )
            for ev in matches[:3]:
                send_listing_card(
                    s["chat_id"],
                    ev,
                    source=ev.get("__source", "main"),
                    with_fav_button=True,
                )
        except Exception as e:
            print("⚠️ Notification send error:", e)

        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE saved_searches SET last_notified_at=? WHERE id=?",
            (now_iso, s.get("id")),
        )
        conn.commit()
        conn.close()


def saved_search_worker():
    while True:
        try:
            process_saved_search_notifications()
        except Exception as e:
            print("⚠️ Saved search worker error:", e)
        time.sleep(3600)


def check_favorite_price_drops():
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.chat_id, f.listing_id, f.source, p.last_price
        FROM favorites f
        LEFT JOIN favorite_price_history p
            ON p.source = f.source AND p.listing_id = f.listing_id
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return

    for row in rows:
        src = row["source"]
        lid = row["listing_id"]
        chat_id = row["chat_id"]
        last_price = row["last_price"]

        ev = fetch_listing_by_source(src, lid)
        if not ev:
            continue

        current_price = get_listing_price(ev)
        if current_price is None:
            continue

        currency = ev.get("currency") or "AZN"

        if last_price is None:
            upsert_favorite_price(src, lid, current_price)
            continue

        if current_price < last_price:
            try:
                msg = (
                    "🔔 Favorit etdiyiniz elanın qiyməti düşdü!\n\n"
                    f"📉 Köhnə qiymət: {format_price(last_price)} {currency}\n"
                    f"📉 Yeni qiymət: {format_price(current_price)} {currency}"
                )
                bot.send_message(chat_id, msg)
                ev["__price_drop"] = True
                send_listing_card(
                    chat_id,
                    ev,
                    source=src,
                    with_fav_button=True,
                    status_controls=False,
                )
            except Exception as e:
                print("⚠️ Favorite price notification error:", e)

        if last_price != current_price:
            upsert_favorite_price(src, lid, current_price)


def favorite_price_worker():
    while True:
        try:
            check_favorite_price_drops()
        except Exception as e:
            print("⚠️ Favorite price worker error:", e)
        time.sleep(3600)


def query_structured_results(filters: dict, offset: int = 0, limit: int = None):
    op_code = filters.get("op", "all")
    prop_code = filters.get("prop", "all")
    price_code = filters.get("price", "s0")
    min_p, max_p = decode_price_range(price_code)
    results = []

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        base = "SELECT * FROM listings"
        flt, params = build_filters_sql(
            op_code, prop_code, None, min_price=min_p, max_price=max_p, mode="main"
        )
        cur.execute(base + flt + " ORDER BY date_read DESC, id DESC", params)
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    base = "SELECT * FROM listings_approved"
    flt, params = build_filters_sql(
        op_code, prop_code, None, min_price=min_p, max_price=max_p, mode="local"
    )
    cur.execute(base + flt + " ORDER BY date_added DESC, id DESC", params)
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    status_map = get_status_map()
    filtered = []
    for ev in results:
        if not is_listing_active(ev, status_map):
            continue
        if not matches_region_rayon(ev, filters):
            continue
        if not matches_rooms(ev, filters.get("rooms")):
            continue
        if not matches_floor(ev, filters.get("floor_range")):
            continue
        filtered.append(ev)

    filtered.sort(key=safe_date, reverse=True)
    total = len(filtered)
    if limit is not None:
        filtered = filtered[offset : offset + limit]
    return filtered, total


def is_fts_ready(conn, table_name: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        )
        if not cur.fetchone():
            return False
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        return (cur.fetchone() or [0])[0] > 0
    except Exception:
        return False


def build_fts_match(words: list) -> str:
    tokens = []
    for w in words:
        t = str(w).strip()
        if not t:
            continue
        t = t.replace("'", " ").replace("\"", " ")
        tokens.append(f"{t}*")
    return " ".join(tokens)


def query_keyword_results(selected_op: str, words: list, offset: int = 0, limit: int = None):
    if not words:
        return [], 0

    words = [w.lower() for w in words if w]
    op_main = detect_db_operation_value(selected_op, "main")
    op_local = detect_db_operation_value(selected_op, "local")

    def build_multi_like_sql(fields):
        sql_parts = []
        params = []
        for w in words:
            part = "(" + " OR ".join([f"LOWER({f}) LIKE ?" for f in fields]) + ")"
            sql_parts.append(part)
            like = f"%{w}%"
            params.extend([like] * len(fields))
        sql = " AND ".join(sql_parts)
        return sql, params

    FIELDS_MAIN = ["prop_type", "operation", "metro", "rooms", "address", "summary", "contact_name"]
    FIELDS_LOCAL = ["prop_type", "operation", "metro", "rooms", "rayon", "summary", "contact_name"]

    results = []

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        if is_fts_ready(conn, "listings_fts"):
            match_q = build_fts_match(words)
            cur.execute(
                """
                SELECT l.* FROM listings l
                JOIN listings_fts f ON l.id = f.rowid
                WHERE l.operation = ? AND f MATCH ?
                ORDER BY l.date_read DESC LIMIT 5000
                """,
                (op_main, match_q),
            )
        else:
            sql_where, params = build_multi_like_sql(FIELDS_MAIN)
            sql = (
                "SELECT * FROM listings WHERE operation = ? AND "
                + sql_where
                + " ORDER BY date_read DESC LIMIT 5000"
            )
            cur.execute(sql, [op_main] + params)
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    if is_fts_ready(conn, "local_listings_fts"):
        match_q = build_fts_match(words)
        cur.execute(
            """
            SELECT l.* FROM listings_approved l
            JOIN local_listings_fts f ON l.id = f.rowid
            WHERE l.operation = ? AND f MATCH ?
            ORDER BY l.date_added DESC LIMIT 5000
            """,
            (op_local, match_q),
        )
    else:
        sql_where, params = build_multi_like_sql(FIELDS_LOCAL)
        sql = (
            "SELECT * FROM listings_approved WHERE operation = ? AND "
            + sql_where
            + " ORDER BY date_added DESC LIMIT 5000"
        )
        cur.execute(sql, [op_local] + params)
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    status_map = get_status_map()
    results = [r for r in results if is_listing_active(r, status_map)]
    results.sort(key=safe_date, reverse=True)
    total = len(results)
    if limit is not None:
        results = results[offset : offset + limit]
    return results, total


def parse_smart_query(text: str) -> dict:
    res = {
        "operation": None,
        "rooms": None,
        "price_min": None,
        "price_max": None,
        "keywords": [],
    }

    if not text:
        return res

    lowered = text.lower()
    tokens = lowered.split()
    used_tokens = set()

    # əməliyyat
    for idx, tok in enumerate(tokens):
        op = normalize_operation_value(tok)
        if op and not res["operation"]:
            res["operation"] = op
            used_tokens.add(idx)

    # otaq sayı
    room_match = re.search(r"(\d+)\s*(otaq|otaqli|otaqlı)", lowered)
    if room_match:
        try:
            res["rooms"] = int(room_match.group(1))
        except Exception:
            pass

    # qiymət
    range_match = re.search(r"(\d+[\.,]?\d*)\s*-\s*(\d+[\.,]?\d*)", lowered)
    if range_match:
        try:
            res["price_min"] = int(float(range_match.group(1).replace(",", "").replace(".", "")))
            res["price_max"] = int(float(range_match.group(2).replace(",", "").replace(".", "")))
        except Exception:
            pass
    else:
        numbers = [
            int(float(n.replace(",", "").replace(".", "")))
            for n in re.findall(r"\b\d+[\.,]?\d*\b", lowered)
        ]
        for n in numbers:
            if res["rooms"] is None and n <= 10:
                res["rooms"] = n
                continue
            if n > 10 and res["price_max"] is None:
                res["price_max"] = n
                break

    # otaq tokenləri (nömrə + otaq) istifadədə kimi işarələ
    for idx, tok in enumerate(tokens):
        if re.match(r"\d+", tok) and idx + 1 < len(tokens):
            nxt = tokens[idx + 1]
            if nxt.startswith("otaq"):
                used_tokens.update({idx, idx + 1})

    # keywords
    keywords = []
    for idx, tok in enumerate(tokens):
        if idx in used_tokens:
            continue
        if any(ch.isdigit() for ch in tok):
            continue
        if tok.startswith("otaq"):
            continue
        if normalize_operation_value(tok):
            continue
        keywords.append(tok)

    res["keywords"] = keywords
    return res


def query_smart_results(criteria: dict, offset: int = 0, limit: int = None):
    keywords = [w.lower() for w in criteria.get("keywords", []) if w]
    op_norm = normalize_operation_value(criteria.get("operation"))
    op_main = detect_db_operation_value(op_norm, "main") if op_norm else None
    op_local = detect_db_operation_value(op_norm, "local") if op_norm else None
    price_min = criteria.get("price_min")
    price_max = criteria.get("price_max")
    room_num = criteria.get("rooms")

    def build_multi_like_sql(words_local, fields):
        if not words_local:
            return "", []
        sql_parts = []
        params = []
        for w in words_local:
            part = "(" + " OR ".join([f"LOWER({f}) LIKE ?" for f in fields]) + ")"
            sql_parts.append(part)
            like = f"%{w}%"
            params.extend([like] * len(fields))
        sql = " AND ".join(sql_parts)
        return sql, params

    def build_filters(op_value):
        clauses = ["1=1"]
        params = []
        if op_value:
            clauses.append("operation = ?")
            params.append(op_value)
        if price_min is not None:
            clauses.append(
                "CAST(REPLACE(REPLACE(price, ',', ''), ' ', '') AS INTEGER) >= ?"
            )
            params.append(price_min)
        if price_max is not None:
            clauses.append(
                "CAST(REPLACE(REPLACE(price, ',', ''), ' ', '') AS INTEGER) <= ?"
            )
            params.append(price_max)
        return " AND ".join(clauses), params

    def passes_room(ev: dict) -> bool:
        if not room_num:
            return True
        val = parse_number(ev.get("rooms") or ev.get("Otaq_sayi"))
        if val is None:
            return True
        try:
            return int(val) == int(room_num)
        except Exception:
            return True

    results = []

    # MAIN
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        where_clause, base_params = build_filters(op_main)
        if keywords and is_fts_ready(conn, "listings_fts"):
            match_q = build_fts_match(keywords)
            cur.execute(
                f"""
                SELECT l.* FROM listings l
                JOIN listings_fts f ON l.id = f.rowid
                WHERE {where_clause} AND f MATCH ?
                ORDER BY l.date_read DESC LIMIT 5000
                """,
                base_params + [match_q],
            )
        elif keywords:
            sql_where, kw_params = build_multi_like_sql(
                keywords, ["summary", "address", "metro", "rayon", "contact_name", "operation"]
            )
            cur.execute(
                f"SELECT * FROM listings WHERE {where_clause} AND {sql_where} "
                "ORDER BY date_read DESC LIMIT 5000",
                base_params + kw_params,
            )
        else:
            cur.execute(
                f"SELECT * FROM listings WHERE {where_clause} "
                "ORDER BY date_read DESC LIMIT 5000",
                base_params,
            )
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    # LOCAL
    conn = get_local_conn()
    cur = conn.cursor()
    where_clause, base_params = build_filters(op_local)
    if keywords and is_fts_ready(conn, "local_listings_fts"):
        match_q = build_fts_match(keywords)
        cur.execute(
            f"""
            SELECT l.* FROM listings_approved l
            JOIN local_listings_fts f ON l.id = f.rowid
            WHERE {where_clause} AND f MATCH ?
            ORDER BY l.date_added DESC LIMIT 5000
            """,
            base_params + [match_q],
        )
    elif keywords:
        sql_where, kw_params = build_multi_like_sql(
            keywords, ["summary", "rayon", "metro", "contact_name", "operation"]
        )
        cur.execute(
            f"SELECT * FROM listings_approved WHERE {where_clause} AND {sql_where} "
            "ORDER BY date_added DESC LIMIT 5000",
            base_params + kw_params,
        )
    else:
        cur.execute(
            f"SELECT * FROM listings_approved WHERE {where_clause} "
            "ORDER BY date_added DESC LIMIT 5000",
            base_params,
        )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    status_map = get_status_map()
    filtered = []
    for ev in results:
        if not is_listing_active(ev, status_map):
            continue
        if not passes_room(ev):
            continue
        filtered.append(ev)

    filtered.sort(key=safe_date, reverse=True)
    total = len(filtered)
    if limit is not None:
        filtered = filtered[offset : offset + limit]
    return filtered, total


def query_phone_results(raw: str, offset: int = 0, limit: int = None):
    like = f"%{raw}%"
    results = []

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM listings
            WHERE REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'+','') LIKE ?
            ORDER BY date_read DESC, id DESC
            LIMIT 2000
        """,
            (like,),
        )
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM listings_approved
        WHERE REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'+','') LIKE ?
        ORDER BY date_added DESC, id DESC
        LIMIT 2000
    """,
        (like,),
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    status_map = get_status_map()
    results = [r for r in results if is_listing_active(r, status_map)]
    results.sort(key=safe_date, reverse=True)
    total = len(results)
    if limit is not None:
        results = results[offset : offset + limit]
    return results, total


def query_favorites_page(chat_id: int, offset: int = 0, limit: int = None):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM favorites WHERE chat_id=?", (chat_id,)
    )
    total = cur.fetchone()[0]
    cur.execute(
        """
        SELECT listing_id, source FROM favorites
        WHERE chat_id=?
        ORDER BY added_at DESC
        LIMIT ? OFFSET ?
    """,
        (chat_id, limit or PAGE_SIZE, offset),
    )
    rows = cur.fetchall()
    conn.close()

    items = []
    for r in rows:
        ev = fetch_listing_by_source(r["source"], r["listing_id"])
        if ev:
            items.append({"data": ev, "source": r["source"]})
    return items, total


def query_status_page(status_code: str, offset: int = 0, limit: int = None):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM listing_status WHERE status=?",
        (status_code,),
    )
    total = cur.fetchone()[0]
    cur.execute(
        """
        SELECT source, listing_id FROM listing_status
        WHERE status=?
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
    """,
        (status_code, limit or PAGE_SIZE, offset),
    )
    rows = cur.fetchall()
    conn.close()

    items = []
    for r in rows:
        ev = fetch_listing_by_source(r["source"], r["listing_id"])
        if ev:
            items.append({"data": ev, "source": r["source"], "id": r["listing_id"]})
    return items, total


def fetch_page_results(chat_id: int, mode: str, params: dict, page: int):
    offset = (page - 1) * PAGE_SIZE
    if mode == "filter":
        filters = params.get("filters") or params
        return query_structured_results(filters, offset=offset, limit=PAGE_SIZE)
    if mode == "keyword":
        return query_keyword_results(
            params.get("operation"), params.get("words", []), offset=offset, limit=PAGE_SIZE
        )
    if mode == "smart":
        return query_smart_results(
            params.get("criteria", {}), offset=offset, limit=PAGE_SIZE
        )
    if mode == "phone":
        return query_phone_results(params.get("digits", ""), offset=offset, limit=PAGE_SIZE)
    if mode == "favorites":
        return query_favorites_page(chat_id, offset=offset, limit=PAGE_SIZE)
    if mode == "statuslist":
        return query_status_page(params.get("status", ""), offset=offset, limit=PAGE_SIZE)
    if mode == "topviews":
        return query_top_viewed_listings(
            days=params.get("days", 7), offset=offset, limit=PAGE_SIZE
        )
    return [], 0


def send_paginated_results(
    chat_id: int,
    mode: str,
    params: dict,
    page: int = 1,
    loading_ref=None,
    show_summary: bool = True,
):
    items, total = fetch_page_results(chat_id, mode, params, page)
    total_pages = compute_total_pages(total) if total else 1
    if page > total_pages:
        page = total_pages
        items, total = fetch_page_results(chat_id, mode, params, page)
    set_pagination_state(chat_id, mode, params, page, total_pages)

    if total == 0:
        if not replace_loading_message(loading_ref, "Siyahı boşdur."):
            bot.send_message(chat_id, "Siyahı boşdur.")
        return

    summary_map = {
        "filter": "🔍 Tapıldı",
        "keyword": "🔍 Tapıldı",
        "smart": "🔥 Tapıldı",
        "phone": "☎️ Bu nömrə ilə",
        "favorites": "⭐ Favorilər",
        "statuslist": params.get("title", "📂 Siyahı"),
        "topviews": "🔥 Ən çox baxılanlar",
    }

    if show_summary:
        prefix = summary_map.get(mode, "📄")
        summary_text = f"{prefix}: {total} elan. Səhifə {page}/{total_pages}" if mode != "favorites" else f"⭐ Favori elanlarınız ({total}): Səhifə {page}/{total_pages}"
        if not replace_loading_message(loading_ref, summary_text):
            bot.send_message(chat_id, summary_text)

    for item in items:
        if mode == "favorites":
            ev = item["data"]
            src = item.get("source", ev.get("__source", "main"))
            lid = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
            rm_btn = types.InlineKeyboardButton(
                "❌ Favoritdən çıxart", callback_data=f"favdel|{src}|{lid}"
            )
            send_listing_card(
                chat_id,
                ev,
                source=src,
                with_fav_button=False,
                status_controls=False,
                extra_buttons=[rm_btn],
            )
        elif mode == "statuslist":
            ev = item["data"]
            src = item.get("source", ev.get("__source", "main"))
            lid = item.get("id")
            undo_label = params.get("undo_label", "🔄 Geri qaytar")
            btn = types.InlineKeyboardButton(
                undo_label, callback_data=f"st|undo|{src}|{lid}"
            )
            send_listing_card(
                chat_id,
                ev,
                source=src,
                with_fav_button=True,
                status_controls=False,
                extra_buttons=[btn],
            )
        else:
            ev = item
            send_listing_card(
                chat_id,
                ev,
                source=ev.get("__source", "main"),
                with_fav_button=True,
            )

    nav = build_pagination_keyboard(page, total_pages)
    bot.send_message(chat_id, f"📄 Səhifə {page}/{total_pages}", reply_markup=nav)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pg:"))
def cb_pagination(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    st = search_state.get(chat_id)
    if not st:
        try:
            bot.answer_callback_query(c.id, "Səhifə tapılmadı.")
        except Exception:
            pass
        return

    action = c.data.split(":", 1)[1]
    if action == "noop":
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return

    current_page = st.get("page", 1)
    total_pages = st.get("total_pages", 1)
    mode = st.get("mode")
    params = st.get("params", {})

    target = current_page
    if action == "first":
        target = 1
    elif action == "prev":
        target = max(1, current_page - 1)
    elif action == "next":
        target = min(total_pages, current_page + 1)
    elif action == "last":
        target = total_pages

    if target != current_page and mode:
        send_paginated_results(
            chat_id, mode=mode, params=params, page=target, show_summary=False
        )

    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def render_op_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "op"
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("💸 Satılır", callback_data="fs|op|sat"),
        types.InlineKeyboardButton("🏢 Kirayə", callback_data="fs|op|kir"),
    )
    mk.add(types.InlineKeyboardButton("🌐 Hamısı", callback_data="fs|op|all"))
    mk.add(types.InlineKeyboardButton("❌ Bağla", callback_data="fs|cancel"))
    structured_send(chat_id, message, "🔍 Əməliyyat növünü seç:", mk)


def render_prop_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "prop"
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("Mənzil", callback_data="fs|tp|m"),
        types.InlineKeyboardButton("Fərdi ev", callback_data="fs|tp|f"),
    )
    mk.add(
        types.InlineKeyboardButton("Qeyri-yaşayış", callback_data="fs|tp|q"),
        types.InlineKeyboardButton("Bağ evi", callback_data="fs|tp|b"),
    )
    mk.add(
        types.InlineKeyboardButton("Torpaq", callback_data="fs|tp|t"),
        types.InlineKeyboardButton("Hamısı", callback_data="fs|tp|all"),
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "🏠 Əmlak tipini seç:", mk)


def render_region_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "region"
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Bütün ərazilər", callback_data="fs|rg|all"))
    mk.add(
        types.InlineKeyboardButton("Bakı", callback_data="fs|rg|bak"),
        types.InlineKeyboardButton("Abşeron", callback_data="fs|rg|abs"),
    )
    mk.add(types.InlineKeyboardButton("Sumqayıt", callback_data="fs|rg|sum"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "📍 Regionu seç:", mk)


def render_rayon_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "rayon"
    region = st.get("filters", {}).get("region", "all")
    rayons = REGION_OPTIONS.get(region, REGION_OPTIONS["all"])["rayons"]
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Hamısı", callback_data="fs|rn|all"))
    row = []
    for idx, rn in enumerate(rayons):
        row.append(types.InlineKeyboardButton(rn, callback_data=f"fs|rn|{idx}"))
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "📍 Rayon seçin:", mk)


def render_price_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "price"
    op = st.get("filters", {}).get("op", "sat")
    mk = types.InlineKeyboardMarkup()
    if op == "kir":
        mk.add(
            types.InlineKeyboardButton("0-500", callback_data="fs|pr|k1"),
            types.InlineKeyboardButton("520-1000", callback_data="fs|pr|k2"),
        )
        mk.add(
            types.InlineKeyboardButton("1050-1500", callback_data="fs|pr|k3"),
            types.InlineKeyboardButton("1550-2000", callback_data="fs|pr|k4"),
        )
        mk.add(types.InlineKeyboardButton("2000+", callback_data="fs|pr|k5"))
    else:
        mk.add(
            types.InlineKeyboardButton("Limitsiz", callback_data="fs|pr|s0"),
            types.InlineKeyboardButton("0-50,000", callback_data="fs|pr|s1"),
        )
        mk.add(
            types.InlineKeyboardButton("50,000-100,000", callback_data="fs|pr|s2"),
            types.InlineKeyboardButton("100,000-200,000", callback_data="fs|pr|s3"),
        )
        mk.add(types.InlineKeyboardButton("200,000+", callback_data="fs|pr|s4"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "💰 Qiymət aralığını seç:", mk)


def render_room_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "rooms"
    mk = types.InlineKeyboardMarkup()
    row = []
    for title, code in ROOM_CODES:
        row.append(types.InlineKeyboardButton(title, callback_data=f"fs|rm|{code}"))
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "🚪 Otaq sayını seç:", mk)


def render_floor_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "floor"
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("1-3", callback_data="fs|fl|f13"),
        types.InlineKeyboardButton("4-9", callback_data="fs|fl|f49"),
    )
    mk.add(types.InlineKeyboardButton("10+", callback_data="fs|fl|f10"))
    mk.add(types.InlineKeyboardButton("Limitsiz", callback_data="fs|fl|fall"))
    mk.add(types.InlineKeyboardButton("✏️ Manual interval", callback_data="fs|fm"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "🏢 Mərtəbə seçin:", mk)


def send_structured_start(chat_id, message=None):
    reset_search_state(chat_id)
    search_state[chat_id] = {
        "mode": "structured",
        "filters": {},
        "history": [],
        "awaiting_floor_range": False,
        "step": "op",
    }
    render_op_step(chat_id, message)


def structured_go_back(chat_id, message=None):
    st = search_state.get(chat_id, {})
    hist = st.get("history", [])
    if not hist:
        reset_search_state(chat_id)
        if message:
            try:
                bot.edit_message_text(
                    "❌ Filtr ləğv edildi.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            except Exception:
                bot.send_message(chat_id, "❌ Filtr ləğv edildi.")
        else:
            bot.send_message(chat_id, "❌ Filtr ləğv edildi.")
        return

    prev = hist.pop()
    st["filters"] = prev.get("filters", {})
    st["step"] = prev.get("step")
    step = st["step"]
    if step == "op":
        render_op_step(chat_id, message)
    elif step == "prop":
        render_prop_step(chat_id, message)
    elif step == "region":
        render_region_step(chat_id, message)
    elif step == "rayon":
        render_rayon_step(chat_id, message)
    elif step == "price":
        render_price_step(chat_id, message)
    elif step == "rooms":
        render_room_step(chat_id, message)
    else:
        render_floor_step(chat_id, message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fs|"))
def cb_structured(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split("|")
    action = parts[1]
    chat_id = c.message.chat.id
    st = search_state.setdefault(chat_id, {
        "mode": "structured",
        "filters": {},
        "history": [],
        "awaiting_floor_range": False,
    })

    if action == "cancel":
        reset_search_state(chat_id)
        try:
            bot.edit_message_text(
                "❌ Filtr ləğv edildi.",
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
            )
        except Exception:
            bot.send_message(chat_id, "❌ Filtr ləğv edildi.")
        return

    if action == "bk":
        structured_go_back(chat_id, c.message)
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return

    if st.get("mode") != "structured":
        send_structured_start(chat_id, c.message)

    if action == "op":
        st.setdefault("filters", {})["op"] = parts[2]
        structured_push_history(chat_id)
        render_prop_step(chat_id, c.message)
    elif action == "tp":
        st.setdefault("filters", {})["prop"] = parts[2]
        structured_push_history(chat_id)
        render_region_step(chat_id, c.message)
    elif action == "rg":
        st.setdefault("filters", {})["region"] = parts[2]
        structured_push_history(chat_id)
        render_rayon_step(chat_id, c.message)
    elif action == "rn":
        val = parts[2]
        region = st.get("filters", {}).get("region", "all")
        rayons = REGION_OPTIONS.get(region, REGION_OPTIONS["all"])["rayons"]
        if val == "all":
            st.setdefault("filters", {})["rayon"] = "all"
        else:
            try:
                idx = int(val)
                st.setdefault("filters", {})["rayon"] = rayons[idx]
            except Exception:
                st.setdefault("filters", {})["rayon"] = "all"
        structured_push_history(chat_id)
        render_price_step(chat_id, c.message)
    elif action == "pr":
        st.setdefault("filters", {})["price"] = parts[2]
        structured_push_history(chat_id)
        render_room_step(chat_id, c.message)
    elif action == "rm":
        st.setdefault("filters", {})["rooms"] = parts[2]
        structured_push_history(chat_id)
        render_floor_step(chat_id, c.message)
    elif action == "fl":
        st.setdefault("filters", {})["floor_range"] = FLOOR_PRESETS.get(parts[2])
        perform_structured_search(
            chat_id,
            offset=0,
            edit_msg=(c.message.chat.id, c.message.message_id),
        )
    elif action == "fm":
        structured_push_history(chat_id)
        st["awaiting_floor_range"] = True
        st["step"] = "floor_manual"
        bot.send_message(chat_id, "✏️ Mərtəbə intervalı yazın (məs: 1-3):")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.message_handler(func=lambda m: search_state.get(m.chat.id, {}).get("awaiting_floor_range"))
def handle_floor_range_input(message):
    chat_id = message.chat.id
    st = search_state.get(chat_id, {})
    txt = (message.text or "").strip()
    import re as _re

    if not st:
        return

    if not _re.match(r"^\d+\s*-\s*\d+$", txt):
        bot.send_message(chat_id, "❌ Düzgün format: 1-3, 4-9 və s.")
        return

    parts = _re.split(r"-", txt)
    try:
        mn = int(parts[0].strip())
        mx = int(parts[1].strip())
    except Exception:
        bot.send_message(chat_id, "❌ Rəqəm yazın (məs: 1-5)")
        return

    st.setdefault("filters", {})["floor_range"] = (mn, mx)
    st["awaiting_floor_range"] = False
    perform_structured_search(chat_id, offset=0, edit_msg=None)


def perform_structured_search(chat_id, offset=0, edit_msg=None):
    st = search_state.get(chat_id)
    if not st or st.get("mode") != "structured":
        bot.send_message(chat_id, "Sessiya tapılmadı. Yenidən başlayın.")
        return

    filters = st.get("filters", {})
    loading_ref = show_loading_message(chat_id, edit_msg)

    op_code = filters.get("op")
    op_norm = None
    if op_code == "sat":
        op_norm = "sale"
    elif op_code == "kir":
        op_norm = "rent"
    elif op_code:
        op_norm = normalize_operation_value(op_code)

    log_search_event(
        chat_id,
        "structured",
        operation=op_norm or op_code,
        rayon=filters.get("rayon"),
        query_text=str(filters),
    )

    page_items, total = query_structured_results(filters, offset=0, limit=PAGE_SIZE)
    st["step"] = "results"
    inc_limit(chat_id, "structured", 1)

    if not total:
        if not replace_loading_message(loading_ref, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin."):
            bot.send_message(chat_id, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin.")
        return

    summary = f"🔍 Tapıldı: {total} elan. İlk nəticələr göstərilir."
    if not replace_loading_message(loading_ref, summary):
        bot.send_message(chat_id, summary)

    send_paginated_results(
        chat_id,
        mode="filter",
        params={"filters": filters},
        page=1,
        show_summary=False,
    )
    offer_save_search(chat_id, build_saved_search_from_structured(filters))


# ===== AÇAR SÖZLƏ AXTARIŞ (paging ilə) =====


def keyword_search_handler(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "keyword", 30):
        bot.send_message(chat_id, "Günlük açar sözlə axtarış limitiniz bitib.")
        return

    text = (message.text or "").strip().lower()
    if not text:
        bot.send_message(chat_id, "Boş sorğu göndərdiniz.")
        return

    st = search_state.get(chat_id, {})
    selected_op = st.get("operation")
    if selected_op not in ("sale", "rent"):
        send_keyword_operation_prompt(chat_id)
        return

    loading_ref = show_loading_message(chat_id)
    log_search_event(
        chat_id,
        "keyword",
        operation=normalize_operation_value(selected_op) or selected_op,
        query_text=text,
    )

    words = [w for w in text.split() if w]

    page_items, total = query_keyword_results(
        selected_op, words, offset=0, limit=PAGE_SIZE
    )

    if not total:
        if not replace_loading_message(loading_ref, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin."):
            bot.send_message(chat_id, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin.")
        return

    inc_limit(chat_id, "keyword", 1)
    send_paginated_results(
        chat_id,
        mode="keyword",
        params={"operation": selected_op, "words": words},
        page=1,
        loading_ref=loading_ref,
    )
    offer_save_search(chat_id, build_saved_search_from_keyword(selected_op))


def smart_search_handler(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "smart", 30):
        bot.send_message(chat_id, "Günlük ağıllı axtarış limitiniz bitib.")
        return

    text = (message.text or "").strip()
    if not text:
        bot.send_message(chat_id, "Boş sorğu göndərdiniz.")
        return

    criteria = parse_smart_query(text)
    loading_ref = show_loading_message(chat_id)
    log_search_event(
        chat_id,
        "smart",
        operation=criteria.get("operation"),
        query_text=text,
    )

    _page_items, total = query_smart_results(criteria, offset=0, limit=PAGE_SIZE)

    if not total:
        if not replace_loading_message(loading_ref, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin."):
            bot.send_message(chat_id, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin.")
        return

    inc_limit(chat_id, "smart", 1)
    send_paginated_results(
        chat_id,
        mode="smart",
        params={"criteria": criteria},
        page=1,
        loading_ref=loading_ref,
    )
    offer_save_search(chat_id, build_saved_search_from_smart(criteria))


# ===== NÖMRƏ İLƏ AXTARIŞ =====


def phone_search_handler(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "phone", 50):
        bot.send_message(chat_id, "Günlük nömrə ilə axtarış limitiniz bitib.")
        return

    raw = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if len(raw) < 7:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa düzgün nömrə yazın (min. 7 rəqəm).")
        return

    loading_ref = show_loading_message(chat_id)
    log_search_event(chat_id, "phone", query_text=raw)

    page_items, total = query_phone_results(raw, offset=0, limit=PAGE_SIZE)

    if not total:
        if not replace_loading_message(loading_ref, "❌ Uyğun elan tapılmadı. Yenidən axtarış edin."):
            bot.send_message(chat_id, "❌ Bu nömrə ilə heç bir elan tapılmadı.")
        return

    inc_limit(chat_id, "phone", 1)
    send_paginated_results(
        chat_id,
        mode="phone",
        params={"digits": raw},
        page=1,
        loading_ref=loading_ref,
    )


# =====================================================
#  🏢 VASITƏÇİ ELANLARI – FULL BLOK
# =====================================================


def agents_panel(c):
    """Admin üçün vasitəçi elanları paneli."""
    chat_id = c.message.chat.id

    if not is_admin(chat_id):
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "🔍 Filtrlə axtar", callback_data="agent_search|filter"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "🔎 Açar söz ilə axtar", callback_data="agent_search|keyword"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "📞 Nömrə ilə axtar", callback_data="agent_search|phone"
        )
    )

    bot.edit_message_text(
        "🏢 Vasitəçi elanları axtarış sistemi:",
        chat_id=chat_id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


# =======================
# 🔍 FILTER MENYUSU
# =======================


@bot.callback_query_handler(func=lambda c: c.data == "agent_search|filter")
def cb_agent_filter(c):
    chat_id = c.message.chat.id

    if not is_admin(chat_id):
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "📍 Rayon / qəsəbə", callback_data="agent_filter|rayon"
        )
    )
    mk.add(
        types.InlineKeyboardButton("🏠 Əmlak növü", callback_data="agent_filter|type")
    )
    mk.add(types.InlineKeyboardButton("💰 Qiymət", callback_data="agent_filter|price"))

    bot.edit_message_text(
        "🔍 Vasitəçi elanları üçün filtr seç:",
        chat_id=chat_id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


# =============================
# 📍 RAYON üzrə axtarış
# =============================


@bot.callback_query_handler(func=lambda c: c.data == "agent_filter|rayon")
def cb_agent_filter_rayon(c):
    chat_id = c.message.chat.id
    if not is_admin(chat_id):
        return

    msg = bot.send_message(chat_id, "📍 Rayon / qəsəbə adı yaz:")
    bot.register_next_step_handler(msg, agent_search_by_rayon)


def agent_search_by_rayon(message):
    if not is_admin(message.chat.id):
        return

    rayon = (message.text or "").strip().lower()
    if not rayon:
        bot.send_message(message.chat.id, "⚠️ Boş sorğu.")
        return

    conn = get_agents_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM arenda_data
        WHERE LOWER(Rayon_Qesebe) LIKE ?
        ORDER BY added_at DESC
        LIMIT 50
        """,
        (f"%{rayon}%",),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "❌ Uyğun vasitəçi elan tapılmadı.")
        return

    bot.send_message(message.chat.id, f"✅ Tapıldı: {len(rows)} elan.")
    for r in rows:
        send_agent_card(message.chat.id, dict(r))


# =============================
# 🔎 Açar sözlə Axtarış
# =============================


@bot.callback_query_handler(func=lambda c: c.data == "agent_search|keyword")
def cb_agent_keyword(c):
    chat_id = c.message.chat.id
    if not is_admin(chat_id):
        return

    msg = bot.send_message(chat_id, "🔎 Vasitəçi elanlarında açar söz yaz:")
    bot.register_next_step_handler(msg, agent_search_by_keyword)


def agent_search_by_keyword(message):
    if not is_admin(message.chat.id):
        return

    kw = (message.text or "").strip().lower()
    if not kw:
        bot.send_message(message.chat.id, "⚠️ Boş sorğu.")
        return

    conn = get_agents_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM arenda_data
        WHERE LOWER(Umumi_melumat) LIKE ?
           OR LOWER(Unvan) LIKE ?
           OR LOWER(Rayon_Qesebe) LIKE ?
           OR LOWER(Emlakin_novu) LIKE ?
        ORDER BY added_at DESC
        LIMIT 50
        """,
        (f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "😕 Heç nə tapılmadı.")
        return

    bot.send_message(message.chat.id, f"✅ Tapıldı: {len(rows)} elan.")
    for r in rows:
        send_agent_card(message.chat.id, dict(r))


# =============================
# 📞 Nömrə ilə Axtarış
# =============================


@bot.callback_query_handler(func=lambda c: c.data == "agent_search|phone")
def cb_agent_phone(c):
    chat_id = c.message.chat.id
    if not is_admin(chat_id):
        return

    msg = bot.send_message(chat_id, "📞 Nömrə daxil et:")
    bot.register_next_step_handler(msg, agent_search_by_phone)


def agent_search_by_phone(message):
    if not is_admin(message.chat.id):
        return

    num = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if len(num) < 7:
        bot.send_message(message.chat.id, "⚠️ Minimum 7 rəqəm yaz.")
        return

    conn = get_agents_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM arenda_data
        WHERE REPLACE(Elaqe_nomresi,' ','') LIKE ?
        ORDER BY added_at DESC
        LIMIT 50
        """,
        (f"%{num}%",),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "❌ Bu nömrə ilə vasitəçi tapılmadı.")
        return

    bot.send_message(message.chat.id, f"✅ Tapıldı: {len(rows)} elan.")
    for r in rows:
        send_agent_card(message.chat.id, dict(r))


# =============================
# 🎫 Elan kartı — Link + status
# =============================


def send_agent_card(chat_id, ev):
    txt = (
        f"🏠 <b>{ev['Emlakin_novu']}</b>\n"
        f"📍 {ev['Rayon_Qesebe']} — {ev['Unvan']}\n"
        f"💰 {ev['Qiymet']} AZN\n"
        f"📞 {ev['Elaqe_nomresi']}\n"
        f"🧾 {ev['Umumi_melumat']}"
    )

    # 🔗 LINK ƏLAVƏ ET
    if ev.get("Link"):
        txt += f"\n\n🔗 <a href='{ev['Link']}'>Elana keçid</a>"

    # Vasitəçi olub-olmadığını göstər
    if ev.get("Mulk_sahibi_veya_Vasiteci"):
        role = ev["Mulk_sahibi_veya_Vasiteci"].lower()
        if "sahib" in role:
            txt += "\n\n🟩 <b>Əmlak sahibi</b>"
        else:
            txt += "\n\n🟦 Vasitəçi"

    bot.send_message(chat_id, txt, parse_mode="HTML")


# =============== 📊 ADMIN PANEL (MENYU + CALLBACK) ===============


@bot.message_handler(func=lambda m: m.text == "📊 Admin Panel")
@bot.message_handler(commands=["admin"])
def open_admin_panel(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Bu bölməyə yalnız admin daxil ola bilər.")
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "✅ Təsdiqlənməyən elanlar", callback_data="adm|pending"
        )
    )
    mk.add(
        types.InlineKeyboardButton("📊 Statistikalar", callback_data="adm|stats")
    )
    mk.add(
        types.InlineKeyboardButton(
            "📤 Vasitəçilərə bildiriş", callback_data="adm|agents_broadcast"
        )
    )
    mk.add(types.InlineKeyboardButton("🔍 Bazada axtar", callback_data="adm|search"))
    mk.add(
        types.InlineKeyboardButton(
            "♻️ Limitləri sıfırla", callback_data="adm|reset_limits"
        )
    )
    mk.add(types.InlineKeyboardButton("👥 İstifadəçilər", callback_data="adm|users"))
    mk.add(
        types.InlineKeyboardButton("🏢 Vasitəçi elanları", callback_data="adm|agents")
    )
    mk.add(
        types.InlineKeyboardButton(
            "🚀 Yeniləmə göndər", callback_data="adm|notify_update"
        )
    )

    bot.send_message(message.chat.id, "🛠 Admin Panel:", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm|"))
def cb_admin(c):
    if not is_admin(c.message.chat.id):
        return

    parts = c.data.split("|")
    if len(parts) < 2:
        return

    cmd = parts[1]

    if cmd == "pending":
        show_pending_listings(c.message.chat.id)

    elif cmd == "stats":
        show_admin_stats(c.message.chat.id)

    elif cmd == "agents_broadcast":
        msg = bot.send_message(
            c.message.chat.id, "✍️ Vasitəçilərə göndəriləcək mətni yaz:"
        )
        bot.register_next_step_handler(msg, admin_agents_broadcast)

    elif cmd == "search":
        msg = bot.send_message(
            c.message.chat.id, "🔍 Açar söz yaz (əsas baza + lokal):"
        )
        bot.register_next_step_handler(msg, admin_search_handler)

    elif cmd == "reset_limits":
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM search_limits")
        conn.commit()
        conn.close()
        bot.send_message(c.message.chat.id, "♻️ Bütün istifadəçi limitləri sıfırlandı.")

    elif cmd == "users":
        show_users_menu(c.message.chat.id)

    elif cmd == "agents":
        agents_panel(c)  # ⚡ BURADA ARTIQ DÜZGÜNDÜR

    elif cmd == "notify_update":
        broadcast_bot_update(c.message.chat.id)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


# =============== 👥 İSTİFADƏÇİLƏR PANELİ ===============


def show_users_menu(chat_id):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("✅ Aktiv", callback_data="userlist|active"),
        types.InlineKeyboardButton("🚫 Bloklanmış", callback_data="userlist|blocked"),
    )
    mk.add(
        types.InlineKeyboardButton(
            "⏳ Təsdiqlənməmiş", callback_data="userlist|pending"
        )
    )
    bot.send_message(
        chat_id,
        "👥 İstifadəçi kateqoriyasını seç:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("userlist|"))
def cb_userlist(c):
    if not is_admin(c.message.chat.id):
        return
    status = c.data.split("|")[1]
    show_all_users(c.message.chat.id, status)


def show_all_users(chat_id, status="active"):
    conn = get_local_conn()
    cur = conn.cursor()

    if status == "active":
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked "
            "FROM users WHERE approved=1 AND blocked=0 "
            "ORDER BY date_joined DESC"
        )
        title = "✅ Aktiv istifadəçilər"
    elif status == "blocked":
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked "
            "FROM users WHERE blocked=1 "
            "ORDER BY date_joined DESC"
        )
        title = "🚫 Bloklanmış istifadəçilər"
    elif status == "pending":
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked "
            "FROM users WHERE approved=0 "
            "ORDER BY date_joined DESC"
        )
        title = "⏳ Təsdiqlənməmiş istifadəçilər"
    else:
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked "
            "FROM users ORDER BY date_joined DESC"
        )
        title = "👥 Bütün istifadəçilər"

    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, f"❌ {title} tapılmadı.")
        return

    bot.send_message(chat_id, f"{title} ({len(rows)} nəfər):")

    for r in rows:
        chat_id_u, full_name, username, approved, blocked = r
        uname = f"@{username}" if username else "—"
        status_text = (
            "✅ Aktiv"
            if approved and not blocked
            else "🚫 Bloklanıb" if blocked else "⏳ Təsdiqlənməyib"
        )

        txt = (
            f"👤 {full_name or 'Ad yoxdur'}\n"
            f"💬 {uname}\n"
            f"🆔 <code>{chat_id_u}</code>\n"
            f"📊 Status: {status_text}"
        )

        mk = types.InlineKeyboardMarkup()
        if approved == 0:
            mk.add(
                types.InlineKeyboardButton(
                    "✅ Qəbul et", callback_data=f"user_approve|{chat_id_u}"
                ),
                types.InlineKeyboardButton(
                    "❌ Dayandır", callback_data=f"user_block|{chat_id_u}"
                ),
            )
        elif blocked:
            mk.add(
                types.InlineKeyboardButton(
                    "✅ Aktiv et",
                    callback_data=f"user_unblock|{chat_id_u}",
                )
            )
        else:
            mk.add(
                types.InlineKeyboardButton(
                    "❌ Dayandır",
                    callback_data=f"user_block|{chat_id_u}",
                )
            )

        bot.send_message(
            chat_id,
            txt,
            parse_mode="HTML",
            reply_markup=mk,
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_block|"))
def cb_user_block_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET blocked=1 WHERE chat_id=?",
        (uid,),
    )
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "🚫 İstifadəçi dayandırıldı.")
    try:
        bot.send_message(
            uid,
            "⚠️ Hesabınız admin tərəfindən dayandırıldı.",
        )
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_approve|"))
def cb_user_approve_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET approved=1, blocked=0 WHERE chat_id=?",
        (uid,),
    )
    conn.commit()
    conn.close()

    try:
        bot.answer_callback_query(c.id, "✅ İstifadəçi təsdiqləndi.")
    except:
        pass

    try:
        bot.send_message(
            uid,
            "🎉 Hesabınız admin tərəfindən təsdiqləndi. Artıq botdan istifadə edə bilərsiniz.",
        )
    except:
        pass

    show_all_users(c.message.chat.id, "pending")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_unblock|"))
def cb_user_unblock_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET blocked=0 WHERE chat_id=?",
        (uid,),
    )
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "✅ İstifadəçi aktiv edildi.")
    try:
        bot.send_message(
            uid,
            "🎉 Hesabınız yenidən aktivləşdirildi!",
        )
    except:
        pass


# =============== 🕓 TƏSDİQ GÖZLƏYƏN ELANLAR ===============


def show_pending_listings(chat_id):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM listings_new WHERE approved=0 " "ORDER BY id DESC LIMIT 50"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(
            chat_id,
            "⛔ Təsdiq gözləyən elan yoxdur.",
        )
        return

    for r in rows:
        ev = dict(r)
        txt = (
            f"ID: {ev['id']}\n"
            f"👤 {ev['role']}\n"
            f"🏠 {ev['prop_type']} | {ev['rooms']}\n"
            f"💸 {ev['operation']} | 💰 {format_price(ev['price'])} {ev['currency']}\n"
            f"📍 {ev['rayon']} — {ev['metro']}\n"
            f"📞 {ev['phone']} ({ev['contact_name']})\n"
            f"🧾 {ev['summary']}"
        )
        if ev.get("link"):
            txt += f"\n🔗 {ev['link']}"

        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "✅ Təsdiqlə",
                callback_data=f"aprv|{ev['id']}",
            ),
            types.InlineKeyboardButton(
                "❌ Sil",
                callback_data=f"del|{ev['id']}",
            ),
        )

        bot.send_message(chat_id, txt, reply_markup=mk)


# =============== İSTİFADƏÇİ TƏSDİQİ (ADMIN) ===============


def show_pending_users(chat_id):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT chat_id, full_name, username, date_joined
        FROM users
        WHERE approved=0 AND blocked=0
        ORDER BY date_joined ASC
        LIMIT 100
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "✅ Gözləyən istifadəçi yoxdur.")
        return

    for uid, full_name, username, dt in rows:
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "✅ Təsdiqlə",
                callback_data=f"uappr|{uid}",
            ),
            types.InlineKeyboardButton(
                "❌ Blokla",
                callback_data=f"ublock|{uid}",
            ),
        )
        prof = f"@{username}" if username else "username yoxdur"
        txt = (
            f"👤 {full_name or '-'}\n"
            f"💬 {prof}\n"
            f"🆔 <code>{uid}</code>\n"
            f"📅 {dt}\n"
        )
        bot.send_message(chat_id, txt, parse_mode="HTML", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("uappr|"))
def cb_user_approve(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET approved=1, blocked=0 WHERE chat_id=?",
        (uid,),
    )
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "✅ İstifadəçi təsdiqləndi.")
    try:
        bot.send_message(
            uid,
            "🎉 Hesabınız admin tərəfindən təsdiqləndi. Artıq botdan istifadə edə bilərsiniz.",
        )
    except:
        pass

    show_pending_users(c.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ublock|"))
def cb_user_block_pending(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET approved=0, blocked=1 WHERE chat_id=?",
        (uid,),
    )
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "⛔ İstifadəçi bloklandı.")
    try:
        bot.send_message(
            uid,
            "⛔ Hesabınız admin tərəfindən bloklandı.",
        )
    except:
        pass


# =============== BOT YENİLƏMƏ BİLDİRİŞİ ===============


def broadcast_bot_update(admin_chat_id):
    """Admin paneldən 'Yeniləmə göndər' basanda hamıya refresh düyməsi göndər."""
    if not is_admin(admin_chat_id):
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM users WHERE blocked=0")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(admin_chat_id, "❌ Aktiv istifadəçi tapılmadı.")
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "🔁 Botu yenilə",
            callback_data="refresh_bot",
        )
    )

    sent = 0
    for (uid,) in rows:
        try:
            bot.send_message(
                uid,
                f"🚀 Bot yeniləndi ({CURRENT_VERSION}). Davam etmək üçün aşağıdakı düyməyə bas:",
                reply_markup=mk,
            )
            sent += 1
        except:
            continue

    bot.send_message(
        admin_chat_id,
        f"✅ Yeniləmə bildirişi {sent} istifadəçiyə göndərildi.",
    )


@bot.callback_query_handler(func=lambda c: c.data == "refresh_bot")
def cb_refresh_bot(c):
    """İstifadəçi 'Botu yenilə' düyməsinə basanda /start işə düşür."""
    try:
        bot.answer_callback_query(c.id, "✅ Yeniləndi.")
    except:
        pass
    start_cmd(c.message)


# =============== PUBLIC MENYUDAN DÜYMƏLƏR ===============


@bot.message_handler(func=lambda m: m.text == "🧑‍💼 Vasitəçilər")
def agents_button(message):
    if not ensure_allowed(message):
        return
    # Vasitəçi elanlarını açıq axtarmaq üçün sadə açar sözlə axtarış
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "🔎 Vasitəçi elanlarında açar sözlə axtar",
            callback_data="pub_agents_kw",
        )
    )
    bot.send_message(
        message.chat.id,
        "🧑‍💼 Vasitəçi elanları bölməsi:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data == "pub_agents_kw")
def cb_pub_agents_kw(c):
    if not ensure_allowed_cb(c):
        return
    msg = bot.send_message(
        c.message.chat.id,
        "🔎 Vasitəçi elanlarında açar söz yaz:",
    )
    bot.register_next_step_handler(msg, pub_agent_search_by_keyword)


def pub_agent_search_by_keyword(message):
    if not ensure_allowed(message):
        return
    kw = (message.text or "").strip().lower()
    if not kw:
        bot.send_message(message.chat.id, "⚠️ Boş sorğu.")
        return

    conn = get_agents_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM arenda_data
        WHERE LOWER(Umumi_melumat) LIKE ?
           OR LOWER(Unvan) LIKE ?
           OR LOWER(Rayon_Qesebe) LIKE ?
           OR LOWER(Emlakin_novu) LIKE ?
        ORDER BY added_at DESC
        LIMIT 30
        """,
        (f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "😕 Uyğun vasitəçi elan tapılmadı.")
        return

    bot.send_message(message.chat.id, f"✅ Tapıldı: {len(rows)} elan.")
    for r in rows:
        send_agent_card(message.chat.id, dict(r))


# =============== ADMIN STATİSTİKA, AXTARIŞ, BROADCAST ===============


def show_admin_stats(chat_id):
    if not is_admin(chat_id):
        return

    now = datetime.utcnow()
    conn = get_local_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM users WHERE approved=1 AND blocked=0")
    active_users = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM listings_new")
    total_new = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM listings_new WHERE approved=0")
    pending_new = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM listings_approved")
    total_local = cur.fetchone()[0] or 0

    today = date.today().isoformat()
    cur.execute(
        "SELECT COUNT(*) FROM search_logs WHERE DATE(created_at)=?",
        (today,),
    )
    today_searches = cur.fetchone()[0] or 0

    since_24h = (now - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT COUNT(*) FROM search_logs WHERE datetime(created_at) >= datetime(?)",
        (since_24h,),
    )
    last_24h_searches = cur.fetchone()[0] or 0

    week_cutoff = (now - timedelta(days=7)).isoformat()
    cur.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(rayon), ''), '(rayon qeyd olunmayıb)') AS rn,
               COUNT(*) AS cnt
        FROM search_logs
        WHERE datetime(created_at) >= datetime(?)
        GROUP BY rn
        ORDER BY cnt DESC
        LIMIT 10
        """,
        (week_cutoff,),
    )
    top_rayons = cur.fetchall()

    cur.execute(
        """
        SELECT ua.chat_id, ua.total_searches, u.full_name, u.username
        FROM user_activity ua
        LEFT JOIN users u ON u.chat_id = ua.chat_id
        ORDER BY ua.total_searches DESC
        LIMIT 10
        """
    )
    top_users = cur.fetchall()

    cur.execute(
        """
        SELECT source, listing_id, views
        FROM listing_views
        WHERE datetime(last_viewed_at) >= datetime(?)
        ORDER BY views DESC, last_viewed_at DESC
        LIMIT 10
        """,
        (week_cutoff,),
    )
    top_viewed = cur.fetchall()
    conn.close()

    # Agents DB
    try:
        conn_a = get_agents_conn()
        cur_a = conn_a.cursor()
        cur_a.execute("SELECT COUNT(*) FROM arenda_data")
        total_agents = cur_a.fetchone()[0] or 0
        conn_a.close()
    except Exception:
        total_agents = 0

    sections = ["📊 *Admin Statistikası*"]
    sections.append(
        "🔎 Axtarış aktivliyi:\n"
        f"• Bu gün: {today_searches}\n"
        f"• Son 24 saat: {last_24h_searches}"
    )

    if top_rayons:
        lines = ["🏘 Son 7 günün TOP rayonları:"]
        for idx, (rn, cnt) in enumerate(top_rayons, start=1):
            lines.append(f"{idx}) {rn}: {cnt}")
        sections.append("\n".join(lines))

    if top_users:
        lines = ["👥 TOP aktiv istifadəçilər:"]
        for idx, row in enumerate(top_users, start=1):
            chat_id_u, total_s, full_name, username = row
            uname = f"@{username}" if username else "—"
            title = full_name or uname or chat_id_u
            lines.append(f"{idx}) {title} ({uname}) — {total_s}")
        sections.append("\n".join(lines))

    if top_viewed:
        status_map = get_status_map()
        lines = ["🔥 Son 7 günün TOP baxılan elanları:"]
        idx = 1
        for row in top_viewed:
            ev = fetch_listing_by_source(row["source"], row["listing_id"])
            if not ev or not is_listing_active(ev, status_map):
                continue
            title = ev.get("prop_type") or ev.get("Emlakin_novu") or "-"
            rooms = ev.get("rooms") or ev.get("Otaq_sayi") or "-"
            op = ev.get("operation") or ev.get("Emeliyyat") or "-"
            price = format_price(ev.get("price") or ev.get("Qiymet"))
            lines.append(
                f"{idx}) {title} | {rooms} — {op}, {price} ({row['views']} baxış)"
            )
            idx += 1
        if len(lines) > 1:
            sections.append("\n".join(lines))

    sections.append(
        "📂 Baza xülasəsi:\n"
        f"• Ümumi istifadəçi: {total_users}\n"
        f"• Aktiv: {active_users}\n"
        f"• Yeni elanlar: {total_new}\n"
        f"• Gözləyən elanlar: {pending_new}\n"
        f"• Lokal elanlar: {total_local}\n"
        f"• Vasitəçi elanları: {total_agents}"
    )

    bot.send_message(chat_id, "\n\n".join(sections), parse_mode="Markdown")


def admin_agents_broadcast(message):
    """Adminin yazdığı mətni bütün agents cədvəlində olanlara göndər."""
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    if not text:
        bot.send_message(message.chat.id, "⚠️ Boş mətni göndərə bilmərəm.")
        return

    conn = get_local_conn()
    cur = conn.cursor()
    # agents cədvəlindən unikal chat_id-lər
    cur.execute("SELECT DISTINCT chat_id FROM agents")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "❌ Vasitəçi tapılmadı.")
        return

    sent = 0
    for (uid,) in rows:
        try:
            bot.send_message(
                uid,
                f"📢 Admin bildirişi:\n{text}",
            )
            sent += 1
        except:
            continue

    bot.send_message(
        message.chat.id,
        f"✅ Bildiriş {sent} vasitəçiyə göndərildi.",
    )


def admin_search_handler(message):
    """Admin üçün ümumi açar sözlə axtarış (əsas + lokal + agent)."""
    if not is_admin(message.chat.id):
        return

    q = (message.text or "").strip().lower()
    if not q:
        bot.send_message(message.chat.id, "⚠️ Boş sorğu.")
        return

    like = f"%{q}%"
    found = 0

    # MAIN DB listings
    if os.path.exists(MAIN_DB):
        try:
            conn = get_main_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM listings
                WHERE LOWER(summary) LIKE ?
                   OR LOWER(address) LIKE ?
                   OR LOWER(metro) LIKE ?
                ORDER BY date_read DESC
                LIMIT 30
                """,
                (like, like, like),
            )
            rows = cur.fetchall()
            conn.close()
            for r in rows:
                send_listing_card(
                    message.chat.id,
                    dict(r),
                    source="main",
                    with_fav_button=False,
                )
                found += 1
        except:
            pass

    # LOCAL APPROVED
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM listings_approved
        WHERE LOWER(summary) LIKE ?
           OR LOWER(rayon) LIKE ?
           OR LOWER(metro) LIKE ?
        ORDER BY date_added DESC
        LIMIT 30
        """,
        (like, like, like),
    )
    rows = cur.fetchall()
    conn.close()
    for r in rows:
        send_listing_card(
            message.chat.id,
            dict(r),
            source="local",
            with_fav_button=False,
        )
        found += 1

    # AGENT LISTINGS
    try:
        conn_a = get_agents_conn()
        cur_a = conn_a.cursor()
        cur_a.execute(
            """
            SELECT * FROM arenda_data
            WHERE LOWER(Umumi_melumat) LIKE ?
               OR LOWER(Unvan) LIKE ?
               OR LOWER(Rayon_Qesebe) LIKE ?
            ORDER BY added_at DESC
            LIMIT 30
            """,
            (like, like, like),
        )
        arows = cur_a.fetchall()
        conn_a.close()
        for r in arows:
            send_agent_card(message.chat.id, dict(r))
            found += 1
    except:
        pass

    if found == 0:
        bot.send_message(message.chat.id, "😕 Heç nə tapılmadı.")
    else:
        bot.send_message(
            message.chat.id,
            f"✅ Admin axtarış nəticəsi: {found} uyğun qeydə baxdın.",
        )


# =============== RUN (Render / Lokal) ===============


def run_bot():
    while True:
        try:
            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=20,
                skip_pending=True,
            )
        except Exception as e:
            print("Polling error:", e)
            time.sleep(5)


def main_menu(chat_id):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    mk.add("📝 Yeni elan əlavə et")
    mk.add("🔎 Axtarış sistemi")
    mk.add("📂 Elan statusları")
    mk.add("⭐ Favorilərim", "📋 Elanlarım")
    mk.add("ℹ️ Haqqında")

    if is_admin(chat_id):
        mk.add("📊 Admin Panel")

    bot.send_message(chat_id, "📋 Əsas menyudan seçim et:", reply_markup=mk)


if __name__ == "__main__":
    print("⚙️ BestHome Unified Bot FULL v9 işə düşür...")
    init_local_db()
    init_agents_db()
    init_main_db_indices()
    ensure_fts_tables()
    check_favorite_price_drops()

    threading.Thread(target=saved_search_worker, daemon=True).start()
    threading.Thread(target=favorite_price_worker, daemon=True).start()

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "✅ BestHome Bot işləyir."

    threading.Thread(target=run_bot, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
