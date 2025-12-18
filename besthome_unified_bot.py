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
import random
import shutil
import tempfile
from datetime import datetime, date, timedelta
from typing import Optional, Tuple
from urllib.parse import quote

import requests
from flask import Flask
import telebot
from telebot import types

# ==============================
# 💳 ABUNƏLİK KONFİQURASİYASI
# ==============================
SUBSCRIPTION_PLANS = {
    "1": {"title": "1 gün", "price": "2 AZN", "days": 1},
    "7": {"title": "7 gün", "price": "5 AZN", "days": 7},
    "15": {"title": "15 gün", "price": "10 AZN", "days": 15},
    "30": {"title": "30 gün", "price": "15 AZN", "days": 30},
}

REFERRAL_REWARD_DAYS = 3
REFERRAL_MILESTONE_COUNT = 10
REFERRAL_MILESTONE_BONUS_DAYS = 45

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
BOT_USERNAME = bot.get_me().username
user_state = {}  # Yeni elan proses state
search_state = {}  # Açar sözlə axtarış paging state
search_reminder_shown = set()  # Session-level reminder flag
session_interactions = {}
db_update_lock = threading.Lock()
complaint_flow_state = {}
complaint_records = {}
admin_reply_state = {}
last_complaint_time = {}
admin_stats_period = {}
admin_direct_message_state = {}
BLOCKED_MESSAGE_TEXT = (
    "⛔ Hesabınız müvəqqəti olaraq dayandırıldı.\n\n"
    "Səbəb:\n"
    "Ödəniş edilməyib və ya demo müddəti bitib.\n\n"
    "✅ Davam etmək üçün seçimlər:\n"
    "• 💳 Ödəniş edin və sorğunu yenidən göndərin\n"
    "• 🎁 Əgər mümkündürsə, 3 günlük demo istifadə edin\n\n"
    "Sorğu göndərdikdən sonra admin tərəfindən yenidən yoxlanılacaq."
)
STATUS_PENDING = "pending"
STATUS_DEMO = "demo"
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
FINANCIAL_REPORTS_BUTTON = "💰 Maliyyə hesabatları"
FINANCIAL_REPORTS_BACK = "⬅️ Geri (Admin Panel)"
FINANCIAL_REPORTS_MENU = [
    "📜 Ödəniş tarixçəsi",
    "🤝 Referral statistikası",
    "📈 Aylıq gəlir hesabatı",
    FINANCIAL_REPORTS_BACK,
]
ADMIN_PANEL_PAGE1 = [
    "✅ Təsdiqlənməyən elanlar",
    "📊 Statistikalar",
    FINANCIAL_REPORTS_BUTTON,
    "📢 Vasitəçilərə bildiriş",
    "🧠 Aktiv / passiv maklerlər",
    "🧪 Demo istifadəçilər",
    "🆔 İstifadəçi ID ilə axtar",
    "👥 İstifadəçilər",
]
ADMIN_PANEL_PAGE2 = [
    "🎟 Promo kodlar",
    "♻️ Limitləri sıfırla",
    "🚀 Yeniləmə göndər",
    "🔥 Ən çox baxılan elanlar",
    "📦 Baza yenilə",
    "📨 İstifadəçiyə mesaj göndər",
]
ADMIN_PANEL_NAV_NEXT = "▶️ Növbəti səhifə"
ADMIN_PANEL_NAV_PREV = "◀️ Əvvəlki səhifə"
ADMIN_PANEL_BACK_MAIN = "⬅️ Əsas menyuya qayıt"
admin_panel_page_state = {}
ADMIN_PANEL_ACTIONS = set(ADMIN_PANEL_PAGE1 + ADMIN_PANEL_PAGE2)

# Pagination
PAGE_SIZE = 20
NEW_LISTING_WINDOW_HOURS = 24
HOT_VIEWS_THRESHOLD = 50
COMPLAINT_CATEGORIES = [
    "🐞 Texniki problem",
    "💡 Təklif",
    "❗ Şikayət",
    "💬 Digər",
]
COMPLAINT_BACK = "⬅️ Geri"
COMPLAINT_COOLDOWN_SECONDS = 300


main_db_connections = set()
main_db_connections_lock = threading.Lock()
main_db_update_in_progress = threading.Event()


def register_main_conn(conn):
    with main_db_connections_lock:
        main_db_connections.add(conn)


def close_main_conn(conn):
    if conn is None:
        return
    try:
        conn.close()
    except Exception as e:
        print("⚠️ main DB close error:", e)
    finally:
        with main_db_connections_lock:
            main_db_connections.discard(conn)


def close_all_main_conns():
    with main_db_connections_lock:
        conns = list(main_db_connections)
        main_db_connections.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception as e:
            print("⚠️ main DB close error:", e)


def get_main_conn():
    while main_db_update_in_progress.is_set():
        time.sleep(0.05)
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    register_main_conn(conn)
    return conn


def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB, check_same_thread=False)  # 🔥 ÇOX VACİB
    conn.row_factory = sqlite3.Row
    return conn


def get_agents_conn():
    conn = sqlite3.connect(AGENTS_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_user_step(chat_id: int):
    state = user_state.get(chat_id)
    if isinstance(state, dict):
        return state.get("step")
    return None


def prepare_main_db_for_swap():
    try:
        conn = sqlite3.connect(MAIN_DB, timeout=5)
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.close()
    except Exception as e:
        print("⚠️ DB checkpoint xətası:", e)


def backup_main_db_file() -> Optional[str]:
    if not os.path.exists(MAIN_DB):
        return None

    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    backup_path = os.path.join(DATA_DIR, f"besthome_backup_{ts}.db")
    shutil.copy2(MAIN_DB, backup_path)
    return backup_path


def restore_main_db_from_backup(backup_path: Optional[str]):
    if not backup_path or not os.path.exists(backup_path):
        return
    try:
        if os.path.exists(MAIN_DB):
            os.remove(MAIN_DB)
    except Exception:
        pass
    shutil.copy2(backup_path, MAIN_DB)


def restart_bot_safely(delay: int = 2):
    time.sleep(delay)
    os._exit(0)


DB_ALLOWED_MIME_TYPES = {
    "application/vnd.sqlite3",
    "application/octet-stream",
    "application/x-sqlite3",
}


def download_main_db_zip(url: str) -> str:
    fd, temp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP status {r.status_code}")
            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return temp_path


def extract_main_db_from_zip(zip_path: str) -> Tuple[str, str]:
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError("Fayl ZIP formatında deyil")

    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if "besthome.db" not in names:
            raise RuntimeError("ZIP daxilində 'besthome.db' tapılmadı")
        zf.extract("besthome.db", path=temp_dir)
    return os.path.join(temp_dir, "besthome.db"), temp_dir


def validate_main_db_file(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM listings")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def sanity_check_main_db():
    conn = sqlite3.connect(MAIN_DB)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM listings")
        cur.fetchone()
    finally:
        conn.close()


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

    # Vasitəçi aktivliyi (axtarış, baxış, WhatsApp, favorit)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_activity (
            chat_id INTEGER PRIMARY KEY,
            last_activity TEXT,
            searches INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            whatsapp INTEGER DEFAULT 0,
            favorites INTEGER DEFAULT 0
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
            blocked INTEGER DEFAULT 0,
            blocked_at TEXT,
            status TEXT,
            joined_at TEXT,
            demo_start_at TEXT,
            demo_end_at TEXT,
            paid_until TEXT,
            last_status_change_at TEXT
        )
        """
    )

    # Promo sahələri (mövcud deyilsə əlavə et)
    for alter_stmt in [
        "ALTER TABLE users ADD COLUMN promo_active INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN promo_expires_at TEXT",
        "ALTER TABLE users ADD COLUMN referred_by INTEGER",
        "ALTER TABLE users ADD COLUMN referral_bonus_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN referral_milestone_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN demo_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN demo_expires_at TEXT",
        "ALTER TABLE users ADD COLUMN blocked_at TEXT",
        "ALTER TABLE users ADD COLUMN status TEXT",
        "ALTER TABLE users ADD COLUMN joined_at TEXT",
        "ALTER TABLE users ADD COLUMN demo_start_at TEXT",
        "ALTER TABLE users ADD COLUMN demo_end_at TEXT",
        "ALTER TABLE users ADD COLUMN paid_until TEXT",
        "ALTER TABLE users ADD COLUMN last_status_change_at TEXT",
    ]:
        try:
            cur.execute(alter_stmt)
        except sqlite3.OperationalError:
            pass

    # Abunəliklər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            chat_id INTEGER PRIMARY KEY,
            plan TEXT,
            expires_at TEXT,
            is_active INTEGER DEFAULT 0,
            is_demo INTEGER DEFAULT 0,
            last_payment_note TEXT
        )
    """
    )

    # Ödənişlər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            plan TEXT,
            amount INTEGER,
            approved_at TEXT DEFAULT (CURRENT_TIMESTAMP)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_chat_id INTEGER,
            referred_chat_id INTEGER PRIMARY KEY,
            created_at TEXT,
            reward_given INTEGER DEFAULT 0
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS referral_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_user_id INTEGER,
            bonus_days INTEGER,
            created_at TEXT
        )
        """
    )

    # Promo kodlar
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            days INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_chat_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_referral_logs_referrer ON referral_logs(referrer_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_referral_logs_referred ON referral_logs(referred_user_id)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_usages (
            code TEXT,
            chat_id INTEGER,
            used_at TEXT,
            expires_at TEXT,
            PRIMARY KEY (code, chat_id)
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listing_stats (
            listing_id INTEGER PRIMARY KEY,
            views INTEGER DEFAULT 0,
            favorites INTEGER DEFAULT 0,
            contacts INTEGER DEFAULT 0,
            popularity_score INTEGER DEFAULT 0,
            last_interaction TEXT
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
        close_main_conn(conn)
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
            close_main_conn(conn)
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


def parse_dt_safe(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except Exception:
        try:
            return datetime.fromisoformat(str(raw).replace(" ", "T"))
        except Exception:
            return None


def derive_status_from_legacy(row: sqlite3.Row, now: datetime) -> str:
    status = (row.get("status") if isinstance(row, dict) else row["status"]) if row else None
    if status in {STATUS_PENDING, STATUS_DEMO, STATUS_ACTIVE, STATUS_BLOCKED}:
        return status
    if row:
        if row["blocked"]:
            return STATUS_BLOCKED
        if row["approved"]:
            return STATUS_ACTIVE
        demo_exp = parse_dt_safe(row["demo_expires_at"])
        if row["demo_used"] and demo_exp and demo_exp > now:
            return STATUS_DEMO
    return STATUS_PENDING


def migrate_user_statuses():
    now = datetime.utcnow()
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT chat_id, status, approved, blocked, demo_used, demo_expires_at,
               date_joined, joined_at, last_status_change_at, demo_start_at,
               demo_end_at, paid_until
        FROM users
        """
    )
    rows = cur.fetchall()
    for row in rows:
        joined_at = row["joined_at"] or row["date_joined"] or datetime.utcnow().isoformat()
        status = derive_status_from_legacy(row, now)
        last_change = row["last_status_change_at"] or joined_at
        cur.execute(
            """
            UPDATE users
            SET status=?, joined_at=?, last_status_change_at=COALESCE(last_status_change_at, ?)
            WHERE chat_id=?
            """,
            (status, joined_at, last_change, row["chat_id"]),
        )
    conn.commit()
    conn.close()


def get_user_record(chat_id: int) -> Optional[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT chat_id, full_name, username, status, joined_at, demo_start_at,
               demo_end_at, paid_until, last_status_change_at, approved, blocked,
               promo_active, promo_expires_at, referred_by, referral_bonus_used,
               referral_milestone_used, demo_used, demo_expires_at, blocked_at
        FROM users WHERE chat_id=?
        """,
        (chat_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["status"] = derive_status_from_legacy(row, datetime.utcnow())
    return data


def update_user_status(
    chat_id: int,
    status: str,
    *,
    demo_start_at: Optional[datetime] = None,
    demo_end_at: Optional[datetime] = None,
    paid_until: Optional[datetime] = None,
    blocked_at: Optional[datetime] = None,
):
    now = datetime.utcnow().isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    approved_val = 1 if status in {STATUS_ACTIVE, STATUS_DEMO} else 0
    blocked_val = 1 if status == STATUS_BLOCKED else 0
    cur.execute(
        """
        UPDATE users
        SET status=?, demo_start_at=?, demo_end_at=?, paid_until=?,
            last_status_change_at=?, approved=?, blocked=?, blocked_at=?,
            demo_used=CASE WHEN ? THEN 1 ELSE demo_used END,
            demo_expires_at=COALESCE(?, demo_expires_at),
            joined_at=COALESCE(joined_at, ?)
        WHERE chat_id=?
        """,
        (
            status,
            demo_start_at.isoformat() if demo_start_at else None,
            demo_end_at.isoformat() if demo_end_at else None,
            paid_until.isoformat() if paid_until else None,
            now,
            approved_val,
            blocked_val,
            blocked_at.isoformat() if blocked_at else None,
            1 if status == STATUS_DEMO else 0,
            demo_end_at.isoformat() if demo_end_at else None,
            datetime.utcnow().isoformat(),
            chat_id,
        ),
    )
    conn.commit()
    conn.close()


def register_user(message):
    chat = message.chat
    uid = chat.id
    full_name = (chat.first_name or "") + (
        (" " + chat.last_name) if chat.last_name else ""
    )
    username = message.from_user.username if message.from_user else None

    conn = get_local_conn()
    cur = conn.cursor()

    joined_at = datetime.utcnow().isoformat()
    # Admin avtomatik approved
    if is_admin(uid):
        cur.execute(
            """
            INSERT INTO users (
                chat_id, full_name, username, date_joined, joined_at,
                approved, blocked, status, last_status_change_at
            )
            VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                full_name=excluded.full_name,
                username=excluded.username
        """,
            (
                uid,
                full_name,
                username or "",
                joined_at,
                joined_at,
                STATUS_ACTIVE,
                joined_at,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO users (chat_id, full_name, username, date_joined, joined_at, status, last_status_change_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                full_name=excluded.full_name,
                username=excluded.username
        """,
            (uid, full_name, username or "", joined_at, joined_at, STATUS_PENDING, joined_at),
        )
    conn.commit()
    conn.close()


# ==============================
# 💳 ABUNƏLİK FUNKSİYALARI
# ==============================

subscription_warn_cache = set()
demo_warn_cache = set()


def ensure_subscription_record(chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note)
        VALUES (?, NULL, NULL, 0, 0, NULL)
        """,
        (chat_id,),
    )
    conn.commit()
    conn.close()


def get_subscription(chat_id: int) -> Optional[dict]:
    ensure_subscription_record(chat_id)
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT chat_id, plan, expires_at, is_active, is_demo, last_payment_note FROM subscriptions WHERE chat_id=?",
        (chat_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "chat_id": row[0],
        "plan": row[1],
        "expires_at": row[2],
        "is_active": row[3],
        "is_demo": row[4],
        "last_payment_note": row[5],
    }


def set_subscription(
    chat_id: int,
    plan: str,
    expires_at: Optional[datetime],
    is_active: int = 1,
    is_demo: int = 0,
    note: Optional[str] = None,
):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE subscriptions
        SET plan=?, expires_at=?, is_active=?, is_demo=?, last_payment_note=COALESCE(?, last_payment_note)
        WHERE chat_id=?
        """,
        (
            plan,
            expires_at.isoformat() if expires_at else None,
            is_active,
            is_demo,
            note,
            chat_id,
        ),
    )
    conn.commit()
    conn.close()


def get_user_demo_status(chat_id: int) -> dict:
    record = get_user_record(chat_id)
    if not record:
        return {"demo_used": 0, "demo_expires_at": None}
    return {
        "demo_used": record.get("demo_used", 0),
        "demo_expires_at": record.get("demo_end_at") or record.get("demo_expires_at"),
    }


def mark_demo_used(chat_id: int, expires_at: datetime):
    update_user_status(
        chat_id,
        STATUS_DEMO,
        demo_start_at=datetime.utcnow(),
        demo_end_at=expires_at,
    )


def set_payment_note(chat_id: int, note: str):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE subscriptions SET last_payment_note=? WHERE chat_id=?",
        (note, chat_id),
    )
    conn.commit()
    conn.close()


def log_approved_payment(chat_id: int, plan: str, amount: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payments (chat_id, plan, amount, approved_at)
        VALUES (?, ?, ?, ?)
        """,
        (chat_id, plan, amount, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def parse_subscription_expiry(sub) -> Optional[datetime]:
    if sub and sub.get("expires_at"):
        try:
            return datetime.fromisoformat(str(sub["expires_at"]))
        except Exception:
            return None
    return None


def parse_referrer_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    for token in parts[1:]:
        if token.startswith("ref_"):
            try:
                ref_id = int(token.split("ref_", 1)[1])
                return ref_id if ref_id > 0 else None
            except Exception:
                return None
    return None


def format_referral_date(dt: datetime) -> str:
    months = [
        "Yanvar",
        "Fevral",
        "Mart",
        "Aprel",
        "May",
        "İyun",
        "İyul",
        "Avqust",
        "Sentyabr",
        "Oktyabr",
        "Noyabr",
        "Dekabr",
    ]
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def save_referral(referrer_chat_id: Optional[int], referred_chat_id: int, is_new_user: bool = False):
    if not referrer_chat_id or referrer_chat_id == referred_chat_id:
        return
    conn = get_local_conn()
    cur = conn.cursor()
    changed = False
    cur.execute(
        "SELECT referred_by FROM users WHERE chat_id=?",
        (referred_chat_id,),
    )
    row = cur.fetchone()
    if row and not is_new_user:
        conn.close()
        return
    cur.execute(
        "UPDATE users SET referred_by=? WHERE chat_id=? AND referred_by IS NULL",
        (referrer_chat_id, referred_chat_id),
    )
    if cur.rowcount:
        changed = True
    cur.execute(
        "SELECT COUNT(*) FROM referrals WHERE referred_chat_id=?",
        (referred_chat_id,),
    )
    exists = cur.fetchone()[0] or 0
    if not exists:
        try:
            cur.execute(
                """
                INSERT INTO referrals (referrer_chat_id, referred_chat_id, created_at, reward_given)
                VALUES (?, ?, ?, 0)
                """,
                (referrer_chat_id, referred_chat_id, datetime.utcnow().isoformat()),
            )
            changed = True
        except sqlite3.IntegrityError:
            pass
    if changed:
        conn.commit()
    conn.close()


def get_referral(referred_chat_id: int) -> Optional[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT referrer_chat_id, referred_chat_id, created_at, reward_given FROM referrals WHERE referred_chat_id=?",
        (referred_chat_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "referrer_chat_id": row[0],
        "referred_chat_id": row[1],
        "created_at": row[2],
        "reward_given": row[3],
    }


def mark_referral_rewarded(referred_chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE referrals SET reward_given=1 WHERE referred_chat_id=?",
        (referred_chat_id,),
    )
    conn.commit()
    conn.close()


def record_referral_log(referrer_id: int, referred_user_id: Optional[int], bonus_days: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO referral_logs (referrer_id, referred_user_id, bonus_days, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (referrer_id, referred_user_id, bonus_days, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def extend_subscription_with_bonus(chat_id: int, bonus_days: int, note: str) -> datetime:
    ensure_subscription_record(chat_id)
    sub = get_subscription(chat_id) or {}
    exp_dt = parse_subscription_expiry(sub)
    base = exp_dt if sub.get("is_active") and exp_dt and exp_dt > datetime.utcnow() else datetime.utcnow()
    new_exp = base + timedelta(days=bonus_days)
    plan_name = sub.get("plan") or note
    set_subscription(chat_id, plan_name, new_exp, is_active=1, is_demo=0, note=note)
    return new_exp


def maybe_award_milestone_bonus(referrer_chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT referral_milestone_used FROM users WHERE chat_id=?",
        (referrer_chat_id,),
    )
    row = cur.fetchone()
    if not row or row[0]:
        conn.close()
        return
    cur.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by=? AND referral_bonus_used=1",
        (referrer_chat_id,),
    )
    total_valid_refs = cur.fetchone()[0] or 0
    if total_valid_refs < REFERRAL_MILESTONE_COUNT:
        conn.close()
        return
    cur.execute(
        "UPDATE users SET referral_milestone_used=1 WHERE chat_id=? AND referral_milestone_used=0",
        (referrer_chat_id,),
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    if not updated:
        return
    new_exp = extend_subscription_with_bonus(
        referrer_chat_id, REFERRAL_MILESTONE_BONUS_DAYS, "referral_milestone"
    )
    record_referral_log(referrer_chat_id, None, REFERRAL_MILESTONE_BONUS_DAYS)
    try:
        bot.send_message(
            referrer_chat_id,
            (
                "🏆 Möhtəşəm!\n\n"
                "10 aktiv istifadəçi dəvət etdiniz 🎉\n"
                "🎁 Əlavə olaraq hesabınıza +45 gün hədiyyə edildi!\n\n"
                f"📅 Yeni bitmə tarixi: {format_referral_date(new_exp)}"
            ),
        )
    except Exception:
        pass


def apply_referral_bonus(referred_chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT referred_by, referral_bonus_used, blocked FROM users WHERE chat_id=?",
        (referred_chat_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    referrer_id, bonus_used, is_blocked = row
    if not referrer_id or bonus_used or is_blocked or referrer_id == referred_chat_id:
        conn.close()
        return
    cur.execute(
        "UPDATE users SET referral_bonus_used=1 WHERE chat_id=? AND referral_bonus_used=0",
        (referred_chat_id,),
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    if not updated:
        return
    new_exp = extend_subscription_with_bonus(
        referrer_id, REFERRAL_REWARD_DAYS, "referral_bonus"
    )
    record_referral_log(referrer_id, referred_chat_id, REFERRAL_REWARD_DAYS)
    mark_referral_rewarded(referred_chat_id)
    try:
        bot.send_message(
            referrer_id,
            (
                "🎉 Təbriklər!\n\n"
                "Dəvət etdiyiniz istifadəçi hesabını aktivləşdirdi.\n"
                f"⏳ Hesabınız +{REFERRAL_REWARD_DAYS} gün uzadıldı.\n\n"
                f"📅 Yeni bitmə tarixi: {format_referral_date(new_exp)}"
            ),
        )
    except Exception:
        pass
    maybe_award_milestone_bonus(referrer_id)


def process_referral_on_payment(referred_chat_id: int, sub_before_payment: Optional[dict], amount_paid: int):
    if amount_paid <= 0:
        return
    apply_referral_bonus(referred_chat_id)


def get_promo(code: str) -> Optional[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, days, is_active, created_at FROM promo_codes WHERE code=?",
        (code,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "code": row[0],
        "days": row[1],
        "is_active": row[2],
        "created_at": row[3],
    }


def generate_promo_code(days: int) -> Optional[str]:
    conn = get_local_conn()
    cur = conn.cursor()
    code = None
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(5):
        candidate = f"BH{days}-" + "".join(random.choices(alphabet, k=6))
        try:
            cur.execute(
                """
                INSERT INTO promo_codes (code, days, is_active, created_at)
                VALUES (?, ?, 1, ?)
                """,
                (candidate, days, datetime.utcnow().isoformat()),
            )
            conn.commit()
            code = candidate
            break
        except sqlite3.IntegrityError:
            continue
    conn.close()
    return code


def set_promo_status(code: str, active: bool):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE promo_codes SET is_active=? WHERE code=?",
        (1 if active else 0, code),
    )
    conn.commit()
    conn.close()


def has_active_promo(chat_id: int) -> bool:
    status = get_user_promo_status(chat_id)
    return bool(status.get("active"))


def has_used_promo(chat_id: int, code: str) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM promo_usages WHERE chat_id=? AND code=?",
        (chat_id, code),
    )
    res = cur.fetchone()[0] or 0
    conn.close()
    return res > 0


def record_promo_usage(code: str, chat_id: int, expires_at: datetime):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO promo_usages (code, chat_id, used_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (code, chat_id, datetime.utcnow().isoformat(), expires_at.isoformat()),
    )
    conn.commit()
    conn.close()


def format_promo_date(dt: Optional[datetime], include_year: bool = False) -> str:
    if not dt:
        return "—"
    fmt = "%d %b %Y" if include_year else "%d %b"
    return dt.strftime(fmt)


def set_user_promo_status(chat_id: int, active: bool, expires_at: Optional[datetime] = None):
    conn = get_local_conn()
    cur = conn.cursor()
    exp_val = expires_at.isoformat() if expires_at else None
    try:
        cur.execute(
            "UPDATE users SET promo_active=?, promo_expires_at=? WHERE chat_id=?",
            (1 if active else 0, exp_val, chat_id),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def get_user_promo_status(chat_id: int) -> dict:
    status = {"active": False, "expires_at": None}
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT promo_active, promo_expires_at FROM users WHERE chat_id=?",
            (chat_id,),
        )
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return status

    if not row:
        conn.close()
        return status

    promo_active = row[0] or 0
    expires_raw = row[1]
    expires_at = None
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(str(expires_raw))
        except Exception:
            expires_at = None

    now = datetime.utcnow()
    if promo_active and expires_at and expires_at > now:
        status.update({"active": True, "expires_at": expires_at})
    else:
        if promo_active:
            try:
                cur.execute(
                    "UPDATE users SET promo_active=0 WHERE chat_id=?", (chat_id,)
                )
                conn.commit()
            except Exception:
                pass

    if not status["active"]:
        try:
            cur.execute(
                """
                SELECT expires_at FROM promo_usages
                WHERE chat_id=? AND datetime(expires_at) > datetime('now')
                ORDER BY expires_at DESC
                LIMIT 1
                """,
                (chat_id,),
            )
            row = cur.fetchone()
            if row and row[0]:
                try:
                    fallback_exp = datetime.fromisoformat(str(row[0]))
                    status.update({"active": True, "expires_at": fallback_exp})
                    cur.execute(
                        "UPDATE users SET promo_active=1, promo_expires_at=? WHERE chat_id=?",
                        (fallback_exp.isoformat(), chat_id),
                    )
                    conn.commit()
                except Exception:
                    pass
        except sqlite3.OperationalError:
            pass

    conn.close()
    return status


def build_promo_button(chat_id: int, include_year: bool = False) -> types.InlineKeyboardButton:
    status = get_user_promo_status(chat_id)
    if status.get("active"):
        exp_text = format_promo_date(status.get("expires_at"), include_year)
        text = f"🎁 Aktiv promo mövcuddur (bitmə: {exp_text})"
        return types.InlineKeyboardButton(text, callback_data="promo_active_info")
    return types.InlineKeyboardButton("🎁 Promo kod daxil et", callback_data="promo_enter")


def send_promo_quick_action(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(build_promo_button(chat_id))
    bot.send_message(chat_id, "🎁 Promo menyusu:", reply_markup=mk)


def apply_promo_code(chat_id: int, code_raw: str):
    code = (code_raw or "").strip().upper()
    if not code:
        return False, "❌ Promo kod tapılmadı", None

    promo = get_promo(code)
    if not promo:
        return False, "❌ Promo kod tapılmadı", None
    if not promo.get("is_active"):
        return False, "❌ Bu promo kod deaktiv edilib", None
    if has_active_promo(chat_id):
        status = get_user_promo_status(chat_id)
        exp_text = format_promo_date(status.get("expires_at"), include_year=True)
        block_msg = (
            "❌ Aktiv promo kodunuz var.\n"
            "Yeni promo daxil etmək üçün mövcud promo bitməlidir.\n"
            f"📅 Cari promo bitmə tarixi: {exp_text}"
        )
        return False, block_msg, status.get("expires_at")
    if has_used_promo(chat_id, code):
        return False, "❌ Bu promo kodu artıq istifadə etmisiniz", None

    ensure_subscription_record(chat_id)
    sub = get_subscription(chat_id) or {}
    now = datetime.utcnow()
    exp_dt = None
    if sub.get("expires_at"):
        try:
            exp_dt = datetime.fromisoformat(str(sub["expires_at"]))
        except Exception:
            exp_dt = None

    base = exp_dt if exp_dt and exp_dt > now else now
    new_exp = base + timedelta(days=promo["days"])
    plan_name = sub.get("plan") or f"promo {promo['days']}g"
    set_subscription(chat_id, plan_name, new_exp, is_active=1, is_demo=0, note=f"promo:{code}")
    record_promo_usage(code, chat_id, new_exp)

    set_user_promo_status(chat_id, True, new_exp)

    success_msg = (
        "🎉 Promo uğurla aktiv edildi!\n"
        f"📅 Bitmə tarixi: {format_promo_date(new_exp, include_year=True)}"
    )

    return True, success_msg, new_exp


def activate_demo_if_needed(chat_id: int, force: bool = False):
    sub = get_subscription(chat_id)
    if not sub:
        return
    if sub.get("is_demo") or sub.get("plan") == "demo":
        return
    if sub.get("plan") or not force:
        return
    expires = datetime.utcnow() + timedelta(days=3)
    set_subscription(chat_id, "demo", expires, is_active=1, is_demo=1, note="demo")


def subscription_payment_code(chat_id: int) -> str:
    return f"BH-{chat_id}"



def is_demo_available(chat_id: int) -> bool:
    record = get_user_record(chat_id)
    status = get_user_demo_status(chat_id)
    lifecycle_status = record.get("status") if record else STATUS_PENDING
    if lifecycle_status in {STATUS_ACTIVE, STATUS_DEMO, STATUS_BLOCKED}:
        return False
    if status.get("demo_used"):
        return False
    sub = get_subscription(chat_id)
    now = datetime.utcnow()
    if sub and sub.get("is_active"):
        exp_dt = parse_subscription_expiry(sub)
        if not exp_dt or exp_dt > now:
            return False
    return True


def build_payment_menu_markup(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    for key, info in SUBSCRIPTION_PLANS.items():
        mk.add(
            types.InlineKeyboardButton(
                f"{info['title']} — {info['price']}", callback_data=f"payplan|{key}"
            )
        )
    if is_demo_available(chat_id):
        mk.add(
            types.InlineKeyboardButton(
                "🎁 3 günlük demo istifadə et", callback_data="demo3"
            )
        )
    mk.add(build_promo_button(chat_id))
    mk.add(types.InlineKeyboardButton("ℹ️ Haqqında", callback_data="payinfo"))
    return mk


def send_payment_menu(chat_id: int):
    mk = build_payment_menu_markup(chat_id)
    bot.send_message(
        chat_id,
        "💳 Abunəlik planını seç və ödəniş et:\n\n" "✅ Demo bitibsə, yeniləmək üçün plan seçin.",
        reply_markup=mk,
    )


def check_subscription(chat_id: int, silent: bool = False) -> bool:
    if is_admin(chat_id):
        return True

    record = get_user_record(chat_id)
    if not record:
        if not silent:
            send_payment_menu(chat_id)
        return False

    status = record.get("status", STATUS_PENDING)
    if status == STATUS_BLOCKED:
        if not silent:
            try:
                bot.send_message(chat_id, "❌ Hesabınız deaktiv edilib. Dəstək ilə əlaqə saxlayın.")
            except Exception:
                pass
        return False

    now = datetime.utcnow()
    sub = get_subscription(chat_id)
    paid_until = parse_dt_safe(record.get("paid_until"))
    if not paid_until and sub and sub.get("expires_at"):
        paid_until = parse_dt_safe(sub.get("expires_at"))
    demo_end = parse_dt_safe(record.get("demo_end_at") or record.get("demo_expires_at"))

    if status == STATUS_DEMO:
        if demo_end and demo_end <= now:
            update_user_status(chat_id, STATUS_PENDING)
            if not silent:
                try:
                    bot.send_message(
                        chat_id,
                        "💳 Demo bitdi. Ödəniş edərək botdan istifadəni davam etdirə bilərsiniz.",
                    )
                except Exception:
                    pass
                send_payment_menu(chat_id)
            return False
        return True

    if status == STATUS_ACTIVE:
        if paid_until and paid_until <= now:
            update_user_status(chat_id, STATUS_PENDING)
            if sub:
                conn = get_local_conn()
                cur = conn.cursor()
                cur.execute("UPDATE subscriptions SET is_active=0 WHERE chat_id=?", (chat_id,))
                conn.commit()
                conn.close()
            if not silent:
                try:
                    bot.send_message(chat_id, "⛔ Abunəlik müddəti bitib. Yeniləmək üçün ödəniş edin.")
                except Exception:
                    pass
                send_payment_menu(chat_id)
            return False
        if paid_until:
            return True
        update_user_status(chat_id, STATUS_PENDING)

    if not silent:
        send_payment_menu(chat_id)
    return False


def is_user_allowed(chat_id: int) -> bool:
    if is_admin(chat_id):
        return True
    record = get_user_record(chat_id)
    if not record:
        return False
    status = record.get("status", STATUS_PENDING)
    return status in {STATUS_ACTIVE, STATUS_DEMO}


def ensure_allowed(message) -> bool:
    chat_id = message.chat.id
    if is_admin(chat_id):
        return True
    if not check_subscription(chat_id):
        return False
    return True


def ensure_allowed_cb(c) -> bool:
    chat_id = c.message.chat.id
    if is_admin(chat_id):
        return True
    if not check_subscription(chat_id):
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
    conn = None
    source_is_main = source == "main" and os.path.exists(MAIN_DB)
    try:
        if source_is_main:
            conn = get_main_conn()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT operation FROM listings LIMIT 200")
        else:
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT operation FROM listings_approved LIMIT 200")
        values = {str(r[0]).strip().lower() for r in cur.fetchall() if r[0]}
    except Exception:
        values = set()
    finally:
        if conn:
            if source_is_main:
                close_main_conn(conn)
            else:
                conn.close()

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

    record_agent_activity(chat_id, metric="searches")


AGENT_METRIC_FIELDS = {
    "searches": "searches",
    "views": "views",
    "whatsapp": "whatsapp",
    "favorites": "favorites",
}


def is_agent_user(chat_id: Optional[int]) -> bool:
    if not chat_id:
        return False
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM agents WHERE chat_id=?", (chat_id,))
        row = cur.fetchone()
        conn.close()
        return bool(row)
    except Exception:
        return False


def record_agent_activity(chat_id: Optional[int], metric: Optional[str] = None):
    if not chat_id or not is_agent_user(chat_id):
        return

    now_iso = datetime.utcnow().isoformat()
    field = AGENT_METRIC_FIELDS.get(metric or "")

    conn = None
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agent_activity (chat_id, last_activity)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET last_activity=excluded.last_activity
            """,
            (chat_id, now_iso),
        )

        if field:
            cur.execute(
                f"UPDATE agent_activity SET {field}={field}+1, last_activity=? WHERE chat_id=?",
                (now_iso, chat_id),
            )

        conn.commit()
    except Exception as e:
        print("⚠️ Agent activity log error:", e)
    finally:
        if conn:
            conn.close()

def fetch_listing_by_source(source: str, listing_id: int):
    if source == "main" and os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM listings WHERE id=?", (listing_id,))
        row = cur.fetchone()
        close_main_conn(conn)
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


def fetch_listing_by_any(listing_id: int):
    for src in ("main", "local", "agents"):
        ev = fetch_listing_by_source(src, listing_id)
        if ev:
            return ev
    return None


def should_track_interaction(chat_id: Optional[int], listing_id: int, action: str) -> bool:
    if chat_id is None:
        return True
    cache = session_interactions.setdefault(chat_id, set())
    key = (action, listing_id)
    if key in cache:
        return False
    cache.add(key)
    return True


def record_listing_stat(listing_id: Optional[int], action: str, chat_id: Optional[int] = None):
    if not listing_id:
        return
    if not should_track_interaction(chat_id, listing_id, action):
        return
    field_map = {"view": "views", "favorite": "favorites", "contact": "contacts"}
    field = field_map.get(action)
    if not field:
        return
    now_iso = datetime.utcnow().isoformat()
    conn = None
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO listing_stats (listing_id, last_interaction)
            VALUES (?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                last_interaction=excluded.last_interaction
            """,
            (listing_id, now_iso),
        )
        cur.execute(
            f"UPDATE listing_stats SET {field}={field}+1 WHERE listing_id=?",
            (listing_id,),
        )
        cur.execute(
            """
            UPDATE listing_stats
            SET popularity_score = views * 1 + favorites * 3 + contacts * 5,
                last_interaction = ?
            WHERE listing_id = ?
            """,
            (now_iso, listing_id),
        )
        conn.commit()
    except Exception as e:
        print("⚠️ Listing stat error:", e)
    finally:
        if conn:
            conn.close()


def record_listing_view(source: str, listing_id: Optional[int], chat_id: Optional[int] = None):
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
    record_listing_stat(listing_id, "view", chat_id)


def query_top_viewed_listings(days: int = 7, offset: int = 0, limit: int = None):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM listing_stats WHERE popularity_score > 0"
    )
    total = cur.fetchone()[0] or 0
    sql = (
        "SELECT listing_id, views, favorites, contacts, popularity_score, last_interaction "
        "FROM listing_stats WHERE popularity_score > 0 "
        "ORDER BY popularity_score DESC, datetime(last_interaction) DESC"
    )
    params = []
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    status_map = get_status_map()
    enriched = []
    for r in rows:
        ev = fetch_listing_by_any(r["listing_id"])
        if not ev:
            continue
        if not is_listing_active(ev, status_map):
            continue
        ev["__views"] = r["views"]
        ev["__favorites"] = r["favorites"]
        ev["__contacts"] = r["contacts"]
        ev["__popularity"] = r["popularity_score"]
        ev["__last_interaction"] = r["last_interaction"]
        enriched.append(ev)

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


def send_refresh_button(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔄 Botu yenilə", callback_data="bot_refresh"))
    bot.send_message(chat_id, "🔄 Botu yenilə", reply_markup=mk)


def send_main_menu(chat_id: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📝 Yeni elan əlavə et")
    kb.row("🔎 Axtarış sistemi")
    kb.row("📂 Elan statusları")
    kb.row("⭐ Favorilərim", "📋 Elanlarım")
    kb.row("💳 Ödəniş", "ℹ️ Haqqında")
    kb.row("📩 Şikayət və təkliflər")
    kb.row("🔄 Botu yenilə")
    if is_admin(chat_id):
        kb.row("📊 Admin Panel")
    bot.send_message(chat_id, "🏠 Əsas menyu:", reply_markup=kb)


def build_search_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Satılır")
    kb.row("🏢 Kirayə verilir")
    kb.row("🔍 Açar sözlə axtar")
    kb.row("📞 Nömrə ilə axtar")
    kb.row("⭐ Favorilərim")
    kb.row("🔔 Bildirişlərim")
    kb.row("⬅️ Əsas menyuya qayıt")
    return kb


def send_search_menu(chat_id: int):
    kb = build_search_menu_keyboard()
    bot.send_message(chat_id, "\u2063", reply_markup=kb)


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
    return f"https://wa.me/{p}?text={quote(text, safe='')}"


def build_whatsapp_message(ev: dict) -> str:
    op_raw = (ev.get("operation") or ev.get("Emeliyyat") or "").lower()
    is_rent = "kir" in op_raw or "rent" in op_raw

    rooms_val = ev.get("rooms") or ev.get("Otaq_sayi") or ""
    rooms_txt = f"{rooms_val} otaqlı" if rooms_val else "mənzil"

    location_raw = ev.get("rayon") or ev.get("Rayon_Qesebe") or ""
    if not location_raw:
        location_raw = ev.get("address") or ev.get("Unvan") or ""

    loc_suffix = ""
    loc_lower = location_raw.lower()
    if location_raw:
        if "qəs" in loc_lower or "qes" in loc_lower:
            loc_suffix = " qəsəbəsində"
        else:
            loc_suffix = " rayonunda"

    body = "Salam, "
    if location_raw:
        body += f"{location_raw}{loc_suffix} paylaşdığınız "
    else:
        body += "paylaşdığınız "

    if is_rent:
        body += f"{rooms_txt} kirayə mənzil hələ mövcuddur?"
    else:
        body += f"{rooms_txt} satışda olan mənzil satılıb?"

    link = ev.get("link") or ev.get("source_link")
    if link:
        body += f"\n{link}"
    return body


def send_listing_card(
    chat_id: int,
    ev: dict,
    source: str = "main",
    with_fav_button: bool = True,
    status_controls: bool = True,
    extra_buttons=None,
    track_view: bool = False,
    viewer_id: Optional[int] = None,
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

    if viewer_id:
        record_agent_activity(viewer_id, metric="views")

    if listing_pk and track_view:
        record_listing_view(source, listing_pk, viewer_id)

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

    wa_message = build_whatsapp_message(ev)
    wa_url = make_whatsapp_url(phone, wa_message)
    if wa_url:
        mk.add(types.InlineKeyboardButton("💬 WhatsApp-da yaz", url=wa_url))

    if extra_buttons:
        for btn in extra_buttons:
            mk.add(btn)

    if link:
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))

    stats_parts = []
    if ev.get("__views") is not None:
        stats_parts.append(f"👁 Baxış sayı: {ev['__views']}")
    if ev.get("__favorites") is not None:
        stats_parts.append(f"⭐ Favorit sayı: {ev['__favorites']}")
    if ev.get("__contacts") is not None:
        stats_parts.append(f"📞 Əlaqə sayı: {ev['__contacts']}")
    if ev.get("__popularity") is not None:
        stats_parts.append(f"🔥 Populyarlıq skoru: {ev['__popularity']}")
    if stats_parts:
        text += "\n" + "\n".join(stats_parts)

    bot.send_message(chat_id, text, reply_markup=mk)


@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_id = message.chat.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""
    first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    search_reminder_shown.discard(chat_id)
    reset_user_state(chat_id)
    search_state.pop(chat_id, None)
    referrer_chat_id = parse_referrer_from_text(message.text or "")
    referred_by_value = referrer_chat_id if referrer_chat_id and referrer_chat_id != chat_id else None

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
            """
            INSERT INTO users (chat_id, username, full_name, first_seen, approved, is_admin, last_version, referred_by, referral_bonus_used, referral_milestone_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                username,
                full_name,
                first_seen,
                0,
                0,
                CURRENT_VERSION,
                referred_by_value,
                0,
                0,
            ),
        )
        conn.commit()

        if referred_by_value:
            save_referral(referred_by_value, chat_id, is_new_user=True)

        update_user_status(chat_id, STATUS_PENDING)
    else:
        record = get_user_record(chat_id)
        existing_status = record.get("status") if record else STATUS_PENDING
        if existing_status not in {STATUS_ACTIVE, STATUS_DEMO, STATUS_BLOCKED}:
            update_user_status(chat_id, STATUS_PENDING)

    ensure_subscription_record(chat_id)
    if is_first_time:
        send_payment_menu(chat_id)

    # 🧩 Admin üçün avtomatik təsdiq
    if chat_id == ADMIN_ID:
        cur.execute(
            "UPDATE users SET approved=1, is_admin=1 WHERE chat_id=?", (chat_id,)
        )
        conn.commit()
        update_user_status(chat_id, STATUS_ACTIVE)
        conn.close()
        main_menu(chat_id)
        return

    conn.close()

    user_record = get_user_record(chat_id)
    if user_record and user_record.get("status") == STATUS_BLOCKED:
        bot.send_message(chat_id, "❌ Hesabınız deaktiv edilib. Dəstək ilə əlaqə saxlayın.")
        return

    if not check_subscription(chat_id, silent=True):
        send_payment_menu(chat_id)
        return

    main_menu(chat_id)


@bot.message_handler(func=lambda m: m.text == "🤝 Dostunu dəvət et")
def share_referral(message):
    chat_id = message.chat.id
    if is_admin(chat_id):
        main_menu(chat_id)
        return
    if not is_user_allowed(chat_id):
        bot.send_message(
            chat_id,
            "🛑 Botdan istifadə üçün admin təsdiqi tələb olunur.",
        )
        return

    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{chat_id}"
    text = (
        "🤝 Dostunu dəvət et və BONUS qazan!\n\n"
        f"Bu linki dostuna göndər:\n{referral_link}\n\n"
        "Bu link vasitəsi ilə botda qeydiyyatdan keçən\n"
        "və hesabını ən azı 1 gün aktivləşdirən\n"
        "HƏR istifadəçi üçün:\n\n"
        "🎁 Sənə +3 gün PULSUZ botdan istifadə!\n\n"
        "🎯 10 istifadəçi tamam olduqda isə:\n"
        "→ +45 gün əlavə BONUS 🎉"
    )
    bot.send_message(chat_id, text)


# =============== 📩 Şikayət və təkliflər ===============


def build_complaint_categories_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for cat in COMPLAINT_CATEGORIES:
        kb.row(cat)
    kb.row(COMPLAINT_BACK)
    return kb


def start_complaint_flow(chat_id: int):
    now = time.time()
    last_ts = last_complaint_time.get(chat_id)
    if last_ts and now - last_ts < COMPLAINT_COOLDOWN_SECONDS:
        bot.send_message(chat_id, "⏳ Zəhmət olmasa bir neçə dəqiqə sonra yenidən göndərin.")
        return
    complaint_flow_state[chat_id] = {"step": "category"}
    bot.send_message(
        chat_id,
        "📂 Kateqoriyanı seçin:",
        reply_markup=build_complaint_categories_keyboard(),
    )


def notify_admin_complaint(message, category: str, user_text: str):
    chat_id = message.chat.id
    user = message.from_user
    full_name = user.full_name if user else ""
    username = f"@{user.username}" if user and user.username else "-"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    complaint_id = str(int(time.time() * 1000)) + str(random.randint(1000, 9999))
    complaint_records[complaint_id] = {
        "complaint_id": complaint_id,
        "user_id": chat_id,
        "category": category,
        "message": user_text,
        "timestamp": ts,
    }
    text = (
        "📩 Yeni şikayət / təklif\n\n"
        "👤 İstifadəçi:\n"
        f"ID: {chat_id}\n"
        f"Ad: {full_name or '-'}\n"
        f"Username: {username}\n\n"
        "📂 Kateqoriya:\n"
        f"{category}\n\n"
        "📝 Mesaj:\n"
        f"{user_text}\n\n"
        "⏰ Tarix:\n"
        f"{ts}\n\n"
        f"🆔 ID: {complaint_id}"
    )
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "✉️ Cavab yaz", callback_data=f"complaint_reply:{complaint_id}:{chat_id}"
        )
    )
    bot.send_message(ADMIN_ID, text, reply_markup=mk)


@bot.message_handler(func=lambda m: m.text == "📩 Şikayət və təkliflər")
def complaint_entry(message):
    if not ensure_allowed(message):
        return
    start_complaint_flow(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "open_complaint")
def cb_open_complaint(c):
    if not ensure_allowed_cb(c):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    start_complaint_flow(c.message.chat.id)


@bot.message_handler(
    func=lambda m: complaint_flow_state.get(m.chat.id, {}).get("step") == "category"
)
def complaint_category_handler(message):
    chat_id = message.chat.id
    choice = message.text
    if choice == COMPLAINT_BACK:
        complaint_flow_state.pop(chat_id, None)
        send_main_menu(chat_id)
        return
    if choice not in COMPLAINT_CATEGORIES:
        bot.send_message(
            chat_id,
            "📂 Kateqoriyanı seçin:",
            reply_markup=build_complaint_categories_keyboard(),
        )
        return
    complaint_flow_state[chat_id] = {"step": "message", "category": choice}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(COMPLAINT_BACK)
    bot.send_message(chat_id, "✍️ Zəhmət olmasa mesajınızı yazın.", reply_markup=kb)


@bot.message_handler(
    func=lambda m: complaint_flow_state.get(m.chat.id, {}).get("step") == "message",
    content_types=["text"],
)
def complaint_message_handler(message):
    chat_id = message.chat.id
    text = message.text
    if text == COMPLAINT_BACK:
        complaint_flow_state[chat_id] = {"step": "category"}
        bot.send_message(
            chat_id,
            "📂 Kateqoriyanı seçin:",
            reply_markup=build_complaint_categories_keyboard(),
        )
        return
    data = complaint_flow_state.pop(chat_id, {})
    category = data.get("category", "-")
    last_complaint_time[chat_id] = time.time()
    try:
        notify_admin_complaint(message, category, text)
    except Exception:
        pass
    bot.send_message(
        chat_id,
        "✅ Mesajınız qəbul edildi.\nTəşəkkür edirik! 🙏",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    send_main_menu(chat_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("complaint_reply:"))
def complaint_reply_callback(c):
    if not is_admin(c.from_user.id):
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return
    parts = c.data.split(":")
    if len(parts) < 3:
        try:
            bot.answer_callback_query(c.id, "Məlumat tapılmadı.")
        except Exception:
            pass
        return
    complaint_id = parts[1]
    try:
        target = int(parts[2])
    except Exception:
        target = None
    if not target:
        try:
            bot.answer_callback_query(c.id, "Məlumat tapılmadı.")
        except Exception:
            pass
        return

    if complaint_id not in complaint_records:
        complaint_records[complaint_id] = {
            "complaint_id": complaint_id,
            "user_id": target,
            "category": "",
            "message": "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    admin_reply_state[c.from_user.id] = {
        "target": target,
        "complaint_id": complaint_id,
    }
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    bot.send_message(c.message.chat.id, "✍️ Cavabı yazın:")


@bot.message_handler(
    func=lambda m: admin_reply_state.get(m.chat.id) is not None,
    content_types=["text"],
)
def admin_reply_to_user(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        admin_reply_state.pop(chat_id, None)
        return
    data = admin_reply_state.pop(chat_id, {})
    target = data.get("target")
    complaint_id = data.get("complaint_id")
    if not target:
        return
    try:
        bot.send_message(target, f"📩 Admin cavabı:\n\n{message.text}")
    except Exception:
        bot.send_message(chat_id, "⚠️ Cavab göndərilə bilmədi.")
        return

    if complaint_id:
        record = complaint_records.get(complaint_id, {})
        record["reply"] = message.text
        record["replied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        complaint_records[complaint_id] = record

    bot.send_message(chat_id, "✅ Cavab istifadəçiyə göndərildi.")


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
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("📩 Şikayət və təkliflər", callback_data="open_complaint"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=mk)


@bot.message_handler(func=lambda m: m.text == "💳 Ödəniş")
def payment_menu_entry(message):
    send_payment_menu(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "payinfo")
def cb_payinfo(c):
    about(c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def send_active_promo_info(chat_id: int):
    status = get_user_promo_status(chat_id)
    exp_text = format_promo_date(status.get("expires_at"), include_year=True)
    bot.send_message(
        chat_id,
        "⚠️ Aktiv promo müddətiniz bitməyib.\n\n"
        f"📅 Bitmə tarixi: {exp_text}\n"
        "⏳ Yeni promo yalnız bu tarixdən sonra aktiv edilə bilər.",
    )


@bot.callback_query_handler(func=lambda c: c.data in ("promoenter", "promo_enter"))
def cb_promo_enter(c):
    chat_id = c.message.chat.id
    status = get_user_promo_status(chat_id)
    if status.get("active"):
        send_active_promo_info(chat_id)
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return

    user_state[chat_id] = {"step": "WAITING_PROMO_CODE"}
    msg = bot.send_message(chat_id, "🎁 Promo kodu daxil edin:")
    bot.register_next_step_handler(msg, promo_code_entry_step)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "promo_active_info")
def cb_promo_active_info(c):
    chat_id = c.message.chat.id
    send_active_promo_info(chat_id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def promo_code_entry_step(message):
    chat_id = message.chat.id
    success, response, _ = apply_promo_code(chat_id, message.text)
    bot.send_message(chat_id, response)
    reset_user_state(chat_id)
    if success:
        main_menu(chat_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("payplan|"))
def cb_payplan(c):
    chat_id = c.message.chat.id
    plan_key = c.data.split("|")[1]
    plan = SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        return
    ensure_subscription_record(chat_id)
    set_payment_note(chat_id, f"plan:{plan_key}")

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("✅ Ödəniş etdim", callback_data=f"paydone|{plan_key}"))

    pay_text = (
        "💳 Ödəniş üçün:\n"
        "Telegram: @esedovesed\n"
        "WhatsApp: 0708468585\n\n"
        "🆔 Ödəniş kodunuz:\n"
        f"{subscription_payment_code(chat_id)}"
    )
    bot.send_message(chat_id, pay_text, reply_markup=mk)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "demo3")
def cb_demo_activate(c):
    chat_id = c.message.chat.id
    if not is_demo_available(chat_id):
        try:
            bot.answer_callback_query(c.id, "Demo artıq istifadə olunub və ya hesab aktivdir", show_alert=True)
        except Exception:
            pass
        return
    ensure_subscription_record(chat_id)
    expires = datetime.utcnow() + timedelta(days=3)
    set_subscription(chat_id, "demo", expires, is_active=1, is_demo=1, note="demo")
    mark_demo_used(chat_id, expires)
    try:
        bot.edit_message_reply_markup(
            chat_id,
            c.message.message_id,
            reply_markup=build_payment_menu_markup(chat_id),
        )
    except Exception:
        pass
    bot.send_message(
        chat_id,
        "🎉 Demo aktiv edildi!\nBotdan 3 gün pulsuz istifadə edə bilərsiniz.",
    )
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT full_name, username FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    full_name = row[0] if row else ""
    username = row[1] if row else ""
    admin_username = f"@{username}" if username else "-"
    admin_text = (
        "👤 Yeni demo istifadəçisi\n\n"
        f"ID: {chat_id}\n"
        f"Ad: {full_name if full_name else '-'}\n"
        f"Username: {admin_username}\n\n"
        "⏳ Demo bitmə tarixi:\n"
        f"{expires.strftime('%d.%m.%Y %H:%M')}"
    )
    bot.send_message(ADMIN_ID, admin_text)
    reset_user_state(chat_id)
    reset_search_state(chat_id)
    send_main_menu(chat_id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("paydone|"))
def cb_paydone(c):
    chat_id = c.message.chat.id
    plan_key = c.data.split("|")[1]
    plan = SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        return
    sub = get_subscription(chat_id) or {}
    demo_status = "Bəli" if sub.get("is_demo") else "Xeyr"

    admin_text = (
        "🆕 Ödəniş sorğusu\n"
        f"👤 chat_id: {chat_id}\n"
        f"🆔 Kod: {subscription_payment_code(chat_id)}\n"
        f"📦 Plan: {plan['title']} ({plan['price']})\n"
        f"🎁 Demo: {demo_status}"
    )
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "✅ Təsdiqlə", callback_data=f"payadm|ok|{chat_id}|{plan_key}"
        ),
        types.InlineKeyboardButton(
            "❌ Ləğv et", callback_data=f"payadm|rej|{chat_id}|{plan_key}"
        ),
    )
    bot.send_message(ADMIN_ID, admin_text, reply_markup=mk)
    bot.send_message(chat_id, "✅ Ödəniş sorğunuz adminə göndərildi. Nəticə barədə məlumat veriləcək.")
    try:
        bot.answer_callback_query(c.id, "Admin təsdiqi gözlənilir")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("payadm|"))
def cb_pay_admin(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    if len(parts) < 4:
        return
    action, uid_raw, plan_key = parts[1], parts[2], parts[3]
    try:
        uid = int(uid_raw)
    except Exception:
        return
    ensure_subscription_record(uid)
    sub = get_subscription(uid)
    plan = SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        return

    if action == "ok":
        expires = datetime.utcnow() + timedelta(days=plan["days"])
        set_subscription(uid, plan["title"], expires, is_active=1, is_demo=0, note=f"plan:{plan_key}")
        amount_val = parse_price_value(plan.get("price")) or 0
        if amount_val > 0:
            log_approved_payment(uid, plan["title"], amount_val)
        process_referral_on_payment(uid, sub, amount_val)
        update_user_status(uid, STATUS_ACTIVE, paid_until=expires)
        try:
            bot.send_message(
                uid,
                "✅ Hesabınız aktivləşdirildi\n"
                f"📅 Bitmə tarixi: {expires.strftime('%d.%m.%Y')}",
            )
        except Exception:
            pass
        bot.answer_callback_query(c.id, "✅ Aktiv edildi")
    elif action == "rej":
        try:
            bot.send_message(uid, "❌ Ödəniş təsdiqlənmədi. Zəhmət olmasa adminlə əlaqə saxlayın.")
        except Exception:
            pass
        bot.answer_callback_query(c.id, "İmtina edildi")


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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "role")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "operation")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "prop_type")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "rooms")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "rayon")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "metro")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "area")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "price")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "currency")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "phone")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "contact_name")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "summary")
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


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "link")
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
    added = cur.rowcount
    conn.commit()
    conn.close()
    record_favorite_price(src, lid)
    if added:
        record_listing_stat(lid, "favorite", chat_id)
        record_agent_activity(chat_id, metric="favorites")
    bot.answer_callback_query(c.id, "⭐ Favoriyə əlavə olundu.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("wa|"))
def cb_whatsapp_click(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split("|")
    if len(parts) != 3:
        return
    src, sid = parts[1], parts[2]
    try:
        lid = int(sid)
    except Exception:
        lid = None
    ev = fetch_listing_by_source(src, lid) if lid else None
    if not ev:
        bot.answer_callback_query(c.id, "❌ Elan tapılmadı.")
        return
    phone = ev.get("phone") or ev.get("Elaqe_nomresi")
    wa_message = build_whatsapp_message(ev)
    wa_url = make_whatsapp_url(phone, wa_message)
    record_listing_stat(lid, "contact", c.message.chat.id)
    record_agent_activity(c.message.chat.id, metric="whatsapp")
    if wa_url:
        try:
            bot.answer_callback_query(c.id, url=wa_url)
        except Exception:
            pass
    else:
        bot.answer_callback_query(c.id, "📞 Nömrə tapılmadı.")


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
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Bu bölmə yalnız admin üçündür.")
        return
    chat_id = message.chat.id
    reset_search_state(chat_id)
    send_paginated_results(chat_id, "topviews", params={"days": 7}, page=1)

# =============== 🔎 AXTARIŞ SİSTEMİ ===============


@bot.message_handler(func=lambda m: m.text == "🔎 Axtarış sistemi")
def search_system_menu(message):
    if not ensure_allowed(message):
        return
    send_search_menu(message.chat.id)


def start_structured_search_from_menu(chat_id: int, op_code: str):
    reset_search_state(chat_id)
    search_state[chat_id] = {
        "mode": "structured",
        "filters": {},
        "history": [],
        "awaiting_floor_range": False,
        "step": "op",
    }
    search_state[chat_id]["filters"]["op"] = op_code
    structured_push_history(chat_id)
    render_prop_step(chat_id)


@bot.message_handler(func=lambda m: m.text in ["🏠 Satılır", "🏢 Kirayə verilir"])
def structured_search_from_menu(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "structured", 200):
        bot.send_message(chat_id, "Günlük filtrli axtarış limitiniz bitib.")
        return
    op_code = "sat" if message.text == "🏠 Satılır" else "kir"
    start_structured_search_from_menu(chat_id, op_code)


@bot.message_handler(func=lambda m: m.text == "🔍 Açar sözlə axtar")
def keyword_search_from_menu(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "keyword", 30):
        bot.send_message(chat_id, "Günlük açar sözlə axtarış limitiniz bitib.")
        return
    search_state[chat_id] = {"mode": "keyword", "operation": None}
    send_keyword_operation_prompt(chat_id)


@bot.message_handler(func=lambda m: m.text == "📞 Nömrə ilə axtar")
def phone_search_from_menu(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "phone", 50):
        bot.send_message(chat_id, "Günlük nömrə ilə axtarış limitiniz bitib.")
        return
    msg = bot.send_message(chat_id, "☎️ Axtarmaq istədiyiniz nömrəni yazın:")
    bot.register_next_step_handler(msg, phone_search_handler)


def return_to_main_menu(chat_id: int):
    search_state.pop(chat_id, None)
    admin_panel_page_state.pop(chat_id, None)
    if is_admin(chat_id):
        send_main_menu(chat_id)
    else:
        main_menu(chat_id)


def format_saved_search_entry(row: dict) -> str:
    op = row.get("operation")
    if op == "sale":
        op_txt = "Satılır"
    elif op == "rent":
        op_txt = "Kirayə"
    else:
        op_txt = "Hamısı"

    parts = [f"💼 {op_txt}"]

    rooms = row.get("rooms")
    if rooms:
        parts.append(f"🚪 {rooms} otaq")

    price_min = row.get("price_min")
    price_max = row.get("price_max")
    if price_min is not None or price_max is not None:
        if price_min and price_max:
            parts.append(f"💰 {price_min}-{price_max}")
        elif price_min:
            parts.append(f"💰 {price_min}+")
        elif price_max:
            parts.append(f"💰 0-{price_max}")

    rayon = row.get("rayon")
    if rayon:
        parts.append(f"📍 {rayon}")

    prop_type = row.get("prop_type")
    if prop_type:
        parts.append(f"🏠 {prop_type}")

    return " | ".join(parts)


@bot.message_handler(
    func=lambda m: not is_admin(m.chat.id) and m.text == ADMIN_PANEL_BACK_MAIN
)
def public_back_to_main(message):
    if not ensure_allowed(message):
        return
    return_to_main_menu(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "🔔 Bildirişlərim")
def show_saved_notifications(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT operation, rooms, price_min, price_max, rayon, prop_type FROM saved_searches WHERE chat_id=?",
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "🔔 Aktiv bildirişiniz yoxdur.")
        return

    lines = [format_saved_search_entry(dict(r)) for r in rows]
    bot.send_message(chat_id, "🔔 Aktiv bildirişləriniz:\n" + "\n".join(lines))


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
        close_main_conn(conn)

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
        close_main_conn(conn)

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
        close_main_conn(conn)

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
        close_main_conn(conn)

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
        close_main_conn(conn)

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
    if mode == "topviews" and not is_admin(chat_id):
        if not replace_loading_message(loading_ref, "❌ Bu bölmə yalnız admin üçündür."):
            bot.send_message(chat_id, "❌ Bu bölmə yalnız admin üçündür.")
        return
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
        track_view = mode in ("favorites", "statuslist")
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
                track_view=track_view,
                viewer_id=chat_id,
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
                track_view=track_view,
                viewer_id=chat_id,
            )
        else:
            ev = item
            send_listing_card(
                chat_id,
                ev,
                source=ev.get("__source", "main"),
                with_fav_button=True,
                track_view=False,
                viewer_id=chat_id,
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
            types.InlineKeyboardButton("Hamısı", callback_data="fs|pr|s0"),
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
    mk.add(types.InlineKeyboardButton("Hamısı", callback_data="fs|fl|fall"))
    mk.add(types.InlineKeyboardButton("✏️ Əl ilə daxil et", callback_data="fs|fm"))
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
        bot.send_message(chat_id, "✏️ Mərtəbəni yazın (məs: 3 və ya 1-3):")
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

    txt_clean = _re.sub(r"\s+", "", txt)

    single_match = _re.fullmatch(r"\d+", txt_clean)
    range_match = _re.fullmatch(r"\d+-\d+", txt_clean)

    if single_match:
        mn = mx = int(txt_clean)
    elif range_match:
        parts = txt_clean.split("-")
        try:
            mn = int(parts[0])
            mx = int(parts[1])
        except Exception:
            bot.send_message(chat_id, "❌ Yanlış format. Məsələn: 3 və ya 1-2")
            return
    else:
        bot.send_message(chat_id, "❌ Yanlış format. Məsələn: 3 və ya 1-2")
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


def build_agents_panel_markup():
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
    return mk


def agents_panel(c):
    """Admin üçün vasitəçi elanları paneli."""
    chat_id = c.message.chat.id

    if not is_admin(chat_id):
        return

    mk = build_agents_panel_markup()

    bot.edit_message_text(
        "🏢 Vasitəçi elanları axtarış sistemi:",
        chat_id=chat_id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


def send_agents_panel_message(chat_id: int):
    if not is_admin(chat_id):
        return
    mk = build_agents_panel_markup()
    bot.send_message(chat_id, "🏢 Vasitəçi elanları axtarış sistemi:", reply_markup=mk)


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


def build_admin_panel_keyboard(chat_id: int, page: int = 1):
    buttons = ADMIN_PANEL_PAGE1 if page == 1 else ADMIN_PANEL_PAGE2
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i in range(0, len(buttons), 2):
        mk.row(*buttons[i : i + 2])
    if page == 1:
        mk.row(ADMIN_PANEL_NAV_NEXT, ADMIN_PANEL_BACK_MAIN)
    else:
        mk.row(ADMIN_PANEL_NAV_PREV, ADMIN_PANEL_BACK_MAIN)
    admin_panel_page_state[chat_id] = page
    return mk


def send_admin_panel(chat_id: int, page: int = 1, text: str = "🛠 Admin Panel:"):
    mk = build_admin_panel_keyboard(chat_id, page)
    bot.send_message(chat_id, text, reply_markup=mk)


@bot.message_handler(func=lambda m: m.text == "📊 Admin Panel")
@bot.message_handler(commands=["admin"])
def open_admin_panel(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Bu bölməyə yalnız admin daxil ola bilər.")
        return

    send_admin_panel(message.chat.id, page=1)


@bot.message_handler(func=lambda m: is_admin(m.chat.id) and m.text == ADMIN_PANEL_NAV_NEXT)
def admin_panel_next_page(message):
    send_admin_panel(message.chat.id, page=2)


@bot.message_handler(func=lambda m: is_admin(m.chat.id) and m.text == ADMIN_PANEL_NAV_PREV)
def admin_panel_prev_page(message):
    send_admin_panel(message.chat.id, page=1)


@bot.message_handler(func=lambda m: is_admin(m.chat.id) and m.text == ADMIN_PANEL_BACK_MAIN)
def admin_panel_back_to_main(message):
    return_to_main_menu(message.chat.id)


@bot.message_handler(func=lambda m: is_admin(m.chat.id) and m.text in ADMIN_PANEL_ACTIONS)
def handle_admin_panel_action(message):
    chat_id = message.chat.id
    txt = message.text

    if txt == "✅ Təsdiqlənməyən elanlar":
        show_pending_listings(chat_id)
    elif txt == "📊 Statistikalar":
        admin_stats_period[chat_id] = "day"
        show_admin_stats(chat_id)
    elif txt == FINANCIAL_REPORTS_BUTTON:
        send_financial_reports_menu(chat_id)
    elif txt == "📢 Vasitəçilərə bildiriş":
        msg = bot.send_message(chat_id, "✍️ Vasitəçilərə göndəriləcək mətni yaz:")
        bot.register_next_step_handler(msg, admin_agents_broadcast)
    elif txt == "🧠 Aktiv / passiv maklerlər":
        show_agent_activity_overview(chat_id)
    elif txt == "🧪 Demo istifadəçilər":
        send_demo_users_report(chat_id)
    elif txt == "🆔 İstifadəçi ID ilə axtar":
        msg = bot.send_message(chat_id, "🔍 İstifadəçi chat_id daxil et:")
        bot.register_next_step_handler(msg, admin_search_by_id_step)
    elif txt == "🎟 Promo kodlar":
        show_admin_promo_menu(chat_id)
    elif txt == "♻️ Limitləri sıfırla":
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM search_limits")
        conn.commit()
        conn.close()
        bot.send_message(chat_id, "♻️ Bütün istifadəçi limitləri sıfırlandı.")
    elif txt == "👥 İstifadəçilər":
        show_users_menu(chat_id)
    elif txt == "🚀 Yeniləmə göndər":
        broadcast_bot_update(chat_id)
    elif txt == "🔥 Ən çox baxılan elanlar":
        reset_search_state(chat_id)
        send_paginated_results(chat_id, "topviews", params={"days": 7}, page=1)
    elif txt == "📦 Baza yenilə":
        start_admin_update_db(chat_id)
    elif txt == "📨 İstifadəçiyə mesaj göndər":
        start_direct_user_message_flow(chat_id)


def send_financial_reports_menu(chat_id: int):
    if not is_admin(chat_id):
        return

    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    mk.row("📜 Ödəniş tarixçəsi", "🤝 Referral statistikası")
    mk.row("📈 Aylıq gəlir hesabatı")
    mk.row(FINANCIAL_REPORTS_BACK)
    bot.send_message(chat_id, "💰 Maliyyə hesabatları:", reply_markup=mk)


@bot.message_handler(func=lambda m: is_admin(m.chat.id) and m.text in FINANCIAL_REPORTS_MENU)
def handle_financial_reports_menu(message):
    if message.text == "📜 Ödəniş tarixçəsi":
        show_payment_history_list(message.chat.id, page=1)
    elif message.text == "🤝 Referral statistikası":
        show_referral_stats(message.chat.id)
    elif message.text == "📈 Aylıq gəlir hesabatı":
        show_revenue_report(message.chat.id)
    elif message.text == FINANCIAL_REPORTS_BACK:
        page = admin_panel_page_state.get(message.chat.id, 1)
        send_admin_panel(message.chat.id, page=page)


def format_remaining_time(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days} gün")
    if hours:
        parts.append(f"{hours} saat")
    if not parts:
        parts.append(f"{minutes} dəq")
    return " ".join(parts)


def send_demo_users_report(chat_id: int):
    if not is_admin(chat_id):
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.chat_id,
               u.full_name,
               u.username,
               u.demo_expires_at,
               s.expires_at AS sub_expires_at
        FROM users u
        LEFT JOIN subscriptions s ON s.chat_id = u.chat_id
        WHERE u.demo_used=1 OR s.is_demo=1
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "❌ Demo istifadəçisi yoxdur.")
        return

    now = datetime.utcnow()
    entries = []

    for r in rows:
        expiry_raw = r["demo_expires_at"] or r["sub_expires_at"]
        expiry_dt = None
        if expiry_raw:
            try:
                expiry_dt = datetime.fromisoformat(str(expiry_raw))
            except Exception:
                try:
                    expiry_dt = datetime.fromisoformat(str(expiry_raw).replace(" ", "T"))
                except Exception:
                    expiry_dt = None

        start_dt = expiry_dt - timedelta(days=3) if expiry_dt else None

        name = r["full_name"] or ""
        if r["username"]:
            uname = f"@{r['username']}"
            name = f"{name} ({uname})" if name else uname
        if not name:
            name = f"ID: {r['chat_id']}"

        line = f"• {name}"
        start_txt = start_dt.strftime("%d.%m.%Y") if start_dt else "-"
        end_txt = expiry_dt.strftime("%d.%m.%Y") if expiry_dt else "-"
        line += f" — start: {start_txt} — bitir: {end_txt}"

        if expiry_dt:
            if expiry_dt > now:
                remaining_txt = format_remaining_time(expiry_dt - now)
                line += f" — qalıq: {remaining_txt}"
            else:
                line += " — Bitib"
        else:
            line += " — Demo tarixi yoxdur"

        entries.append({"line": line, "expiry": expiry_dt or datetime.min})

    entries.sort(key=lambda x: x["expiry"], reverse=True)

    lines = ["🧪 Demo istifadəçilər"] + [item["line"] for item in entries]
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3800:
            bot.send_message(chat_id, chunk.strip())
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        bot.send_message(chat_id, chunk.strip())


def start_direct_user_message_flow(chat_id: int):
    if not is_admin(chat_id):
        return

    admin_direct_message_state[chat_id] = {"step": "awaiting_id"}
    msg = bot.send_message(
        chat_id, "📨 Mesaj göndərmək üçün istifadəçi ID-sini daxil et:"
    )
    bot.register_next_step_handler(msg, admin_direct_message_get_user)


def admin_direct_message_get_user(message):
    if not is_admin(message.chat.id):
        return

    state = admin_direct_message_state.get(message.chat.id)
    if not state or state.get("step") != "awaiting_id":
        return

    try:
        target_id = int(str(message.text).strip())
    except Exception:
        msg = bot.send_message(message.chat.id, "⚠️ Düzgün rəqəm daxil et:")
        bot.register_next_step_handler(msg, admin_direct_message_get_user)
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT full_name, username FROM users WHERE chat_id=?", (target_id,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        msg = bot.send_message(
            message.chat.id, "❌ İstifadəçi tapılmadı. Yenidən ID daxil et:"
        )
        bot.register_next_step_handler(msg, admin_direct_message_get_user)
        return

    name = row["full_name"] or "-"
    uname = f"@{row['username']}" if row["username"] else "—"

    admin_direct_message_state[message.chat.id] = {
        "step": "awaiting_message",
        "target_id": target_id,
        "target_name": name,
    }

    msg = bot.send_message(
        message.chat.id,
        f"📨 İstifadəçi tapıldı:\nID: {target_id}\nAd: {name}\nİstifadəçi adı: {uname}\n\n✍️ Mesajı yazın:",
    )
    bot.register_next_step_handler(msg, admin_direct_message_send)


def admin_direct_message_send(message):
    if not is_admin(message.chat.id):
        return

    state = admin_direct_message_state.get(message.chat.id)
    if not state or state.get("step") != "awaiting_message":
        return

    target_id = state.get("target_id")
    if not target_id:
        admin_direct_message_state.pop(message.chat.id, None)
        return

    if not message.text or not message.text.strip():
        msg = bot.send_message(message.chat.id, "⚠️ Mesaj boş ola bilməz. Yenidən yaz:")
        bot.register_next_step_handler(msg, admin_direct_message_send)
        return

    try:
        bot.send_message(target_id, message.text)
        bot.send_message(message.chat.id, "✅ Mesaj istifadəçiyə göndərildi.")
    except Exception:
        bot.send_message(
            message.chat.id, "⚠️ Mesaj göndərilə bilmədi. İstifadəçi əlçatmazdır."
        )
    finally:
        admin_direct_message_state.pop(message.chat.id, None)


def start_admin_update_db(chat_id: int, callback_id: Optional[str] = None):
    if not is_admin(chat_id):
        return

    if db_update_lock.locked():
        if callback_id:
            try:
                bot.answer_callback_query(callback_id, "⚠️ Baza yenilənir.")
            except Exception:
                pass
        bot.send_message(chat_id, "⚠️ Hal-hazırda baza yenilənir. Zəhmət olmasa gözləyin.")
        return

    if callback_id:
        try:
            bot.answer_callback_query(callback_id, "📦 Baza yeniləmə")
        except Exception:
            pass

    user_state[chat_id] = "WAITING_MAIN_DB"
    bot.send_message(
        chat_id,
        "🔗 Yeni besthome.zip yükləmə linkini göndərin.",
    )


@bot.callback_query_handler(func=lambda c: c.data == "admin_update_db")
def cb_admin_update_db(c):
    start_admin_update_db(c.message.chat.id, callback_id=c.id)


@bot.message_handler(
    content_types=["text"],
    func=lambda m: m.from_user
    and m.from_user.id == ADMIN_ID
    and user_state.get(m.chat.id) == "WAITING_MAIN_DB",
)
def handle_admin_db_upload(message):
    chat_id = message.chat.id
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return

    if user_state.get(chat_id) != "WAITING_MAIN_DB":
        return

    url = message.text.strip() if message.text else ""
    if not url or not re.match(r"https?://", url):
        bot.send_message(chat_id, "❌ Zəhmət olmasa keçərli link göndərin.")
        return

    if db_update_lock.locked():
        bot.send_message(
            chat_id,
            "⚠️ Hal-hazırda baza yenilənir. Zəhmət olmasa gözləyin.",
        )
        return

    temp_zip_path = None
    extracted_db_path = None
    extracted_dir = None
    with db_update_lock:
        try:
            main_db_update_in_progress.set()
            user_state[chat_id] = "UPDATING_MAIN_DB"
            temp_zip_path = download_main_db_zip(url)
            extracted_db_path, extracted_dir = extract_main_db_from_zip(temp_zip_path)
            validate_main_db_file(extracted_db_path)
            old_count = validate_main_db_file(MAIN_DB)
            close_all_main_conns()
            prepare_main_db_for_swap()

            shutil.copyfile(extracted_db_path, MAIN_DB)

            with open(MAIN_DB, "rb") as f:
                os.fsync(f.fileno())

            conn = sqlite3.connect(MAIN_DB)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM listings")
                row = cur.fetchone()
                final_count = int(row[0]) if row and row[0] is not None else 0
            finally:
                conn.close()

            added = final_count - old_count
            if added < 0:
                added = 0

            bot.send_message(
                chat_id,
                "✅ Baza uğurla yeniləndi.\n📦 Yeni elanlar: "
                f"{added}\n📊 Ümumi elan sayı: {final_count}",
            )
        except Exception as e:
            print("DB update error:", e)
            bot.send_message(
                chat_id,
                f"❌ Xəta baş verdi: {e}",
            )
        finally:
            main_db_update_in_progress.clear()
            user_state.pop(chat_id, None)
            for path in (temp_zip_path, extracted_db_path):
                if path and os.path.exists(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            os.remove(path)
                    except Exception:
                        pass
            if extracted_dir and os.path.exists(extracted_dir):
                shutil.rmtree(extracted_dir, ignore_errors=True)


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
        admin_stats_period[c.message.chat.id] = "day"
        show_admin_stats(c.message.chat.id)

    elif cmd == "revenue":
        show_revenue_report(c.message.chat.id)

    elif cmd == "referrals":
        show_referral_stats(c.message.chat.id)

    elif cmd == "agents_broadcast":
        msg = bot.send_message(
            c.message.chat.id, "✍️ Vasitəçilərə göndəriləcək mətni yaz:"
        )
        bot.register_next_step_handler(msg, admin_agents_broadcast)

    elif cmd == "agent_activity":
        show_agent_activity_overview(c.message.chat.id)

    elif cmd == "search":
        msg = bot.send_message(
            c.message.chat.id, "🔍 Açar söz yaz (əsas baza + lokal):"
        )
        bot.register_next_step_handler(msg, admin_search_handler)

    elif cmd == "user_search_id":
        msg = bot.send_message(c.message.chat.id, "🔍 İstifadəçi chat_id daxil et:")
        bot.register_next_step_handler(msg, admin_search_by_id_step)

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

    elif cmd == "topviews":
        reset_search_state(c.message.chat.id)
        send_paginated_results(c.message.chat.id, "topviews", params={"days": 7}, page=1)

    elif cmd == "payhist":
        show_payment_history_list(c.message.chat.id, page=1)

    elif cmd == "promos":
        show_admin_promo_menu(c.message.chat.id)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


def show_admin_promo_menu(chat_id: int):
    if not is_admin(chat_id):
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("🎁 1 gün", callback_data="prm|gen|1"),
        types.InlineKeyboardButton("🎁 3 gün", callback_data="prm|gen|3"),
    )
    mk.add(
        types.InlineKeyboardButton("🎁 5 gün", callback_data="prm|gen|5"),
        types.InlineKeyboardButton("🎁 7 gün", callback_data="prm|gen|7"),
    )
    mk.add(types.InlineKeyboardButton("📋 Promo siyahısı", callback_data="prm|list|1"))
    mk.add(types.InlineKeyboardButton("📊 Promo statistikası", callback_data="prm|stats|1"))
    bot.send_message(chat_id, "🎟 Promo kod idarəsi:", reply_markup=mk)


def show_admin_promo_list(chat_id: int, page: int = 1):
    if not is_admin(chat_id):
        return

    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM promo_codes")
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * PAGE_SIZE
    cur.execute(
        """
        SELECT code, days, is_active, created_at
        FROM promo_codes
        ORDER BY datetime(created_at) DESC, code DESC
        LIMIT ? OFFSET ?
        """,
        (PAGE_SIZE, offset),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("↩️ Geri", callback_data="adm|promos"))
        bot.send_message(chat_id, "❌ Promo kod yoxdur.", reply_markup=mk)
        return

    lines = ["📋 Promo kod siyahısı:"]
    for r in rows:
        status = "✅ Aktiv" if r["is_active"] else "⛔ Deaktiv"
        created_txt = "-"
        if r["created_at"]:
            try:
                created_txt = (
                    datetime.fromisoformat(str(r["created_at"]).replace(" ", "T"))
                    .strftime("%d.%m.%Y")
                )
            except Exception:
                created_txt = str(r["created_at"])
        lines.append(
            f"{r['code']} — {r['days']} gün | {status} | {created_txt}"
        )

    txt = "\n".join(lines) + f"\n\nSəhifə: {page}/{total_pages}"

    mk = types.InlineKeyboardMarkup()
    for r in rows:
        toggle_action = "deact" if r["is_active"] else "act"
        toggle_label = "⛔" if r["is_active"] else "✅"
        mk.add(
            types.InlineKeyboardButton(
                f"{toggle_label} {r['code']}",
                callback_data=f"prm|{toggle_action}|{r['code']}|{page}",
            )
        )

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            types.InlineKeyboardButton("⬅️ Əvvəlki", callback_data=f"prm|list|{page-1}")
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton("➡️ Növbəti", callback_data=f"prm|list|{page+1}")
        )
    if nav_buttons:
        mk.add(*nav_buttons)

    mk.add(types.InlineKeyboardButton("↩️ Geri", callback_data="adm|promos"))
    bot.send_message(chat_id, txt, reply_markup=mk)


def show_admin_promo_stats(chat_id: int, page: int = 1):
    if not is_admin(chat_id):
        return

    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM promo_codes")
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * PAGE_SIZE
    cur.execute(
        """
        SELECT
            pc.code,
            pc.days,
            COUNT(pu.chat_id) AS total_users,
            SUM(
                CASE
                    WHEN pu.chat_id IS NOT NULL
                         AND datetime(COALESCE(s.expires_at, pu.expires_at)) > datetime('now')
                    THEN 1 ELSE 0 END
            ) AS active_users,
            SUM(
                CASE
                    WHEN pu.chat_id IS NOT NULL
                         AND datetime(COALESCE(s.expires_at, pu.expires_at)) <= datetime('now')
                    THEN 1 ELSE 0 END
            ) AS expired_users
        FROM promo_codes pc
        LEFT JOIN promo_usages pu ON pu.code = pc.code
        LEFT JOIN subscriptions s ON s.chat_id = pu.chat_id
        GROUP BY pc.code, pc.days
        ORDER BY datetime(pc.created_at) DESC, pc.code DESC
        LIMIT ? OFFSET ?
        """,
        (PAGE_SIZE, offset),
    )
    rows = cur.fetchall()

    # Promo gəlir və ödəniş edən istifadəçi statistikasını yığ
    cur.execute(
        """
        SELECT pu.code, pu.chat_id, pu.expires_at, COALESCE(s.is_demo, 0) AS is_demo
        FROM promo_usages pu
        LEFT JOIN subscriptions s ON s.chat_id = pu.chat_id
        """
    )
    usage_rows = cur.fetchall()

    cur.execute("SELECT chat_id, amount, approved_at FROM payments")
    payment_rows = cur.fetchall()

    payments_by_user = {}
    for p in payment_rows:
        payments_by_user.setdefault(p["chat_id"], []).append(p)

    revenue_map = {}
    for u in usage_rows:
        if u["is_demo"]:
            continue  # Demo istifadəçiləri istisna et
        exp_raw = u["expires_at"]
        try:
            exp_dt = datetime.fromisoformat(exp_raw) if exp_raw else None
        except Exception:
            try:
                exp_dt = datetime.strptime(exp_raw, "%Y-%m-%d %H:%M:%S") if exp_raw else None
            except Exception:
                exp_dt = None

        for pay in payments_by_user.get(u["chat_id"], []):
            appr_raw = pay["approved_at"]
            try:
                appr_dt = datetime.fromisoformat(appr_raw) if appr_raw else None
            except Exception:
                try:
                    appr_dt = datetime.strptime(appr_raw, "%Y-%m-%d %H:%M:%S") if appr_raw else None
                except Exception:
                    appr_dt = None

            if not appr_dt or not exp_dt or appr_dt <= exp_dt:
                continue

            code = u["code"]
            if code not in revenue_map:
                revenue_map[code] = {"revenue": 0, "paid_users": set()}
            revenue_map[code]["revenue"] += pay["amount"] or 0
            revenue_map[code]["paid_users"].add(u["chat_id"])
    conn.close()

    if not rows:
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("↩️ Geri", callback_data="adm|promos"))
        bot.send_message(chat_id, "❌ Promo kod yoxdur.", reply_markup=mk)
        return

    lines = []
    for r in rows:
        total_users = r["total_users"] or 0
        active_users = r["active_users"] or 0
        expired_users = r["expired_users"] or 0
        promo_data = revenue_map.get(r["code"], {"revenue": 0, "paid_users": set()})
        revenue = promo_data.get("revenue", 0)
        paid_users = len(promo_data.get("paid_users", set()))
        conversion_pct = (paid_users / total_users * 100) if total_users else 0
        conversion_base = total_users if total_users else 0
        lines.append(f"🎁 {r['code']} — {r['days']} gün")
        lines.append(f"👥 İstifadə edənlər: {total_users}")
        lines.append(f"🟢 Aktiv: {active_users}")
        lines.append(f"🔴 Bitmiş: {expired_users}")
        lines.append(f"💳 Ödəniş edənlər: {paid_users}")
        lines.append(f"💰 Gəlir: {revenue} AZN")
        lines.append(
            f"📈 Konversiya: {paid_users}/{conversion_base} ({conversion_pct:.1f}%)"
        )
        lines.append("")

    txt = "\n".join(lines).strip() + f"\n\nSəhifə: {page}/{total_pages}"

    mk = types.InlineKeyboardMarkup()
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            types.InlineKeyboardButton("⬅️ Əvvəlki", callback_data=f"prm|stats|{page-1}")
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton("➡️ Növbəti", callback_data=f"prm|stats|{page+1}")
        )
    if nav_buttons:
        mk.add(*nav_buttons)
    mk.add(types.InlineKeyboardButton("↩️ Geri", callback_data="adm|promos"))

    bot.send_message(chat_id, txt, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("prm|"))
def cb_admin_promo(c):
    if not is_admin(c.message.chat.id):
        return

    parts = c.data.split("|")
    if len(parts) < 2:
        return

    action = parts[1]

    if action == "gen" and len(parts) > 2:
        try:
            days = int(parts[2])
        except Exception:
            days = 0
        code = generate_promo_code(days) if days > 0 else None
        if code:
            bot.send_message(
                c.message.chat.id, f"✅ {days} günlük promo kod yaradıldı:\n{code}"
            )
        else:
            bot.send_message(c.message.chat.id, "❌ Promo kod yaradıla bilmədi.")
    elif action == "list":
        page = 1
        if len(parts) > 2:
            try:
                page = int(parts[2])
            except Exception:
                page = 1
        show_admin_promo_list(c.message.chat.id, page=page)
    elif action == "stats":
        page = 1
        if len(parts) > 2:
            try:
                page = int(parts[2])
            except Exception:
                page = 1
        show_admin_promo_stats(c.message.chat.id, page=page)
    elif action in {"act", "deact"} and len(parts) > 2:
        code = parts[2]
        page = 1
        if len(parts) > 3:
            try:
                page = int(parts[3])
            except Exception:
                page = 1
        set_promo_status(code, action == "act")
        show_admin_promo_list(c.message.chat.id, page=page)

    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def admin_search_by_id_step(message):
    if not is_admin(message.chat.id):
        return
    try:
        target_id = int((message.text or "").strip())
    except Exception:
        bot.send_message(message.chat.id, "⚠️ Düzgün chat_id yazın.")
        return
    admin_show_subscription_info(message.chat.id, target_id)


def admin_show_subscription_info(admin_chat_id: int, target_id: int):
    ensure_subscription_record(target_id)
    sub = get_subscription(target_id)
    exp_txt = "-"
    exp_dt = None
    if sub and sub.get("expires_at"):
        try:
            exp_dt = datetime.fromisoformat(str(sub["expires_at"]))
            exp_txt = exp_dt.strftime("%d.%m.%Y")
        except Exception:
            exp_txt = str(sub.get("expires_at"))
    plan_txt = sub.get("plan") if sub else "-"
    is_active = bool(sub.get("is_active")) if sub else False
    demo_txt = "Bəli" if sub and sub.get("is_demo") else "Xeyr"

    info_txt = (
        f"🆔 İstifadəçi: {target_id}\n"
        f"📦 Plan: {plan_txt or '-'}\n"
        f"📅 Bitmə tarixi: {exp_txt}\n"
        f"✅ Aktiv: {'Bəli' if is_active else 'Xeyr'}\n"
        f"🎁 Demo: {demo_txt}\n"
        f"🆔 Ödəniş kodu: {subscription_payment_code(target_id)}"
    )

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ 3 gün uzat", callback_data=f"subctl|add|{target_id}|3"
        ),
        types.InlineKeyboardButton(
            "➕ 7 gün uzat", callback_data=f"subctl|add|{target_id}|7"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "➕ 15 gün uzat", callback_data=f"subctl|add|{target_id}|15"
        ),
        types.InlineKeyboardButton(
            "➕ 30 gün uzat", callback_data=f"subctl|add|{target_id}|30"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "⛔ Dayandır", callback_data=f"subctl|stop|{target_id}"
        ),
        types.InlineKeyboardButton(
            "▶️ Aktiv et", callback_data=f"subctl|act|{target_id}"
        ),
    )

    bot.send_message(admin_chat_id, info_txt, reply_markup=mk)


def show_payment_history_list(chat_id: int, page: int = 1):
    if not is_admin(chat_id):
        return

    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(DISTINCT chat_id) FROM payments")
    total_users = cur.fetchone()[0] or 0
    if total_users == 0:
        conn.close()
        bot.send_message(chat_id, "❌ Ödəniş qeydi yoxdur.")
        return

    total_pages = max(1, math.ceil(total_users / PAGE_SIZE))
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * PAGE_SIZE
    cur.execute(
        """
        SELECT chat_id,
               COALESCE(SUM(amount), 0) AS total_paid,
               MAX(approved_at) AS last_payment_date
        FROM payments
        GROUP BY chat_id
        ORDER BY datetime(last_payment_date) DESC, chat_id DESC
        LIMIT ? OFFSET ?
        """,
        (PAGE_SIZE, offset),
    )
    rows = cur.fetchall()
    conn.close()

    lines = ["📜 Ödəniş tarixçəsi (admin):"]
    for r in rows:
        last_dt = "-"
        if r["last_payment_date"]:
            try:
                last_dt = datetime.fromisoformat(str(r["last_payment_date"]).replace(" ", "T")).strftime(
                    "%d.%m.%Y"
                )
            except Exception:
                last_dt = str(r["last_payment_date"])
        lines.append(
            f"🆔 {r['chat_id']} — {r['total_paid']} AZN (son: {last_dt})"
        )

    txt = "\n".join(lines) + f"\n\nSəhifə: {page}/{total_pages}"

    mk = types.InlineKeyboardMarkup()
    for r in rows:
        mk.add(
            types.InlineKeyboardButton(
                f"{r['chat_id']} | {r['total_paid']} AZN",
                callback_data=f"paydetail|{r['chat_id']}|1|{page}",
            )
        )

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            types.InlineKeyboardButton("⬅️ Əvvəlki", callback_data=f"payhist|{page-1}")
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton("➡️ Növbəti", callback_data=f"payhist|{page+1}")
        )
    if nav_buttons:
        mk.row(*nav_buttons)

    bot.send_message(chat_id, txt, reply_markup=mk)


def show_user_payment_details(admin_chat_id: int, target_id: int, page: int = 1, list_page: int = 1):
    if not is_admin(admin_chat_id):
        return

    page = max(1, int(page or 1))
    list_page = max(1, int(list_page or 1))
    conn = get_local_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM payments WHERE chat_id=?",
        (target_id,),
    )
    total_payments = cur.fetchone()[0] or 0
    if total_payments == 0:
        conn.close()
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "⬅️ Siyahıya qayıt", callback_data=f"payhist|{list_page}"
            )
        )
        bot.send_message(admin_chat_id, "❌ Bu istifadəçinin ödənişləri yoxdur.", reply_markup=mk)
        return

    total_pages = max(1, math.ceil(total_payments / PAGE_SIZE))
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE

    cur.execute(
        """
        SELECT plan, amount, approved_at
        FROM payments
        WHERE chat_id=?
        ORDER BY datetime(approved_at) DESC
        LIMIT ? OFFSET ?
        """,
        (target_id, PAGE_SIZE, offset),
    )
    payments = cur.fetchall()

    cur.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total_paid,
               MAX(approved_at) AS last_dt
        FROM payments
        WHERE chat_id=?
        """,
        (target_id,),
    )
    summary = cur.fetchone()
    conn.close()

    last_payment = "-"
    if summary["last_dt"]:
        try:
            last_payment = datetime.fromisoformat(str(summary["last_dt"]).replace(" ", "T")).strftime(
                "%d.%m.%Y %H:%M"
            )
        except Exception:
            last_payment = str(summary["last_dt"])

    header = (
        f"🧾 Ödənişlər — {target_id}\n"
        f"💰 Toplam: {summary['total_paid']} AZN\n"
        f"📅 Son ödəniş: {last_payment}\n"
        f"Səhifə: {page}/{total_pages}\n"
    )

    lines = []
    for idx, p in enumerate(payments, start=offset + 1):
        pay_date = "-"
        if p["approved_at"]:
            try:
                pay_date = datetime.fromisoformat(str(p["approved_at"]).replace(" ", "T")).strftime(
                    "%d.%m.%Y %H:%M"
                )
            except Exception:
                pay_date = str(p["approved_at"])
        lines.append(f"{idx}) {pay_date} — {p['plan']} — {p['amount']} AZN")

    mk = types.InlineKeyboardMarkup()
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            types.InlineKeyboardButton(
                "⬅️ Əvvəlki", callback_data=f"paydetail|{target_id}|{page-1}|{list_page}"
            )
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton(
                "➡️ Növbəti", callback_data=f"paydetail|{target_id}|{page+1}|{list_page}"
            )
        )
    if nav_buttons:
        mk.row(*nav_buttons)

    mk.add(
        types.InlineKeyboardButton(
            "⬅️ İstifadəçi siyahısı", callback_data=f"payhist|{list_page}"
        )
    )

    bot.send_message(
        admin_chat_id,
        header + "\n".join(lines),
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("payhist|"))
def cb_pay_history_list(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    page = 1
    if len(parts) > 1:
        try:
            page = int(parts[1])
        except Exception:
            page = 1
    show_payment_history_list(c.message.chat.id, page=page)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("paydetail|"))
def cb_pay_user_detail(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    if len(parts) < 3:
        return
    try:
        target_id = int(parts[1])
    except Exception:
        return
    try:
        page = int(parts[2]) if len(parts) > 2 else 1
    except Exception:
        page = 1
    try:
        list_page = int(parts[3]) if len(parts) > 3 else 1
    except Exception:
        list_page = 1

    show_user_payment_details(c.message.chat.id, target_id, page=page, list_page=list_page)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("subctl|"))
def cb_subscription_control(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    if len(parts) < 3:
        return
    action, uid_raw = parts[1], parts[2]
    try:
        uid = int(uid_raw)
    except Exception:
        return

    ensure_subscription_record(uid)
    sub = get_subscription(uid) or {}
    exp_dt = parse_subscription_expiry(sub)

    if action == "add" and len(parts) > 3:
        try:
            days = int(parts[3])
        except Exception:
            days = 0
        if days > 0:
            base = exp_dt if sub.get("is_active") and exp_dt and exp_dt > datetime.utcnow() else datetime.utcnow()
            new_exp = base + timedelta(days=days)
            plan_name = sub.get("plan") or f"manual {days}g"
            set_subscription(uid, plan_name, new_exp, is_active=1, is_demo=0, note=f"extend:{days}")
            try:
                bot.send_message(uid, f"⏳ Hesabınız {days} gün uzadıldı")
            except Exception:
                pass
    elif action == "stop":
        set_subscription(
            uid,
            sub.get("plan") or "manual",
            exp_dt,
            is_active=0,
            is_demo=sub.get("is_demo") or 0,
            note="stopped",
        )
        try:
            bot.send_message(uid, "⛔ Hesabınız deaktiv edildi")
        except Exception:
            pass
    elif action == "act":
        new_exp = exp_dt or (datetime.utcnow() + timedelta(days=1))
        set_subscription(
            uid,
            sub.get("plan") or "manual",
            new_exp,
            is_active=1,
            is_demo=0,
            note="activated",
        )
        try:
            bot.send_message(uid, "▶️ Hesabınız aktivləşdirildi")
        except Exception:
            pass

    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    admin_show_subscription_info(c.message.chat.id, uid)


# =============== 👥 İSTİFADƏÇİLƏR PANELİ ===============


def show_users_menu(chat_id):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("✅ Aktiv", callback_data="userlist|active"),
        types.InlineKeyboardButton("🎁 Demo", callback_data="userlist|demo"),
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


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_search|"))
def cb_user_search(c):
    if not is_admin(c.message.chat.id):
        return
    try:
        uid = int(c.data.split("|")[1])
    except Exception:
        return
    admin_show_subscription_info(c.message.chat.id, uid)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def parse_join_datetime(dt_raw: Optional[str]) -> Tuple[str, str]:
    if not dt_raw:
        return "-", "-"
    try:
        join_dt = datetime.fromisoformat(str(dt_raw).replace(" ", "T"))
        return join_dt.strftime("%Y-%m-%d"), join_dt.strftime("%H:%M")
    except Exception:
        return str(dt_raw), "-"


def block_user(chat_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT blocked FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return False

    if row["blocked"]:
        conn.close()
        return False

    blocked_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn.close()
    update_user_status(chat_id, STATUS_BLOCKED, blocked_at=datetime.fromisoformat(blocked_at))

    try:
        bot.send_message(chat_id, BLOCKED_MESSAGE_TEXT)
    except Exception:
        pass

    return True


def restore_user_to_pending(chat_id: int):
    update_user_status(chat_id, STATUS_PENDING)


def delete_user_fully(chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM favorites WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM subscriptions WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM agent_activity WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()


def show_all_users(chat_id, status="active"):
    conn = get_local_conn()
    cur = conn.cursor()

    base_query = (
        "SELECT chat_id, full_name, username, joined_at, status, demo_start_at, demo_end_at, paid_until, last_status_change_at, blocked_at FROM users"
    )

    status_filter = ""
    title = "👥 Bütün istifadəçilər"
    if status == "active":
        status_filter = f" WHERE status='{STATUS_ACTIVE}'"
        title = "✅ Aktiv istifadəçilər"
    elif status == "blocked":
        status_filter = f" WHERE status='{STATUS_BLOCKED}'"
        title = "🚫 Bloklanmış istifadəçilər"
    elif status == "pending":
        status_filter = f" WHERE status='{STATUS_PENDING}'"
        title = "⏳ Təsdiqlənməmiş istifadəçilər"
    elif status == "demo":
        status_filter = f" WHERE status='{STATUS_DEMO}'"
        title = "🎁 Demo istifadəçilər"

    cur.execute(base_query + status_filter + " ORDER BY joined_at DESC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, f"❌ {title} tapılmadı.")
        return

    bot.send_message(chat_id, f"{title} ({len(rows)} nəfər):")
    now = datetime.utcnow()

    if status == "demo":
        active_count = 0
        expired_count = 0
        for r in rows:
            demo_end = parse_dt_safe(r["demo_end_at"])
            if demo_end and demo_end > now:
                active_count += 1
            else:
                expired_count += 1
        summary = (
            f"🟢 Aktiv demo: {active_count}\n"
            f"🔴 Vaxtı bitmiş demo: {expired_count}"
        )
        bot.send_message(chat_id, summary)

    for r in rows:
        chat_id_u = r["chat_id"]
        full_name = r["full_name"] or "Ad yoxdur"
        username_value = f"@{r['username']}" if r["username"] else "yoxdur"
        joined_at = parse_dt_safe(r["joined_at"])
        joined_txt = joined_at.strftime("%Y-%m-%d") if joined_at else "-"
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                f"🆔 ID: {chat_id_u}", callback_data=f"user_search|{chat_id_u}"
            )
        )

        if status == "pending":
            waiting_days = (now - joined_at).days if joined_at else 0
            txt = (
                f"👤 Ad: {full_name}\n"
                f"🆔 ID: <code>{chat_id_u}</code>\n"
                f"👤 Username: {username_value}\n"
                f"📅 Qoşulma tarixi: {joined_txt}\n"
                f"⏳ Neçə gündür gözləyir: {waiting_days} gün"
            )
            mk.row(
                types.InlineKeyboardButton("✅ Aktiv et", callback_data=f"user_approve|{chat_id_u}"),
                types.InlineKeyboardButton("🎁 Demo ver", callback_data=f"user_demo|{chat_id_u}"),
            )
            mk.add(
                types.InlineKeyboardButton("🚫 Dayandır", callback_data=f"user_block|{chat_id_u}")
            )
        elif status == "active":
            activation_dt = parse_dt_safe(r["last_status_change_at"]) or joined_at
            activation_txt = activation_dt.strftime("%Y-%m-%d") if activation_dt else "-"
            paid_until = parse_dt_safe(r["paid_until"])
            remaining_txt = "-"
            if paid_until:
                remaining = paid_until - now
                if remaining.total_seconds() > 0:
                    remaining_txt = format_remaining_time(remaining)
                else:
                    remaining_txt = "Bitib"
            txt = (
                f"👤 Ad: {full_name}\n"
                f"🆔 ID: <code>{chat_id_u}</code>\n"
                f"📅 Qoşulma tarixi: {joined_txt}\n"
                f"💳 Aktivləşmə tarixi: {activation_txt}\n"
                f"⏳ Qalan gün: {remaining_txt}"
            )
            mk.add(
                types.InlineKeyboardButton("🚫 Dayandır", callback_data=f"user_block|{chat_id_u}")
            )
        elif status == "demo":
            demo_end = parse_dt_safe(r["demo_end_at"]) or parse_dt_safe(r["demo_start_at"])
            remaining_txt = "Bitib"
            if demo_end:
                remaining = demo_end - now
                remaining_txt = format_remaining_time(remaining)
            txt = (
                f"👤 Ad: {full_name}\n"
                f"🆔 ID: <code>{chat_id_u}</code>\n"
                f"⏱ Qalan vaxt: {remaining_txt}"
            )
            mk.add(
                types.InlineKeyboardButton("🚫 Dayandır", callback_data=f"user_block|{chat_id_u}")
            )
        elif status == "blocked":
            blocked_at = parse_dt_safe(r["blocked_at"] or r["last_status_change_at"])
            blocked_txt = blocked_at.strftime("%Y-%m-%d") if blocked_at else "-"
            txt = (
                f"🚫 Bloklanmış istifadəçi:\n\n"
                f"• 👤 Ad: {full_name}\n"
                f"• 🆔 ID: <code>{chat_id_u}</code>\n"
                f"• 👤 Username: {username_value}\n"
                f"• 📅 Bloklanma tarixi: {blocked_txt}"
            )
            mk.add(
                types.InlineKeyboardButton("🔄 Geri qaytar", callback_data=f"user_restore|{chat_id_u}"),
                types.InlineKeyboardButton("🗑 Tam sil", callback_data=f"user_delete|{chat_id_u}"),
            )
        else:
            txt = (
                f"👤 {full_name}\n"
                f"💬 Username: {username_value}\n"
                f"🆔 <code>{chat_id_u}</code>"
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

    block_user(uid)

    bot.answer_callback_query(c.id, "🚫 İstifadəçi dayandırıldı.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_approve|"))
def cb_user_approve_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    ensure_subscription_record(uid)
    sub = get_subscription(uid)
    paid_until = parse_subscription_expiry(sub) if sub else None
    update_user_status(uid, STATUS_ACTIVE, paid_until=paid_until)

    apply_referral_bonus(uid)

    try:
        bot.answer_callback_query(c.id, "✅ İstifadəçi təsdiqləndi.")
    except:
        pass

    try:
        bot.send_message(
            uid,
            "🎉 Hesabınız admin tərəfindən aktiv edildi. Ödəniş statusunu yoxlayın.",
        )
    except:
        pass

    show_all_users(c.message.chat.id, "pending")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_demo|"))
def cb_user_demo_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])
    ensure_subscription_record(uid)
    expires = datetime.utcnow() + timedelta(days=3)
    set_subscription(uid, "demo", expires, is_active=1, is_demo=1, note="admin_demo")
    mark_demo_used(uid, expires)
    try:
        bot.send_message(
            uid,
            "🎉 Admin tərəfindən 3 günlük demo aktiv edildi!\nBotdan tam istifadə edə bilərsiniz.",
        )
    except Exception:
        pass
    try:
        bot.answer_callback_query(c.id, "🎁 Demo verildi")
    except Exception:
        pass
    show_all_users(c.message.chat.id, "pending")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_restore|"))
def cb_user_restore_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    restore_user_to_pending(uid)

    bot.answer_callback_query(c.id, "↩️ İstifadəçi gözləməyə qaytarıldı.")
    show_all_users(c.message.chat.id, "blocked")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_delete|"))
def cb_user_delete_action(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    delete_user_fully(uid)

    bot.answer_callback_query(c.id, "🗑 İstifadəçi silindi.")
    show_all_users(c.message.chat.id, "blocked")


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
                "❌ Dayandır",
                callback_data=f"ublock|{uid}",
            ),
        )
        prof = f"@{username}" if username else "yoxdur"
        join_date, join_time = parse_join_datetime(dt)
        txt = (
            "❌ Təsdiqlənməmiş istifadəçilər:\n\n"
            f"• 👤 Ad: {full_name or '-'}\n"
            f"• 🆔 ID: <code>{uid}</code>\n"
            f"• 👤 Username: {prof}\n"
            f"• 📅 Sorğu tarixi: {join_date}\n"
            f"• ⏰ Saat: {join_time}"
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

    apply_referral_bonus(uid)

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

    block_user(uid)

    bot.answer_callback_query(c.id, "⛔ İstifadəçi bloklandı.")


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


def handle_bot_refresh(message):
    chat_id = message.chat.id
    user_state.pop(chat_id, None)
    search_state.pop(chat_id, None)
    session_interactions.pop(chat_id, None)
    search_reminder_shown.discard(chat_id)
    admin_panel_page_state.pop(chat_id, None)
    return_to_main_menu(chat_id)


@bot.message_handler(func=lambda m: m.text == "🔄 Botu yenilə")
def refresh_button_message(message):
    handle_bot_refresh(message)


@bot.callback_query_handler(func=lambda c: c.data == "bot_refresh")
def cb_bot_refresh(c):
    try:
        bot.answer_callback_query(c.id, "✅ Yeniləndi.")
    except:
        pass
    handle_bot_refresh(c.message)


@bot.callback_query_handler(func=lambda c: c.data == "refresh_bot")
def cb_refresh_bot(c):
    """İstifadəçi 'Botu yenilə' düyməsinə basanda /start işə düşür."""
    try:
        bot.answer_callback_query(c.id, "✅ Yeniləndi.")
    except:
        pass
    handle_bot_refresh(c.message)


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


# =============== VASITƏÇİ ANALİTİKASI ===============


def show_agent_activity_overview(chat_id: int):
    if not is_admin(chat_id):
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.chat_id, a.name, a.phone,
               aa.last_activity, aa.searches, aa.views, aa.whatsapp, aa.favorites
        FROM agents a
        LEFT JOIN agent_activity aa ON aa.chat_id = a.chat_id
        ORDER BY (aa.last_activity IS NULL), datetime(aa.last_activity) DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "❌ Heç bir vasitəçi qeydiyyatı tapılmadı.")
        return

    cutoff = datetime.utcnow() - timedelta(days=7)
    active, passive = [], []

    for r in rows:
        last_raw = r["last_activity"]
        last_dt = None
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(str(last_raw))
            except Exception:
                last_dt = None

        metrics = (
            f"🔍 {r['searches'] or 0} | 👁 {r['views'] or 0} | "
            f"💬 {r['whatsapp'] or 0} | ⭐ {r['favorites'] or 0}"
        )
        last_txt = last_dt.strftime("%d.%m.%Y") if last_dt else "-"
        name = r["name"] or "(ad yoxdur)"
        phone = r["phone"] or "-"
        item_txt = f"• {name} ({r['chat_id']}, {phone}) — {metrics} — Son: {last_txt}"

        if last_dt and last_dt >= cutoff:
            active.append(item_txt)
        else:
            passive.append(item_txt)

    resp = "🧠 Aktiv / passiv maklerlər\n\n"
    resp += "🔥 Aktiv (son 7 gün):\n"
    resp += "\n".join(active) if active else "• Aktiv vasitəçi yoxdur"
    resp += "\n\n⏸ Passiv (7+ gün):\n"
    resp += "\n".join(passive) if passive else "• Passiv vasitəçi yoxdur"

    bot.send_message(chat_id, resp)


# =============== ADMIN STATİSTİKA, AXTARIŞ, BROADCAST ===============


STATS_PERIOD_MAP = {
    "day": "Bu gün",
    "week": "Bu həftə",
    "month": "Bu ay",
}
def stats_period_keyboard(selected: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    buttons = [
        types.InlineKeyboardButton("📆 Bu gün", callback_data="stats_period:day"),
        types.InlineKeyboardButton("📆 Bu həftə", callback_data="stats_period:week"),
        types.InlineKeyboardButton("📆 Bu ay", callback_data="stats_period:month"),
    ]
    mk.row(*buttons)
    return mk


def stats_period_range(period: str) -> Tuple[date, date, str]:
    today = date.today()
    if period == "week":
        start = today - timedelta(days=6)
    elif period == "month":
        start = today.replace(day=1)
    else:
        period = "day"
        start = today
    end = today
    label = STATS_PERIOD_MAP.get(period, "Bu gün")
    return start, end, label


def format_ranked_lines(items, name_key: str, count_key: str):
    if not items:
        return []

    lines = []
    for idx, row in enumerate(items, start=1):
        try:
            name_raw = row[name_key]
        except Exception:
            name_raw = row[0] if len(row) > 0 else ""
        try:
            count_raw = row[count_key]
        except Exception:
            count_raw = row[1] if len(row) > 1 else 0

        name = str(name_raw or "").strip() or "Naməlum"
        count = int(count_raw or 0)
        lines.append(f"{idx}) {name} — {count} axtarış")
    return lines


def show_admin_stats(chat_id, period: Optional[str] = None, message_id: Optional[int] = None):
    if not is_admin(chat_id):
        return

    selected_period = period or admin_stats_period.get(chat_id, "day")
    admin_stats_period[chat_id] = selected_period
    start_date, end_date, period_label = stats_period_range(selected_period)

    def table_exists(cur, name: str) -> bool:
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            )
            return cur.fetchone() is not None
        except Exception:
            return False

    def safe_count(cur, query: str, params: Tuple = ()) -> int:
        try:
            cur.execute(query, params)
            row = cur.fetchone()
            return (row[0] if row else 0) or 0
        except Exception:
            return 0

    def column_lookup(cur, table: str):
        try:
            cur.execute(f"PRAGMA table_info('{table}')")
            return {row[1].lower(): row[1] for row in cur.fetchall()}
        except Exception:
            return {}

    def detect_operation_column(cur, table: str) -> Optional[str]:
        cols = column_lookup(cur, table)
        for key in ("operation", "emeliyyat", "əməliyyat", "eməliyyat"):
            if key in cols:
                return cols[key]
        return None

    def op_counts(cur, table: str):
        total = safe_count(cur, f"SELECT COUNT(*) FROM {table}") if table_exists(cur, table) else 0
        sale = rent = 0
        if table_exists(cur, table):
            op_col = detect_operation_column(cur, table)
            if op_col:
                try:
                    cur.execute(
                        f"SELECT LOWER({op_col}) as op, COUNT(*) FROM {table} GROUP BY LOWER({op_col})"
                    )
                    for op, cnt in cur.fetchall():
                        norm = normalize_operation_value(op)
                        if norm == "sale":
                            sale += cnt or 0
                        elif norm == "rent":
                            rent += cnt or 0
                except Exception:
                    pass
        return total, sale, rent

    total_users = active_users = pending_users = 0
    demo_users = 0
    period_searches = 0
    top_rayons = []
    top_users = []
    search_stats_available = False

    try:
        conn_local = get_local_conn()
        cur_local = conn_local.cursor()

        if table_exists(cur_local, "users"):
            total_users = safe_count(cur_local, "SELECT COUNT(*) FROM users")
            active_users = safe_count(
                cur_local, "SELECT COUNT(*) FROM users WHERE approved=1 AND blocked=0"
            )
            pending_users = safe_count(cur_local, "SELECT COUNT(*) FROM users WHERE approved=0")

        if table_exists(cur_local, "subscriptions"):
            cols = column_lookup(cur_local, "subscriptions")
            active_col = cols.get("is_active")
            demo_col = cols.get("is_demo")
            active_from_subs = (
                safe_count(cur_local, f"SELECT COUNT(*) FROM subscriptions WHERE {active_col}=1")
                if active_col
                else 0
            )
            demo_from_subs = (
                safe_count(cur_local, f"SELECT COUNT(*) FROM subscriptions WHERE {demo_col}=1")
                if demo_col
                else 0
            )
            if active_from_subs or demo_from_subs:
                active_users = active_from_subs
                demo_users = demo_from_subs

        if table_exists(cur_local, "search_logs"):
            search_stats_available = True
            start_str = start_date.isoformat()
            end_str = end_date.isoformat()
            period_searches = safe_count(
                cur_local,
                """
                SELECT COUNT(*) FROM search_logs
                WHERE DATE(created_at) BETWEEN ? AND ?
                """,
                (start_str, end_str),
            )
            try:
                cur_local.execute(
                    """
                    SELECT COALESCE(NULLIF(TRIM(rayon), ''), '') AS rn,
                           COUNT(*) AS cnt
                    FROM search_logs
                    WHERE DATE(created_at) BETWEEN ? AND ?
                    GROUP BY rn
                    ORDER BY cnt DESC
                    LIMIT 5
                    """,
                    (start_str, end_str),
                )
                top_rayons = cur_local.fetchall()
            except Exception:
                top_rayons = []

            try:
                cur_local.execute(
                    """
                    SELECT sl.chat_id,
                           COUNT(*) AS cnt,
                           COALESCE(NULLIF(u.full_name, ''), NULLIF(u.username, ''), CAST(sl.chat_id AS TEXT)) AS nm
                    FROM search_logs sl
                    LEFT JOIN users u ON u.chat_id = sl.chat_id
                    WHERE DATE(sl.created_at) BETWEEN ? AND ?
                    GROUP BY sl.chat_id
                    ORDER BY cnt DESC
                    LIMIT 5
                    """,
                    (start_str, end_str),
                )
                top_users = cur_local.fetchall()
            except Exception:
                top_users = []
    finally:
        try:
            conn_local.close()
        except Exception:
            pass

    main_total = main_sale = main_rent = 0
    conn_main = None
    if os.path.exists(MAIN_DB):
        try:
            conn_main = get_main_conn()
            cur_main = conn_main.cursor()
            cur_main.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%listing%'"
            )
            rows = cur_main.fetchall()
            main_table = None
            for row in rows:
                candidate = row[0]
                if candidate:
                    main_table = candidate
                    if candidate.lower() == "listings":
                        break
            if main_table:
                main_total, main_sale, main_rent = op_counts(cur_main, main_table)
        except Exception:
            pass
        finally:
            try:
                close_main_conn(conn_main)
            except Exception:
                pass

    local_total = local_sale = local_rent = 0
    conn_local_counts = None
    try:
        conn_local_counts = get_local_conn()
        cur_local_counts = conn_local_counts.cursor()
        if table_exists(cur_local_counts, "listings_approved"):
            local_total, local_sale, local_rent = op_counts(cur_local_counts, "listings_approved")
    finally:
        try:
            conn_local_counts.close()
        except Exception:
            pass

    total_listings = main_total + local_total
    sale_total = main_sale + local_sale
    rent_total = main_rent + local_rent

    lines = [f"📊 BestHome Statistikalar — {period_label}", ""]
    lines.append("👥 İstifadəçilər:")
    lines.append(f"• Cəmi: {total_users}")
    lines.append(f"• Aktiv: {active_users}")
    lines.append(f"• Demo: {demo_users}")
    lines.append(f"• Təsdiqsiz: {pending_users}")
    lines.append("")

    lines.append("🏠 Elanlar:")
    lines.append(f"• Ümumi: {total_listings}")
    lines.append(f"• Satılır: {sale_total}")
    lines.append(f"• Kirayə: {rent_total}")
    lines.append("")

    lines.append(f"📍 Top rayonlar ({period_label}):")
    if search_stats_available and top_rayons:
        lines.extend(format_ranked_lines(top_rayons, "rn", "cnt"))
    else:
        lines.append("• Məlumat yoxdur")
    lines.append("")

    lines.append(f"🔍 Axtarışlar ({period_label}):")
    if search_stats_available and period_searches > 0:
        lines.append(f"• Cəmi: {period_searches}")
    else:
        lines.append("• Məlumat yoxdur")
    lines.append("")

    lines.append(f"⚡ Aktiv istifadəçilər ({period_label}):")
    if search_stats_available and top_users:
        lines.extend(format_ranked_lines(top_users, "nm", "cnt"))
    else:
        lines.append("• Məlumat yoxdur")

    text = "\n".join(lines)
    keyboard = stats_period_keyboard(selected_period)

    if message_id:
        try:
            bot.edit_message_text(
                text,
                chat_id,
                message_id,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("stats_period:"))
def handle_stats_period_callback(c):
    period = c.data.split(":", 1)[1] if c.data else "day"
    if period not in STATS_PERIOD_MAP:
        period = "day"

    chat_id = c.message.chat.id if c.message else c.from_user.id
    if not is_admin(chat_id):
        try:
            bot.answer_callback_query(c.id, "❌ Yalnız adminlər üçün.")
        except Exception:
            pass
        return

    admin_stats_period[chat_id] = period
    try:
        bot.answer_callback_query(c.id, f"📆 {STATS_PERIOD_MAP.get(period, 'Bu gün')}")
    except Exception:
        pass

    if c.message:
        show_admin_stats(chat_id, period=period, message_id=c.message.message_id)


def show_referral_stats(chat_id: int):
    if not is_admin(chat_id):
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL AND blocked=0"
    )
    total_referred = cur.fetchone()[0] or 0

    cur.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL AND referral_bonus_used=1 AND blocked=0"
    )
    total_rewarded = cur.fetchone()[0] or 0
    pending = max(total_referred - total_rewarded, 0)

    cur.execute("SELECT COALESCE(SUM(bonus_days), 0) FROM referral_logs")
    total_bonus_days = cur.fetchone()[0] or 0

    cur.execute(
        """
        SELECT referred_by AS referrer_id, COUNT(*) AS total_refs, SUM(COALESCE(referral_bonus_used, 0)) AS rewarded
        FROM users
        WHERE referred_by IS NOT NULL AND blocked=0
        GROUP BY referred_by
        ORDER BY rewarded DESC, total_refs DESC, referrer_id DESC
        LIMIT 10
        """
    )
    top_rows = cur.fetchall()
    conn.close()

    lines = ["🤝 Referral statistikası"]
    lines.append(f"👥 Ümumi dəvət olunan istifadəçilər: {total_referred}")
    lines.append(f"✅ Aktiv bonus verilənlər: {total_rewarded}")
    lines.append(f"⏳ Gözləyən (hələ aktiv olmayanlar): {pending}")
    lines.append(f"🎁 Verilmiş bonus günlərinin cəmi: {total_bonus_days}")

    if top_rows:
        lines.append("\n🏆 Ən çox dəvət edənlər:")
        for idx, row in enumerate(top_rows, start=1):
            rewarded = row["rewarded"] or 0
            lines.append(
                f"{idx}) {row['referrer_id']} — {row['total_refs']} dəvət, bonus: {rewarded}"
            )
    else:
        lines.append("Hələ referral qeydiyyatı yoxdur.")

    bot.send_message(chat_id, "\n".join(lines))


def show_revenue_report(chat_id: int):
    if not is_admin(chat_id):
        return

    def month_range(months_back: int):
        now = datetime.utcnow()
        year = now.year
        month = now.month - months_back
        while month <= 0:
            month += 12
            year -= 1
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return start, end

    conn = get_local_conn()
    cur = conn.cursor()

    def month_stats(offset: int):
        start, end = month_range(offset)
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt
            FROM payments
            WHERE datetime(approved_at) >= datetime(?)
              AND datetime(approved_at) < datetime(?)
            """,
            (start.isoformat(), end.isoformat()),
        )
        row = cur.fetchone() or (0, 0)
        return start, row[0] or 0, row[1] or 0

    cur.execute(
        """
        SELECT COUNT(*) FROM subscriptions
        WHERE is_active=1
          AND is_demo=0
          AND expires_at IS NOT NULL
          AND datetime(expires_at) >= datetime('now')
        """
    )
    active_subs = cur.fetchone()[0] or 0

    current_start, current_total, current_count = month_stats(0)

    history_lines = []
    for i in range(1, 4):
        m_start, total, cnt = month_stats(i)
        history_lines.append(
            f"{m_start.strftime('%B %Y')}: {total} AZN ({cnt} ödəniş)"
        )

    conn.close()

    report_lines = [
        f"📅 {current_start.strftime('%B %Y')} (cari ay):\n"
        f"• Toplam gəlir: {current_total} AZN\n"
        f"• Ödəniş sayı: {current_count}\n"
        f"• Aktiv abunə sayı: {active_subs}",
        "📈 Son 3 ay:\n" + "\n".join(history_lines),
    ]

    bot.send_message(chat_id, "\n\n".join(report_lines))


def admin_agents_broadcast(message):
    """Adminin yazdığı mətni bütün qeydiyyatlı istifadəçilərə göndər."""
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    if not text:
        bot.send_message(message.chat.id, "⚠️ Boş mətni göndərə bilmərəm.")
        return

    def fetch_targets_and_stats():
        conn = None
        total_users = 0
        blocked_users = 0
        paid_users = 0
        demo_users = 0
        targets = []
        try:
            conn = get_local_conn()
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0] or 0

            cur.execute("SELECT COUNT(*) FROM users WHERE blocked=1")
            blocked_users = cur.fetchone()[0] or 0

            cur.execute("SELECT chat_id FROM users WHERE blocked=0")
            targets = [r[0] for r in cur.fetchall() if r[0]]

            try:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM subscriptions
                    WHERE is_demo=0
                      AND is_active=1
                      AND expires_at IS NOT NULL
                      AND datetime(expires_at) >= datetime('now')
                    """
                )
                paid_users = cur.fetchone()[0] or 0
            except Exception:
                paid_users = 0

            try:
                cur.execute("SELECT COUNT(*) FROM subscriptions WHERE is_demo=1")
                demo_users = cur.fetchone()[0] or 0
            except Exception:
                demo_users = 0
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        return targets, total_users, blocked_users, paid_users, demo_users

    target_ids, total_users, blocked_users, paid_users, demo_users = fetch_targets_and_stats()

    if not target_ids:
        bot.send_message(message.chat.id, "❌ İstifadəçi tapılmadı.")
        return

    def send_broadcast(
        admin_chat_id, recipients, payload, total_users, blocked_users, paid_users, demo_users
    ):
        success = 0
        failed = 0
        for uid in recipients:
            try:
                bot.send_message(uid, f"📢 Admin bildirişi:\n{payload}")
                success += 1
            except Exception:
                failed += 1

        summary = (
            "📣 Bildiriş göndərildi\n"
            f"👥 Ümumi qeydiyyatlı istifadəçi: {total_users}\n"
            f"📤 Uğurla göndərildi: {success}\n"
            f"🚫 Bloklanmış istifadəçilər: {blocked_users}\n"
            f"⚠️ Uğursuz: {failed}"
        )
        summary += (
            f"\n\n💳 Ödənişli istifadəçilər: {paid_users}"
            f"\n🎁 Demo istifadəçilər: {demo_users}"
        )
        try:
            bot.send_message(admin_chat_id, summary)
        except Exception:
            pass

    threading.Thread(
        target=send_broadcast,
        args=(
            message.chat.id,
            list({uid for uid in target_ids if uid}),
            text,
            total_users,
            blocked_users,
            paid_users,
            demo_users,
        ),
        daemon=True,
    ).start()

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
            close_main_conn(conn)
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


def subscription_notifier():
    while True:
        try:
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT chat_id, expires_at, is_active FROM subscriptions"
            )
            rows = cur.fetchall()
            conn.close()
            now = datetime.utcnow()
            for chat_id, expires_at, is_active in rows:
                if not expires_at:
                    continue
                try:
                    exp_dt = datetime.fromisoformat(str(expires_at))
                except Exception:
                    continue

                if is_active and exp_dt <= now:
                    conn2 = get_local_conn()
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "UPDATE subscriptions SET is_active=0 WHERE chat_id=?",
                        (chat_id,),
                    )
                    conn2.commit()
                    conn2.close()
                    update_user_status(chat_id, STATUS_PENDING)
                    try:
                        bot.send_message(chat_id, "⛔ Hesabınızın müddəti bitdi")
                    except Exception:
                        pass
                    continue

                if is_active and timedelta(0) < (exp_dt - now) <= timedelta(days=1):
                    key = (chat_id, exp_dt.date())
                    if key not in subscription_warn_cache:
                        try:
                            bot.send_message(
                                chat_id,
                                "⚠️ Hesabınızın bitməsinə 1 gün qalıb",
                            )
                        except Exception:
                            pass
                        subscription_warn_cache.add(key)

            conn_demo = get_local_conn()
            cur_demo = conn_demo.cursor()
            cur_demo.execute(
                "SELECT chat_id, demo_end_at FROM users WHERE status=?",
                (STATUS_DEMO,),
            )
            demo_rows = cur_demo.fetchall()
            conn_demo.close()
            for demo_row in demo_rows:
                demo_chat = demo_row["chat_id"]
                demo_end_raw = demo_row["demo_end_at"]
                demo_end = parse_dt_safe(demo_end_raw)
                if not demo_end:
                    continue
                warn_key = (demo_chat, demo_end.date(), "warn")
                end_key = (demo_chat, demo_end.isoformat(), "end")
                if demo_end <= now:
                    if end_key not in demo_warn_cache:
                        update_user_status(demo_chat, STATUS_PENDING)
                        try:
                            bot.send_message(
                                demo_chat,
                                "💳 Demo bitdi. Ödəniş edərək botdan istifadəni davam etdirə bilərsiniz.",
                            )
                        except Exception:
                            pass
                        demo_warn_cache.add(end_key)
                    continue
                if timedelta(0) < (demo_end - now) <= timedelta(hours=6):
                    if warn_key not in demo_warn_cache:
                        try:
                            bot.send_message(
                                demo_chat,
                                "🔔 Demo bitməsinə 6 saat qalıb. Davam etmək üçün ödəniş edin.",
                            )
                        except Exception:
                            pass
                        demo_warn_cache.add(warn_key)
        except Exception as e:
            print("Subscription notifier error:", e)
        time.sleep(3600)


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
    mk.add("💳 Ödəniş", "ℹ️ Haqqında")
    mk.add("📩 Şikayət və təkliflər")
    mk.add("🔄 Botu yenilə")

    if not is_admin(chat_id):
        mk.add("🤝 Dostunu dəvət et")

    if is_admin(chat_id):
        mk.add("📊 Admin Panel")

    bot.send_message(chat_id, "📋 Əsas menyudan seçim et:", reply_markup=mk)


if __name__ == "__main__":
    print("⚙️ BestHome Unified Bot FULL v9 işə düşür...")
    init_local_db()
    migrate_user_statuses()
    init_agents_db()
    init_main_db_indices()
    ensure_fts_tables()
    check_favorite_price_drops()

    threading.Thread(target=saved_search_worker, daemon=True).start()
    threading.Thread(target=favorite_price_worker, daemon=True).start()
    threading.Thread(target=subscription_notifier, daemon=True).start()

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "✅ BestHome Bot işləyir."

    threading.Thread(target=run_bot, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
