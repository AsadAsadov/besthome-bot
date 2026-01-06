# ============================================
# 🏠 BestHome Unified Bot — FULL v9
# Elan əlavə • Filtrlə axtarış • Açar sözlə axtarış • Nömrə ilə axtarış
# Favorilər • Admin Panel • Vasitəçi bazası • İstifadəçi təsdiqi
# besthome.db + local_data.db + agents.db
# ©️ 2025 Əsəd Əsədov (@esedovesed)
# ============================================

CURRENT_VERSION = "v10"

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
import html
import logging
import json
import hashlib
import glob
from datetime import datetime, date, timedelta, timezone
from collections import Counter, defaultdict
from functools import wraps
from typing import Optional, Tuple, List, Dict, Any, Literal, Union, Set
from urllib.parse import quote, unquote, urlsplit, urlunsplit, parse_qs, urlencode
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import requests
from flask import (
    Flask,
    jsonify,
    redirect,
    request,
    send_file,
    session,
    url_for,
)
import telebot
from telebot import types

# ==============================
# 💳 ABUNƏLİK KONFİQURASİYASI
# ==============================
SUBSCRIPTION_PLANS = {
    "1": {"title": "1 gün", "price": "1 AZN", "days": 1},
    "7": {"title": "7 gün", "price": "3 AZN", "days": 7},
    "15": {"title": "15 gün", "price": "5 AZN", "days": 15},
    "30": {"title": "30 gün", "price": "9 AZN", "days": 30},
}

# Kapital Bank hazır olduqda URL-ı burada təyin etmək kifayətdir (məsələn,
# https://pay.kapitalbank.az/merchant/XXXX).
CARD_PAYMENT_URL = os.getenv("CARD_PAYMENT_URL")

REFERRAL_REWARD_DAYS = 3
REFERRAL_MILESTONE_COUNT = 10
REFERRAL_MILESTONE_BONUS_DAYS = 45

ALLOWED_START_AREAS = {
    "mehle",
    "nerimanov",
    "xetai",
    "28may",
    "genclik",
    "saray",
    "neftciler",
}

# ==============================
# 🔐 BOT KONFİQURASİYASI
# ==============================
BOT_TOKEN = None
BOT_USERNAME = None


class _BotProxy:
    def __init__(self):
        self._bot = None
        self._pending_handlers = []

    def bind(self, real_bot):
        self._bot = real_bot
        for handler_type, args, kwargs, func in self._pending_handlers:
            getattr(self._bot, handler_type)(*args, **kwargs)(func)
        self._pending_handlers.clear()

    def _store_handler(self, handler_type, *args, **kwargs):
        def decorator(func):
            if self._bot is not None:
                getattr(self._bot, handler_type)(*args, **kwargs)(func)
            else:
                self._pending_handlers.append((handler_type, args, kwargs, func))
            return func

        return decorator

    def message_handler(self, *args, **kwargs):
        return self._store_handler("message_handler", *args, **kwargs)

    def callback_query_handler(self, *args, **kwargs):
        return self._store_handler("callback_query_handler", *args, **kwargs)

    def __getattr__(self, name):
        if self._bot is None:
            raise RuntimeError("Bot is not initialized. Call main() first.")
        attr = getattr(self._bot, name)

        if (
            name.startswith("send_")
            or name.startswith("edit_message")
            or name
            in (
                "answer_callback_query",
                "delete_message",
            )
        ):

            def wrapped(*args, **kwargs):
                try:
                    return attr(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"Telegram send error ignored: {e}")

            return wrapped

        return attr


ADMIN_ID = 1311851277
CHANNEL_ID = -1001878623087  # Bot bu kanalda admin olmalıdır

bot = _BotProxy()

# ==============================
# 💾 DATABASE KONFİQURASİYASI
# ==============================
from config import ENV, ADMIN_IDS as CONFIG_ADMIN_IDS, PRIMARY_ADMIN_ID
import os

ADMIN_ID = PRIMARY_ADMIN_ID or ADMIN_ID
ADMIN_IDS = CONFIG_ADMIN_IDS or [ADMIN_ID]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

WEB_APP_URL = (
    os.environ.get("WEB_APP_URL")
    or os.environ.get("PUBLIC_WEB_APP_URL")
    or os.environ.get("PUBLIC_BASE_URL")
)

BASE_DATA_DIR = os.getenv("DATA_DIR", "/data")
os.environ.setdefault("DATA_DIR", BASE_DATA_DIR)
os.makedirs(BASE_DATA_DIR, exist_ok=True)
DATA_DIR = BASE_DATA_DIR

MAIN_DB = os.path.join(BASE_DATA_DIR, "besthome.db")
LOCAL_DB = os.path.join(BASE_DATA_DIR, "local_data.db")
AGENTS_DB = os.path.join(BASE_DATA_DIR, "agents.db")


def _load_bot_token():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN env dəyişəni tapılmadı. Zəhmət olmasa BOT_TOKEN dəyərini təyin edin."
        )
    return token


def _log_db_status():
    for label, db_path in (
        ("besthome", MAIN_DB),
        ("local", LOCAL_DB),
        ("agents", AGENTS_DB),
    ):
        if os.path.exists(db_path):
            try:
                size = os.path.getsize(db_path)
                logger.info("DB status %s path=%s size=%s bytes", label, db_path, size)
            except Exception:
                logger.info("DB status %s path=%s exists", label, db_path)
        else:
            logger.warning("DB status %s path=%s MISSING", label, db_path)


# ==============================
# 🛡️ DB TƏHLÜKƏSİZLİK YOXLAMASI
# ==============================

# ==============================
# 🧠 STATE-LƏR
# ==============================
user_state = {}  # Yeni elan prosesi
search_state = {}  # Axtarış paging və filter state
customer_request_state = {}
agent_request_lookup_state = {}
admin_customer_request_state = {}
USER_STATE: Dict[int, str] = {}
CUSTOMER_REQUEST_COOLDOWN_SECONDS = 300
LISTING_SESSION_TTL_SECONDS = 4 * 3600
listing_sessions: Dict[int, Dict[str, Any]] = {}
user_stats_filter: Dict[int, str] = {}
today_results_cache: Dict[int, Dict[str, Any]] = {}
payment_plan_selection: Dict[int, Dict[str, Any]] = {}

logger = logging.getLogger("besthome_bot")


@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = message.chat.id

    def fetch_user_row():
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT approved, blocked, demo_end_at, demo_expires_at, paid_until
            FROM users
            WHERE chat_id=?
            """,
            (chat_id,),
        )
        row = cur.fetchone()
        conn.close()
        return row

    user_row = fetch_user_row()
    now = datetime.now(timezone.utc)

    text = message.text or ""
    parts = text.split(maxsplit=1)
    start_arg = parts[1].strip().lower() if len(parts) > 1 else ""

    logger.info("START HANDLER HIT user=%s arg=%s", chat_id, start_arg)

    try:
        handle_start_attribution_and_demo(message, start_arg)
    except Exception as e:
        logger.exception("Start logic failed")
        bot.send_message(
            chat_id,
            "⚠️ Sistem yenilənir, zəhmət olmasa 1 dəqiqə sonra yenidən yoxlayın.",
        )

    user_row = fetch_user_row()

    def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
        if not dt:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    blocked = bool(user_row["blocked"]) if user_row and "blocked" in user_row.keys() else False
    paid_until_raw = user_row["paid_until"] if user_row and "paid_until" in user_row.keys() else None
    demo_end_raw = None
    if user_row:
        if "demo_end_at" in user_row.keys() and user_row["demo_end_at"]:
            demo_end_raw = user_row["demo_end_at"]
        elif "demo_expires_at" in user_row.keys() and user_row["demo_expires_at"]:
            demo_end_raw = user_row["demo_expires_at"]

    paid_until_dt = to_utc(parse_dt_safe(paid_until_raw))
    demo_end_dt = to_utc(parse_dt_safe(demo_end_raw))

    user_active = (
        (not blocked)
        and (
            (paid_until_dt is not None and paid_until_dt > now)
            or (demo_end_dt is not None and demo_end_dt > now)
        )
    )

    if not user_active:
        bot.send_message(
            chat_id,
            "⏳ Pulsuz sınaq müddətiniz başa çatıb.\n"
            "🔒 Botdan tam şəkildə istifadə etmək üçün\n"
            "📌 Ödəniş bölməsindən uyğun paketi seçə bilərsiniz.",
        )

    send_main_menu(chat_id)


def handle_start_attribution_and_demo(message, start_arg: str):
    # existing start logic here (do not remove)
    register_or_update_user_if_needed(message, start_arg)


def _pbkdf2_hash_password(password: str, iterations: int = 120_000) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def _verify_password_hash(password: str, stored_hash: str) -> bool:
    try:
        algo, iter_str, salt, digest = str(stored_hash).split("$")
        iterations = int(iter_str)
    except Exception:
        return False
    if algo != "pbkdf2_sha256":
        return False
    check_digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return hashlib.compare_digest(check_digest, digest)


def _load_admin_password_config():
    mapping = {}
    raw = os.environ.get("ADMIN_PANEL_PASSWORDS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                mapping.update({str(k).lower(): str(v) for k, v in parsed.items()})
            elif isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    ident = (
                        item.get("id") or item.get("username") or item.get("chat_id")
                    )
                    pwd_hash = item.get("password_hash") or item.get("hash")
                    if ident and pwd_hash:
                        mapping[str(ident).lower()] = str(pwd_hash)
        except Exception:
            logger.exception("Failed to parse ADMIN_PANEL_PASSWORDS")

    shared_hash = os.environ.get("ADMIN_PANEL_SHARED_PASSWORD_HASH", "").strip()
    shared_plain = os.environ.get("ADMIN_PANEL_SHARED_PASSWORD", "").strip()
    if not shared_hash and shared_plain:
        shared_hash = _pbkdf2_hash_password(shared_plain)

    return mapping, (shared_hash or None)


ADMIN_PANEL_PASSWORD_HASHES, ADMIN_PANEL_SHARED_PASSWORD_HASH = (
    _load_admin_password_config()
)

user_state = {}  # Yeni elan proses state
search_state = {}  # Açar sözlə axtarış paging state
today_flow_state = {}
UI_CONTEXT_MAIN = "main_menu"
UI_CONTEXT_TODAY = "browsing_today_listings"
UI_CONTEXT_SEARCH = "browsing_search_results"
UI_CONTEXT_ADMIN = "browsing_admin_users"
ui_context_state: Dict[int, str] = defaultdict(lambda: UI_CONTEXT_MAIN)
search_reminder_shown = set()  # Session-level reminder flag
session_interactions = {}
db_update_state_lock = threading.Lock()
db_update_state: Dict[int, Dict[str, Any]] = {}
db_update_lock_by_admin: Dict[int, bool] = {}
db_update_started_at: Dict[int, datetime] = {}
db_update_lock_by_admin_lock = threading.Lock()
DB_UPDATE_TTL_SECONDS = 600
DB_UPDATE_MAX_ZIP_BYTES = 500 * 1024 * 1024
DB_UPDATE_MIN_ZIP_BYTES = 1 * 1024 * 1024
DB_UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 240
DB_UPDATE_UNZIP_TIMEOUT_SECONDS = 120
DB_UPDATE_VALIDATE_TIMEOUT_SECONDS = 60
DB_UPDATE_REPLACE_TIMEOUT_SECONDS = 60
DB_UPDATE_STATE_PATH = os.path.join(DATA_DIR, "db_update_state.json")
DB_UPDATE_TMP_DIR = "/tmp/besthome_update"
DB_UPDATE_BACKUP_DIR = "/tmp/besthome_backups"
DB_UPDATE_ZIP_PATH = "/tmp/besthome_update.zip"
DB_UPDATE_DB_PATH = "/tmp/besthome_update.db"
complaint_flow_state = {}
complaint_records = {}
admin_reply_state = {}
last_complaint_time = {}
admin_stats_period = {}
admin_direct_message_state = {}
admin_user_message_state = {}
admin_user_extend_state = {}
admin_user_action_state = {}
admin_message_state = {}
admin_update_state = {}
admin_state: Dict[int, str] = {}
ui_state = defaultdict(list)
customer_request_rule_state = {}
keyword_alert_state = {}
notification_rule_state = {}
notification_menu_state = {}
keyword_hits_context = {}
keyword_notification_state: Dict[int, Dict[str, Any]] = {}
admin_selected_users: Dict[int, set] = defaultdict(set)
admin_user_rows_cache: Dict[int, List[sqlite3.Row]] = defaultdict(list)
admin_bulk_action_state: Dict[int, Dict[str, Any]] = {}
admin_pending_action: Dict[int, Dict[str, Any]] = {}
user_callback_locks: Dict[int, threading.Lock] = defaultdict(threading.Lock)
admin_user_last_list: Dict[int, str] = {}
admin_navigation_state: Dict[int, Dict[str, Any]] = {}
bonus_probability_edit_state: Dict[int, bool] = {}
BLOCKED_MESSAGE_TEXT = "Hesabınız müvəqqəti olaraq dayandırıldı."
BLOCKED_PROMPT_TEXT = (
    "Hesabınız aktiv deyil. Davam etmək üçün ödəniş edin və ya 3 gün demo istifadə edin."
)
STATUS_PENDING = "pending"
STATUS_ACTIVE_PAID = "active_paid"
STATUS_ACTIVE_DEMO = "active_demo"
STATUS_ACTIVE_FREE = "active_free"
STATUS_BLOCKED = "blocked"
STATUS_REJECTED = "rejected"
ACTIVE_STATUSES = {STATUS_ACTIVE_PAID, STATUS_ACTIVE_DEMO, STATUS_ACTIVE_FREE}

DEMO_DAYS = 3
BONUS_SPIN_COOLDOWN_HOURS = 24
BONUS_DEFAULT_PROBABILITIES = {
    0: 30,
    1: 30,
    2: 20,
    3: 10,
    5: 7,
    7: 3,
}
DEFAULT_DAILY_CHANCE_LIMIT = 1
TEXTS_AZ = {
    "admin_panel_button": "📊 Admin Panel",
    "admin_panel_title": "🛠 Admin Panel:",
    "admin_panel_nav_next": "▶️ Növbəti səhifə",
    "admin_panel_nav_prev": "◀️ Əvvəlki səhifə",
    "admin_panel_back_main": "⬅️ Geri",
    "admin_panel_pending_listings": "✅ Təsdiqlənməyən elanlar",
    "admin_panel_stats": "📊 Statistikalar",
    "admin_panel_customer_requests": "📌 Müştəri istəkləri",
    "admin_panel_financial_reports": "💰 Maliyyə hesabatları",
    "admin_panel_bonus_stats": "📊 Şans Statistikası",
    "admin_panel_agents_notify": "📢 Vasitəçilərə bildiriş",
    "admin_panel_user_search": "🆔 İstifadəçi ID ilə axtar",
    "admin_panel_users": "👥 İstifadəçilər",
    "admin_panel_promos": "🎟 Promo kodlar",
    "admin_panel_reset_limits": "♻️ Limitləri sıfırla",
    "admin_panel_send_update": "🚀 Yeniləmə göndər",
    "admin_panel_topviews": "🔥 Ən çox baxılan elanlar",
    "admin_panel_db_update": "📦 Bazanı yenilə (Dropbox)",
    "admin_panel_direct_message": "📨 İstifadəçiyə mesaj göndər",
    "admin_panel_customer_requests_access": "📌 Müştəri istəkləri icazəsi",
    "admin_panel_archived_requests": "🗄 Arxivlənmiş müştəri istəkləri",
    "financial_reports_back": "⬅️ Geri (Admin Panel)",
    "financial_reports_history": "📜 Ödəniş tarixçəsi",
    "financial_reports_referral": "🤝 Referral statistikası",
    "financial_reports_monthly": "📈 Aylıq gəlir hesabatı",
    "admin_users_menu_prompt": "İstifadəçi kateqoriyasını seç:",
    "admin_users_menu_active": "✅ Aktiv istifadəçilər",
    "admin_users_menu_demo": "🎁 Demo istifadəçilər",
    "admin_users_menu_expired": "⏳ Vaxtı bitmiş istifadəçilər",
    "admin_users_menu_blocked": "⛔ Bloklananlar",
    "admin_users_menu_pending": "⏸ Təsdiqlənməmişlər",
    "admin_userlist_title_active": "✅ Aktiv istifadəçilər",
    "admin_userlist_title_demo": "🎁 Demo istifadəçilər",
    "admin_userlist_title_expired": "⏳ Vaxtı bitmiş istifadəçilər",
    "admin_userlist_title_blocked": "⛔ Bloklananlar",
    "admin_userlist_title_pending": "⏸ Təsdiqlənməmişlər",
    "admin_userlist_page_label": "Səhifə",
    "admin_userlist_empty": "Bu kateqoriyada istifadəçi yoxdur.",
    "admin_userlist_entry_name": "👤 Ad",
    "admin_userlist_entry_id": "🆔 ID",
    "admin_userlist_entry_username": "👤 Username",
    "admin_userlist_entry_joined": "📅 Qoşulma tarixi",
    "admin_userlist_entry_expiry": "⏳ Bitmə tarixi",
    "admin_userlist_entry_remaining": "🕒 Qalan gün",
    "admin_userlist_nav_first": "⏮ İlk",
    "admin_userlist_nav_prev": "◀️ Geri",
    "admin_userlist_nav_page": "📄 {page}/{total}",
    "admin_userlist_nav_next": "▶️ İrəli",
    "admin_userlist_nav_last": "⏭ Son",
    "admin_userlist_category_missing": "❌ Kateqoriya oxunmadı.",
    "admin_userlist_category_invalid": "❌ Yanlış istifadəçi kateqoriyası.",
    "admin_userlist_load_error": "❌ İstifadəçilər yüklənərkən xəta baş verdi.",
    "admin_userlist_open_error": "❌ İstifadəçi siyahısı açıla bilmədi. Loglara baxın.",
    "admin_user_action_menu": (
        "👤 User ID: {user_id}\n"
        "Status: {status}\n\n"
        "Seçimlər:\n"
        "1️⃣ Mesaj göndər\n"
        "2️⃣ 1 gün aktiv et (1 AZN)\n"
        "3️⃣ 7 gün aktiv et (3 AZN)\n"
        "4️⃣ 15 gün aktiv et (5 AZN)\n"
        "5️⃣ 30 gün aktiv et (9 AZN)\n"
        "6️⃣ Limitsiz et\n"
        "0️⃣ Geri\n\n"
        "{price_text}"
    ),
    "admin_payment_price_text": (
        "💳 Qiymətlər:\n"
        "• 1 gün – 1 AZN\n"
        "• 7 gün – 3 AZN\n"
        "• 15 gün – 5 AZN\n"
        "• 30 gün – 9 AZN"
    ),
    "admin_pending_listings_none": "✅ Təsdiq gözləyən elan yoxdur.",
    "admin_listing_approve": "✅ Təsdiqlə",
    "admin_listing_delete": "🗑 Sil",
    "admin_pending_users_none": "✅ Gözləyən istifadəçi yoxdur.",
    "admin_pending_users_title": "❌ Təsdiqlənməmiş istifadəçilər:",
    "admin_pending_user_approve": "✅ Aktiv et",
    "admin_pending_user_demo": "🎁 Demo ver",
    "admin_pending_user_free": "♾ Limitsiz et",
    "admin_pending_user_reject": "❌ Rədd et",
    "admin_pending_user_block": "⛔ Blokla",
    "admin_user_not_found": "❌ İstifadəçi tapılmadı",
    "admin_user_no_plan": "❌ Aktiv plan tapılmadı",
    "admin_user_activated": "✅ Aktiv edildi",
    "admin_user_demo_given": "🎁 Demo verildi",
    "admin_back_button": "⬅️ Geri",
    "admin_promo_menu_title": "🎟 Promo kod idarəsi:",
    "admin_promo_generate_1": "🎁 1 gün",
    "admin_promo_generate_3": "🎁 3 gün",
    "admin_promo_generate_5": "🎁 5 gün",
    "admin_promo_generate_7": "🎁 7 gün",
    "admin_promo_list": "📋 Promo siyahısı",
    "admin_promo_stats": "📊 Promo statistikası",
    "admin_promo_empty": "❌ Promo kod yoxdur.",
    "admin_promo_list_title": "📋 Promo kod siyahısı:",
    "admin_promo_status_active": "✅ Aktiv",
    "admin_promo_status_inactive": "⛔ Deaktiv",
    "admin_promo_toggle_active": "✅",
    "admin_promo_toggle_inactive": "⛔",
    "admin_promo_nav_prev": "⬅️ Əvvəlki",
    "admin_promo_nav_next": "➡️ Növbəti",
    "admin_promo_back": "↩️ Geri",
    "admin_promo_created": "✅ {days} günlük promo kod yaradıldı:\n{code}",
    "admin_promo_create_failed": "❌ Promo kod yaradılmadı.",
    "admin_payment_history_title": "📜 Ödəniş tarixçəsi (admin):",
    "admin_payment_history_none": "❌ Ödəniş qeydi yoxdur.",
    "admin_payment_history_back": "⬅️ Siyahıya qayıt",
    "admin_subscription_extend_3": "➕ 3 gün uzat",
    "admin_subscription_extend_7": "➕ 7 gün uzat",
    "admin_subscription_extend_15": "➕ 15 gün uzat",
    "admin_subscription_extend_30": "➕ 30 gün uzat",
    "admin_subscription_stop": "⛔ Dayandır",
    "admin_subscription_activate": "▶️ Aktiv et",
    "admin_stats_period_day": "📆 Bu gün",
    "admin_stats_period_week": "📆 Bu həftə",
    "admin_stats_period_month": "📆 Bu ay",
    "admin_stats_customer_requests": "📌 Müştəri istəkləri",
    "admin_req_period_day": "📆 Bu gün",
    "admin_req_period_week": "🗓 Bu həftə",
    "admin_req_period_month": "🗓 Bu ay",
    "admin_req_type_sale": "🏠 Satılır",
    "admin_req_type_rent": "🏡 Kirayə verilir",
    "admin_req_back_types": "⬅️ Satılır / Kirayə seçiminə qayıt",
    "admin_req_back_rayons": "⬅️ Rayonlara qayıt",
    "admin_req_back_rayon_list": "⬅️ Rayon siyahısı",
    "admin_req_back_main": "🏠 Əsas menyu",
    "admin_req_flagged": "⭐ İşarələnənlər",
    "admin_req_flag": "⭐ İşarələ",
    "admin_req_archive": "🗄 Arxivlə",
    "admin_req_delete": "🗑 Sil",
    "admin_req_rayon_item": "📍 {rayon} ({count})",
    "admin_req_user_id": "🆔 ID: {user_id}",
    "admin_req_user_activate": "🟢 Aktiv et",
    "admin_req_user_disable": "🔴 Söndür",
    "admin_req_whatsapp": "💬 WhatsApp yaz",
    "admin_req_restore": "♻️ Geri qaytar",
    "admin_req_delete_full": "🗑 Tam sil",
    "admin_req_back_requests": "⬅️ Müştəri istəkləri",
    "admin_nav_first_icon": "⏮",
    "admin_nav_prev_icon": "◀️",
    "admin_nav_page": "📄 {page}/{total}",
    "admin_nav_next_icon": "▶️",
    "admin_nav_last_icon": "⏭",
}
ADMIN_PAYMENT_PRICE_TEXT = TEXTS_AZ["admin_payment_price_text"]

# Legacy status-related columns kept for backward compatibility.
DEPRECATED_USER_COLUMNS = {
    "status",
    "demo_used",
    "demo_expires_at",
    "last_status_change_at",
    "demo_start_at",
}

USERLIST_STATUS_FILTERS = {
    "active": (STATUS_ACTIVE_PAID, STATUS_ACTIVE_DEMO, STATUS_ACTIVE_FREE),
    "free": (STATUS_ACTIVE_FREE,),
    "blocked": (STATUS_BLOCKED,),
    "rejected": (STATUS_REJECTED,),
    "pending": (STATUS_PENDING,),
}
FINANCIAL_REPORTS_BUTTON = TEXTS_AZ["admin_panel_financial_reports"]
FINANCIAL_REPORTS_BACK = TEXTS_AZ["financial_reports_back"]
FINANCIAL_REPORTS_MENU = [
    TEXTS_AZ["financial_reports_history"],
    TEXTS_AZ["financial_reports_referral"],
    TEXTS_AZ["financial_reports_monthly"],
    FINANCIAL_REPORTS_BACK,
]
ADMIN_PANEL_BUTTONS = [
    TEXTS_AZ["admin_panel_stats"],
    "📊 QR Statistikası",
    FINANCIAL_REPORTS_BUTTON,
    TEXTS_AZ["admin_panel_bonus_stats"],
    TEXTS_AZ["admin_panel_agents_notify"],
    TEXTS_AZ["admin_panel_user_search"],
    TEXTS_AZ["admin_panel_users"],
    TEXTS_AZ["admin_panel_promos"],
    TEXTS_AZ["admin_panel_send_update"],
    TEXTS_AZ["admin_panel_topviews"],
    TEXTS_AZ["admin_panel_db_update"],
    TEXTS_AZ["admin_panel_direct_message"],
]
ADMIN_PANEL_BACK_MAIN = TEXTS_AZ["admin_panel_back_main"]
admin_panel_page_state = {}
ADMIN_PANEL_ACTIONS = list(ADMIN_PANEL_BUTTONS)
ADMIN_PANEL_ACTION_SET = set(ADMIN_PANEL_ACTIONS)
ADMIN_PANEL_ACTION_KEYS = {
    text: str(idx) for idx, text in enumerate(ADMIN_PANEL_ACTIONS)
}
ADMIN_PANEL_ACTION_LOOKUP = {v: k for k, v in ADMIN_PANEL_ACTION_KEYS.items()}

QR_SOURCE_AREAS = {
    "mehle": "Məhəllə",
    "nerimanov": "Nərimanov",
    "xetai": "Xətai",
    "28may": "28 May",
    "genclik": "Gənclik",
    "saray": "Saray",
    "neftciler": "Neftçilər",
}

QR_STATS_AREAS = [
    "xetai",
    "mehle",
    "genclik",
    "nerimanov",
    "28may",
    "saray",
    "neftciler",
]

QR_STATS_RANGE_LABELS = {
    "24h": "Son 24 saat",
    "7d": "Son 7 gün",
    "30d": "Son 30 gün",
    "all": "Ümumi",
}

# Pagination
PAGE_SIZE = 20
NEW_LISTING_WINDOW_HOURS = 24
HOT_VIEWS_THRESHOLD = 50
PAGE_SIZE_REQ = 10
PAGE_SIZE_DEMO_USERS = 5
COMPLAINT_CATEGORIES = [
    "🐞 Texniki problem",
    "💡 Təklif",
    "❗ Şikayət",
    "💬 Digər",
]
COMPLAINT_BACK = "⬅️ Geri"
COMPLAINT_COOLDOWN_SECONDS = 300

# Admin user lists
PAGE_SIZE_USERS = 10
PAGE_SIZE_NOTIFICATIONS = 10
admin_user_page_state = {}


main_db_connections = set()
main_db_connections_lock = threading.Lock()
main_db_update_in_progress = threading.Event()
main_db_replace_lock = threading.Lock()


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
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    register_main_conn(conn)
    return conn


def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB, check_same_thread=False)  # 🔥 ÇOX VACİB
    conn.row_factory = sqlite3.Row
    return conn


def get_db():
    conn = sqlite3.connect(LOCAL_DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_agents_conn():
    conn = sqlite3.connect(AGENTS_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cur.fetchall() if len(row) > 1}
    except Exception:
        return set()


def normalize_text(text: str) -> str:
    mapping = {
        "ə": "e",
        "ş": "s",
        "ı": "i",
        "ö": "o",
        "ü": "u",
        "ç": "c",
        "ğ": "g",
    }
    text = (text or "").lower().strip()
    for k, v in mapping.items():
        text = text.replace(k, v)
    text = " ".join(text.split())
    return text


def set_ui_context(chat_id: int, context: str):
    ui_context_state[chat_id] = context


def get_ui_context(chat_id: int) -> str:
    return ui_context_state.get(chat_id, UI_CONTEXT_MAIN)


def safe_send(chat_id: int, text: str, **kwargs):
    return bot.send_message(chat_id, text, **kwargs)


def safe_admin_step(chat_id: int, text: str, **kwargs):
    try:
        safe_send(chat_id, text, **kwargs)
    except Exception:
        logger.exception("Admin send failed chat_id=%s text=%s", chat_id, text)


def safe_answer_callback_query(
    callback_id: Optional[str], text: Optional[str] = None, **kwargs
):
    if not callback_id:
        return
    try:
        bot.answer_callback_query(callback_id, text, **kwargs)
    except Exception:
        logger.exception("answer_callback_query failed callback_id=%s", callback_id)


def callback_guard(handler):
    @wraps(handler)
    def wrapper(call):
        safe_answer_callback_query(call.id)
        logger.info(
            "callback entry handler=%s chat_id=%s from=%s data=%s",
            handler.__name__,
            (
                getattr(getattr(call, "message", None), "chat", None).id
                if getattr(call, "message", None)
                else None
            ),
            getattr(getattr(call, "from_user", None), "id", None),
            getattr(call, "data", None),
        )
        try:
            return handler(call)
        except Exception as exc:
            logger.exception("Callback failed data=%s", getattr(call, "data", None))
            chat_id = None
            if call and getattr(call, "message", None):
                chat_id = call.message.chat.id
            primary_admin = ADMIN_IDS[0] if ADMIN_IDS else ADMIN_ID
            notify_chat_id = chat_id if chat_id and is_admin(chat_id) else primary_admin
            if notify_chat_id is not None:
                safe_admin_step(
                    notify_chat_id,
                    f"⚠️ Xəta oldu: {exc} (chat_id={chat_id})",
                )
            if chat_id:
                safe_send(chat_id, "Əsas menyu bərpa edildi")
                recover_main_menu(chat_id, getattr(call, "message", None))

    return wrapper


def set_user_state(chat_id: int, state: str):
    USER_STATE[chat_id] = state


def clear_user_state(chat_id: int):
    USER_STATE.pop(chat_id, None)


def get_user_state(chat_id: int) -> Optional[str]:
    return USER_STATE.get(chat_id)


def acquire_user_action_lock(user_id: Optional[int]) -> Optional[threading.Lock]:
    if not user_id:
        return None
    lock = user_callback_locks.setdefault(user_id, threading.Lock())
    if lock.acquire(blocking=False):
        return lock
    return None


def run_callback_background(
    call,
    task,
    *,
    waiting_text: str = "⏳ Zəhmət olmasa gözləyin...",
    send_menu_on_finish: bool = False,
):
    chat_id = None
    try:
        chat_id = call.message.chat.id
    except Exception:
        chat_id = getattr(getattr(call, "from_user", None), "id", None)

    user_id = getattr(getattr(call, "from_user", None), "id", chat_id)
    lock = acquire_user_action_lock(user_id)
    if not lock:
        if chat_id:
            safe_send(
                chat_id,
                "⏳ Əvvəlki əməliyyat davam edir, zəhmət olmasa gözləyin...",
            )
        return

    wait_message = None
    if chat_id:
        try:
            wait_message = safe_send(chat_id, waiting_text)
        except Exception:
            wait_message = None

    def runner():
        try:
            task()
        except Exception:
            logger.exception("Background task failed")
            if chat_id:
                safe_send(chat_id, "⚠️ Xəta baş verdi, zəhmət olmasa yenidən cəhd edin.")
        finally:
            if chat_id:
                try:
                    if wait_message:
                        bot.delete_message(chat_id, wait_message.message_id)
                except Exception:
                    pass
                if send_menu_on_finish:
                    send_main_menu(chat_id)
            lock.release()

    threading.Thread(target=runner, daemon=True).start()


def run_with_timeout(step_name: str, timeout_seconds: int, func, *args, **kwargs):
    logger.info("DB update step start step=%s timeout=%s", step_name, timeout_seconds)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            result = future.result(timeout=timeout_seconds)
            logger.info("DB update step done step=%s", step_name)
            return result
        except FutureTimeoutError:
            logger.error(
                "DB update step timeout step=%s timeout=%s", step_name, timeout_seconds
            )
            raise RuntimeError(f"{step_name} vaxt limiti bitdi")
        except Exception:
            logger.exception("DB update step failed step=%s", step_name)
            raise


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_db_update_state_file() -> Dict[str, Any]:
    if not os.path.exists(DB_UPDATE_STATE_PATH):
        return {}
    try:
        with open(DB_UPDATE_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to read db update state file")
        return {}


def _save_db_update_state_file(state: Dict[str, Any]) -> None:
    try:
        with open(DB_UPDATE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        logger.exception("Failed to write db update state file")


def load_last_update_max_id() -> Optional[int]:
    data = _load_db_update_state_file()
    try:
        val = data.get("last_max_id")
        return int(val) if val is not None else None
    except Exception:
        return None


def save_last_update_max_id(max_id: int) -> None:
    data = _load_db_update_state_file()
    data["last_max_id"] = int(max_id)
    data["updated_at"] = now_utc().isoformat()
    _save_db_update_state_file(data)


def cleanup_stale_db_updates() -> List[int]:
    stale = []
    now = now_utc()
    with db_update_state_lock:
        for admin_id, state in list(db_update_state.items()):
            started_at = state.get("started_at")
            if not started_at:
                continue
            elapsed = (now - started_at).total_seconds()
            if elapsed > DB_UPDATE_TTL_SECONDS:
                stale.append(admin_id)
                db_update_state.pop(admin_id, None)
    if stale:
        with db_update_lock_by_admin_lock:
            for admin_id in stale:
                db_update_lock_by_admin.pop(admin_id, None)
                db_update_started_at.pop(admin_id, None)
        logger.warning("stale db update lock recovered admins=%s", stale)
        for admin_id in stale:
            admin_update_state.pop(admin_id, None)
    return stale


def get_running_db_update() -> Optional[Tuple[int, Dict[str, Any]]]:
    with db_update_state_lock:
        for admin_id, state in db_update_state.items():
            if state.get("status") == "running":
                return admin_id, dict(state)
    return None


def set_db_update_state(admin_id: int, status: str) -> None:
    with db_update_state_lock:
        db_update_state[admin_id] = {
            "status": status,
            "started_at": now_utc(),
            "last_progress": now_utc(),
        }


def acquire_db_update_lock(admin_id: int) -> bool:
    now = now_utc()
    with db_update_lock_by_admin_lock:
        if db_update_lock_by_admin.get(admin_id):
            started_at = db_update_started_at.get(admin_id)
            if (
                started_at
                and (now - started_at).total_seconds() > DB_UPDATE_TTL_SECONDS
            ):
                logger.warning("stale db update lock recovered admin_id=%s", admin_id)
                db_update_lock_by_admin.pop(admin_id, None)
                db_update_started_at.pop(admin_id, None)
            else:
                return False
        db_update_lock_by_admin[admin_id] = True
        db_update_started_at[admin_id] = now
        return True


def release_db_update_lock(admin_id: int) -> None:
    with db_update_lock_by_admin_lock:
        db_update_lock_by_admin.pop(admin_id, None)
        db_update_started_at.pop(admin_id, None)


def update_db_update_progress(admin_id: int) -> None:
    with db_update_state_lock:
        state = db_update_state.get(admin_id)
        if state:
            state["last_progress"] = now_utc()


def clear_db_update_state(admin_id: int) -> None:
    with db_update_state_lock:
        db_update_state.pop(admin_id, None)


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes} dəq {secs} san"
    return f"{secs} san"


_users_schema_cache = None
_users_schema_lock = threading.Lock()


def detect_users_schema():
    global _users_schema_cache
    if _users_schema_cache is not None:
        return _users_schema_cache
    with _users_schema_lock:
        if _users_schema_cache is not None:
            return _users_schema_cache
        columns = set()
        conn = None
        try:
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(users)")
            for row in cur.fetchall():
                try:
                    name = row["name"]
                except Exception:
                    name = row[1] if len(row) > 1 else None
                if name:
                    columns.add(name)
            logger.info("Detected users schema columns: %s", sorted(columns))
        except Exception:
            logger.exception("Failed to detect users schema")
        finally:
            if conn:
                conn.close()
        _users_schema_cache = {"columns": columns}
        return _users_schema_cache


def get_user_step(chat_id: int):
    state = user_state.get(chat_id)
    if isinstance(state, dict):
        return state.get("step")
    return None


class DiskFullError(RuntimeError):
    pass


def clean_old_update_artifacts():
    for path in glob.glob(os.path.join(DATA_DIR, "besthome_update_*.db")):
        try:
            os.remove(path)
        except Exception:
            pass

    data_tmp_dir = os.path.join(DATA_DIR, "tmp")
    if os.path.isdir(data_tmp_dir):
        for entry in os.listdir(data_tmp_dir):
            entry_path = os.path.join(data_tmp_dir, entry)
            try:
                if os.path.isdir(entry_path):
                    shutil.rmtree(entry_path, ignore_errors=True)
                else:
                    os.remove(entry_path)
            except Exception:
                pass

    legacy_backup_dir = os.path.join(DATA_DIR, "backups")
    if os.path.isdir(legacy_backup_dir):
        shutil.rmtree(legacy_backup_dir, ignore_errors=True)

    logger.info("🧹 Old backups cleaned")


def ensure_tmp_workspace():
    shutil.rmtree(DB_UPDATE_TMP_DIR, ignore_errors=True)
    os.makedirs(DB_UPDATE_TMP_DIR, exist_ok=True)


def ensure_sufficient_disk_space(admin_id: int):
    usage = shutil.disk_usage(DATA_DIR)
    if usage.free < 200 * 1024 * 1024:
        logger.warning("❌ Update aborted: disk full")
        safe_admin_step(admin_id, "❌ Disk doludur. Köhnə backup-lar silinməlidir.")
        raise DiskFullError("Diskdə kifayət qədər boş yer yoxdur")


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

    os.makedirs(DB_UPDATE_BACKUP_DIR, exist_ok=True)
    ts = now_utc().strftime("%Y%m%d_%H%M")
    backup_path = os.path.join(DB_UPDATE_BACKUP_DIR, f"besthome_{ts}.db")
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


def normalize_dropbox_urls(url: str) -> List[str]:
    url = url.strip()
    if not url:
        return []
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        return []
    query = parse_qs(parts.query)
    query["dl"] = ["1"]
    base = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), "")
    )
    candidates = [base]
    if "dropbox.com" in parts.netloc:
        alt_netloc = "dl.dropboxusercontent.com"
        candidates.append(urlunsplit((parts.scheme, alt_netloc, parts.path, "", "")))
    return list(dict.fromkeys(candidates))


def download_zip_stream(url: str) -> str:
    last_error = None
    for candidate in normalize_dropbox_urls(url):
        os.makedirs(DB_UPDATE_TMP_DIR, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(suffix=".zip", dir=DB_UPDATE_TMP_DIR)
        os.close(fd)
        try:
            with requests.get(
                candidate,
                stream=True,
                timeout=(10, 120),
                allow_redirects=True,
            ) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP status {r.status_code}")
                content_length = r.headers.get("Content-Length")
                if content_length:
                    try:
                        size = int(content_length)
                        if size > DB_UPDATE_MAX_ZIP_BYTES:
                            raise RuntimeError("ZIP faylı çox böyükdür")
                    except ValueError:
                        pass
                total = 0
                with open(temp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > DB_UPDATE_MAX_ZIP_BYTES:
                            raise RuntimeError("ZIP faylı çox böyükdür")
                        f.write(chunk)
                if total < DB_UPDATE_MIN_ZIP_BYTES:
                    raise RuntimeError("ZIP fayl ölçüsü çox kiçikdir")
            if not zipfile.is_zipfile(temp_path):
                raise RuntimeError("Yüklənən fayl ZIP deyil")
            return temp_path
        except Exception as exc:
            last_error = exc
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logger.warning("Failed to download zip from %s: %s", candidate, exc)
    raise RuntimeError(f"ZIP yükləmə alınmadı: {last_error}")


def download_main_db_file(url: str) -> Tuple[str, bool]:
    last_error = None
    for candidate in normalize_dropbox_urls(url):
        os.makedirs(DB_UPDATE_TMP_DIR, exist_ok=True)
        temp_path = DB_UPDATE_ZIP_PATH
        for path in (DB_UPDATE_ZIP_PATH, DB_UPDATE_DB_PATH):
            if os.path.exists(path):
                os.remove(path)
        try:
            with requests.get(
                candidate,
                stream=True,
                timeout=(10, 120),
                allow_redirects=True,
            ) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP status {r.status_code}")
                total = 0
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > DB_UPDATE_MAX_ZIP_BYTES:
                        raise RuntimeError("Fayl çox böyükdür")
                    with open(temp_path, "ab") as f:
                        f.write(chunk)
                if total <= 0:
                    raise RuntimeError("Fayl ölçüsü sıfırdır")
            is_zip = zipfile.is_zipfile(temp_path)
            if not is_zip:
                os.replace(temp_path, DB_UPDATE_DB_PATH)
                temp_path = DB_UPDATE_DB_PATH
            return temp_path, is_zip
        except Exception as exc:
            last_error = exc
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logger.warning("Failed to download db from %s: %s", candidate, exc)
    raise RuntimeError(f"DB yükləmə alınmadı: {last_error}")


def extract_main_db_from_zip(zip_path: str) -> Tuple[str, str]:
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError("Fayl ZIP formatında deyil")

    ensure_tmp_workspace()
    temp_dir = DB_UPDATE_TMP_DIR
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file:
            raise RuntimeError(f"ZIP faylında zədəli fayl var: {bad_file}")
        target = None
        for name in zf.namelist():
            if os.path.basename(name).lower() == "besthome.db":
                target = name
                break
        if not target:
            raise RuntimeError("ZIP daxilində besthome.db tapılmadı")
        extracted_path = os.path.join(temp_dir, "besthome.db")
        with zf.open(target) as src, open(extracted_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
    if not os.path.exists(extracted_path):
        raise RuntimeError("ZIP extraction failed: /tmp/besthome_update/besthome.db not found")
    logger.info("📦 DB extracted to /tmp")
    return extracted_path, temp_dir


def validate_main_db_file(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("DB daxilində 'listings' cədvəli tapılmadı")
        cur.execute("SELECT COUNT(*) FROM listings")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def get_max_listing_id(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(id) FROM listings")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def count_new_listings_since(db_path: str, last_max_id: Optional[int]) -> int:
    if last_max_id is None:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM listings WHERE id > ?", (last_max_id,))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def atomic_replace_main_db(new_db_path: str) -> Optional[str]:
    temp_target = os.path.join(BASE_DATA_DIR, "besthome.db.new")
    backup_path = backup_main_db_file()
    last_error = None
    for _ in range(3):
        try:
            if os.path.exists(temp_target):
                os.remove(temp_target)
            shutil.copy2(new_db_path, temp_target)
            os.replace(temp_target, MAIN_DB)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    try:
        if os.path.exists(temp_target):
            os.remove(temp_target)
    except Exception:
        pass
    if last_error:
        raise last_error
    with open(MAIN_DB, "rb") as f:
        os.fsync(f.fileno())
    logger.info("✅ Database replaced successfully (copy + replace)")
    return backup_path


def send_db_update_progress(admin_id: int, message: str) -> None:
    update_db_update_progress(admin_id)
    logger.info("DB update progress chat_id=%s step=%s", admin_id, message)
    safe_admin_step(admin_id, message)


def run_db_update_pipeline(admin_id: int, url: str) -> None:
    temp_download_path = None
    extracted_db_path = None
    extracted_dir = None
    backup_path = None
    try:
        main_db_update_in_progress.set()
        clean_old_update_artifacts()
        ensure_tmp_workspace()
        for path in (DB_UPDATE_ZIP_PATH, DB_UPDATE_DB_PATH):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        ensure_sufficient_disk_space(admin_id)
        send_db_update_progress(admin_id, "⬇️ Fayl yüklənir…")
        temp_download_path, is_zip = run_with_timeout(
            "db_download",
            DB_UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
            download_main_db_file,
            url,
        )
        if is_zip:
            send_db_update_progress(admin_id, "📦 Zip açılır…")
            extracted_db_path, extracted_dir = run_with_timeout(
                "zip_extract",
                DB_UPDATE_UNZIP_TIMEOUT_SECONDS,
                extract_main_db_from_zip,
                temp_download_path,
            )
        else:
            extracted_db_path = temp_download_path

        send_db_update_progress(admin_id, "🗄️ DB yoxlanır…")
        run_with_timeout(
            "db_validate",
            DB_UPDATE_VALIDATE_TIMEOUT_SECONDS,
            validate_main_db_file,
            extracted_db_path,
        )

        last_max_id = load_last_update_max_id()
        if last_max_id is None and os.path.exists(MAIN_DB):
            last_max_id = get_max_listing_id(MAIN_DB)

        send_db_update_progress(admin_id, "🔁 DB əvəz olunur…")
        with main_db_replace_lock:
            close_all_main_conns()
            prepare_main_db_for_swap()
            backup_path = run_with_timeout(
                "db_replace",
                DB_UPDATE_REPLACE_TIMEOUT_SECONDS,
                atomic_replace_main_db,
                extracted_db_path,
            )

        conn = sqlite3.connect(MAIN_DB)
        try:
            ensure_created_at_column(
                conn,
                "listings",
                ("inserted_at", "date_read", "date_added", "Elanin_tarixi", "added_at"),
            )
            conn.commit()
        finally:
            conn.close()

        send_db_update_progress(admin_id, "🧱 FTS/indeks yenilənir…")
        try:
            init_main_db_indices()
            ensure_fts_tables()
        except Exception:
            logger.exception("FTS/index rebuild failed after update")

        send_db_update_progress(admin_id, "📊 Statistika hesablanır…")
        try:
            new_listings = count_new_listings_since(MAIN_DB, last_max_id)
            new_max_id = get_max_listing_id(MAIN_DB)
            save_last_update_max_id(new_max_id)

            total_active = count_main_active_listings(use_direct_conn=True)
            sale_active = count_main_active_listings(
                op_code="sat", use_direct_conn=True
            )
            rent_active = count_main_active_listings(
                op_code="kir", use_direct_conn=True
            )
            today_active = count_main_active_listings(
                only_today=True, use_direct_conn=True
            )
            try:
                process_keyword_alerts_for_new_listings()
            except Exception as e:
                logger.warning("keyword alert listing scan error: %s", e)

            report = (
                "✅ Baza uğurla yeniləndi.\n"
                f"📦 Yeni elanlar: {new_listings}\n"
                f"📊 Ümumi elan sayı: {total_active}\n"
                f"1⃣ Satılır: {sale_active}\n"
                f"2⃣ Kirayə verilir: {rent_active}\n"
                f"🕒 Son 24 saat elanları: {today_active}"
            )
            safe_admin_step(admin_id, report)
            logger.info(
                "DB update completed chat_id=%s new=%s total=%s",
                admin_id,
                new_listings,
                total_active,
            )
        except Exception:
            logger.exception("DB stats failed after update chat_id=%s", admin_id)
            safe_admin_step(
                admin_id,
                "✅ DB yeniləndi, amma statistika hesablama zamanı xəta oldu (bot işləkdir).",
            )
    except DiskFullError:
        logger.info("❌ Update aborted: disk full")
    except Exception as exc:
        logger.exception("DB update failed chat_id=%s", admin_id)
        safe_admin_step(admin_id, f"❌ Yenilənmə alınmadı: {exc}")
        if backup_path:
            restore_main_db_from_backup(backup_path)
    finally:
        main_db_update_in_progress.clear()
        release_db_update_lock(admin_id)
        admin_update_state.pop(admin_id, None)
        clear_db_update_state(admin_id)
        for path in (temp_download_path, extracted_db_path, DB_UPDATE_ZIP_PATH):
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
        elif os.path.exists(DB_UPDATE_TMP_DIR):
            shutil.rmtree(DB_UPDATE_TMP_DIR, ignore_errors=True)


def sanity_check_main_db():
    conn = sqlite3.connect(MAIN_DB)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM listings")
        cur.fetchone()
    finally:
        conn.close()


def init_local_db():
    os.makedirs(BASE_DATA_DIR, exist_ok=True)
    conn = get_local_conn()
    cur = conn.cursor()

    # Yeni elanlar
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listings_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_added TEXT,
            created_at TEXT,
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
            created_at TEXT,
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

    ensure_created_at_column(conn, "listings_new", ("date_added",))
    ensure_created_at_column(conn, "listings_approved", ("date_added",))
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_listings_approved_created_at ON listings_approved(created_at)"
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
            first_name TEXT,
            role TEXT,
            date_joined TEXT,
            approved INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            blocked_at TEXT,
            last_error TEXT,
            status TEXT,
            is_active INTEGER DEFAULT 1,
            deleted_at TEXT,
            joined_at TEXT,
            demo_start_at TEXT,
            demo_end_at TEXT,
            paid_until TEXT,
            last_status_change_at TEXT,
            customer_requests_enabled INTEGER DEFAULT 0,
            source_type TEXT,
            source_area TEXT,
            join_source TEXT,
            attribution_created_at TEXT,
            created_at TEXT,
            demo_days INTEGER,
            bonus_allowed INTEGER DEFAULT 0,
            last_spin_at TEXT,
            chance_last_used_at TEXT,
            chance_blocked INTEGER DEFAULT 0
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
        "ALTER TABLE users ADD COLUMN role TEXT",
        "ALTER TABLE users ADD COLUMN blocked_at TEXT",
        "ALTER TABLE users ADD COLUMN status TEXT",
        "ALTER TABLE users ADD COLUMN first_seen TEXT",
        "ALTER TABLE users ADD COLUMN last_seen TEXT",
        "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_version TEXT",
        "ALTER TABLE users ADD COLUMN joined_at TEXT",
        "ALTER TABLE users ADD COLUMN demo_start_at TEXT",
        "ALTER TABLE users ADD COLUMN demo_end_at TEXT",
        "ALTER TABLE users ADD COLUMN paid_until TEXT",
        "ALTER TABLE users ADD COLUMN last_status_change_at TEXT",
        "ALTER TABLE users ADD COLUMN is_first_start INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN customer_requests_enabled INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN first_name TEXT",
        "ALTER TABLE users ADD COLUMN source_type TEXT",
        "ALTER TABLE users ADD COLUMN source_area TEXT",
        "ALTER TABLE users ADD COLUMN join_source TEXT",
        "ALTER TABLE users ADD COLUMN attribution_created_at TEXT",
        "ALTER TABLE users ADD COLUMN created_at TEXT",
        "ALTER TABLE users ADD COLUMN demo_days INTEGER",
        "ALTER TABLE users ADD COLUMN bonus_allowed INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_spin_at TEXT",
        "ALTER TABLE users ADD COLUMN chance_last_used_at TEXT",
        "ALTER TABLE users ADD COLUMN chance_blocked INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_error TEXT",
        "ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE users ADD COLUMN deleted_at TEXT",
    ]:
        try:
            cur.execute(alter_stmt)
        except sqlite3.OperationalError:
            pass

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bonus_probabilities (
            days INTEGER PRIMARY KEY,
            weight INTEGER NOT NULL
        )
        """
    )
    for days, weight in BONUS_DEFAULT_PROBABILITIES.items():
        cur.execute(
            "INSERT OR IGNORE INTO bonus_probabilities (days, weight) VALUES (?, ?)",
            (days, weight),
        )

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

    cur.execute("DROP VIEW IF EXISTS users_with_status")
    user_columns = _table_columns(conn, "users")
    demo_end_expr = (
        "COALESCE(strftime('%s', u.demo_end_at), 0)"
        if "demo_end_at" in user_columns
        else "0"
    )
    promo_expr = (
        "COALESCE(strftime('%s', u.promo_expires_at), 0)"
        if "promo_expires_at" in user_columns
        else "0"
    )
    cur.execute(
        f"""
        CREATE VIEW users_with_status AS
        SELECT
            u.chat_id,
            u.full_name,
            u.username,
            u.approved,
            u.blocked,
            u.is_active,
            u.deleted_at,

            -- effective_expires_at ALWAYS as unix timestamp
            MAX(
                COALESCE(strftime('%s', s.expires_at), 0),
                {demo_end_expr},
                CASE
                    WHEN u.promo_active = 1
                    THEN {promo_expr}
                    ELSE 0
                END
            ) AS effective_expires_at,

            CASE
                WHEN u.blocked = 1 THEN 'BLOCKED'
                WHEN COALESCE(u.is_active, 1) = 0 THEN 'BLOCKED'
                WHEN u.deleted_at IS NOT NULL AND u.deleted_at != '' THEN 'BLOCKED'
                WHEN u.approved = 0 THEN 'PENDING'
                WHEN
                    MAX(
                        COALESCE(strftime('%s', s.expires_at), 0),
                        {demo_end_expr},
                        CASE
                            WHEN u.promo_active = 1
                            THEN {promo_expr}
                            ELSE 0
                        END
                    ) > strftime('%s','now')
                THEN 'ACTIVE'
                ELSE 'EXPIRED'
            END AS computed_status

        FROM users u
        LEFT JOIN subscriptions s
            ON s.chat_id = u.chat_id
            AND s.is_active = 1

        GROUP BY u.chat_id, u.full_name, u.username, u.approved, u.blocked, u.is_active, u.deleted_at
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
        "CREATE INDEX IF NOT EXISTS idx_users_admin_pending ON users(approved, blocked, first_seen DESC)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id_lookup ON users(chat_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_paid_until ON users(paid_until)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_demo_end ON users(demo_end_at, demo_expires_at)"
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
        CREATE TABLE IF NOT EXISTS user_view_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            listing_id INTEGER,
            source TEXT,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_view_logs_user_date ON user_view_logs(chat_id, created_at DESC)"
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
            last_notified_at TEXT,
            is_active INTEGER DEFAULT 1
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            criteria_id INTEGER,
            listing_id INTEGER,
            created_at TEXT,
            status TEXT DEFAULT 'new',
            UNIQUE(chat_id, criteria_id, listing_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            request_type TEXT,
            rayon TEXT,
            rooms TEXT,
            budget TEXT,
            notes TEXT,
            phone TEXT,
            created_at DATETIME DEFAULT (CURRENT_TIMESTAMP),
            status TEXT DEFAULT 'active'
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_requests_access (
            user_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_interests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_chat_id INTEGER,
            request_id INTEGER,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
            UNIQUE(agent_chat_id, request_id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_interests_agent ON agent_interests(agent_chat_id)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_chat_id INTEGER,
            request_id INTEGER,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
            status TEXT DEFAULT 'new',
            UNIQUE(agent_chat_id, request_id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_notifications_agent ON agent_notifications(agent_chat_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_requests_status ON customer_requests(status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_requests_type_status ON customer_requests(request_type, status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_requests_rayon_status ON customer_requests(rayon, status)"
    )
    try:
        cur.execute(
            "ALTER TABLE customer_requests ADD COLUMN flagged INTEGER DEFAULT 0"
        )
    except Exception:
        pass
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_requests_flagged ON customer_requests(flagged)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_request_favorites (
            user_id INTEGER,
            request_id INTEGER,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
            PRIMARY KEY (user_id, request_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_request_archives (
            user_id INTEGER,
            request_id INTEGER,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
            PRIMARY KEY (user_id, request_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_request_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            request_type TEXT,
            rayons TEXT,
            price_min INTEGER,
            price_max INTEGER,
            rooms TEXT,
            keyword TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_request_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            rule_id INTEGER,
            request_id INTEGER,
            created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
            UNIQUE(user_id, rule_id, request_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            keywords TEXT,
            regions TEXT,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_alert_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            user_id INTEGER,
            target_type TEXT,
            target_id INTEGER,
            source TEXT DEFAULT '',
            created_at DATETIME,
            UNIQUE(user_id, alert_id, target_type, target_id, source)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_alert_state (
            key TEXT PRIMARY KEY,
            last_checked_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            listing_id INTEGER,
            keyword TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_request_favorites_user ON customer_request_favorites(user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_request_archives_user ON customer_request_archives(user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_request_rules_user ON customer_request_rules(user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_request_alerts_user ON customer_request_alerts(user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword_alerts_user ON keyword_alerts(user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword_alerts_user_created ON keyword_alerts(user_id, created_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword_alert_hits_user ON keyword_alert_hits(user_id)"
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

    # saved_searches cədvəli üçün sütun yoxlaması
    cur.execute("PRAGMA table_info(saved_searches)")
    saved_cols = {row[1] for row in cur.fetchall()}
    if "last_notified_at" not in saved_cols:
        cur.execute("ALTER TABLE saved_searches ADD COLUMN last_notified_at TEXT")
    if "created_at" not in saved_cols:
        cur.execute("ALTER TABLE saved_searches ADD COLUMN created_at TEXT")
    if "is_active" not in saved_cols:
        cur.execute("ALTER TABLE saved_searches ADD COLUMN is_active INTEGER DEFAULT 1")

    conn.commit()
    conn.close()
    print("✅ local_data.db hazırdır.")


def init_agents_db():
    """Vasitəçi elanları üçün ayrıca baza."""
    os.makedirs(BASE_DATA_DIR, exist_ok=True)
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
        ensure_created_at_column(
            conn,
            "listings",
            ("inserted_at", "date_read", "date_added", "Elanin_tarixi", "added_at"),
        )
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
        cur.execute(f"PRAGMA table_info({base_table})")
        cols = {row[1].lower(): row[1] for row in cur.fetchall()}
        if not cols:
            logger.warning("FTS skip: table not found base_table=%s", base_table)
            return

        def pick_cols_in_order(names: List[str]) -> List[str]:
            picked: List[str] = []
            for name in names:
                col = cols.get(name.lower())
                if col:
                    picked.append(col)
            return picked

        preferred_text_cols = [
            "title",
            "description",
            "summary",
            "text",
            "details",
            "Umumi_melumat",
        ]
        text_cols = pick_cols_in_order(preferred_text_cols)
        if not text_cols:
            logger.warning(
                "FTS skip: no text column found base_table=%s columns=%s",
                base_table,
                sorted(cols.values()),
            )
            return

        address_cols = pick_cols_in_order(["address", "unvan", "adres"])
        source_text_cols = pick_cols_in_order(["source_text"])
        extra_cols = pick_cols_in_order(
            ["metro", "rayon", "contact_name", "operation", "project_name"]
        )

        chosen = list(
            dict.fromkeys(text_cols + address_cols + source_text_cols + extra_cols)
        )

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (fts_name,)
        )
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(f"PRAGMA table_info({fts_name})")
            existing_cols = [row[1] for row in cur.fetchall()]
            if [c.lower() for c in existing_cols] != [c.lower() for c in chosen]:
                logger.info(
                    "FTS schema mismatch, rebuilding fts_name=%s old=%s new=%s",
                    fts_name,
                    existing_cols,
                    chosen,
                )
                cur.execute(f"DROP TABLE IF EXISTS {fts_name}")
                exists = False

        if not exists:
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS "
                f"{fts_name} USING fts5({', '.join(chosen)}, "
                f"content='{base_table}', content_rowid='id')"
            )

        logger.info(
            "FTS build base_table=%s fts_table=%s columns=%s",
            base_table,
            fts_name,
            chosen,
        )
        cur.execute(f"SELECT COUNT(*) FROM {fts_name}")
        existing = cur.fetchone()[0] or 0
        if existing > 0:
            return

        select_sql = f"SELECT id, {', '.join(chosen)} FROM {base_table}"
        cur.execute(f"INSERT INTO {fts_name}(rowid, {', '.join(chosen)}) " + select_sql)
        conn.commit()

    try:
        if os.path.exists(MAIN_DB):
            conn = get_main_conn()
            build_fts(conn, "listings", "listings_fts")
            close_main_conn(conn)
    except Exception:
        logger.exception("FTS (main) yaradarkən xəta")

    try:
        conn = get_local_conn()
        build_fts(conn, "listings_approved", "local_listings_fts")
        conn.close()
    except Exception:
        logger.exception("FTS (local) yaradarkən xəta")


# =============== ÜMUMİ UTIL FUNKSİYALAR ===============


def is_admin(chat_id: int) -> bool:
    try:
        cid = int(chat_id)
    except Exception:
        return False

    try:
        if cid in set(int(x) for x in ADMIN_IDS):
            return True
    except Exception:
        return False

    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("SELECT is_admin FROM users WHERE chat_id=?", (cid,))
        row = cur.fetchone()
        conn.close()
        if row and (row[0] == 1 or row[0] == "1"):
            return True
    except Exception:
        logger.exception("Admin check failed for chat_id=%s", chat_id)
    return False


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


def safe_clear_ui(bot_ref, chat_id: int, message_ids: List[int]):
    for message_id in list(message_ids):
        try:
            bot_ref.delete_message(chat_id, message_id)
        except Exception:
            pass


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


def extract_listing_datetime(row: dict) -> Optional[datetime]:
    if not row:
        return None
    for key in (
        "created_at",
        "published_at",
        "inserted_at",
        "date_added",
        "date_read",
        "Elanin_tarixi",
        "added_at",
    ):
        v = _row_value_safe(row, key)
        if v:
            dt = parse_dt_safe(v)
            if dt:
                return dt
    return None


def safe_date(row: dict):
    dt = extract_listing_datetime(row)
    return dt or datetime.min


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


def _row_value_safe(row, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, sqlite3.Row):
        try:
            return row[key]
        except Exception:
            return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return getattr(row, key)
    except Exception:
        return default


def resolve_user_status(
    user_row,
) -> Literal["active", "demo", "expired", "unverified", "blocked", "rejected"]:
    chat_id = _row_value_safe(user_row, "chat_id")
    status = get_user_computed_status(chat_id) if chat_id else None
    if status == "BLOCKED":
        return "blocked"
    if status == "PENDING":
        return "unverified"
    if status == "ACTIVE":
        return "active"
    if status == "EXPIRED":
        return "expired"
    return "expired"


def admin_datetime_expr(column: str) -> str:
    return (
        "CASE "
        f"WHEN {column} IS NULL THEN NULL "
        f"WHEN typeof({column})='integer' THEN datetime({column}, 'unixepoch') "
        f"WHEN typeof({column})='real' THEN datetime({column}, 'unixepoch') "
        f"WHEN typeof({column})='text' AND {column} GLOB '[0-9]*' "
        f"THEN datetime({column}, 'unixepoch') "
        f"ELSE datetime(replace({column},'T',' ')) END"
    )


def admin_effective_expires_expr() -> str:
    paid_expr = admin_datetime_expr("u.paid_until")
    demo_expr = admin_datetime_expr("u.demo_end_at")
    promo_expr = admin_datetime_expr("u.promo_expires_at")
    return (
        "MAX("
        f"{paid_expr}, "
        f"{demo_expr}, "
        f"CASE WHEN COALESCE(u.promo_active,0)=1 THEN {promo_expr} END"
        ")"
    )


def admin_user_status_subquery() -> str:
    return (
        "(SELECT uw.chat_id, uw.full_name, uw.username, uw.effective_expires_at, "
        "uw.computed_status, u.paid_until, u.demo_end_at, u.demo_expires_at "
        "FROM users_with_status uw "
        "LEFT JOIN users u ON u.chat_id = uw.chat_id)"
    )


def admin_user_status_case_sql() -> str:
    return "computed_status"


def demo_user_clause(prefix_status: str = "uw", prefix_user: str = "u") -> str:
    def col(prefix: str, name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

    status_col = col(prefix_status, "computed_status")
    effective_col = col(prefix_status, "effective_expires_at")
    demo_end_col = (
        f"COALESCE({col(prefix_user, 'demo_end_at')}, {col(prefix_user, 'demo_expires_at')})"
        if prefix_user is not None
        else "COALESCE(demo_end_at, demo_expires_at)"
    )
    paid_until_col = (
        col(prefix_user, "paid_until") if prefix_user is not None else "paid_until"
    )
    effective_int = f"CAST({effective_col} AS INTEGER)"
    paid_blank = f"TRIM(COALESCE({paid_until_col}, ''))"
    return (
        f"({status_col} = 'DEMO' OR "
        f"({effective_int} > strftime('%s','now') AND {demo_end_col} IS NOT NULL "
        f"AND ({paid_until_col} IS NULL OR {paid_blank}=''))"
        ")"
    )


def admin_user_status_where(status: str) -> tuple:
    normalized = (status or "active").lower()
    if normalized == "unverified":
        normalized = "pending"
    status_map = {
        "active": "ACTIVE",
        "expired": "EXPIRED",
        "pending": "PENDING",
        "blocked": "BLOCKED",
        "demo": "DEMO",
    }
    if normalized == "demo":
        clause = (
            "((demo_end_at IS NOT NULL AND "
            "strftime('%s', demo_end_at) > strftime('%s','now')) "
            "OR (demo_expires_at IS NOT NULL AND "
            "strftime('%s', demo_expires_at) > strftime('%s','now')))"
        )
        return clause, ()
    computed = status_map.get(normalized, "ACTIVE")
    return "computed_status = ?", (computed,)


def admin_user_status_count(cur: sqlite3.Cursor, status: str) -> int:
    where_clause, params = admin_user_status_where(status)
    try:
        base_query = admin_user_status_subquery()
        cur.execute(f"SELECT COUNT(*) FROM {base_query} WHERE " + where_clause, params)
        row = cur.fetchone()
        return (row[0] if row else 0) or 0
    except Exception:
        return 0


def get_effective_expires_at(chat_id: int) -> Optional[datetime]:
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT effective_expires_at FROM users_with_status WHERE chat_id=?",
            (chat_id,),
        )
        row = cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not row:
        return None
    raw = row["effective_expires_at"] if isinstance(row, sqlite3.Row) else row[0]
    return parse_effective_expires_at(raw)


def resolve_extension_base(chat_id: int) -> datetime:
    now = datetime.utcnow()
    effective = get_effective_expires_at(chat_id)
    if effective and effective > now:
        return effective
    return now


def get_user_record(chat_id: int) -> Optional[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.chat_id, u.full_name, u.username, u.status, u.joined_at, u.demo_start_at,
               u.demo_end_at, u.paid_until, u.last_status_change_at, u.approved, u.blocked,
               u.is_blocked, u.promo_active, u.promo_expires_at, u.referred_by,
               u.referral_bonus_used, u.referral_milestone_used, u.demo_used,
               u.demo_expires_at, u.blocked_at, u.last_error, u.is_active, u.deleted_at,
               uw.computed_status, uw.effective_expires_at, u.bonus_allowed, u.last_spin_at,
               u.join_source, u.chance_last_used_at, u.chance_blocked
        FROM users u
        LEFT JOIN users_with_status uw ON uw.chat_id = u.chat_id
        WHERE u.chat_id=?
        """,
        (chat_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def ensure_user_exists(chat_id: int, username: str = "", full_name: str = "") -> dict:
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = get_user_record(chat_id)
    if record:
        try:
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET last_seen=?, username=COALESCE(NULLIF(?, ''), username), full_name=COALESCE(NULLIF(?, ''), full_name) WHERE chat_id=?",
                (now_iso, username or "", full_name or "", chat_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("Failed to update last_seen for chat_id=%s", chat_id)
        return record

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (
            chat_id, username, full_name, first_seen, approved, is_admin,
            last_version, referred_by, referral_bonus_used, referral_milestone_used,
            is_first_start, last_seen
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO NOTHING
        """,
        (
            chat_id,
            username or "",
            full_name or "",
            now_iso,
            1 if is_admin(chat_id) else 0,
            1 if is_admin(chat_id) else 0,
            CURRENT_VERSION,
            None,
            0,
            0,
            0,
            now_iso,
        ),
    )
    conn.commit()
    conn.close()
    return get_user_record(chat_id) or {}


def get_user_computed_status(chat_id: int) -> Optional[str]:
    record = get_user_record(chat_id)
    if not record:
        return None
    if record.get("blocked") or record.get("is_blocked"):
        return "BLOCKED"
    if record.get("is_active") == 0:
        return "BLOCKED"
    if record.get("deleted_at"):
        return "BLOCKED"
    if record.get("approved") == 0:
        return "PENDING"

    if is_user_unlimited(chat_id):
        return "ACTIVE"

    now = datetime.utcnow()
    demo_end = parse_dt_safe(record.get("demo_end_at") or record.get("demo_expires_at"))
    if demo_end and demo_end > now:
        return "ACTIVE"

    effective = get_effective_expires_at(chat_id)
    if effective and effective > now:
        return "ACTIVE"

    paid_until = parse_dt_safe(record.get("paid_until"))
    if paid_until and paid_until > now:
        return "ACTIVE"
    return "EXPIRED"


def is_user_active(chat_id: int) -> bool:
    return get_user_computed_status(chat_id) == "ACTIVE"


def parse_effective_expires_at(raw: Optional[str]) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        return datetime.utcfromtimestamp(float(raw))
    except Exception:
        return parse_dt_safe(raw)


def has_customer_requests_access(user_id: int) -> bool:
    if not is_user_active(user_id):
        return False
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT enabled FROM customer_requests_access WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    value = row["enabled"] if isinstance(row, dict) else row[0]
    return value == 1


def get_customer_requests_enabled(chat_id: int) -> bool:
    return has_customer_requests_access(chat_id)


def set_customer_requests_enabled(chat_id: int, enabled: bool):
    conn = get_local_conn()
    cur = conn.cursor()
    if enabled:
        cur.execute(
            """
            INSERT OR REPLACE INTO customer_requests_access (user_id, enabled, updated_at)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            """,
            (chat_id,),
        )
    else:
        cur.execute(
            """
            UPDATE customer_requests_access
            SET enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (chat_id,),
        )
    conn.commit()
    conn.close()


def ensure_customer_requests_enabled(chat_id: int) -> bool:
    if not has_customer_requests_access(chat_id):
        bot.send_message(chat_id, "❌ Bu funksiya sizin üçün aktiv deyil.")
        return False
    return True


def fetch_customer_requests_access_users() -> List[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cra.user_id, cra.enabled, u.full_name
        FROM customer_requests_access cra
        LEFT JOIN users u ON u.chat_id = cra.user_id
        ORDER BY cra.updated_at DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def show_customer_requests_access_admin(
    chat_id: int, message: Optional[types.Message] = None
):
    users = fetch_customer_requests_access_users()
    text_lines = ["📌 Müştəri istəkləri icazəsi", ""]
    if not users:
        text_lines.append("🟡 Aktiv icazə verilmiş istifadəçi yoxdur.")
    else:
        for row in users:
            status_txt = "🟢 Aktiv" if _row_value_safe(row, "enabled") else "🔴 Deaktiv"
            name = _row_value_safe(row, "full_name") or "-"
            text_lines.append(
                f"🆔 {_row_value_safe(row, 'user_id')} | 👤 {name} | {status_txt}"
            )
    text = "\n".join(text_lines)

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ Yeni istifadəçi əlavə et", callback_data="cust_req_access_add"
        )
    )
    for row in users:
        user_id = _row_value_safe(row, "user_id")
        if not user_id:
            continue
        if _row_value_safe(row, "enabled"):
            mk.row(
                types.InlineKeyboardButton(
                    "🔴 Söndür", callback_data=f"cust_req_access_disable:{user_id}"
                ),
                types.InlineKeyboardButton(
                    "👤 Profilə bax", callback_data=f"admin_view_profile:{user_id}"
                ),
            )
        else:
            mk.row(
                types.InlineKeyboardButton(
                    "👤 Profilə bax", callback_data=f"admin_view_profile:{user_id}"
                ),
            )
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def get_first_start_flag(chat_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT is_first_start FROM users WHERE chat_id=?", (chat_id,))
    except sqlite3.OperationalError:
        conn.close()
        return False
    row = cur.fetchone()
    conn.close()
    if not row:
        return True
    value = _row_value_safe(row, "is_first_start", row[0] if len(row) > 0 else None)
    if value is None:
        return False
    return bool(value)


def set_first_start_false_for_user(chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET is_first_start=0 WHERE chat_id=?", (chat_id,))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


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
    approved_val = 1 if status in ACTIVE_STATUSES else 0
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
            1 if status == STATUS_ACTIVE_DEMO else 0,
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
                STATUS_ACTIVE_FREE,
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
            (
                uid,
                full_name,
                username or "",
                joined_at,
                joined_at,
                STATUS_PENDING,
                joined_at,
            ),
        )
    conn.commit()
    conn.close()


# ==============================
# 💳 ABUNƏLİK FUNKSİYALARI
# ==============================

subscription_warn_cache = set()
demo_warn_cache = set()


def ensure_subscription_record(chat_id: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note)
            VALUES (?, NULL, NULL, 0, 0, NULL)
            """,
            (chat_id,),
        )
        conn.commit()


def get_subscription(chat_id: int) -> Optional[dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT chat_id, plan, expires_at, is_active, is_demo, last_payment_note FROM subscriptions WHERE chat_id=?",
            (chat_id,),
        )
        row = cur.fetchone()
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


def ensure_bonus_tables(cur: sqlite3.Cursor):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bonus_probabilities (
            days INTEGER PRIMARY KEY,
            weight INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chance_bonus_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            granted_days INTEGER,
            created_at TEXT
        )
        """
    )
    for days, weight in BONUS_DEFAULT_PROBABILITIES.items():
        cur.execute(
            "INSERT OR IGNORE INTO bonus_probabilities (days, weight) VALUES (?, ?)",
            (days, weight),
        )


def get_bonus_probabilities() -> Dict[int, int]:
    conn = get_local_conn()
    cur = conn.cursor()
    ensure_bonus_tables(cur)
    cur.execute("SELECT days, weight FROM bonus_probabilities ORDER BY days")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return dict(BONUS_DEFAULT_PROBABILITIES)
    weights: Dict[int, int] = {}
    for row in rows:
        try:
            day = int(row[0])
            weight = int(row[1])
        except Exception:
            continue
        weights[day] = weight
    return weights or dict(BONUS_DEFAULT_PROBABILITIES)


def update_bonus_probabilities(new_weights: Dict[int, int]) -> Dict[int, int]:
    normalized: Dict[int, int] = {}
    for day, weight in new_weights.items():
        try:
            normalized[int(day)] = max(0, int(weight))
        except Exception:
            continue
    if not normalized:
        normalized = dict(BONUS_DEFAULT_PROBABILITIES)
    conn = get_local_conn()
    cur = conn.cursor()
    ensure_bonus_tables(cur)
    cur.execute("DELETE FROM bonus_probabilities")
    cur.executemany(
        "INSERT INTO bonus_probabilities (days, weight) VALUES (?, ?)",
        list(normalized.items()),
    )
    conn.commit()
    conn.close()
    return normalized


def fetch_bonus_stats() -> dict:
    now = datetime.utcnow()
    start_of_today = datetime(now.year, now.month, now.day)
    conn = get_local_conn()
    cur = conn.cursor()
    ensure_bonus_tables(cur)

    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(granted_days), 0) FROM chance_bonus_logs"
    )
    total_spins, total_days = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(granted_days), 0)
        FROM chance_bonus_logs
        WHERE created_at >= ?
        """,
        (start_of_today.isoformat(),),
    )
    today_spins, today_days = cur.fetchone()

    cur.execute(
        """
        SELECT user_id, granted_days, created_at
        FROM chance_bonus_logs
        ORDER BY created_at DESC
        LIMIT 5
        """
    )
    recent = cur.fetchall()

    conn.close()

    return {
        "today_spins": today_spins or 0,
        "today_days": today_days or 0,
        "total_spins": total_spins or 0,
        "total_days": total_days or 0,
        "recent": recent,
    }


def set_bonus_allowed(user_id: int, allowed: bool) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET bonus_allowed=? WHERE chat_id=?",
        (1 if allowed else 0, user_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def reset_bonus_spin(user_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_spin_at=NULL WHERE chat_id=?", (user_id,))
    conn.commit()
    conn.close()


def set_last_spin_at(user_id: int, ts: datetime):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_spin_at=? WHERE chat_id=?", (ts.isoformat(), user_id))
    conn.commit()
    conn.close()


def parse_last_spin_at(record: Optional[dict]) -> Optional[datetime]:
    if not record:
        return None
    return parse_dt_safe(record.get("last_spin_at"))


def resolve_user_entitlement(chat_id: int) -> Tuple[Optional[str], Optional[datetime]]:
    record = get_user_record(chat_id)
    if not record:
        return None, None
    entitlements: List[Tuple[str, datetime]] = []

    def _add_ent(kind: str, raw_dt: Optional[str]):
        dt = parse_dt_safe(raw_dt)
        if dt:
            entitlements.append((kind, dt))

    _add_ent("demo", record.get("demo_end_at") or record.get("demo_expires_at"))
    sub = get_subscription(chat_id)
    if sub:
        _add_ent("demo" if sub.get("is_demo") else "paid", sub.get("expires_at"))
    _add_ent("paid", record.get("paid_until"))

    if not entitlements:
        return None, None

    entitlements.sort(key=lambda x: x[1], reverse=True)
    return entitlements[0]


def apply_bonus_days(user_id: int, bonus_days: int, entitlement_type: Optional[str]):
    ensure_subscription_record(user_id)
    if entitlement_type == "demo":
        base = resolve_extension_base(user_id)
        new_exp = base + timedelta(days=bonus_days)
        update_user_demo_end(user_id, new_exp, approve=True)
        set_subscription(
            user_id, "bonus_demo", new_exp, is_active=1, is_demo=1, note="bonus_spin"
        )
        return new_exp
    return extend_subscription_with_bonus(user_id, bonus_days, "bonus_spin")


def pick_bonus_days() -> int:
    probabilities = get_bonus_probabilities()
    options = list(probabilities.keys())
    weights = [max(0, int(probabilities[o])) for o in options]
    if not any(weights):
        options = list(BONUS_DEFAULT_PROBABILITIES.keys())
        weights = list(BONUS_DEFAULT_PROBABILITIES.values())
    return random.choices(options, weights=weights, k=1)[0]


def is_paid_user(record: Optional[dict], chat_id: int) -> bool:
    if is_user_unlimited(chat_id):
        return True
    record = record or get_user_record(chat_id) or {}
    now = datetime.utcnow()
    status = record.get("status")
    if status == STATUS_ACTIVE_PAID:
        return True
    paid_until = parse_dt_safe(record.get("paid_until"))
    if paid_until and paid_until > now:
        return True
    sub = get_subscription(chat_id)
    if sub and sub.get("is_active") and not sub.get("is_demo"):
        exp = parse_dt_safe(sub.get("expires_at"))
        if exp and exp > now:
            return True
    return False


def update_user_chance_usage(chat_id: int, last_used_at: Optional[datetime]):
    conn = get_local_conn()
    cur = conn.cursor()
    _ensure_chance_columns_exists(conn)
    cur.execute(
        """
        UPDATE users
        SET chance_last_used_at=?
        WHERE chat_id=?
        """,
        (last_used_at.isoformat() if last_used_at else None, chat_id),
    )
    conn.commit()
    conn.close()


def log_chance_bonus(user_id: int, granted_days: int, created_at: datetime):
    conn = get_local_conn()
    cur = conn.cursor()
    ensure_bonus_tables(cur)
    cur.execute(
        """
        INSERT INTO chance_bonus_logs (user_id, granted_days, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, granted_days, created_at.isoformat()),
    )
    conn.commit()
    conn.close()


def can_use_chance(record: Optional[dict], now: datetime) -> Tuple[bool, Optional[datetime]]:
    last_used_at = parse_dt_safe(record.get("chance_last_used_at")) if record else None
    if not last_used_at:
        return True, None
    next_available = last_used_at + timedelta(hours=24)
    return now >= next_available, next_available


def ensure_chance_usage_state(
    chat_id: int, record: Optional[dict], now: Optional[datetime] = None
) -> Tuple[int, int, Optional[datetime], int]:
    now = now or datetime.utcnow()
    record = record or get_user_record(chat_id) or {}
    allowed_today = 1
    allowed, _next_available = can_use_chance(record, now)
    last_used_at = parse_dt_safe(record.get("chance_last_used_at"))
    used_today = 0
    if last_used_at:
        used_today = 0 if allowed else 1
    extra_clicks = 0

    return allowed_today, used_today, last_used_at, extra_clicks


def handle_chance_request(user_id: int) -> None:
    chat_id = user_id
    record = get_user_record(user_id) or {}
    now = datetime.utcnow()

    if record.get("chance_blocked"):
        bot.send_message(
            chat_id,
            "⛔ Şans funksiyası sizin üçün deaktiv edilib.\nƏlavə məlumat üçün adminə müraciət edin.",
        )
        return

    last_used_at = parse_dt_safe(record.get("chance_last_used_at")) if record else None
    if last_used_at is not None and now - last_used_at < timedelta(hours=24):
        available_at = last_used_at + timedelta(hours=24)
        display_time = available_at + timedelta(hours=4)
        bot.send_message(
            chat_id,
            (
                "⏳ Bu gün artıq şansınızı istifadə etmisiniz.\n\n"
                "Növbəti şans:\n"
                f"📅 {display_time.strftime('%d.%m.%Y')}\n"
                f"⏰ {display_time.strftime('%H:%M')}"
            ),
        )
        return

    if record and (record.get("blocked") or record.get("is_blocked")):
        send_blocked_prompt(chat_id)
        return

    update_user_chance_usage(chat_id=user_id, last_used_at=now)

    entitlement_type, _ = resolve_user_entitlement(user_id)
    bonus_days = pick_bonus_days()

    if bonus_days > 0:
        apply_bonus_days(user_id, bonus_days, entitlement_type)
        log_chance_bonus(user_id, bonus_days, now)

    bot.send_message(
        chat_id,
        f"🎁 Təbriklər!\nBu gün üçün **{bonus_days} gün** qazandınız 🎉",
        parse_mode="Markdown",
    )



def update_user_demo_end(chat_id: int, demo_end_at: Optional[datetime], approve: bool):
    conn = get_local_conn()
    cur = conn.cursor()
    updates = ["demo_end_at=?", "demo_expires_at=?"]
    params = [
        demo_end_at.isoformat() if demo_end_at else None,
        demo_end_at.isoformat() if demo_end_at else None,
    ]
    if approve:
        updates.append("approved=1")
    params.append(chat_id)
    cur.execute(
        f"UPDATE users SET {', '.join(updates)} WHERE chat_id=?",
        params,
    )
    conn.commit()
    conn.close()


def insert_subscription(
    chat_id: int,
    plan: str,
    expires_at: Optional[datetime],
    is_demo: int = 0,
    note: Optional[str] = None,
):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            plan=excluded.plan,
            expires_at=excluded.expires_at,
            is_active=1,
            is_demo=excluded.is_demo,
            last_payment_note=COALESCE(excluded.last_payment_note, subscriptions.last_payment_note)
        """,
        (
            chat_id,
            plan,
            expires_at.isoformat() if expires_at else None,
            is_demo,
            note,
        ),
    )
    conn.commit()
    conn.close()


def mark_demo_used(chat_id: int, expires_at: datetime):
    update_user_demo_end(chat_id, expires_at, approve=True)


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


def save_referral(
    referrer_chat_id: Optional[int], referred_chat_id: int, is_new_user: bool = False
):
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


def record_referral_log(
    referrer_id: int, referred_user_id: Optional[int], bonus_days: int
):
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


def extend_subscription_with_bonus(
    chat_id: int, bonus_days: int, note: str
) -> datetime:
    sub = get_subscription(chat_id) or {}
    base = resolve_extension_base(chat_id)
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


def process_referral_on_payment(
    referred_chat_id: int, sub_before_payment: Optional[dict], amount_paid: int
):
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
    display_time = dt + timedelta(hours=4)
    return display_time.strftime(fmt)


def set_user_promo_status(
    chat_id: int, active: bool, expires_at: Optional[datetime] = None
):
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


def build_promo_button(
    chat_id: int, include_year: bool = False
) -> types.InlineKeyboardButton:
    status = get_user_promo_status(chat_id)
    if status.get("active"):
        exp_text = format_promo_date(status.get("expires_at"), include_year)
        text = f"🎁 Aktiv promo mövcuddur (bitmə: {exp_text})"
        return types.InlineKeyboardButton(text, callback_data="promo_active_info")
    return types.InlineKeyboardButton(
        "🎁 Promo kod daxil et", callback_data="promo_enter"
    )


def send_promo_quick_action(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(build_promo_button(chat_id))
    bot.send_message(chat_id, "🎁 Promo menyusu:", reply_markup=mk)


def apply_promo_code(chat_id: int, code_raw: str):
    code = (code_raw or "").strip().upper()
    if not code:
        return False, "? Promo kod tap?lmad?", None

    promo = get_promo(code)
    if not promo:
        return False, "? Promo kod tap?lmad?", None
    if not promo.get("is_active"):
        return False, "? Bu promo kod deaktiv edilib", None
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
        return False, " Bu promo kodu artıq istifadə etmisiniz", None

    sub = get_subscription(chat_id) or {}
    record = get_user_record(chat_id) or {}
    now = datetime.utcnow()

    paid_until = parse_dt_safe(record.get("paid_until"))
    demo_end_at = parse_dt_safe(
        record.get("demo_end_at") or record.get("demo_expires_at")
    )

    new_exp = None
    if paid_until and paid_until > now:
        new_paid_until = paid_until + timedelta(days=promo["days"])
        new_exp = new_paid_until
        update_user_status(
            chat_id,
            STATUS_ACTIVE_PAID,
            paid_until=new_paid_until,
            demo_end_at=demo_end_at,
        )
        plan_name = sub.get("plan") or f"promo {promo['days']}g"
        set_subscription(
            chat_id,
            plan_name,
            new_paid_until,
            is_active=1,
            is_demo=0,
            note=f"promo:{code}",
        )
    elif demo_end_at and demo_end_at > now:
        new_demo_end = demo_end_at + timedelta(days=promo["days"])
        new_exp = new_demo_end
        demo_start = parse_dt_safe(record.get("demo_start_at")) or now
        update_user_status(
            chat_id,
            STATUS_ACTIVE_DEMO,
            demo_start_at=demo_start,
            demo_end_at=new_demo_end,
            paid_until=paid_until,
        )
        plan_name = sub.get("plan") or "demo"
        set_subscription(
            chat_id,
            plan_name,
            new_demo_end,
            is_active=1,
            is_demo=1,
            note=f"promo:{code}",
        )
    else:
        new_demo_end = now + timedelta(days=promo["days"])
        new_exp = new_demo_end
        demo_start = parse_dt_safe(record.get("demo_start_at")) or now
        update_user_status(
            chat_id,
            STATUS_ACTIVE_DEMO,
            demo_start_at=demo_start,
            demo_end_at=new_demo_end,
            paid_until=paid_until,
        )
        plan_name = sub.get("plan") or "demo"
        set_subscription(
            chat_id,
            plan_name,
            new_demo_end,
            is_active=1,
            is_demo=1,
            note=f"promo:{code}",
        )

    record_promo_usage(code, chat_id, new_exp)
    set_user_promo_status(chat_id, True, new_exp)

    success_msg = (
        "✅ Promo uğurla aktiv edildi!\n"
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
    status = get_user_computed_status(chat_id)
    if status == "ACTIVE":
        return False

    record = get_user_record(chat_id) or {}
    demo_used = record.get("demo_used", 0)
    demo_end = parse_dt_safe(record.get("demo_end_at") or record.get("demo_expires_at"))
    now = datetime.utcnow()

    if demo_end and demo_end > now:
        return False
    if demo_used:
        return False
    return True


def build_card_payment_button(plan_key: str) -> types.InlineKeyboardButton:
    if CARD_PAYMENT_URL:
        return types.InlineKeyboardButton("💳 Kartla ödəniş", url=CARD_PAYMENT_URL)
    return types.InlineKeyboardButton(
        "💳 Kartla ödəniş (tezliklə)", callback_data=f"cardpay|{plan_key}"
    )


def build_payment_action_markup(
    plan_key: str, plan: dict, payment_code: str, include_card_button: bool = True
) -> types.InlineKeyboardMarkup:
    contact_message = (
        "Salam.\n"
        "Best Home Əmlak Botu üçün 1 günlük paket almaq istəyirəm.\n\n"
        f"Ödəniş kodu: {payment_code}"
    )
    encoded_message = quote(contact_message, safe="")
    whatsapp_url = f"https://wa.me/994708468585?text={encoded_message}"
    telegram_url = f"https://t.me/esedovesed?text={encoded_message}"

    mk = types.InlineKeyboardMarkup(row_width=1)
    if include_card_button:
        mk.add(build_card_payment_button(plan_key))
    mk.add(types.InlineKeyboardButton("📲 WhatsApp-da yaz", url=whatsapp_url))
    mk.add(types.InlineKeyboardButton("✈️ Telegram-da yaz", url=telegram_url))
    mk.add(types.InlineKeyboardButton("✅ Ödəniş etdim", callback_data=f"paydone|{plan_key}"))
    return mk


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
        "💳 Abunəlik planını seç və ödəniş et:\n\n"
        "✅ Demo bitibsə, yeniləmək üçün plan seçin.",
        reply_markup=mk,
    )


def send_blocked_prompt(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("💳 Ödəniş et", callback_data="open_pay_menu"))
    if is_demo_available(chat_id):
        mk.add(
            types.InlineKeyboardButton(
                "🎁 3 gün demo istifadə et", callback_data="demo3"
            )
        )
    bot.send_message(chat_id, BLOCKED_PROMPT_TEXT, reply_markup=mk)


def check_subscription(
    chat_id: int, silent: bool = False, allow_blocked: bool = False
) -> bool:
    status = get_user_computed_status(chat_id)
    if status == "ACTIVE":
        return True
    if status == "BLOCKED":
        if allow_blocked:
            return True
        if not silent:
            logger.info("User blocked access attempt chat_id=%s", chat_id)
            send_blocked_prompt(chat_id)
        return False
    if not silent:
        send_payment_menu(chat_id)
    return False


def is_user_allowed(chat_id: int) -> bool:
    return is_user_active(chat_id)


def ensure_allowed(message, allow_blocked: bool = False) -> bool:
    chat_id = message.chat.id
    return check_subscription(chat_id, allow_blocked=allow_blocked)


def ensure_allowed_cb(c, allow_blocked: bool = False) -> bool:
    chat_id = c.message.chat.id
    return check_subscription(chat_id, allow_blocked=allow_blocked)


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
    clear_user_state(chat_id)


def reset_search_state(chat_id: int):
    state = search_state.get(chat_id)
    if (
        state
        and state.get("mode") == "structured"
        and state.get("awaiting_floor_range")
    ):
        try:
            bot.send_message(chat_id, "↩️ Filter mərhələsi ləğv edildi.")
        except:
            pass
    search_state.pop(chat_id, None)


def compute_total_pages(total_count: int) -> int:
    return max(1, math.ceil(total_count / PAGE_SIZE))


def get_last_24h_window():
    try:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("SELECT datetime('now','-1 day'), datetime('now')")
        row = cur.fetchone()
        conn.close()
        if row and row[0] and row[1]:
            start = datetime.fromisoformat(str(row[0]))
            now = datetime.fromisoformat(str(row[1]))
            logger.info("Last 24h stats computed using database time window")
            return start, now
    except Exception:
        logger.exception("Failed to fetch SQLite time window, falling back to UTC")

    now = datetime.utcnow()
    start = now - timedelta(hours=24)
    logger.info("Last 24h stats computed using rolling window (now-24h)")
    return start, now


def get_today_bounds():
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def format_sqlite_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_table_columns(cur, table: str):
    try:
        cur.execute(f"PRAGMA table_info({table})")
        return {row[1].lower(): row[1] for row in cur.fetchall()}
    except Exception:
        return {}


def detect_user_listings_table(conn) -> Optional[str]:
    cur = conn.cursor()

    table = _select_first_existing_table(cur, ("listings",))
    if table:
        try:
            cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
            if cur.fetchone():
                return table
        except Exception:
            pass

    try:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows = cur.fetchall() or []
    except Exception:
        rows = []

    for row in rows:
        candidate = row[0]
        candidate_l = str(candidate or "").lower()
        if not candidate:
            continue
        if candidate_l.endswith("_approved"):
            logger.error(
                "User stats table candidate rejected (approved) table=%s", candidate
            )
            continue
        if candidate_l.startswith("local_") or candidate_l == "local_listings":
            continue
        cols = get_table_columns(cur, candidate)
        if not cols:
            continue
        required = {"price", "operation", "prop_type"}
        if not required.issubset(set(cols.keys())):
            continue
        try:
            cur.execute(f"SELECT 1 FROM {candidate} LIMIT 1")
            if not cur.fetchone():
                continue
        except Exception:
            continue
        return candidate

    logger.warning(
        "User stats listings table not found in besthome.db (checked %s tables)",
        len(rows),
    )
    return None


def detect_table_date_column(cur, table: str) -> Optional[str]:
    cols = get_table_columns(cur, table)
    for key in ("inserted_at", "created_at", "date_added", "date_read", "added_at"):
        if key in cols:
            return cols[key]
    return None


def _detect_ts_kind(cur, table: str, col: str) -> Optional[str]:
    try:
        cur.execute(
            f"SELECT {col} FROM {table} "
            f"WHERE {col} IS NOT NULL AND {col} != '' ORDER BY ROWID DESC LIMIT 1"
        )
        row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None

    value = row[0]
    if isinstance(value, (int, float)):
        if len(str(int(value))) >= 10:
            return "unix"
        return None
    value_str = str(value or "").strip()
    if value_str.isdigit() and len(value_str) >= 10:
        return "unix"
    if len(value_str) >= 10:
        return "iso"
    return None


def _select_first_existing_table(cur, candidates: Tuple[str, ...]) -> Optional[str]:
    for name in candidates:
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (name,),
            )
            if cur.fetchone():
                return name
        except Exception:
            continue
    return None


def detect_stats_source(cur, stat_context: str) -> Dict[str, Any]:
    table = None
    if stat_context == STAT_CONTEXT_USER:
        table = detect_user_listings_table(cur.connection)
    if not table:
        candidates = ("listings",) if stat_context == STAT_CONTEXT_USER else ("listings_approved", "listings")
        table = _select_first_existing_table(cur, candidates)
        if not table:
            try:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND lower(name) LIKE '%listing%' ORDER BY name"
                )
                rows = cur.fetchall() or []
                for row in rows:
                    candidate = row[0]
                    if stat_context == STAT_CONTEXT_USER and str(candidate or "").lower().endswith("_approved"):
                        continue
                    cols = get_table_columns(cur, candidate)
                    if cols and (
                        any(c in cols for c in STATS_TS_CANDIDATES)
                        or any(c in cols for c in STATS_OPERATION_COLUMNS)
                        or any(c in cols for c in STATS_PROPERTY_COLUMNS)
                    ):
                        table = candidate
                        break
                if not table and rows:
                    table = rows[0][0]
            except Exception:
                table = None

    cols = get_table_columns(cur, table) if table else {}

    ts_col = None
    ts_kind = None
    for candidate in STATS_TS_CANDIDATES:
        if candidate in cols:
            ts_col = cols[candidate]
            ts_kind = _detect_ts_kind(cur, table, ts_col)
            if ts_kind:
                break
    if ts_col and not ts_kind:
        ts_kind = "iso"
    op_col = None
    for candidate in STATS_OPERATION_COLUMNS:
        if candidate in cols:
            op_col = cols[candidate]
            break
    type_col = None
    for candidate in STATS_PROPERTY_COLUMNS:
        if candidate in cols:
            type_col = cols[candidate]
            break

    meta = {
        "table": table,
        "ts_col": ts_col,
        "ts_kind": ts_kind or "none",
        "op_col": op_col,
        "type_col": type_col,
    }
    log_prefix = "USER_STATS" if stat_context == STAT_CONTEXT_USER else "STATS"
    logger.info(
        "%s source_table=%s ts_col=%s ts_kind=%s op_col=%s type_col=%s",
        log_prefix,
        table,
        ts_col,
        ts_kind or "none",
        op_col,
        type_col,
    )
    return meta


def ensure_created_at_column(
    conn, table: str, fallback_cols: Optional[Tuple[str, ...]] = None
):
    cur = conn.cursor()
    cols = get_table_columns(cur, table)
    if "created_at" in cols or "inserted_at" in cols:
        target_col = cols.get("created_at")
    else:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN created_at TEXT")
        cols = get_table_columns(cur, table)
        target_col = cols.get("created_at")

    if not target_col:
        return

    candidates = fallback_cols or ()
    for candidate in candidates:
        if candidate in cols and candidate != target_col:
            cur.execute(
                f"UPDATE {table} SET {target_col} = {cols[candidate]} "
                f"WHERE {target_col} IS NULL OR {target_col} = ''"
            )
            break
    else:
        cur.execute(
            f"UPDATE {table} SET {target_col} = ? "
            f"WHERE {target_col} IS NULL OR {target_col} = ''",
            (format_sqlite_datetime(datetime.now()),),
        )


def build_last_24h_clause(
    column: Optional[str],
    window: Optional[Tuple[datetime, datetime]] = None,
):
    if not column:
        return "", []
    clause = (
        " AND ((typeof({col})='integer' "
        "AND {col} >= strftime('%s','now','-1 day') AND {col} < strftime('%s','now')) "
        "OR (datetime({col}) >= datetime('now','-1 day') AND datetime({col}) < datetime('now')))"
    ).format(col=column)
    return clause, []


def build_today_clause(
    column: Optional[str],
    window: Optional[Tuple[datetime, datetime]] = None,
):
    return build_last_24h_clause(column, window)


def build_date_range_clause(
    column: Optional[str], date_days: Optional[Union[int, str]]
):
    """Return SQL snippet for date filtering using SQLite's datetime semantics."""

    if not column or date_days in (None, "all"):
        return "", []

    now = datetime.utcnow()
    if date_days == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        try:
            days_int = int(date_days)
        except Exception:
            return "", []
        start = now - timedelta(days=days_int)
        end = now

    return (
        f" AND datetime({column}) BETWEEN datetime(?) AND datetime(?)",
        [format_sqlite_datetime(start), format_sqlite_datetime(end)],
    )


def attach_local_db(conn) -> bool:
    try:
        conn.execute("ATTACH DATABASE ? AS local_db", (LOCAL_DB,))
        return True
    except Exception:
        return False


def detach_local_db(conn, attached: bool):
    if not attached:
        return
    try:
        conn.execute("DETACH DATABASE local_db")
    except Exception:
        pass


def build_rayon_filter_sql(cur, table: str, rayon: Optional[str], prefix: str):
    if not rayon or rayon == "all":
        return "", []
    cols = get_table_columns(cur, table)
    targets = []
    if "rayon" in cols:
        targets.append(f"{prefix}{cols['rayon']}")
    if "address" in cols:
        targets.append(f"{prefix}{cols['address']}")
    if "summary" in cols:
        targets.append(f"{prefix}{cols['summary']}")
    if not targets:
        return "", []
    conds = [f"LOWER(COALESCE({col},'')) LIKE ?" for col in targets]
    params = [f"%{rayon.lower()}%"] * len(targets)
    return " AND (" + " OR ".join(conds) + ")", params


def count_main_active_listings(
    op_code: str = "all",
    prop_code: str = "all",
    rayon: Optional[str] = None,
    only_today: bool = False,
    use_direct_conn: bool = False,
) -> int:
    if not os.path.exists(MAIN_DB):
        return 0
    if use_direct_conn or main_db_update_in_progress.is_set():
        conn = sqlite3.connect(MAIN_DB)
        conn.row_factory = sqlite3.Row
        use_direct_conn = True
    else:
        conn = get_main_conn()
    attached = False
    try:
        cur = conn.cursor()
        attached = attach_local_db(conn)
        flt, params = build_filters_sql(op_code, prop_code, None, mode="main")
        date_sql, date_params = ("", [])
        if only_today:
            date_col = detect_table_date_column(cur, "listings")
            if date_col:
                window = get_last_24h_window()
                date_sql, date_params = build_today_clause(f"l.{date_col}", window)
        rayon_sql, rayon_params = build_rayon_filter_sql(cur, "listings", rayon, "l.")
        sql = "SELECT COUNT(*) FROM listings l " + flt + date_sql + rayon_sql
        cur.execute(sql, params + date_params + rayon_params)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        detach_local_db(conn, attached)
        if use_direct_conn:
            conn.close()
        else:
            close_main_conn(conn)


def count_local_active_listings(
    op_code: str = "all",
    prop_code: str = "all",
    rayon: Optional[str] = None,
    only_today: bool = False,
) -> int:
    conn = get_local_conn()
    try:
        cur = conn.cursor()
        flt, params = build_filters_sql(op_code, prop_code, None, mode="local")
        date_sql, date_params = ("", [])
        if only_today:
            date_col = detect_table_date_column(cur, "listings_approved")
            if date_col:
                window = get_last_24h_window()
                date_sql, date_params = build_today_clause(f"l.{date_col}", window)
        rayon_sql, rayon_params = build_rayon_filter_sql(
            cur, "listings_approved", rayon, "l."
        )
        sql = "SELECT COUNT(*) FROM listings_approved l " + flt + date_sql + rayon_sql
        cur.execute(sql, params + date_params + rayon_params)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def count_today_listings(filters: dict, op_override: Optional[str] = None) -> int:
    filters = dict(filters or {})
    if op_override is not None:
        filters["op"] = op_override
    _, total = query_today_results(filters, offset=0, limit=None)
    return total


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


def set_pagination_state(
    chat_id: int, mode: str, params: dict, page: int, total_pages: int
):
    search_state[chat_id] = {
        "mode": mode,
        "params": params or {},
        "page": page,
        "total_pages": total_pages,
    }


def cleanup_listing_sessions():
    now = time.time()
    for chat_id, sess in list(listing_sessions.items()):
        if now - sess.get("timestamp", 0) > LISTING_SESSION_TTL_SECONDS:
            listing_sessions.pop(chat_id, None)


def get_active_listing_session(chat_id: int):
    cleanup_listing_sessions()
    session = listing_sessions.get(chat_id)
    if not session:
        return None
    if time.time() - session.get("timestamp", 0) > LISTING_SESSION_TTL_SECONDS:
        listing_sessions.pop(chat_id, None)
        return None
    return session


def make_listing_ref(source: str, listing_id: int) -> str:
    return f"{source}:{listing_id}"


def normalize_listing_item(item: dict):
    ev = item.get("data") if isinstance(item, dict) and "data" in item else item
    if not isinstance(ev, dict):
        return None
    source = (
        item.get("source")
        if isinstance(item, dict)
        else ev.get("__source")
        or "main"
    )
    try:
        listing_id = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
        listing_id = int(str(listing_id))
    except Exception:
        return None
    return {
        "source": source,
        "id": listing_id,
        "data": ev,
    }


def build_listing_action_keyboard(
    favorite_label: Optional[str],
    favorite_callback: Optional[str],
    listing_link: Optional[str],
    whatsapp_url: Optional[str],
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    row1 = []
    if whatsapp_url:
        row1.append(types.InlineKeyboardButton("💬 WhatsApp-da yaz", url=whatsapp_url))
    if row1:
        mk.row(*row1)

    row2 = []
    if favorite_label and favorite_callback:
        row2.append(
            types.InlineKeyboardButton(favorite_label, callback_data=favorite_callback)
        )
    if listing_link:
        row2.append(types.InlineKeyboardButton("🌐 Elana bax", url=listing_link))
    if row2:
        mk.row(*row2)

    return mk


def build_listing_navigation_keyboard(
    is_favorite: bool,
    listing_link: Optional[str] = None,
    whatsapp_url: Optional[str] = None,
) -> types.InlineKeyboardMarkup:
    fav_label = "❤️ Favori" if is_favorite else "🤍 Favori"
    mk = build_listing_action_keyboard(
        fav_label, "fav:toggle", listing_link, whatsapp_url
    )
    mk.row(
        types.InlineKeyboardButton("⬅️ Əvvəlki", callback_data="nav:prev"),
        types.InlineKeyboardButton("➡️ Növbəti", callback_data="nav:next"),
    )
    mk.row(
        types.InlineKeyboardButton("⏭ +5", callback_data="nav:+5"),
        types.InlineKeyboardButton("⏮ -5", callback_data="nav:-5"),
    )
    mk.add(types.InlineKeyboardButton("🏠 Əsas menyu", callback_data="nav:home"))
    return mk


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

    price_min = filters.get("min_price")
    price_max = filters.get("max_price")
    if price_min is None and price_max is None:
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
        prop_type = resolve_property_type_from_code(prop_code)

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


def ensure_notification_records(
    chat_id: int, criteria_id: Optional[int], listing_ids: List[int]
) -> int:
    if not listing_ids:
        return 0

    now_iso = datetime.utcnow().isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    inserted = 0
    for lid in listing_ids:
        try:
            lid_int = int(lid)
        except Exception:
            continue
        cur.execute(
            """
            INSERT OR IGNORE INTO user_notifications
            (chat_id, criteria_id, listing_id, created_at, status)
            VALUES (?, ?, ?, ?, 'new')
            """,
            (chat_id, criteria_id, lid_int, now_iso),
        )
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
    conn.commit()
    conn.close()
    return inserted


def fetch_listing_for_notification(listing_id: int):
    try:
        lid = int(listing_id)
    except Exception:
        return None

    if os.path.exists(MAIN_DB):
        conn_main = get_main_conn()
        cur_main = conn_main.cursor()
        cur_main.execute("SELECT * FROM listings WHERE id=?", (lid,))
        row_main = cur_main.fetchone()
        if row_main:
            d_main = dict(row_main)
            d_main["__source"] = "main"
            close_main_conn(conn_main)
            return d_main
        close_main_conn(conn_main)

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_approved WHERE id=?", (lid,))
    row = cur.fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["__source"] = "local"
        return d
    return None


def get_saved_searches(chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM saved_searches WHERE chat_id=?", (chat_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


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
            bot.edit_message_text(
                text, chat_id=edit_target[0], message_id=edit_target[1]
            )
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


def log_search_event(
    chat_id: int, search_type: str, operation=None, rayon=None, query_text=None
):
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
            (
                chat_id,
                datetime.utcnow().isoformat(),
            ),
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
        if row:
            conn.close()
            return True
        try:
            cur.execute("SELECT role FROM users WHERE chat_id=?", (chat_id,))
            r_user = cur.fetchone()
            if r_user and str(r_user[0] or "").lower() == "agent":
                conn.close()
                return True
        except Exception:
            pass
        conn.close()
    except Exception:
        return False

    try:
        conn_a = get_agents_conn()
        cur_a = conn_a.cursor()
        cur_a.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
        )
        has_agents_table = cur_a.fetchone()
        if has_agents_table:
            cur_a.execute("SELECT 1 FROM agents WHERE chat_id=?", (chat_id,))
            if cur_a.fetchone():
                conn_a.close()
                return True
        conn_a.close()
    except Exception:
        pass
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


def add_favorite_entry(chat_id: int, source: str, listing_id: int) -> bool:
    source = source or "main"
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO favorites (chat_id, listing_id, source, added_at)
        VALUES (?, ?, ?, ?)
        """,
        (chat_id, listing_id, source, datetime.utcnow().isoformat()),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    if inserted:
        record_favorite_price(source, listing_id)
        record_listing_stat(listing_id, "favorite", chat_id)
        record_agent_activity(chat_id, metric="favorites")
    return inserted


def remove_favorite_entry(chat_id: int, source: str, listing_id: int) -> bool:
    source = source or "main"
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM favorites WHERE chat_id=? AND listing_id=? AND source=?",
        (chat_id, listing_id, source),
    )
    removed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return removed


def is_favorite_entry(chat_id: int, source: str, listing_id: int) -> bool:
    source = source or "main"
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM favorites WHERE chat_id=? AND listing_id=? AND source=?",
        (chat_id, listing_id, source),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def should_track_interaction(
    chat_id: Optional[int], listing_id: int, action: str
) -> bool:
    if chat_id is None:
        return True
    cache = session_interactions.setdefault(chat_id, set())
    key = (action, listing_id)
    if key in cache:
        return False
    cache.add(key)
    return True


def record_listing_stat(
    listing_id: Optional[int], action: str, chat_id: Optional[int] = None
):
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


def record_listing_view(
    source: str, listing_id: Optional[int], chat_id: Optional[int] = None
):
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
        if chat_id:
            try:
                cur.execute(
                    """
                    INSERT INTO user_view_logs (chat_id, listing_id, source, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chat_id, listing_id, source, datetime.utcnow().isoformat()),
                )
            except Exception:
                logger.debug("User view log skipped chat_id=%s listing_id=%s", chat_id, listing_id)
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
    cur.execute("SELECT COUNT(*) FROM listing_stats WHERE popularity_score > 0")
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

    enriched = []
    for r in rows:
        ev = fetch_listing_by_any(r["listing_id"])
        if not ev:
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


MENU_REFRESH_BUTTON = "🔄 Botu yenilə"
MENU_VISIBILITY_HINT_TEXT = "ℹ️ Əsas menyu görünmür?\n" "➡️ /start yazın."
MENU_VISIBILITY_HINT_COOLDOWN_SECONDS = 300
menu_visibility_hint_last_sent = {}
STATISTICS_CACHE_TTL_SECONDS = 60
statistics_cache: Dict[str, Dict[str, Any]] = {}
MARKET_PULSE_CACHE_TTL_SECONDS = 300
MARKET_PULSE_SPEED_THRESHOLDS = (10, 25)
market_pulse_cache: Dict[str, Dict[str, Any]] = {}
STAT_CONTEXT_USER = "user"
STAT_CONTEXT_ADMIN = "admin"


def send_refresh_button(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton(MENU_REFRESH_BUTTON, callback_data="bot_refresh"))
    bot.send_message(chat_id, MENU_REFRESH_BUTTON, reply_markup=mk)


def send_menu_visibility_hint(chat_id: int):
    now = time.time()
    last_ts = menu_visibility_hint_last_sent.get(chat_id, 0)
    if now - last_ts < MENU_VISIBILITY_HINT_COOLDOWN_SECONDS:
        return
    menu_visibility_hint_last_sent[chat_id] = now
    bot.send_message(chat_id, MENU_VISIBILITY_HINT_TEXT)


def recover_main_menu(
    chat_id: Optional[int],
    message: Optional[types.Message] = None,
    text: Optional[str] = None,
):
    if not chat_id:
        return
    if message:
        try:
            bot.edit_message_reply_markup(
                chat_id, message.message_id, reply_markup=None
            )
        except Exception:
            logger.debug("Menu recovery edit failed chat_id=%s", chat_id)
    try:
        send_main_menu(chat_id, text, force=True)
    except Exception:
        logger.exception("Failed to send main menu chat_id=%s", chat_id)


def send_with_reply_keyboard(
    chat_id: int,
    text: str,
    keyboard: types.ReplyKeyboardMarkup,
    *,
    parse_mode: Optional[str] = None,
    disable_preview: Optional[bool] = None,
):
    bot.send_message(
        chat_id,
        text,
        reply_markup=keyboard,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_preview,
    )
    send_menu_visibility_hint(chat_id)


def build_main_menu(
    is_admin_user: bool,
    has_customer_access: bool = False,
    show_bonus_button: bool = False,
) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True, one_time_keyboard=False, is_persistent=True, row_width=2
    )

    buttons: List[Union[str, types.KeyboardButton]] = []

    if WEB_APP_URL:
        buttons.append(
            types.KeyboardButton(
                "🌐 Web Panel", web_app=types.WebAppInfo(url=WEB_APP_URL)
            )
        )

    buttons.extend(
        [
            "🔎 Axtarış sistemi",
            "🕒 Son 24 saat",
            "👤 Hesabım",
            "📊 Statistika",
        ]
    )

    if show_bonus_button:
        buttons.append("🎁 Şansını sına")

    buttons.append("💳 Ödəniş")

    if is_admin_user:
        buttons.append("ℹ️ Haqqında")
    else:
        buttons.extend(["🤝 Dostunu dəvət et", "ℹ️ Haqqında"])

    buttons.append("📩 Şikayət və təkliflər")

    if is_admin_user:
        buttons.append(TEXTS_AZ["admin_panel_button"])

    buttons.append(MENU_REFRESH_BUTTON)

    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i : i + 2])

    return kb


def should_show_bonus_button(chat_id: int) -> bool:
    record = get_user_record(chat_id)
    if record and (record.get("blocked") or record.get("is_blocked")):
        return False
    return True


def send_main_menu(
    chat_id: int,
    text: Optional[str] = None,
    *,
    parse_mode: Optional[str] = None,
    disable_preview: Optional[bool] = None,
    force: bool = False,
):
    current_ctx = get_ui_context(chat_id)
    if not force and current_ctx != UI_CONTEXT_MAIN:
        logger.debug(
            "Skipping main menu for chat_id=%s context=%s", chat_id, current_ctx
        )
        return
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    kb = build_main_menu(
        is_admin(chat_id),
        has_customer_requests_access(chat_id),
        should_show_bonus_button(chat_id),
    )
    send_with_reply_keyboard(
        chat_id,
        text or "🏠 Əsas menyu:",
        kb,
        parse_mode=parse_mode,
        disable_preview=disable_preview,
    )


def build_search_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Satılır", "🏢 Kirayə verilir")
    kb.row("🔍 Açar sözlə axtar", "📞 Nömrə ilə axtar")
    kb.row("⭐ Favorilərim", "🔔 Bildirişlər")
    kb.row("⬅️ Geri")
    return kb


def send_search_menu(chat_id: int):
    kb = build_search_menu_keyboard()
    send_with_reply_keyboard(chat_id, "\u2063", kb)


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
    rooms_txt = f"{rooms_val} otaqlı" if rooms_val else ""

    location_raw = ev.get("rayon") or ev.get("Rayon_Qesebe") or ""
    if not location_raw:
        location_raw = ev.get("address") or ev.get("Unvan") or ""

    message_lines = ["Salam."]

    address = location_raw.strip()
    base = ""
    if address:
        base += f"{address} yerləşən "
    if rooms_txt:
        base += f"{rooms_txt} mənziliniz "
    else:
        base += "mənziliniz "

    if is_rent:
        message_lines.append(f"{base}kirayə verilir?")
    else:
        message_lines.append(f"{base}satışdadır?")

    link = ev.get("link") or ev.get("source_link")
    if link:
        message_lines.extend(["", "Elan linki:", link])
    return "\n".join(message_lines)


def _strip_contact_details(text: str, ev: dict) -> str:
    phone_raw = ev.get("phone") or ev.get("Elaqe_nomresi")
    owner_name = (
        ev.get("owner_name")
        or ev.get("owner")
        or ev.get("elan_sahibi")
        or ev.get("sahib")
    )

    cleaned = text or ""

    sensitive_values = []
    if phone_raw:
        sensitive_values.append(str(phone_raw))
        digits = "".join(ch for ch in str(phone_raw) if ch.isdigit())
        if len(digits) >= 7:
            digit_pattern = "\\s*".join(list(digits))
            try:
                cleaned = re.sub(digit_pattern, "", cleaned)
            except re.error:
                pass
    if owner_name:
        sensitive_values.append(str(owner_name))

    for val in sensitive_values:
        if val:
            cleaned = cleaned.replace(str(val), "")

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def build_listing_text(ev: dict, source: str, progress_text: Optional[str] = None) -> str:
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
    raw_summary = ev.get("summary") or ev.get("Umumi_melumat") or ""
    summary = _strip_contact_details(raw_summary, ev) or "-"
    listing_id = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
    listing_code = f"🆔 Elan kodu: #{listing_id}" if listing_id else "🆔 Elan kodu: -"

    location = addr or rayon
    if metro:
        if location:
            location += f" — {metro}"
        else:
            location = metro

    badges = []
    try:
        if datetime.utcnow() - safe_date(ev) <= timedelta(
            hours=NEW_LISTING_WINDOW_HOURS
        ):
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
        f"🧾 {summary}"
    )

    link = ev.get("link") or ev.get("source_link")
    if link:
        text += f"\n🔗 {link}"

    matched_kw = ev.get("__matched_keywords") or []
    if matched_kw:
        uniq_kw = sorted(dict.fromkeys(matched_kw))
        text += "\n🔑 Uyğun açar sözlər: " + ", ".join(uniq_kw)

    if ev.get("__views") is not None:
        text += f"\n👁️ Baxış: {ev['__views']}"

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

    if progress_text:
        text += f"\n\n{progress_text}"
    text += f"\n\n{listing_code}"
    if not text.strip():
        return "ℹ️ Elan məlumatı mövcud deyil"
    return text


def send_listing_card(
    chat_id: int,
    ev: dict,
    source: str = "main",
    with_fav_button: bool = True,
    extra_buttons=None,
    track_view: bool = False,
    viewer_id: Optional[int] = None,
):
    if viewer_id:
        record_agent_activity(viewer_id, metric="views")

    if track_view:
        listing_id = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
        try:
            listing_pk = int(str(listing_id)) if listing_id is not None else None
        except (TypeError, ValueError):
            listing_pk = None
        if listing_pk:
            record_listing_view(source, listing_pk, viewer_id)

    phone = ev.get("phone") or ev.get("Elaqe_nomresi")
    wa_message = build_whatsapp_message(ev)
    wa_url = make_whatsapp_url(phone, wa_message)
    link = ev.get("link") or ev.get("source_link")

    favorite_label = "⭐ Favoriyə əlavə et" if with_fav_button else None
    favorite_callback = (
        f"fav|{source}|{ev['id']}" if with_fav_button and ev.get("id") else None
    )
    mk = build_listing_action_keyboard(
        favorite_label, favorite_callback, link, wa_url
    )

    if extra_buttons:
        for btn in extra_buttons:
            mk.add(btn)

    text = build_listing_text(ev, source)
    bot.send_message(chat_id, text, reply_markup=mk)


def register_or_update_user_if_needed(message, start_arg: str):
    chat_id = message.chat.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    full_name = message.from_user.full_name or ""
    start_arg = (start_arg or "").strip().lower()
    join_source_value: Optional[str] = (
        start_arg if start_arg in ALLOWED_START_AREAS else None
    )
    if start_arg in ALLOWED_START_AREAS:
        source_type = "qr"
        source_area: Optional[str] = start_arg
        demo_days = 7
    else:
        source_type = "direct"
        source_area = None
        demo_days = 3
    first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    search_reminder_shown.discard(chat_id)
    reset_user_state(chat_id)
    search_state.pop(chat_id, None)
    referrer_chat_id = parse_referrer_from_text(message.text or "")
    referred_by_value = (
        referrer_chat_id if referrer_chat_id and referrer_chat_id != chat_id else None
    )

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT chat_id, approved, blocked, is_admin, last_version, is_first_start, source_type,
               source_area, demo_days, demo_end_at, demo_expires_at, demo_used, paid_until
        FROM users
        WHERE chat_id=?
        """,
        (chat_id,),
    )
    row = cur.fetchone()
    is_first_time = False
    is_first_start = False
    attribution_created_at = datetime.now(timezone.utc).isoformat()
    created_at = attribution_created_at
    demo_expiry: Optional[datetime] = None
    demo_info_text: Optional[str] = None

    # 🧩 Əgər user bazada yoxdursa, əlavə et
    if not row:
        is_first_time = True
        is_first_start = True
        demo_expiry = datetime.utcnow() + timedelta(days=demo_days)
        try:
            cur.execute(
                """
                INSERT INTO users (chat_id, username, full_name, first_seen, approved, is_admin, last_version, referred_by, referral_bonus_used, referral_milestone_used, is_first_start, first_name, source_type, source_area, join_source, attribution_created_at, created_at, demo_days, demo_end_at, demo_expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    1,
                    first_name,
                    source_type,
                    source_area,
                    join_source_value,
                    attribution_created_at,
                    created_at,
                    demo_days,
                    demo_expiry.isoformat() if demo_expiry else None,
                    demo_expiry.isoformat() if demo_expiry else None,
                ),
            )
            conn.commit()
            logger.info(
                "📍 User attribution saved: area=%s, type=%s, user_id=%s",
                source_area,
                source_type,
                chat_id,
            )
        except Exception as e:
            conn.rollback()
            logger.exception("Failed to insert new user chat_id=%s", chat_id)
            bot.send_message(chat_id, "⚠️ Texniki problem oldu, amma bot aktivdir.")
        else:
            try:
                username_display = username if username else "-"
                username_line = (
                    f"@{username_display}" if username_display != "-" else "-"
                )
                joined_at = (datetime.utcnow() + timedelta(hours=4)).strftime(
                    "%d.%m.%Y %H:%M"
                )
                admin_text = (
                    "🆕 Yeni istifadəçi qoşuldu\n\n"
                    f"👤 ID: {chat_id}\n"
                    f"👤 Username: {username_line}\n"
                    f"👤 Ad: {first_name}\n"
                    f"⏰ Tarix: {joined_at}"
                )
                bot.send_message(ADMIN_ID, admin_text)
            except Exception as e:
                logger.warning(
                    "Failed to notify admin about new user %s: %s", chat_id, e
                )

        if referred_by_value:
            try:
                save_referral(referred_by_value, chat_id, is_new_user=True)
            except Exception:
                logger.exception("Failed to save referral for chat_id=%s", chat_id)
                bot.send_message(chat_id, "⚠️ Texniki problem oldu, amma bot aktivdir.")
        try:
            cur.execute(
                "UPDATE users SET approved=0, blocked=0 WHERE chat_id=?", (chat_id,)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.exception("Failed to reset approval flags chat_id=%s", chat_id)
            bot.send_message(chat_id, "⚠️ Texniki problem oldu, amma bot aktivdir.")
    else:
        is_first_start = (
            bool(row["is_first_start"]) if row["is_first_start"] is not None else False
        )
        demo_end_raw = None
        if row:
            if "demo_end_at" in row and row["demo_end_at"]:
                demo_end_raw = row["demo_end_at"]
            elif "demo_expires_at" in row and row["demo_expires_at"]:
                demo_end_raw = row["demo_expires_at"]
        demo_expiry = parse_dt_safe(demo_end_raw)
        if demo_expiry and demo_expiry > datetime.utcnow():
            remaining = demo_expiry - datetime.utcnow()
            remaining_days = remaining.days
            remaining_hours = (remaining.seconds // 3600)
            display_time = demo_expiry + timedelta(hours=4)
            demo_info_text = (
                "🎁 Demo aktivdir. \n"
                f"Bitmə tarixi: {display_time.strftime('%d.%m.%Y %H:%M')} (qalıb {remaining_days} gün {remaining_hours} saat)"
            )
        elif "demo_used" in row and row["demo_used"]:
            demo_info_text = "🎁 Demo müddətiniz bitib. Ödəniş menyusundan yeniləyə bilərsiniz."

    if is_first_time:
        send_payment_menu(chat_id)

    # 🧩 Admin üçün avtomatik təsdiq
    if is_admin(chat_id):
        try:
            cur.execute(
                "UPDATE users SET approved=1, is_admin=1 WHERE chat_id=?", (chat_id,)
            )
            conn.commit()
            set_user_state(chat_id, "ADMIN")
        except Exception:
            conn.rollback()
            logger.exception("Failed to auto-approve admin chat_id=%s", chat_id)
            bot.send_message(chat_id, "⚠️ Texniki problem oldu, amma bot aktivdir.")

    conn.close()

    if is_first_start and not is_admin(chat_id):
        bot.send_message(
            chat_id,
            "👋 Xoş gəldiniz!\n\n"
            "Bu bot vasitəsilə satılan və kirayə verilən evləri filtr, açar söz və əlaqə nömrəsi ilə rahat axtara bilərsiniz.\n"
            "⏳ Davam etmək üçün admin təsdiqini gözləyin.\n"
            "Təsdiq tamamlanan kimi bot avtomatik aktiv olacaq.",
            disable_web_page_preview=True,
        )
        set_first_start_false_for_user(chat_id)

    set_user_state(chat_id, "MAIN")
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    if is_first_time:
        if source_type == "qr":
            demo_info_text = (
                "🎉 QR vasitəsilə qoşuldunuz.\n\n"
                "Sizə avtomatik olaraq 7 gün PULSUZ demo aktiv edildi.\n\n"
                "⏳ Hazırda hesabınız qısa yoxlama mərhələsindədir.\n"
                "Bu, botun təhlükəsiz və stabil işləməsi üçündür.\n\n"
                "✅ Admin təsdiqi tamamlanan kimi bütün funksiyalar avtomatik açılacaq.\n"
                "📌 Bu proses adətən çox çəkmir.\n\n"
                "Gözlədiyiniz üçün təşəkkür edirik 🙏"
            )
        else:
            demo_info_text = (
                "Sizə 3 gün PULSUZ demo ayrıldı.\n"
                "🔒 Demo admin təsdiqindən sonra aktiv olacaq.\n\n"
                "📌 Təsdiqdən sonra botu tam şəkildə istifadə edə biləcəksiniz."
            )
    if demo_info_text:
        bot.send_message(chat_id, demo_info_text)

    logger.info("/start executed successfully for user %s", chat_id)


@bot.message_handler(func=lambda m: m.text == "🤝 Dostunu dəvət et")
def share_referral(message):
    if message.text and message.text.startswith('/'):
        return

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


# =============== 📝 MÜŞTƏRİ SORĞULARI ===============


REQUEST_RAYONS = [
    "Binəqədi",
    "Qaradağ",
    "Sabunçu",
    "Səbail",
    "Suraxanı",
    "Xəzər",
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


def build_request_rayon_keyboard(
    include_back: bool = True,
) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    row = []
    for rayon in REQUEST_RAYONS:
        row.append(rayon)
        if len(row) == 3:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    if include_back:
        kb.row("⬅️ Geri (Əsas menyu)")
    return kb


def build_request_rooms_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("1", "2")
    kb.row("3", "4+")
    kb.row("⬅️ Geri (Əsas menyu)")
    return kb


def reset_customer_request(chat_id: int):
    customer_request_state.pop(chat_id, None)


def get_customer_request_step(chat_id: int) -> Optional[str]:
    return customer_request_state.get(chat_id, {}).get("step")


def handle_customer_request_nav(message) -> bool:
    chat_id = message.chat.id
    if message.text in {"⬅️ Geri (Əsas menyu)", *CANCEL_CMDS}:
        reset_customer_request(chat_id)
        bot.send_message(chat_id, "❌ Sorğu ləğv edildi.")
        return_to_main_menu(chat_id)
        return True
    return False


def validate_phone_number(phone: str) -> bool:
    if not phone:
        return False
    digits = re.sub(r"[^0-9]", "", phone)
    if len(digits) < 9:
        return False
    pattern = r"^\+?\d[\d\s\-()]{7,}$"
    return bool(re.match(pattern, phone.strip()))


def check_request_rate_limit(chat_id: int) -> Optional[int]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT created_at FROM customer_requests WHERE chat_id=? ORDER BY datetime(created_at) DESC LIMIT 1",
        (chat_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    last_dt = parse_dt_safe(
        _row_value_safe(row, "created_at", row[0] if len(row) > 0 else None)
    )
    if not last_dt:
        return None
    diff = datetime.utcnow() - last_dt
    remaining = CUSTOMER_REQUEST_COOLDOWN_SECONDS - diff.total_seconds()
    if remaining > 0:
        return int(remaining)
    return None


def persist_customer_request(chat_id: int, data: dict) -> Optional[int]:
    conn = get_local_conn()
    cur = conn.cursor()

    def has_created_at_column() -> bool:
        try:
            cur.execute("PRAGMA table_info(customer_requests)")
            return any(
                (row[1] if not isinstance(row, dict) else _row_value_safe(row, "name"))
                == "created_at"
                for row in cur.fetchall()
            )
        except Exception:
            return False

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    if has_created_at_column():
        cur.execute(
            """
            INSERT INTO customer_requests (chat_id, request_type, rayon, rooms, budget, notes, phone, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                chat_id,
                data.get("request_type"),
                data.get("rayon"),
                data.get("rooms"),
                data.get("budget"),
                data.get("notes"),
                data.get("phone"),
                created_at,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO customer_requests (chat_id, request_type, rayon, rooms, budget, notes, phone, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                chat_id,
                data.get("request_type"),
                data.get("rayon"),
                data.get("rooms"),
                data.get("budget"),
                data.get("notes"),
                data.get("phone"),
            ),
        )
    req_id = cur.lastrowid
    conn.commit()
    conn.close()
    return req_id


def format_customer_request_card(row: dict) -> str:
    req_type = _row_value_safe(row, "request_type")
    req_txt = "Alış" if req_type == "buy" else "Kirayə"
    created_raw = _row_value_safe(row, "created_at")
    created_dt = parse_dt_safe(created_raw)
    display_time = created_dt + timedelta(hours=4) if created_dt else None
    date_txt = display_time.strftime("%d.%m.%Y") if display_time else "-"

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "👤 Müştəri istəyi",
        f"📍 Rayon: {_row_value_safe(row, 'rayon') or '-'}",
        f"🏠 Tip: {req_txt}",
        f"🚪 Otaq: {_row_value_safe(row, 'rooms') or '-'}",
        f"💰 Büdcə: {_row_value_safe(row, 'budget') or '-'}",
        f"📝 Qeyd: {_row_value_safe(row, 'notes') or '-'}",
        f"📞 Əlaqə: {_row_value_safe(row, 'phone') or '-'}",
        f"📅 Tarix: {date_txt}",
        "━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def is_customer_request_favorited(user_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM customer_request_favorites
        WHERE user_id=? AND request_id=?
        """,
        (user_id, request_id),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def add_customer_request_favorite(user_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO customer_request_favorites (user_id, request_id)
        VALUES (?, ?)
        """,
        (user_id, request_id),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def is_customer_request_archived(user_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM customer_request_archives
        WHERE user_id=? AND request_id=?
        """,
        (user_id, request_id),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def add_customer_request_archive(user_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO customer_request_archives (user_id, request_id)
        VALUES (?, ?)
        """,
        (user_id, request_id),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def remove_customer_request_archive(user_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM customer_request_archives
        WHERE user_id=? AND request_id=?
        """,
        (user_id, request_id),
    )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def fetch_active_requests_by_rayon(
    rayon: str,
    limit: int = 50,
    include_all_status: bool = False,
    user_id: Optional[int] = None,
) -> list:
    conn = get_local_conn()
    cur = conn.cursor()
    query = "SELECT * FROM customer_requests WHERE LOWER(rayon) LIKE LOWER(?)"
    params = [f"%{rayon.strip()}%"]
    if not include_all_status:
        query += " AND status='active'"
    if user_id:
        query += " AND id NOT IN (SELECT request_id FROM customer_request_archives WHERE user_id=?)"
        params.append(user_id)
    query += " ORDER BY datetime(created_at) DESC LIMIT ?"
    params.append(limit)
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_customer_request_by_id(req_id: Optional[int]) -> Optional[dict]:
    if not req_id:
        return None
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM customer_requests WHERE id=?", (req_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def ensure_customer_request_action_allowed(admin_chat_id: int, req_id: str) -> bool:
    try:
        req_id_int = int(req_id)
    except Exception:
        bot.send_message(admin_chat_id, "⚠️ Sorğu tapılmadı.")
        return False
    req = fetch_customer_request_by_id(req_id_int)
    if not req:
        bot.send_message(admin_chat_id, "⚠️ Sorğu tapılmadı.")
        return False
    if req.get("status") == "deleted":
        bot.send_message(admin_chat_id, "⚠️ Sorğu artıq silinib.")
        return False
    return True


def format_agent_request_card(row: dict) -> str:
    req_type = _row_value_safe(row, "request_type")
    req_txt = "Alış" if req_type == "buy" else "Kirayə"
    created_raw = _row_value_safe(row, "created_at") or _row_value_safe(
        row, "request_created_at"
    )
    created_dt = parse_dt_safe(created_raw)
    display_time = created_dt + timedelta(hours=4) if created_dt else None
    date_txt = display_time.strftime("%d.%m.%Y") if display_time else "-"

    lines = [
        "👥 Müştəri istəyi",
        f"📍 Rayon: {_row_value_safe(row, 'rayon') or '-'}",
        f"🏠 Tip: {req_txt}",
        f"🚪 Otaq: {_row_value_safe(row, 'rooms') or '-'}",
        f"💰 Büdcə: {_row_value_safe(row, 'budget') or '-'}",
        f"📝 Qeyd: {_row_value_safe(row, 'notes') or '-'}",
        f"📞 Əlaqə: {_row_value_safe(row, 'phone') or '-'}",
        f"📅 Tarix: {date_txt}",
    ]
    return "\n".join(lines)


def fetch_agent_requests_page(
    rayon: str,
    request_type: str,
    page: int,
    page_size: int = PAGE_SIZE_REQ,
    user_id: Optional[int] = None,
):
    conn = get_local_conn()
    cur = conn.cursor()
    params = [request_type, rayon.strip()]
    count_query = (
        "SELECT COUNT(*) FROM customer_requests "
        "WHERE status='active' AND request_type=? AND LOWER(rayon) = LOWER(?)"
    )
    if user_id:
        count_query += " AND id NOT IN (SELECT request_id FROM customer_request_archives WHERE user_id=?)"
        params.append(user_id)
    cur.execute(count_query, params)
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size
    list_params = [request_type, rayon.strip()]
    list_query = (
        "SELECT * FROM customer_requests "
        "WHERE status='active' AND request_type=? AND LOWER(rayon) = LOWER(?)"
    )
    if user_id:
        list_query += " AND id NOT IN (SELECT request_id FROM customer_request_archives WHERE user_id=?)"
        list_params.append(user_id)
    list_query += " ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?"
    list_params.extend([page_size, offset])
    cur.execute(list_query, list_params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total, total_pages, page


def fetch_customer_request_type_counts(user_id: Optional[int]) -> dict:
    conn = get_local_conn()
    cur = conn.cursor()
    params: List = []
    where = "status='active' AND request_type IN ('buy', 'rent')"
    if user_id:
        where += " AND id NOT IN (SELECT request_id FROM customer_request_archives WHERE user_id=?)"
        params.append(user_id)
    cur.execute(
        f"""
        SELECT request_type, COUNT(*) as cnt
        FROM customer_requests
        WHERE {where}
        GROUP BY request_type
        """,
        params,
    )
    counts = {row["request_type"]: row["cnt"] for row in cur.fetchall()}
    conn.close()
    return counts


def fetch_customer_request_district_counts(
    request_type: str, user_id: Optional[int]
) -> List[Tuple[str, int]]:
    conn = get_local_conn()
    cur = conn.cursor()
    params: List = [request_type]
    where = "status='active' AND request_type=? AND TRIM(COALESCE(rayon, '')) != ''"
    if user_id:
        where += " AND id NOT IN (SELECT request_id FROM customer_request_archives WHERE user_id=?)"
        params.append(user_id)
    cur.execute(
        f"""
        SELECT MIN(rayon) as rayon, COUNT(*) as cnt
        FROM customer_requests
        WHERE {where}
        GROUP BY LOWER(rayon)
        ORDER BY cnt DESC, rayon ASC
        """,
        params,
    )
    rows = [(row["rayon"], row["cnt"]) for row in cur.fetchall()]
    conn.close()
    return rows


def build_customer_requests_operation_menu(
    chat_id: int, message: Optional[types.Message] = None
):
    counts = fetch_customer_request_type_counts(chat_id)
    sale_count = counts.get("buy", 0)
    rent_count = counts.get("rent", 0)
    mk = types.InlineKeyboardMarkup()
    row = []
    if sale_count:
        row.append(
            types.InlineKeyboardButton(
                f"🏠 Satılır ({sale_count})", callback_data="cust_req_op:buy"
            )
        )
    if rent_count:
        row.append(
            types.InlineKeyboardButton(
                f"🏢 Kirayə verilir ({rent_count})",
                callback_data="cust_req_op:rent",
            )
        )
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="cust_req_back"))
    text = TEXTS_AZ["admin_stats_customer_requests"]
    if not row:
        text = "😕 Aktiv müştəri istəyi yoxdur."
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def show_customer_request_district_menu(
    chat_id: int, request_type: str, message: Optional[types.Message] = None
):
    districts = fetch_customer_request_district_counts(request_type, chat_id)
    mk = types.InlineKeyboardMarkup()
    row = []
    for rayon, cnt in districts:
        if not cnt:
            continue
        row.append(
            types.InlineKeyboardButton(
                f"📍 {rayon} ({cnt})",
                callback_data=f"agt_req:{request_type}:{quote(rayon)}:1",
            )
        )
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="cust_req_ops"))
    title = "🏠 Satılır" if request_type == "buy" else "🏢 Kirayə verilir"
    text = f"{title} — rayon seçin:"
    if not districts:
        text = f"😕 {title} üzrə aktiv sorğu yoxdur."
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def agent_has_interest(agent_chat_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM agent_interests WHERE agent_chat_id=? AND request_id=?",
        (agent_chat_id, request_id),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row)


def store_agent_interest(agent_chat_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO agent_interests (agent_chat_id, request_id)
        VALUES (?, ?)
        """,
        (agent_chat_id, request_id),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def add_agent_notification(agent_chat_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO agent_notifications (agent_chat_id, request_id, status)
        VALUES (?, ?, 'new')
        """,
        (agent_chat_id, request_id),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def notify_agents_for_request(req_row: Optional[dict]):
    if not req_row:
        return
    rayon = (_row_value_safe(req_row, "rayon") or "").strip()
    if not rayon:
        return
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT ai.agent_chat_id
        FROM agent_interests ai
        JOIN customer_requests cr ON cr.id = ai.request_id
        WHERE cr.status='active' AND LOWER(cr.rayon) LIKE LOWER(?)
        """,
        (f"%{rayon}%",),
    )
    agent_rows = [r[0] for r in cur.fetchall() if r and r[0]]
    conn.close()

    for agent_id in agent_rows:
        if not has_customer_requests_access(agent_id):
            continue
        if add_agent_notification(agent_id, _row_value_safe(req_row, "id")):
            try:
                bot.send_message(
                    agent_id,
                    f"📢 Yeni müştəri istəyi var ({rayon}) — Bildirişlər bölməsinə baxın",
                )
            except Exception:
                pass


def parse_int_from_text(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    match = re.findall(r"\d+", str(value))
    if not match:
        return None
    try:
        return int(match[0])
    except Exception:
        return None


def parse_request_price(value: Optional[str]) -> Optional[int]:
    return parse_int_from_text(value)


def fetch_active_customer_request_rules() -> List[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM customer_request_rules
        WHERE is_active=1
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def customer_request_matches_rule(req_row: dict, rule: dict) -> bool:
    if not req_row or not rule:
        return False
    req_type = _row_value_safe(req_row, "request_type")
    rule_type = rule.get("request_type")
    if rule_type and req_type != rule_type:
        return False

    rule_rayons = [
        r.strip() for r in (rule.get("rayons") or "").split(",") if r.strip()
    ]
    req_rayon = (_row_value_safe(req_row, "rayon") or "").strip().lower()
    if rule_rayons:
        if not req_rayon:
            return False
        match_any = any(rayon.lower() in req_rayon for rayon in rule_rayons)
        if not match_any:
            return False

    price_val = parse_request_price(_row_value_safe(req_row, "budget"))
    min_val = rule.get("price_min")
    max_val = rule.get("price_max")
    if min_val is not None or max_val is not None:
        if price_val is None:
            return False
        if min_val is not None and price_val < int(min_val):
            return False
        if max_val is not None and price_val > int(max_val):
            return False

    rule_rooms = (rule.get("rooms") or "").strip()
    if rule_rooms:
        req_rooms = parse_int_from_text(_row_value_safe(req_row, "rooms"))
        if req_rooms is None:
            return False
        if "+" in rule_rooms:
            min_rooms = parse_int_from_text(rule_rooms)
            if min_rooms is not None and req_rooms < min_rooms:
                return False
        else:
            rule_rooms_val = parse_int_from_text(rule_rooms)
            if rule_rooms_val is not None and req_rooms != rule_rooms_val:
                return False

    keyword = (rule.get("keyword") or "").strip().lower()
    if keyword:
        notes = (_row_value_safe(req_row, "notes") or "").lower()
        if keyword not in notes:
            return False

    return True


def add_customer_request_alert(user_id: int, rule_id: int, request_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO customer_request_alerts (user_id, rule_id, request_id)
        VALUES (?, ?, ?)
        """,
        (user_id, rule_id, request_id),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def format_customer_request_alert_text(req_row: dict) -> str:
    req_type = format_request_rule_type(_row_value_safe(req_row, "request_type"))
    rayon = _row_value_safe(req_row, "rayon") or "-"
    budget = _row_value_safe(req_row, "budget") or "-"
    rooms = _row_value_safe(req_row, "rooms") or "-"
    return (
        "🆕 Yeni müştəri istəyi tapıldı:\n"
        f"📍 {rayon} | {req_type}\n"
        f"💰 {budget}\n"
        f"🛏 {rooms} otaq"
    )


def notify_users_for_customer_request(req_row: Optional[dict]):
    if not req_row:
        return
    rules = fetch_active_customer_request_rules()
    if not rules:
        return
    for rule in rules:
        user_id = rule.get("user_id")
        if not user_id or not has_customer_requests_access(user_id):
            continue
        if not customer_request_matches_rule(req_row, rule):
            continue
        if not add_customer_request_alert(
            user_id, rule.get("id"), _row_value_safe(req_row, "id")
        ):
            continue
        mk = types.InlineKeyboardMarkup()
        mk.row(
            types.InlineKeyboardButton(
                "👁 Müştəriyə bax",
                callback_data=f"cr_alert_view:{_row_value_safe(req_row, 'id')}",
            ),
            types.InlineKeyboardButton(
                "🛑 Bu qaydanı dayandır",
                callback_data=f"cr_rule_stop:{rule.get('id')}",
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "🗑 Bildirişi sil",
                callback_data=f"cr_alert_delete:{_row_value_safe(req_row, 'id')}:{rule.get('id')}",
            )
        )
        try:
            bot.send_message(
                user_id, format_customer_request_alert_text(req_row), reply_markup=mk
            )
        except Exception:
            pass


def fetch_active_keyword_alerts() -> List[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM keyword_alerts
        WHERE is_active=1
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_keyword_alert_last_checked(key: str) -> Optional[datetime]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT last_checked_at FROM keyword_alert_state WHERE key=?",
        (key,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    last_raw = _row_value_safe(row, "last_checked_at", row[0] if len(row) > 0 else None)
    if not last_raw:
        return None
    try:
        return datetime.fromisoformat(str(last_raw))
    except Exception:
        return None


def set_keyword_alert_last_checked(key: str, value: datetime):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO keyword_alert_state (key, last_checked_at)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET last_checked_at=excluded.last_checked_at
        """,
        (key, value.isoformat()),
    )
    conn.commit()
    conn.close()


def parse_keyword_regions(regions_raw: Optional[str]) -> List[str]:
    return [normalize_text(r) for r in (regions_raw or "").split(",") if r.strip()]


def keyword_region_matches(rayon_raw: str, regions_raw: str) -> bool:
    regions = parse_keyword_regions(regions_raw)
    if not regions:
        return True
    rayon_norm = normalize_text(rayon_raw or "")
    if not rayon_norm:
        return False
    return any(region in rayon_norm for region in regions)


def build_listing_text_blob(ev: dict) -> str:
    title = ev.get("title") or ev.get("prop_type") or ev.get("Emlakin_novu") or ""
    description = (
        ev.get("description") or ev.get("summary") or ev.get("Umumi_melumat") or ""
    )
    address = ev.get("address") or ev.get("Unvan") or ""
    project_name = ev.get("project_name") or ""
    notes = ev.get("notes") or ""
    parts = [title, description, address, project_name, notes]
    return normalize_text(" ".join([str(p) for p in parts if p]))


def build_listing_unique_key(ev: dict, source: str) -> Optional[str]:
    listing_id = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
    if listing_id:
        return f"{source}:{listing_id}".strip()
    link = (
        ev.get("source_link")
        or ev.get("source_url")
        or ev.get("link")
        or ev.get("Link")
    )
    if link:
        return f"{source}:{str(link).strip()}"
    phone = ev.get("phone") or ev.get("Elaqe_nomresi") or ""
    price_val = get_listing_price(ev)
    date_val = ev.get("date_read") or ev.get("date_added") or ev.get("created_at")
    components = [normalize_text(str(phone)), str(price_val or ""), str(date_val or "")]
    fallback = ":".join([source] + [c for c in components if c])
    return fallback if fallback else None


def build_request_text_blob(req_row: dict) -> str:
    parts = [
        _row_value_safe(req_row, "request_type"),
        _row_value_safe(req_row, "rayon"),
        _row_value_safe(req_row, "rooms"),
        _row_value_safe(req_row, "budget"),
        _row_value_safe(req_row, "notes"),
    ]
    return normalize_text(" ".join([str(p) for p in parts if p]))


def keyword_matches_text(keyword_raw: str, text_blob: str) -> bool:
    if not keyword_raw or not text_blob:
        return False
    keyword_norm = normalize_text(keyword_raw)
    if not keyword_norm:
        return False
    tokens = keyword_norm.split()
    if not tokens:
        return False
    text_norm = normalize_text(text_blob)
    if not text_norm:
        return False
    text_tokens = set(text_norm.split())
    return all(token in text_tokens for token in tokens)


def record_keyword_alert_hit(
    alert_id: int,
    user_id: int,
    target_type: str,
    target_id: int,
    source: str = "",
) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO keyword_alert_hits
        (alert_id, user_id, target_type, target_id, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            user_id,
            target_type,
            target_id,
            source or "",
            datetime.utcnow().isoformat(),
        ),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def store_keyword_notification_match(
    user_id: int,
    listing: dict,
    source: str,
    matched_keywords: List[str],
    scan_state: Dict[int, Dict[str, Any]],
    listing_key: Optional[str] = None,
):
    if not listing_key:
        listing_key = build_listing_unique_key(listing, source)
    if not listing_key:
        return

    ctx = scan_state.setdefault(
        user_id, {"items": [], "seen": set(), "index": {}, "id_index": {}}
    )
    listing_id_raw = listing.get("id") or listing.get("ID") or listing.get("Elan_kodu")
    listing_id_key = str(listing_id_raw).strip() if listing_id_raw else None

    if listing_id_key and listing_id_key in ctx.get("id_index", {}):
        existing = ctx["id_index"].get(listing_id_key)
        if existing is not None:
            kw = set(existing.get("__matched_keywords", []))
            kw.update(matched_keywords or [])
            existing["__matched_keywords"] = sorted(kw)
        return
    if listing_key in ctx["seen"]:
        existing = ctx["index"].get(listing_key)
        if existing is not None:
            kw = set(existing.get("__matched_keywords", []))
            kw.update(matched_keywords or [])
            existing["__matched_keywords"] = sorted(kw)
            if listing_id_key:
                ctx["id_index"][listing_id_key] = existing
        return

    listing_copy = dict(listing or {})
    listing_copy["__source"] = source or "main"
    listing_copy["__matched_keywords"] = matched_keywords or []
    ctx["seen"].add(listing_key)
    ctx["items"].append(listing_copy)
    ctx["index"][listing_key] = listing_copy
    if listing_id_key:
        ctx["id_index"][listing_id_key] = listing_copy


def process_keyword_alerts_for_listing(
    ev: dict, source: str = "main", alerts: Optional[List[dict]] = None, scan_state=None
):
    if not ev:
        return
    alerts = alerts or fetch_active_keyword_alerts()
    if not alerts:
        return
    listing_text = normalize_text(build_listing_text_blob(ev))
    listing_rayon = (
        ev.get("rayon")
        or ev.get("Rayon_Qesebe")
        or ev.get("address")
        or ev.get("Unvan")
        or ""
    )
    listing_id_raw = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
    try:
        listing_id_int = int(listing_id_raw)
    except Exception:
        listing_id_int = None

    listing_key = build_listing_unique_key(ev, source)
    matches_by_user: Dict[int, Dict[str, Any]] = {}

    for alert in alerts:
        user_id = alert.get("user_id")
        if not user_id or not is_user_allowed(user_id):
            continue
        keyword_raw = (alert.get("keywords") or "").strip()
        if not keyword_raw:
            continue
        if not keyword_matches_text(keyword_raw, listing_text):
            continue
        if not keyword_region_matches(listing_rayon, alert.get("regions") or ""):
            continue
        entry = matches_by_user.setdefault(
            user_id, {"keywords": set(), "alerts": set()}
        )
        entry["keywords"].add(keyword_raw)
        if alert.get("id"):
            entry["alerts"].add(alert.get("id"))

    if not matches_by_user:
        return

    for user_id, info in matches_by_user.items():
        for alert_id in info.get("alerts", set()):
            if listing_id_int is not None:
                record_keyword_alert_hit(
                    alert_id, user_id, "listing", listing_id_int, source=source
                )
        matched_keywords = sorted(info.get("keywords") or [])
        if scan_state is not None and matched_keywords:
            store_keyword_notification_match(
                user_id, ev, source, matched_keywords, scan_state, listing_key
            )
        logger.info(
            "keyword match listing_key=%s user_id=%s keywords=%s",
            listing_key,
            user_id,
            matched_keywords,
        )


def send_keyword_notification_summaries(scan_state: Dict[int, Dict[str, Any]]):
    global keyword_notification_state
    keyword_notification_state = {}
    for user_id, ctx in (scan_state or {}).items():
        items = ctx.get("items") or []
        if not items:
            continue
        keyword_notification_state[user_id] = {
            "items": items,
            "seen": ctx.get("seen", set()),
            "index": ctx.get("index", {}),
            "ts": time.time(),
        }
        total = len(items)
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton("📂 Elanlara bax", callback_data="kw_notif_view")
        )
        summary_text = f"🔔 Açar sözlər üzrə {total} uyğun elan tapıldı"
        try:
            bot.send_message(user_id, summary_text, reply_markup=mk)
            logger.info(
                "keyword notification summary sent chat_id=%s total=%s", user_id, total
            )
        except Exception:
            logger.exception("Failed to send keyword summary chat_id=%s", user_id)


def process_keyword_alerts_for_request(req_row: dict):
    return


def process_keyword_alerts_for_existing_requests(alert_id: int):
    return


def process_keyword_alerts_for_new_listings():
    last_checked = get_keyword_alert_last_checked("listings")
    now = datetime.utcnow()
    if last_checked is None:
        set_keyword_alert_last_checked("listings", now)
        keyword_notification_state.clear()
        return
    candidates = load_recent_listings(last_checked)
    alerts = fetch_active_keyword_alerts()
    if not candidates or not alerts:
        set_keyword_alert_last_checked("listings", now)
        keyword_notification_state.clear()
        return

    scan_state: Dict[int, Dict[str, Any]] = {}
    for ev in candidates:
        process_keyword_alerts_for_listing(
            ev,
            source=ev.get("__source", "main"),
            alerts=alerts,
            scan_state=scan_state,
        )

    send_keyword_notification_summaries(scan_state)

    set_keyword_alert_last_checked("listings", now)


def show_request_type_menu(chat_id: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Almaq istəyirəm")
    kb.row("🏢 Kirayə götürmək istəyirəm")
    kb.row("⬅️ Geri (Əsas menyu)")
    send_with_reply_keyboard(
        chat_id,
        "📝 Nə üçün sorğu yaratmaq istəyirsiniz?",
        kb,
    )


@bot.message_handler(func=lambda m: m.text == "📝 Ev axtarıram")
def start_customer_request_flow(message):
    return
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if not is_user_allowed(chat_id):
        bot.send_message(chat_id, "🛑 Sorğu göndərmək üçün hesabınız təsdiqlənməlidir.")
        return
    reset_customer_request(chat_id)
    show_request_type_menu(chat_id)


@bot.message_handler(func=lambda m: m.text == "⬅️ Geri (Əsas menyu)")
def customer_request_back(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    reset_customer_request(message.chat.id)
    return_to_main_menu(message.chat.id)


@bot.message_handler(
    func=lambda m: m.text in ["🏠 Almaq istəyirəm", "🏢 Kirayə götürmək istəyirəm"]
)
def handle_request_type_selection(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if not is_user_allowed(chat_id):
        bot.send_message(chat_id, "🛑 Sorğu göndərmək üçün hesabınız təsdiqlənməlidir.")
        return

    remaining = check_request_rate_limit(chat_id)
    if remaining:
        minutes = math.ceil(remaining / 60)
        bot.send_message(
            chat_id,
            f"⏳ Sorğu artıq mövcuddur. {minutes} dəqiqə sonra yenidən cəhd edin.",
        )
        return

    req_type = "buy" if "Almaq" in message.text else "rent"
    customer_request_state[chat_id] = {"step": "rayon", "request_type": req_type}
    bot.send_message(
        chat_id,
        "📍 Rayon / ərazini seçin və ya yazın:",
        reply_markup=build_request_rayon_keyboard(),
    )
    send_menu_visibility_hint(chat_id)


@bot.message_handler(func=lambda m: get_customer_request_step(m.chat.id) == "rayon")
def handle_request_rayon(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if handle_customer_request_nav(message):
        return
    val = (message.text or "").strip()
    if not val:
        bot.send_message(chat_id, "⚠️ Ərazi boş ola bilməz.")
        return
    st = customer_request_state.get(chat_id, {})
    st["rayon"] = val
    st["step"] = "rooms"
    bot.send_message(
        chat_id,
        "🚪 Otaq sayını seçin və ya yazın:",
        reply_markup=build_request_rooms_keyboard(),
    )
    send_menu_visibility_hint(chat_id)


@bot.message_handler(func=lambda m: get_customer_request_step(m.chat.id) == "rooms")
def handle_request_rooms(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if handle_customer_request_nav(message):
        return
    val = (message.text or "").strip()
    if not val:
        bot.send_message(chat_id, "⚠️ Otaq sayı boş ola bilməz.")
        return
    st = customer_request_state.get(chat_id, {})
    st["rooms"] = val
    st["step"] = "budget"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⬅️ Geri (Əsas menyu)")
    send_with_reply_keyboard(chat_id, "💰 Büdcəni yazın (məs: 800 AZN):", kb)


@bot.message_handler(func=lambda m: get_customer_request_step(m.chat.id) == "budget")
def handle_request_budget(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if handle_customer_request_nav(message):
        return
    val = (message.text or "").strip()
    if not val:
        bot.send_message(chat_id, "⚠️ Büdcə boş ola bilməz.")
        return
    st = customer_request_state.get(chat_id, {})
    st["budget"] = val
    st["step"] = "notes"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Keç")
    kb.row("⬅️ Geri (Əsas menyu)")
    send_with_reply_keyboard(
        chat_id,
        "📝 Əlavə qeydlər (istəyə bağlı) yazın və ya 'Keç' seçin:",
        kb,
    )


@bot.message_handler(func=lambda m: get_customer_request_step(m.chat.id) == "notes")
def handle_request_notes(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if handle_customer_request_nav(message):
        return
    val = (message.text or "").strip()
    st = customer_request_state.get(chat_id, {})
    st["notes"] = "" if val.lower() == "keç" else val
    st["step"] = "phone"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⬅️ Geri (Əsas menyu)")
    send_with_reply_keyboard(chat_id, "📞 Əlaqə nömrəsini yazın:", kb)


@bot.message_handler(func=lambda m: get_customer_request_step(m.chat.id) == "phone")
def handle_request_phone(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not ensure_allowed(message):
        return
    if handle_customer_request_nav(message):
        return
    phone = (message.text or "").strip()
    if not validate_phone_number(phone):
        bot.send_message(chat_id, "⚠️ Telefon nömrəsi düzgün deyil. Misal: 0501234567")
        return

    st = customer_request_state.get(chat_id, {})
    st["phone"] = phone

    req_id = persist_customer_request(chat_id, st)
    reset_customer_request(chat_id)
    bot.send_message(
        chat_id,
        "✅ Sorğunuz qeydə alındı.\nUyğun vasitəçilər sizinlə əlaqə saxlayacaq.",
    )
    try:
        req_row = fetch_customer_request_by_id(req_id)
        notify_agents_for_request(req_row)
        notify_users_for_customer_request(req_row)
        process_keyword_alerts_for_request(req_row)
    except Exception:
        pass
    return_to_main_menu(chat_id)


# =============== 📩 Şikayət və təkliflər ===============


def is_complaint_access_allowed(chat_id: int) -> bool:
    return True


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
        bot.send_message(
            chat_id, "⏳ Zəhmət olmasa bir neçə dəqiqə sonra yenidən göndərin."
        )
        return
    complaint_flow_state[chat_id] = {"step": "category"}
    send_with_reply_keyboard(
        chat_id,
        "📂 Kateqoriyanı seçin:",
        build_complaint_categories_keyboard(),
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
    if message.text and message.text.startswith('/'):
        return

    if not is_complaint_access_allowed(message.chat.id):
        return
    start_complaint_flow(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "open_complaint")
@callback_guard
def cb_open_complaint(c):
    if not is_complaint_access_allowed(c.message.chat.id):
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
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    choice = message.text
    if choice == COMPLAINT_BACK:
        complaint_flow_state.pop(chat_id, None)
        set_ui_context(chat_id, UI_CONTEXT_MAIN)
        send_with_reply_keyboard(
            chat_id,
            "✅ Şikayət göndərmə ləğv edildi.",
            build_main_menu(
                is_admin(chat_id),
                has_customer_requests_access(chat_id),
                should_show_bonus_button(chat_id),
            ),
        )
        return
    if choice not in COMPLAINT_CATEGORIES:
        bot.send_message(
            chat_id,
            "📂 Kateqoriyanı seçin:",
            reply_markup=build_complaint_categories_keyboard(),
        )
        send_menu_visibility_hint(chat_id)
        return
    complaint_flow_state[chat_id] = {"step": "message", "category": choice}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(COMPLAINT_BACK)
    send_with_reply_keyboard(chat_id, "✍️ Zəhmət olmasa mesajınızı yazın.", kb)


@bot.message_handler(
    func=lambda m: complaint_flow_state.get(m.chat.id, {}).get("step") == "message",
    content_types=["text"],
)
def complaint_message_handler(message):
    chat_id = message.chat.id
    if message.text and message.text.startswith("/"):
        return
    text = message.text
    if text == COMPLAINT_BACK:
        complaint_flow_state[chat_id] = {"step": "category"}
        send_with_reply_keyboard(
            chat_id,
            "📂 Kateqoriyanı seçin:",
            build_complaint_categories_keyboard(),
        )
        return
    data = complaint_flow_state.pop(chat_id, {})
    category = data.get("category", "-")
    last_complaint_time[chat_id] = time.time()
    try:
        notify_admin_complaint(message, category, text)
    except Exception:
        pass
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    send_with_reply_keyboard(
        chat_id,
        "✅ Mesajınız qəbul edildi.\nTəşəkkür edirik! 🙏",
        build_main_menu(
            is_admin(chat_id),
            has_customer_requests_access(chat_id),
            should_show_bonus_button(chat_id),
        ),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("complaint_reply:"))
@callback_guard
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
    if message.text and message.text.startswith("/"):
        return
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


# =============== 👤 Hesabım ===============


@bot.message_handler(func=lambda m: m.text == "👤 Hesabım")
def show_account_status(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message, allow_blocked=True):
        return
    chat_id = message.chat.id
    text = build_account_status_text(chat_id)
    bot.send_message(chat_id, text, parse_mode="HTML")


# =============== 📊 Statistika ===============


STATS_FILTER_LABELS = {
    "24h": "🕒 24 saat",
    "7d": "📆 7 gün",
    "30d": "📅 30 gün",
    "all": "🧾 Ümumi",
}

STATS_TS_CANDIDATES = (
    "created_at",
    "inserted_at",
    "added_at",
    "published_at",
    "date_added",
    "ts",
    "created_ts",
)

STATS_OPERATION_COLUMNS = ("operation", "deal_type", "listing_type")
STATS_PROPERTY_COLUMNS = ("prop_type", "property_type", "category", "type")
STATS_RECENT_LIMIT = 500

STATS_OPERATION_BUCKETS = {
    "satilir": ["satilir", "satılır", "sale", "satish", "for sale", "sell"],
    "kiraye": ["kiraye", "kirayə", "rent", "icarə", "for rent"],
}

STATS_PROPERTY_BUCKETS = {
    "menzil": ["menzil", "mənzil", "apartment", "kvartira"],
    "heyet_evi": ["heyet evi", "həyət evi", "house", "villa"],
    "torpaq": ["torpaq", "land", "plot"],
}

PROP_TYPE_EMOJI_MAP = {
    "Mənzil": "🏠",
    "Bağ evi": "🏡",
    "Həyət evi": "🏘️",
    "Obyekt / Ofis": "🏢",
    "Torpaq": "🌱",
}


def build_user_stats_keyboard(selected: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    buttons = []
    for key in ("24h", "7d", "30d", "all"):
        label = STATS_FILTER_LABELS.get(key, key)
        prefix = "✅ " if key == selected else ""
        buttons.append(
            types.InlineKeyboardButton(f"{prefix}{label}", callback_data=f"stats:{key}")
        )
    mk.row(*buttons)
    return mk


def format_market_pulse_text(records: List[Dict[str, Any]]) -> str:
    if not records:
        return "❌ Rayon statistikası üçün məlumat tapılmadı."
    lines: List[str] = []
    for item in records:
        lines.append(f"📊 {item.get('rayon', '-')}:" )
        lines.append(f"• Son 7 gün: {item.get('new_count', 0)} yeni elan")
        lines.append("• Satış sürəti:")
        lines.append(f"    - {item.get('speed', 'orta')}")
        lines.append("• Orta qiymət:")
        lines.append(f"    - {item.get('price_trend', '→ stabil')}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_stats_text(
    base_stats: dict, period_stats: dict, period_key: str, is_admin: bool = False
) -> str:
    label = STATS_FILTER_LABELS.get(period_key, "🧾 Ümumi")
    lines = [
        "📊 <b>BestHome Statistikası</b>",
        "─────────────",
        "",
        "📦 Ümumi göstəricilər:",
        f"• Ümumi elanlar: {base_stats.get('total', 0)}",
        f"• 🔑 Satılır: {base_stats.get('sale_count', 0)}",
        f"• 🛏 Kirayə: {base_stats.get('rent_count', 0)}",
    ]
    base_prop_types = base_stats.get("prop_type_counts", {}) or {}
    period_prop_types = period_stats.get("prop_type_counts", {}) or {}

    if base_prop_types:
        for prop_type, count in base_prop_types.items():
            emoji = PROP_TYPE_EMOJI_MAP.get(prop_type, "🏠")
            lines.append(f"• {emoji} {prop_type}: {count}")

    lines.extend(
        [
            "",
            f"{label} göstəriciləri:",
            f"• 🆕 Yeni elanlar: {period_stats.get('total', 0)}",
            f"• 🔑 Satılır: {period_stats.get('sale_count', 0)}",
            f"• 🛏 Kirayə: {period_stats.get('rent_count', 0)}",
        ]
    )

    if period_prop_types:
        for prop_type, count in period_prop_types.items():
            emoji = PROP_TYPE_EMOJI_MAP.get(prop_type, "🏠")
            lines.append(f"• {emoji} {prop_type}: {count}")

    if period_stats.get("note"):
        lines.append(f"({period_stats['note']})")

    if is_admin:
        meta = period_stats.get("meta") or base_stats.get("meta") or {}
        lines.append(
            ""
        )
        lines.append(
            "(dbg: table={} ts={} {} op={} type={})".format(
                meta.get("table") or "-",
                meta.get("ts_col") or "-",
                meta.get("ts_kind") or "none",
                meta.get("op_col") or "-",
                meta.get("type_col") or "-",
            )
        )
    return "\n".join(lines)


def send_user_statistics(chat_id: int, period_key: str, message_id: Optional[int] = None):
    selected = period_key if period_key in STATS_FILTER_LABELS else "24h"
    user_stats_filter[chat_id] = selected
    base_stats = compute_user_statistics("all")
    period_stats = compute_user_statistics(selected)
    is_admin = chat_id in set(int(x) for x in ADMIN_IDS)
    text = format_stats_text(base_stats, period_stats, selected, is_admin=is_admin)
    keyboard = build_user_stats_keyboard(selected)

    if message_id:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def send_market_pulse_overview(chat_id: int):
    records = compute_market_pulse()
    text = format_market_pulse_text(records)
    bot.send_message(chat_id, text)


@bot.message_handler(func=lambda m: m.text == "📊 Statistika")
def show_global_statistics(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message, allow_blocked=True):
        return
    chat_id = message.chat.id
    default_period = user_stats_filter.get(chat_id, "24h")
    send_user_statistics(chat_id, default_period)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("stats:"))
@callback_guard
def handle_user_stats_callback(c):
    period = c.data.split(":", 1)[1] if c.data else "all"
    if not ensure_allowed_cb(c, allow_blocked=True):
        return
    chat_id = c.message.chat.id if c.message else c.from_user.id
    user_stats_filter[chat_id] = period
    try:
        bot.answer_callback_query(c.id, STATS_FILTER_LABELS.get(period, "🧾 Ümumi"))
    except Exception:
        pass
    if c.message:
        send_user_statistics(chat_id, period, message_id=c.message.message_id)


# =============== ℹ️ Haqqında ===============


@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    if message.text and message.text.startswith('/'):
        return

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
    mk.add(
        types.InlineKeyboardButton(
            "📩 Şikayət və təkliflər", callback_data="open_complaint"
        )
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=mk)


@bot.message_handler(func=lambda m: m.text == "💳 Ödəniş")
def payment_menu_entry(message):
    if message.text and message.text.startswith('/'):
        return

    send_payment_menu(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "open_pay_menu")
@callback_guard
def cb_open_pay_menu(c):
    chat_id = c.message.chat.id
    send_payment_menu(chat_id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "payinfo")
@callback_guard
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
@callback_guard
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
@callback_guard
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
@callback_guard
def cb_payplan(c):
    chat_id = c.message.chat.id
    plan_key = c.data.split("|")[1]
    plan = SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        return
    set_payment_note(chat_id, f"plan:{plan_key}")
    payment_plan_selection[chat_id] = {
        "selected_plan": plan_key,
        "selected_days": plan.get("days"),
        "selected_price": plan.get("price"),
    }
    payment_code = subscription_payment_code(chat_id)
    mk = build_payment_action_markup(plan_key, plan or {}, payment_code)

    pay_text = (
        "🎁 BONUS İMKAN\n\n"
        "Hər 24 saatda 1 dəfə\n"
        "əsas menyudakı 🎁 Şansını sına\n"
        "düyməsindən istifadə edərək\n"
        "pulsuz gün qazana bilərsiniz.\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "💳 ÖDƏNİŞ\n\n"
        "Planı seçin və\n"
        "aşağıdakı düymələrdən biri ilə\n"
        "birbaşa yazın 👇\n\n"
        "🆔 Ödəniş kodu:\n"
        f"{payment_code}"
    )
    bot.send_message(chat_id, pay_text, reply_markup=mk)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cardpay|"))
@callback_guard
def cb_card_payment_info(c):
    chat_id = c.message.chat.id
    plan_key = c.data.split("|", 1)[1] if c.data and "|" in c.data else ""
    plan = SUBSCRIPTION_PLANS.get(plan_key) or {}
    logger.info(
        "User clicked card payment (preparation mode) chat_id=%s plan=%s",
        chat_id,
        plan_key,
    )

    info_text = (
        "----------------------------------\n"
        "💳 Kartla ödəniş\n\n"
        "Hazırda bank kartı ilə ödəniş\n"
        "aktivləşmə mərhələsindədir.\n\n"
        "Çox yaxın zamanda:\n"
        "• Bir kliklə ödəniş\n"
        "• Avtomatik aktivləşmə\n"
        "• Gecikməsiz giriş\n\n"
        "Bu müddətdə ödəniş üçün\n"
        "aşağıdakı düymələrdən istifadə edin 👇\n"
        "----------------------------------"
    )

    mk = build_payment_action_markup(
        plan_key or "", plan, subscription_payment_code(chat_id), include_card_button=False
    )
    bot.send_message(chat_id, info_text, reply_markup=mk)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "demo3")
@callback_guard
def cb_demo_activate(c):
    chat_id = c.message.chat.id
    record = get_user_record(chat_id) or {}
    now = datetime.utcnow()
    demo_end = parse_dt_safe(record.get("demo_end_at") or record.get("demo_expires_at"))
    demo_used = record.get("demo_used", 0)

    if demo_end and demo_end > now:
        display_time = demo_end + timedelta(hours=4)
        msg = (
            "ℹ️ Siz artıq demo istifadə edirsiniz.\n"
            f"Bitmə tarixi: {display_time.strftime('%d.%m.%Y %H:%M')}"
        )
        bot.send_message(chat_id, msg)
        logger.info("Demo denied (already active) chat_id=%s", chat_id)
        return

    if demo_used:
        logger.info("Demo denied (expired) chat_id=%s", chat_id)
        send_payment_menu(chat_id)
        return
    expires = now + timedelta(days=DEMO_DAYS)
    was_blocked = bool(record.get("blocked"))
    update_user_status(
        chat_id,
        STATUS_ACTIVE_DEMO,
        demo_start_at=now,
        demo_end_at=expires,
        paid_until=parse_dt_safe(record.get("paid_until")),
        blocked_at=None,
    )
    set_subscription(chat_id, "demo", expires, is_active=1, is_demo=1, note="demo")
    try:
        bot.edit_message_reply_markup(
            chat_id,
            c.message.message_id,
            reply_markup=build_payment_menu_markup(chat_id),
        )
    except Exception:
        pass

    display_expiry = expires + timedelta(hours=4)
    bot.send_message(
        chat_id,
        f"🎁 Demo aktiv edildi. Bitmə tarixi: {display_expiry.strftime('%d.%m.%Y %H:%M')}",
    )
    if was_blocked:
        logger.info("User auto-unblocked via demo chat_id=%s", chat_id)
    logger.info("Demo activated chat_id=%s expires=%s", chat_id, expires.isoformat())

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
        f"{display_expiry.strftime('%d.%m.%Y %H:%M')}"
    )
    bot.send_message(ADMIN_ID, admin_text)
    reset_user_state(chat_id)
    reset_search_state(chat_id)
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    send_with_reply_keyboard(
        chat_id,
        "🏠 Əsas menyu açıqdır.",
        build_main_menu(
            is_admin(chat_id),
            has_customer_requests_access(chat_id),
            should_show_bonus_button(chat_id),
        ),
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("paydone|"))
@callback_guard
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
    bot.send_message(
        chat_id,
        "✅ Ödəniş sorğunuz adminə göndərildi. Nəticə barədə məlumat veriləcək.",
    )
    try:
        bot.answer_callback_query(c.id, "Admin təsdiqi gözlənilir")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("payadm|"))
@callback_guard
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
    sub = get_subscription(uid)
    plan = SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        return

    if action == "ok":
        base = resolve_extension_base(uid)
        expires = base + timedelta(days=plan["days"])
        insert_subscription(
            uid, plan["title"], expires, is_demo=0, note=f"plan:{plan_key}"
        )
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET approved=1 WHERE chat_id=?", (uid,))
        conn.commit()
        conn.close()
        amount_val = parse_price_value(plan.get("price")) or 0
        if amount_val > 0:
            log_approved_payment(uid, plan["title"], amount_val)
        process_referral_on_payment(uid, sub, amount_val)
        try:
            bot.send_message(
                uid,
                "✅ Hesabınız aktivləşdirildi\n"
                f"📅 Bitmə tarixi: {(expires + timedelta(hours=4)).strftime('%d.%m.%Y')}",
            )
        except Exception:
            pass
        bot.answer_callback_query(c.id, "? Aktiv edildi")
    elif action == "rej":
        try:
            bot.send_message(
                uid, " ödəniş təsdiqlənmədi. Zəhmət olmasa adminlə əlaqə saxlayın. @esedovesed"
            )
        except Exception:
            pass
        bot.answer_callback_query(c.id, "?mtina edildi")


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
        set_ui_context(chat_id, UI_CONTEXT_MAIN)
        send_with_reply_keyboard(
            chat_id,
            "❌ Əməliyyat ləğv edildi.",
            build_main_menu(
                is_admin(chat_id),
                has_customer_requests_access(chat_id),
                should_show_bonus_button(chat_id),
            ),
        )
        return True
    return False


@bot.message_handler(func=lambda m: m.text == "📝 Yeni elan əlavə et")
def start_new_listing(message):
    return
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    reset_user_state(chat_id)

    instr = (
        "📝 *Yeni elan əlavə etmə qaydası:*\n"
        "1️⃣ Rol (Vasitəçi / Əmlak sahibi)\n"
        "2️⃣ Əməliyyat (Satılır / Kirayə verilir)\n"
        "3️⃣ Əmlak tipi (Mənzil / Həyət evi / Obyekt / Ofis / Bağ evi / Torpaq)\n"
        "4️⃣ Otaq sayı, ərazi, metro, sahə, qiymət, əlaqə\n"
        "5️⃣ Elan admin təsdiqindən sonra sistemə düşəcək."
    )
    bot.send_message(chat_id, instr, parse_mode="Markdown")

    kb = new_listing_keyboard(extra=[["Vasitəçi", "Əmlak sahibi"]])
    user_state[chat_id] = {"step": "role", "chat_id": chat_id}
    bot.send_message(chat_id, "👤 Rolunuzu seçin:", reply_markup=kb)


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "role")
def step_role(message):
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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

    extra = [["Mənzil", "Həyət evi"], ["Obyekt / Ofis", "Bağ evi"], ["Torpaq"]]
    kb = new_listing_keyboard(extra=extra)
    st["step"] = "prop_type"
    bot.send_message(chat_id, "🏠 Əmlak tipini seçin:", reply_markup=kb)


@bot.message_handler(func=lambda m: get_user_step(m.chat.id) == "prop_type")
def step_prop_type(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    choice = (message.text or "").strip()
    valid = ["Mənzil", "Həyət evi", "Obyekt / Ofis", "Bağ evi", "Torpaq"]
    normalized_choice = normalize_property_type_ui_value(choice)
    if not normalized_choice or normalized_choice not in valid:
        bot.send_message(chat_id, "Verilən siyahıdan əmlak tipini seçin.")
        return
    st = user_state[chat_id]
    st["prop_type"] = normalized_choice

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

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
            date_added, created_at, chat_id, role, prop_type, operation,
            rayon, metro, rooms, area_kvm, price, currency,
            phone, contact_name, summary, link, approved
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """,
        (
            datetime.now().date().isoformat(),
            format_sqlite_datetime(datetime.now()),
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
    if message.text and message.text.startswith('/'):
        return

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
            types.InlineKeyboardButton(
                "✅ Təsdiqlə", callback_data=f"admin_approve:{new_id}"
            ),
            types.InlineKeyboardButton(
                "❌ Sil", callback_data=f"admin_delete:{new_id}"
            ),
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


@bot.message_handler(func=lambda m: m.text in ["📋 Elanlarım", "📂 Elanlarım"])
def my_listings(message):
    return
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    reset_search_state(chat_id)
    send_paginated_results(chat_id, "favorites", params={}, page=1)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav|"))
@callback_guard
def cb_add_favorite(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    _, src, sid = c.data.split("|")
    lid = int(sid)
    add_favorite_entry(chat_id, src, lid)
    bot.answer_callback_query(c.id, "⭐ Favoriyə əlavə olundu.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("wa|"))
@callback_guard
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
@callback_guard
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

    remove_favorite_entry(chat_id, src, lid)

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


@bot.message_handler(func=lambda m: m.text == "🔥 Ən çox baxılan elanlar")
def show_top_viewed(message):
    if message.text and message.text.startswith('/'):
        return

    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Bu bölmə yalnız admin üçündür.")
        return
    chat_id = message.chat.id
    reset_search_state(chat_id)
    send_paginated_results(chat_id, "topviews", params={"days": 7}, page=1)


# =============== 🔎 AXTARIŞ SİSTEMİ ===============


@bot.message_handler(func=lambda m: m.text == "🔎 Axtarış sistemi")
def search_system_menu(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    send_search_menu(message.chat.id)


@bot.message_handler(func=lambda m: m.text == "🎁 Şansını sına")
def handle_bonus_spin_request(message):
    if message.text and message.text.startswith('/'):
        return

    handle_chance_request(message.from_user.id)


@bot.message_handler(func=lambda m: m.text == "⬅️ Geri")
def return_to_main_menu(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    reset_search_state(chat_id)
    reset_user_state(chat_id)
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    send_main_menu(chat_id, "🏠 Əsas menyu", force=True)


def prompt_today_operation(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("✅ Satılır", callback_data="td|op|sat"),
        types.InlineKeyboardButton("✅ Kirayə verilir", callback_data="td|op|kir"),
    )
    mk.add(types.InlineKeyboardButton("🌐 Hamısı", callback_data="td|op|all"))
    bot.send_message(chat_id, "Əməliyyat növünü seç:", reply_markup=mk)


def prompt_today_property(chat_id: int):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("Mənzil", callback_data="td|tp|m"),
        types.InlineKeyboardButton("Həyət evi", callback_data="td|tp|f"),
    )
    mk.add(
        types.InlineKeyboardButton("Obyekt", callback_data="td|tp|q"),
        types.InlineKeyboardButton("Bağ evi", callback_data="td|tp|b"),
    )
    mk.add(
        types.InlineKeyboardButton("Torpaq", callback_data="td|tp|t"),
        types.InlineKeyboardButton("Digər", callback_data="td|tp|d"),
    )
    mk.add(types.InlineKeyboardButton("Hamısı", callback_data="td|tp|all"))
    bot.send_message(chat_id, "🏠 Əmlak tipini seç:", reply_markup=mk)


def get_today_rayon_counts(listings: List[dict]) -> Dict[str, int]:
    region_counter = Counter(
        normalize_region(item.get("rayon"))
        for item in listings
        if item.get("rayon")
    )
    logger.info(
        "REGION COUNTS DEBUG total=%d regions=%s",
        len(listings),
        dict(region_counter),
    )
    return dict(region_counter)


def build_today_rayon_keyboard(
    filtered_listings: List[dict], rayons: List[str]
) -> types.InlineKeyboardMarkup:
    normalized_counts = get_today_rayon_counts(filtered_listings)
    buttons = []
    for rayon_name in rayons:
        normalized = normalize_region(rayon_name)
        count = normalized_counts.get(normalized, 0)
        if count <= 0:
            continue

        text = f"{rayon_name} ({count})"
        buttons.append((text, rayon_name, count))

    buttons.sort(key=lambda x: x[2], reverse=True)
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Hamısı", callback_data="td|rn|all"))
    row = []
    for text, rayon_name, count in buttons:
        row.append(
            types.InlineKeyboardButton(text, callback_data=f"td|rn|{rayon_name}")
        )
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    return mk


def prompt_today_rayon(chat_id: int):
    rayons = REGION_OPTIONS.get("all", {}).get("rayons", [])
    if not rayons:
        send_today_results(chat_id, today_flow_state.get(chat_id, {}))
        return
    filters = today_flow_state.get(chat_id, {"op": "all", "prop": "all", "rayon": "all"})
    filters_copy = dict(filters)
    cached_results = today_results_cache.get(chat_id, {})
    cached_filters = cached_results.get("filters") or {}
    filtered_listings = (
        cached_results.get("items")
        if cached_filters == filters_copy
        else None
    )
    if filtered_listings is None:
        filtered_listings, _ = fetch_all_results(
            chat_id, mode="today", params={"filters": filters_copy}
        )
        today_results_cache[chat_id] = {
            "filters": filters_copy,
            "items": filtered_listings,
        }

    filtered_listings = filtered_listings or []
    mk = build_today_rayon_keyboard(filtered_listings, rayons)
    bot.send_message(chat_id, "📍 Rayon seçin:", reply_markup=mk)


def compute_today_stats(filters: dict) -> dict:
    op = filters.get("op", "all")
    if op == "sat":
        sale = count_today_listings(filters, op_override="sat")
        rent = 0
        total = sale
    elif op == "kir":
        sale = 0
        rent = count_today_listings(filters, op_override="kir")
        total = rent
    else:
        sale = count_today_listings(filters, op_override="sat")
        rent = count_today_listings(filters, op_override="kir")
        total = sale + rent
    return {"total": total, "sale": sale, "rent": rent}


def send_today_stats_message(chat_id: int, filters: dict):
    stats = compute_today_stats(filters)
    text = (
        "🕒 Son 24 saat\n"
        f"📊 Ümumi: {stats['total']}\n"
        f"1⃣ Satılır: {stats['sale']}\n"
        f"2⃣ Kirayə: {stats['rent']}"
    )
    bot.send_message(chat_id, text)


def send_today_results(chat_id: int, filters: dict, message=None):
    loading_ref = show_loading_message(chat_id, message)
    log_search_event(
        chat_id,
        "today",
        operation=normalize_operation_value(filters.get("op")) or filters.get("op"),
        query_text=str(filters),
    )
    send_paginated_results(
        chat_id,
        mode="today",
        params={"filters": dict(filters)},
        page=1,
        loading_ref=loading_ref,
    )


def start_today_flow(chat_id: int):
    reset_search_state(chat_id)
    today_results_cache.pop(chat_id, None)
    today_flow_state[chat_id] = {"op": "all", "prop": "all", "rayon": "all"}
    set_ui_context(chat_id, UI_CONTEXT_TODAY)
    send_today_stats_message(chat_id, today_flow_state[chat_id])
    prompt_today_operation(chat_id)


@bot.message_handler(func=lambda m: m.text == "🕒 Son 24 saat")
def handle_today_menu(message):
    if not ensure_allowed(message):
        return
    start_today_flow(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("td|"))
@callback_guard
def handle_today_callbacks(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split("|")
    if len(parts) < 3:
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        recover_main_menu(c.message.chat.id, c.message)
        return

    chat_id = c.message.chat.id
    st = today_flow_state.setdefault(
        chat_id, {"op": "all", "prop": "all", "rayon": "all"}
    )
    action, value = parts[1], parts[2]

    if action == "op":
        st["op"] = value
        if value == "all":
            st["prop"] = "all"
            st["rayon"] = "all"
        send_today_stats_message(chat_id, st)
        prompt_today_property(chat_id)
    elif action == "tp":
        st["prop"] = value
        if value == "all":
            st["rayon"] = "all"
        send_today_stats_message(chat_id, st)
        prompt_today_rayon(chat_id)
    elif action == "rn":
        rayons = REGION_OPTIONS.get("all", {}).get("rayons", [])
        if value == "all":
            st["rayon"] = "all"
        else:
            st["rayon"] = value if value in rayons else "all"
        send_today_stats_message(chat_id, st)
        send_today_results(
            chat_id, st, message=(c.message.chat.id, c.message.message_id)
        )
    else:
        recover_main_menu(chat_id, c.message, "Əsas menyu bərpa edildi")

    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def start_structured_search_from_menu(chat_id: int, op_code: str):
    reset_search_state(chat_id)
    search_state[chat_id] = {
        "mode": "structured",
        "filters": {},
        "history": [],
        "awaiting_floor_range": False,
        "awaiting_price_min": False,
        "awaiting_price_max": False,
        "step": "op",
    }
    search_state[chat_id]["filters"]["op"] = op_code
    render_date_range_step(chat_id)


@bot.message_handler(func=lambda m: m.text in ["🏠 Satılır", "🏢 Kirayə verilir"])
def structured_search_from_menu(message):
    if message.text and message.text.startswith('/'):
        return

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
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "keyword", 30):
        bot.send_message(chat_id, "Günlük açar sözlə axtarış limitiniz bitib.")
        return
    reset_search_state(chat_id)
    search_state[chat_id] = {
        "mode": "keyword",
        "operation": None,
        "date_selected": False,
    }
    send_keyword_operation_prompt(chat_id)


@bot.message_handler(func=lambda m: m.text == "📞 Nömrə ilə axtar")
def phone_search_from_menu(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not check_limit(chat_id, "phone", 50):
        bot.send_message(chat_id, "Günlük nömrə ilə axtarış limitiniz bitib.")
        return
    msg = bot.send_message(chat_id, "☎️ Axtarmaq istədiyiniz nömrəni yazın:")
    bot.register_next_step_handler(msg, phone_search_handler)


@bot.message_handler(func=lambda m: m.text == "📌 Müştəri istəkləri")
def customer_requests_from_menu(message):
    return
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if is_admin(chat_id):
        show_admin_customer_request_types(chat_id)
        return
    if not has_customer_requests_access(chat_id):
        bot.send_message(chat_id, "❌ Bu funksiya sizin üçün aktiv deyil.")
        return
    build_customer_requests_operation_menu(chat_id)


@bot.message_handler(
    func=lambda m: customer_request_rule_state.get(m.chat.id, {}).get("step") == "type"
)
def handle_customer_request_rule_type(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not has_customer_requests_access(chat_id):
        bot.send_message(chat_id, "❌ Bu funksiya sizin üçün aktiv deyil.")
        customer_request_rule_state.pop(chat_id, None)
        return
    text = (message.text or "").strip()
    if text == "⬅️ Geri":
        customer_request_rule_state.pop(chat_id, None)
        show_customer_request_rules(chat_id)
        return
    if text not in {"🏠 Satılır", "🏢 Kirayə"}:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa seçim edin.")
        return
    req_type = "buy" if "Satılır" in text else "rent"
    customer_request_rule_state[chat_id] = {
        "step": "rayon",
        "request_type": req_type,
        "rayons": [],
    }
    send_customer_request_rule_rayon_prompt(chat_id)


@bot.message_handler(
    func=lambda m: customer_request_rule_state.get(m.chat.id, {}).get("step")
    == "min_price"
)
def handle_customer_request_rule_min_price(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if text == "⬅️ Geri":
        customer_request_rule_state[chat_id]["step"] = "rayon"
        send_customer_request_rule_rayon_prompt(chat_id)
        return
    if text == "⚪️ Keç":
        customer_request_rule_state[chat_id]["price_min"] = None
    else:
        value = parse_int_from_text(text)
        if value is None:
            bot.send_message(chat_id, "⚠️ Minimum qiyməti rəqəm ilə yazın.")
            return
        customer_request_rule_state[chat_id]["price_min"] = value
    customer_request_rule_state[chat_id]["step"] = "max_price"
    bot.send_message(
        chat_id,
        "💰 Maksimum qiymət yazın (istəyə görə):",
        reply_markup=build_optional_input_keyboard(),
    )


@bot.message_handler(
    func=lambda m: customer_request_rule_state.get(m.chat.id, {}).get("step")
    == "max_price"
)
def handle_customer_request_rule_max_price(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if text == "⬅️ Geri":
        customer_request_rule_state[chat_id]["step"] = "min_price"
        bot.send_message(
            chat_id,
            "💰 Minimum qiymət yazın (istəyə görə):",
            reply_markup=build_optional_input_keyboard(),
        )
        return
    if text == "⚪️ Keç":
        customer_request_rule_state[chat_id]["price_max"] = None
    else:
        value = parse_int_from_text(text)
        if value is None:
            bot.send_message(chat_id, "⚠️ Maksimum qiyməti rəqəm ilə yazın.")
            return
        customer_request_rule_state[chat_id]["price_max"] = value
    customer_request_rule_state[chat_id]["step"] = "rooms"
    bot.send_message(
        chat_id,
        "🛏 Otaq sayı yazın (istəyə görə):",
        reply_markup=build_optional_input_keyboard(),
    )


@bot.message_handler(
    func=lambda m: customer_request_rule_state.get(m.chat.id, {}).get("step") == "rooms"
)
def handle_customer_request_rule_rooms(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if text == "⬅️ Geri":
        customer_request_rule_state[chat_id]["step"] = "max_price"
        bot.send_message(
            chat_id,
            "💰 Maksimum qiymət yazın (istəyə görə):",
            reply_markup=build_optional_input_keyboard(),
        )
        return
    if text == "⚪️ Keç":
        customer_request_rule_state[chat_id]["rooms"] = ""
    else:
        customer_request_rule_state[chat_id]["rooms"] = text
    customer_request_rule_state[chat_id]["step"] = "keyword"
    bot.send_message(
        chat_id,
        "🔎 Açar söz yazın (istəyə görə):",
        reply_markup=build_optional_input_keyboard(),
    )


@bot.message_handler(
    func=lambda m: customer_request_rule_state.get(m.chat.id, {}).get("step")
    == "keyword"
)
def handle_customer_request_rule_keyword(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if text == "⬅️ Geri":
        customer_request_rule_state[chat_id]["step"] = "rooms"
        bot.send_message(
            chat_id,
            "🛏 Otaq sayı yazın (istəyə görə):",
            reply_markup=build_optional_input_keyboard(),
        )
        return
    if text == "⚪️ Keç":
        customer_request_rule_state[chat_id]["keyword"] = ""
    else:
        customer_request_rule_state[chat_id]["keyword"] = text
    data = customer_request_rule_state.pop(chat_id, {})
    data["rayons"] = ",".join(data.get("rayons") or [])
    rule_id = save_customer_request_rule(chat_id, data)
    bot.send_message(chat_id, f"✅ Qayda yaradıldı (ID: {rule_id}).")
    show_customer_request_rules(chat_id)


def return_to_main_menu(chat_id: int):
    search_state.pop(chat_id, None)
    admin_panel_page_state.pop(chat_id, None)
    admin_state.pop(chat_id, None)
    set_user_state(chat_id, "MAIN")
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    if is_admin(chat_id):
        send_main_menu(chat_id, force=True)
    else:
        main_menu(chat_id)


def format_saved_search_entry(row: dict) -> str:
    op = _row_value_safe(row, "operation")
    if op == "sale":
        op_txt = "Satılır"
    elif op == "rent":
        op_txt = "Kirayə"
    else:
        op_txt = "Hamısı"

    parts = [f"💼 {op_txt}"]

    rooms = _row_value_safe(row, "rooms")
    if rooms:
        parts.append(f"🚪 {rooms} otaq")

    price_min = _row_value_safe(row, "price_min")
    price_max = _row_value_safe(row, "price_max")
    if price_min is not None or price_max is not None:
        if price_min and price_max:
            parts.append(f"💰 {price_min}-{price_max}")
        elif price_min:
            parts.append(f"💰 {price_min}+")
        elif price_max:
            parts.append(f"💰 0-{price_max}")

    rayon = _row_value_safe(row, "rayon")
    if rayon:
        rayons = [r.strip() for r in str(rayon).split(",") if r.strip()]
        parts.append(f"📍 {', '.join(rayons)}")

    prop_type = _row_value_safe(row, "prop_type")
    if prop_type:
        parts.append(f"🏠 {prop_type}")

    return " | ".join(parts)


def show_notifications_menu(chat_id: int, message=None):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "📌 Açar söz bildirişləri", callback_data="notif_kw_hits"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "🎯 Kriteriya bildirişləri", callback_data="notif_crit"
        )
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="notif_back"))
    text = "🔔 Bildirişlər"
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def format_notification_listing_line(idx: int, listing: dict) -> str:
    listing_id = (
        listing.get("id") or listing.get("ID") or listing.get("Elan_kodu") or "-"
    )
    title = listing.get("prop_type") or listing.get("Emlakin_novu") or "-"
    rooms = listing.get("rooms") or listing.get("Otaq_sayi") or "-"
    op = listing.get("operation") or listing.get("Emeliyyat") or "-"
    price = format_price(listing.get("price") or listing.get("Qiymet"))
    rayon = listing.get("rayon") or listing.get("Rayon_Qesebe") or "-"
    return (
        f"{idx}. 🆔 Elan kodu: #{listing_id} | {title} | {rooms} | {op} | {price} | {rayon}"
    )


def fetch_notification_listings_page(
    chat_id: int, period: str, page: int = 1
) -> Tuple[List[dict], int, int, int]:
    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    where = "chat_id=?"
    params = [chat_id]
    period_clause, period_params = build_period_filter(period, "created_at")
    where += period_clause
    params.extend(period_params)
    cur.execute(f"SELECT COUNT(*) FROM user_notifications WHERE {where}", params)
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_NOTIFICATIONS)) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_NOTIFICATIONS
    cur.execute(
        f"""
        SELECT * FROM user_notifications
        WHERE {where}
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        params + [PAGE_SIZE_NOTIFICATIONS, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total, total_pages, page


def fetch_notification_listings(chat_id: int, period: str) -> List[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    where = "chat_id=?"
    params = [chat_id]
    period_clause, period_params = build_period_filter(period, "created_at")
    where += period_clause
    params.extend(period_params)
    cur.execute(
        f"""
        SELECT * FROM user_notifications
        WHERE {where}
        ORDER BY datetime(created_at) DESC
        """,
        params,
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def fetch_notification_listings_page(
    chat_id: int, period: str, page: int = 1
) -> Tuple[List[dict], int, int, int]:
    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    where = "chat_id=?"
    params = [chat_id]
    period_clause, period_params = build_period_filter(period, "created_at")
    where += period_clause
    params.extend(period_params)

    cur.execute(f"SELECT COUNT(*) FROM user_notifications WHERE {where}", params)
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_NOTIFICATIONS)) if total else 1
    if page > total_pages:
        page = total_pages

    cur.execute(
        f"UPDATE user_notifications SET status='seen' WHERE {where} AND status='new'",
        params,
    )
    conn.commit()

    offset = (page - 1) * PAGE_SIZE_NOTIFICATIONS
    cur.execute(
        f"""
        SELECT * FROM user_notifications
        WHERE {where}
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        params + [PAGE_SIZE_NOTIFICATIONS, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total, total_pages, page


def show_notifications_inbox(chat_id: int, period: str, page: int = 1, message=None):
    rows = fetch_notification_listings(chat_id, period)
    total = len(rows)
    if total == 0:
        mk_empty = types.InlineKeyboardMarkup()
        mk_empty.add(
            types.InlineKeyboardButton("⚙️ Kriteriyalar", callback_data="notif_crit")
        )
        mk_empty.add(
            types.InlineKeyboardButton(
                "🔔 Açar söz bildirişləri", callback_data="notif_kw_hits"
            )
        )
        mk_empty.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="notif_menu"))
        try:
            if message:
                bot.edit_message_text(
                    "🔔 Yeni bildiriş yoxdur.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk_empty,
                )
            else:
                bot.send_message(
                    chat_id, "🔔 Yeni bildiriş yoxdur.", reply_markup=mk_empty
                )
        except Exception:
            pass
        return

    unseen_ids = [r.get("id") for r in rows if r.get("status") == "new"]
    if unseen_ids:
        conn = get_local_conn()
        cur = conn.cursor()
        placeholders = ",".join(["?"] * len(unseen_ids))
        cur.execute(
            f"UPDATE user_notifications SET status='seen' WHERE id IN ({placeholders})",
            unseen_ids,
        )
        conn.commit()
        conn.close()

    listings = []
    for r in rows:
        listing = fetch_listing_for_notification(r.get("listing_id"))
        if listing:
            listings.append(listing)

    if not listings:
        mk_empty = types.InlineKeyboardMarkup()
        mk_empty.add(
            types.InlineKeyboardButton("⚙️ Kriteriyalar", callback_data="notif_crit")
        )
        mk_empty.add(
            types.InlineKeyboardButton(
                "🔔 Açar söz bildirişləri", callback_data="notif_kw_hits"
            )
        )
        mk_empty.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="notif_menu"))
        text = "🔔 Bildiriş elanları tapılmadı."
        try:
            if message:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk_empty,
                )
            else:
                bot.send_message(chat_id, text, reply_markup=mk_empty)
        except Exception:
            pass
        return

    start_index = max(
        0,
        min((page - 1) * PAGE_SIZE_NOTIFICATIONS, len(listings) - 1),
    )
    loading_ref = (message.chat.id, message.message_id) if message else None
    start_listing_session(
        chat_id,
        mode="notifications",
        params={"period": period},
        items=listings,
        start_index=start_index,
        loading_ref=loading_ref,
        track_view=False,
    )


def show_agent_notifications_inbox(chat_id: int, page: int = 1, message=None):
    bot.send_message(chat_id, "🔔 Sorğu bildirişləri deaktiv edilib.")
    return
    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM agent_notifications WHERE agent_chat_id=?", (chat_id,)
    )
    total = cur.fetchone()[0] or 0

    if total == 0:
        conn.close()
        mk_empty = types.InlineKeyboardMarkup()
        mk_empty.add(
            types.InlineKeyboardButton(
                "🏠 Elan bildirişləri", callback_data="notif_menu"
            )
        )
        mk_empty.add(
            types.InlineKeyboardButton(
                "🎯 Müştəri istəkləri", callback_data="agent_requests"
            )
        )
        try:
            if message:
                bot.edit_message_text(
                    "🔔 Müştəri istəyi bildirişi yoxdur.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk_empty,
                )
            else:
                bot.send_message(
                    chat_id,
                    "🔔 Müştəri istəyi bildirişi yoxdur.",
                    reply_markup=mk_empty,
                )
        except Exception:
            pass
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE_NOTIFICATIONS))
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_NOTIFICATIONS

    cur.execute(
        """
        SELECT an.id as notif_id, an.request_id, an.created_at as notif_created_at,
               an.status as notif_status, cr.*
        FROM agent_notifications an
        JOIN customer_requests cr ON cr.id = an.request_id
        WHERE an.agent_chat_id=?
        ORDER BY datetime(an.created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (chat_id, PAGE_SIZE_NOTIFICATIONS, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]

    unseen_ids = [r.get("notif_id") for r in rows if r.get("notif_status") == "new"]
    if unseen_ids:
        placeholders = ",".join(["?"] * len(unseen_ids))
        cur.execute(
            f"UPDATE agent_notifications SET status='seen' WHERE id IN ({placeholders})",
            unseen_ids,
        )
        conn.commit()
    conn.close()

    header_lines = [
        "🔔 Müştəri istəkləri",
        f"Səhifə: {page} / {total_pages}",
        f"Cəmi: {total}",
    ]
    mk = types.InlineKeyboardMarkup()
    nav_buttons = [
        types.InlineKeyboardButton("⏮ İlk", callback_data="agent_notif:1"),
        types.InlineKeyboardButton(
            "◀️ Geri", callback_data=f"agent_notif:{max(1, page - 1)}"
        ),
        types.InlineKeyboardButton(
            f"📄 {page} / {total_pages}", callback_data=f"agent_notif:{page}"
        ),
        types.InlineKeyboardButton(
            "▶️ İrəli", callback_data=f"agent_notif:{min(total_pages, page + 1)}"
        ),
        types.InlineKeyboardButton("⏭ Son", callback_data=f"agent_notif:{total_pages}"),
    ]
    mk.row(*nav_buttons)
    mk.add(
        types.InlineKeyboardButton("🏠 Elan bildirişləri", callback_data="notif_menu")
    )
    mk.add(
        types.InlineKeyboardButton("👥 Mənim müştərilərim", callback_data="agt_my:1")
    )

    header_text = "\n".join(header_lines)
    try:
        if message:
            bot.edit_message_text(
                header_text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, header_text, reply_markup=mk)
    except Exception:
        pass

    for r in rows:
        card = format_agent_request_card(r)
        mk_card = types.InlineKeyboardMarkup()
        wa_url = make_whatsapp_url(
            r.get("phone"), "Salam, müştəri sorğunuz ilə maraqlanıram."
        )
        if wa_url:
            mk_card.add(types.InlineKeyboardButton("💬 WhatsApp yaz", url=wa_url))
        mk_card.add(
            types.InlineKeyboardButton(
                "✅ Maraqlanıram", callback_data=f"agt_int:{r.get('request_id')}"
            )
        )
        try:
            bot.send_message(chat_id, card, reply_markup=mk_card)
        except Exception:
            continue


def send_criteria_list(chat_id: int, message=None):
    rows = get_saved_searches(chat_id)
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ Yeni bildiriş qaydası", callback_data="notif_rule_new"
        )
    )
    if not rows:
        mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="crit_alert_menu"))
        try:
            if message:
                bot.edit_message_text(
                    "⚙️ Saxlanılmış kriteriya yoxdur.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
            else:
                bot.send_message(
                    chat_id,
                    "⚙️ Saxlanılmış kriteriya yoxdur.",
                    reply_markup=mk,
                )
        except Exception:
            pass
        return

    for row in rows:
        row = row or {}
        cid = _row_value_safe(row, "id")
        status_flag = _row_value_safe(row, "is_active", 1)
        status_txt = "🟢 Aktiv" if status_flag else "⚪️ Deaktiv"
        descr = format_saved_search_entry(row)
        mk.add(
            types.InlineKeyboardButton(
                f"{status_txt} | {descr}", callback_data=f"crit_toggle:{cid}"
            )
        )
        mk.add(types.InlineKeyboardButton("🗑 Sil", callback_data=f"crit_del:{cid}"))

    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="crit_alert_menu"))
    try:
        if message:
            bot.edit_message_text(
                "⚙️ Bildiriş kriteriyaları:",
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, "⚙️ Bildiriş kriteriyaları:", reply_markup=mk)
    except Exception:
        pass


def build_notification_rule_operation_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Satılır", "🏢 Kirayə")
    kb.row("⬅️ Geri")
    return kb


def build_notification_rayon_markup(
    selected: List[str],
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    row = []
    selected_set = {s.lower() for s in selected}
    for rayon in ALL_RAYONS:
        label = f"✅ {rayon}" if rayon.lower() in selected_set else rayon
        row.append(
            types.InlineKeyboardButton(
                label, callback_data=f"notif_rule_rayon_toggle:{quote(rayon)}"
            )
        )
        if len(row) == 3:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(
        types.InlineKeyboardButton("✅ Bitdi", callback_data="notif_rule_rayon_done")
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="notif_rule_rayon_back"))
    return mk


def send_notification_rayon_prompt(chat_id: int, message=None):
    selected = notification_rule_state.get(chat_id, {}).get("rayons", [])
    mk = build_notification_rayon_markup(selected)
    text = "📍 Rayon seçin (bir neçəsini seçə bilərsiniz):"
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def build_notification_property_type_markup() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton("Mənzil", callback_data="notif_rule_prop:m"),
        types.InlineKeyboardButton("Bağ evi", callback_data="notif_rule_prop:b"),
    )
    mk.row(
        types.InlineKeyboardButton("Torpaq", callback_data="notif_rule_prop:t"),
        types.InlineKeyboardButton("Hamısı", callback_data="notif_rule_prop:all"),
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="notif_rule_prop_back"))
    return mk


def send_notification_property_type_prompt(chat_id: int, message=None):
    text = "🏠 Əmlak növü seçin:"
    mk = build_notification_property_type_markup()
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def start_notification_rule_flow(chat_id: int):
    # Notification rules have their own flow and do not reuse search filters.
    notification_rule_state[chat_id] = {"step": "operation"}
    bot.send_message(
        chat_id,
        "🔔 Bildiriş qaydası üçün əməliyyat növünü seçin:",
        reply_markup=build_notification_rule_operation_keyboard(),
    )


def save_notification_rule(user_id: int, data: dict) -> Optional[int]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO saved_searches (
            chat_id, operation, rooms, price_min, price_max, rayon, prop_type,
            created_at, last_notified_at, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            data.get("operation"),
            data.get("rooms"),
            data.get("price_min"),
            data.get("price_max"),
            data.get("rayon"),
            data.get("prop_type"),
            datetime.utcnow().isoformat(),
            None,
        ),
    )
    rule_id = cur.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def show_keyword_alert_menu(chat_id: int, message=None):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("➕ Açar söz əlavə et", callback_data="kw_alert_add")
    )
    mk.add(
        types.InlineKeyboardButton("📋 Açar sözlərim", callback_data="kw_alert_list:1")
    )
    mk.add(
        types.InlineKeyboardButton("🔔 Gələn bildirişlər", callback_data="kw_hits_menu")
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_alert_back"))
    text = "📌 Açar söz bildirişləri"
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def show_criteria_alert_menu(chat_id: int, message=None):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("⚙️ Kriteriyalarım", callback_data="crit_alert_list")
    )
    mk.add(
        types.InlineKeyboardButton("🔔 Gələn bildirişlər", callback_data="crit_hits_menu")
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="notif_menu"))
    text = "🎯 Kriteriya bildirişləri"
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def save_keyword_alert(user_id: int, keyword: str, regions: List[str]) -> int:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO keyword_alerts (user_id, keywords, regions, is_active, created_at)
        VALUES (?, ?, ?, 1, ?)
        """,
        (
            user_id,
            keyword.strip(),
            ", ".join([r.strip() for r in regions if r.strip()]),
            datetime.utcnow().isoformat(),
        ),
    )
    alert_id = cur.lastrowid
    conn.commit()
    conn.close()
    return alert_id


def fetch_keyword_alerts_page(user_id: int, page: int = 1):
    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM keyword_alerts WHERE user_id=?", (user_id,))
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_NOTIFICATIONS))
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_NOTIFICATIONS
    cur.execute(
        """
        SELECT * FROM keyword_alerts
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, PAGE_SIZE_NOTIFICATIONS, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total, total_pages, page


def toggle_keyword_alert(user_id: int, alert_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT is_active FROM keyword_alerts WHERE id=? AND user_id=?",
        (alert_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    is_active = _row_value_safe(row, "is_active", row[0] if len(row) > 0 else None)
    new_val = 0 if str(is_active) in {"1", "True", "true"} else 1
    cur.execute(
        "UPDATE keyword_alerts SET is_active=? WHERE id=? AND user_id=?",
        (new_val, alert_id, user_id),
    )
    conn.commit()
    conn.close()
    return True


def delete_keyword_alert(user_id: int, alert_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM keyword_alerts WHERE id=? AND user_id=?",
        (alert_id, user_id),
    )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def format_keyword_alert_entry(row: dict) -> str:
    keyword = _row_value_safe(row, "keywords") or "-"
    regions = _row_value_safe(row, "regions") or "Hamısı"
    status_txt = (
        "🟢 Aktiv"
        if str(_row_value_safe(row, "is_active", 1)) in {"1", "True", "true"}
        else "⚪️ Deaktiv"
    )
    return f"{status_txt} | 🔎 {keyword} | 📍 {regions}"


def show_keyword_alert_list(chat_id: int, page: int = 1, message=None):
    rows, total, total_pages, current_page = fetch_keyword_alerts_page(chat_id, page)
    if total == 0:
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➕ Açar söz əlavə et", callback_data="kw_alert_add"
            )
        )
        mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_alert_menu"))
        try:
            if message:
                bot.edit_message_text(
                    "🔔 Açar söz bildirişi yoxdur.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
            else:
                bot.send_message(
                    chat_id, "🔔 Açar söz bildirişi yoxdur.", reply_markup=mk
                )
        except Exception:
            pass
        return

    header = f"🔔 Açar sözlər\nSəhifə: {current_page} / {total_pages}\nCəmi: {total}"
    mk = types.InlineKeyboardMarkup()
    nav_buttons = [
        types.InlineKeyboardButton("⏮ İlk", callback_data="kw_alert_list:1"),
        types.InlineKeyboardButton(
            "◀️ Geri", callback_data=f"kw_alert_list:{max(1, current_page - 1)}"
        ),
        types.InlineKeyboardButton(
            f"📄 {current_page} / {total_pages}",
            callback_data=f"kw_alert_list:{current_page}",
        ),
        types.InlineKeyboardButton(
            "▶️ İrəli",
            callback_data=f"kw_alert_list:{min(total_pages, current_page + 1)}",
        ),
        types.InlineKeyboardButton(
            "⏭ Son", callback_data=f"kw_alert_list:{total_pages}"
        ),
    ]
    mk.row(*nav_buttons)
    mk.add(
        types.InlineKeyboardButton("➕ Açar söz əlavə et", callback_data="kw_alert_add")
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_alert_menu"))
    try:
        if message:
            bot.edit_message_text(
                header,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, header, reply_markup=mk)
    except Exception:
        pass

    for row in rows:
        alert_id = _row_value_safe(row, "id")
        entry = format_keyword_alert_entry(row)
        mk_row = types.InlineKeyboardMarkup()
        mk_row.row(
            types.InlineKeyboardButton(
                "🔄 Aktiv/Deaktiv", callback_data=f"kw_alert_toggle:{alert_id}"
            ),
            types.InlineKeyboardButton(
                "🗑 Sil", callback_data=f"kw_alert_delete:{alert_id}"
            ),
        )
        try:
            bot.send_message(chat_id, entry, reply_markup=mk_row)
        except Exception:
            continue


def build_keyword_alert_rayon_markup(selected: List[str]):
    mk = types.InlineKeyboardMarkup()
    row = []
    for rayon in REQUEST_RAYONS:
        label = f"✅ {rayon}" if rayon in selected else rayon
        row.append(
            types.InlineKeyboardButton(
                label, callback_data=f"kw_alert_rayon_toggle:{quote(rayon)}"
            )
        )
        if len(row) == 3:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(
        types.InlineKeyboardButton("✅ Tamamla", callback_data="kw_alert_rayon_done")
    )
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_alert_rayon_back"))
    return mk


def send_keyword_alert_rayon_prompt(chat_id: int, message=None):
    selected = keyword_alert_state.get(chat_id, {}).get("regions", [])
    text = "📍 Açar söz üçün rayonları seçin (çoxlu seçim mümkündür):"
    mk = build_keyword_alert_rayon_markup(selected)
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def period_start(period: str) -> Optional[datetime]:
    now = datetime.now()
    if period == "today":
        return datetime(now.year, now.month, now.day)
    if period == "week":
        start_day = now - timedelta(days=now.weekday())
        return datetime(start_day.year, start_day.month, start_day.day)
    if period == "month":
        return datetime(now.year, now.month, 1)
    if period == "older":
        return datetime(now.year, now.month, 1)
    return None


def build_period_filter(period: Optional[str], column: str, allow_older: bool = False):
    period = period or "today"
    start_dt = period_start(period)
    if not start_dt:
        return "", []
    if allow_older and period == "older":
        return f" AND datetime({column}) < datetime(?)", [start_dt.isoformat()]
    return f" AND datetime({column}) >= datetime(?)", [start_dt.isoformat()]


def fetch_keyword_alert_hits_page(user_id: int, period: str, page: int = 1):
    period = period or "today"
    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    params = [user_id]
    where = "kah.user_id=? AND kah.target_type='listing'"
    period_clause, period_params = build_period_filter(
        period, "kah.created_at", allow_older=True
    )
    where += period_clause
    params.extend(period_params)
    cur.execute(
        f"SELECT COUNT(DISTINCT kah.target_id) FROM keyword_alert_hits kah WHERE {where}",
        params,
    )
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_NOTIFICATIONS))
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_NOTIFICATIONS
    cur.execute(
        f"""
        SELECT kah.target_id as listing_id, kah.source, kah.created_at
        FROM keyword_alert_hits kah
        JOIN (
            SELECT target_id, MAX(datetime(created_at)) as max_created_at
            FROM keyword_alert_hits
            WHERE {where}
            GROUP BY target_id
        ) latest ON latest.target_id = kah.target_id
        WHERE {where} AND datetime(kah.created_at) = latest.max_created_at
        ORDER BY datetime(kah.created_at) DESC
        LIMIT ? OFFSET ?
        """,
        params + params + [PAGE_SIZE_NOTIFICATIONS, offset],
    )
    rows_raw = [dict(r) for r in cur.fetchall()]
    rows = []
    seen_ids: Set[str] = set()
    for r in rows_raw:
        listing_id = r.get("listing_id") or r.get("target_id")
        if listing_id is None:
            continue
        key = str(listing_id)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        rows.append(r)
    conn.close()
    return rows, total, total_pages, page


def fetch_keyword_alert_hits(user_id: int, period: str) -> List[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        where = "kah.user_id=? AND kah.target_type='listing'"
        period_clause, period_params = build_period_filter(period, "kah.created_at")
        params = [user_id] + period_params
        where += period_clause
        cur.execute(
            f"SELECT COUNT(DISTINCT kah.target_id) FROM keyword_alert_hits kah WHERE {where}",
            params,
        )
        total = cur.fetchone()[0] or 0
        cur.execute(
            f"""
            SELECT kah.target_id as listing_id, kah.source, kah.created_at
            FROM keyword_alert_hits kah
            WHERE {where}
            ORDER BY datetime(kah.created_at) DESC
            """,
            params,
        )
        rows_raw = [dict(r) for r in cur.fetchall()]
        rows: List[dict] = []
        seen_ids: Set[str] = set()
        for r in rows_raw:
            listing_id = r.get("listing_id") or r.get("target_id")
            if listing_id is None:
                continue
            key = str(listing_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            rows.append(r)
    except sqlite3.OperationalError:
        rows = []
        total = 0
    finally:
        conn.close()
    return rows, total


def load_notification_listings(chat_id: int, notif_type: str, period: str) -> List[dict]:
    listings: List[dict] = []
    if notif_type == "keyword":
        rows, _ = fetch_keyword_alert_hits(chat_id, period)
        for row in rows:
            source = _row_value_safe(row, "source") or "main"
            target_id = _row_value_safe(row, "listing_id") or _row_value_safe(
                row, "target_id"
            )
            try:
                target_id_int = int(target_id)
            except Exception:
                continue
            listing = fetch_listing_by_source(source, target_id_int)
            if listing:
                listing["__source"] = source
                listings.append(listing)
    elif notif_type == "criteria":
        rows = fetch_notification_listings(chat_id, period)
        for row in rows:
            listing = fetch_listing_for_notification(row.get("listing_id"))
            if listing:
                listings.append(listing)

    return listings


def render_notification_listing(
    chat_id: int,
    message,
    listings: List[dict],
    notif_type: str,
    period: str,
    index: int,
    back_callback: str,
):
    total = len(listings)
    if total == 0:
        return
    idx = max(0, min(index, total - 1))
    listing = listings[idx]
    source = listing.get("__source", "main")

    phone = listing.get("phone") or listing.get("Elaqe_nomresi")
    wa_message = build_whatsapp_message(listing)
    wa_url = make_whatsapp_url(phone, wa_message)
    link = listing.get("link") or listing.get("source_link")

    favorite_label = (
        "⭐ Favoriyə əlavə et" if listing.get("id") is not None else None
    )
    favorite_callback = (
        f"fav|{source}|{listing['id']}" if listing.get("id") is not None else None
    )

    mk = build_listing_action_keyboard(
        favorite_label,
        favorite_callback,
        link,
        wa_url,
    )

    if total > 1:
        prev_cb = (
            f"notif_view:{notif_type}:{period}:{idx - 1}"
            if idx > 0
            else "notif_noop"
        )
        next_cb = (
            f"notif_view:{notif_type}:{period}:{idx + 1}"
            if idx < total - 1
            else "notif_noop"
        )
        mk.row(
            types.InlineKeyboardButton("⬅️ Əvvəlki", callback_data=prev_cb),
            types.InlineKeyboardButton("➡️ Növbəti", callback_data=next_cb),
        )

    mk.add(types.InlineKeyboardButton("🔙 Bildirişlərə qayıt", callback_data=back_callback))

    progress_text = f"🔔 Bildiriş {idx + 1} / {total}"
    text = build_listing_text(listing, source, progress_text=progress_text)

    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        try:
            bot.send_message(chat_id, text, reply_markup=mk)
        except Exception:
            pass


def show_keyword_alert_hits(chat_id: int, period: str, index: int = 0, message=None):
    rows, total = fetch_keyword_alert_hits(chat_id, period)
    period_labels = {
        "today": "Bu gün",
        "week": "Bu həftə",
        "month": "Bu ay",
    }
    period_label = period_labels.get(period, "Bu gün")
    if total == 0:
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_hits_menu"))
        text = f"🔔 {period_label} üçün bildiriş yoxdur."
        try:
            if message:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
            else:
                bot.send_message(chat_id, text, reply_markup=mk)
        except Exception:
            pass
        return

    listings = load_notification_listings(chat_id, "keyword", period)

    if not listings:
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_hits_menu"))
        try:
            if message:
                bot.edit_message_text(
                    f"🔔 {period_label} üçün bildiriş elanları tapılmadı.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
            else:
                bot.send_message(
                    chat_id,
                    f"🔔 {period_label} üçün bildiriş elanları tapılmadı.",
                    reply_markup=mk,
                )
        except Exception:
            pass
        return

    render_notification_listing(
        chat_id,
        message,
        listings,
        notif_type="keyword",
        period=period,
        index=index,
        back_callback="kw_hits_menu",
    )


def show_keyword_hits_filter_menu(chat_id: int, message=None):
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Bu gün", callback_data="kw_hits:today:0"))
    mk.add(types.InlineKeyboardButton("Bu həftə", callback_data="kw_hits:week:0"))
    mk.add(types.InlineKeyboardButton("Bu ay", callback_data="kw_hits:month:0"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kw_alert_menu"))
    text = "🔔 Açar söz bildirişləri\n\n🗓 Zaman aralığı seç:"
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def show_criteria_hits_filter_menu(chat_id: int, message=None):
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Bu gün", callback_data="crit_hits:today:0"))
    mk.add(types.InlineKeyboardButton("Bu həftə", callback_data="crit_hits:week:0"))
    mk.add(types.InlineKeyboardButton("Bu ay", callback_data="crit_hits:month:0"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="crit_alert_menu"))
    text = "🎯 Kriteriya bildirişləri\n\n🗓 Zaman aralığı seç:"
    try:
        if message:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, text, reply_markup=mk)
    except Exception:
        pass


def show_criteria_alert_hits(chat_id: int, period: str, index: int = 0, message=None):
    rows = fetch_notification_listings(chat_id, period)
    total = len(rows)
    period_labels = {
        "today": "Bu gün",
        "week": "Bu həftə",
        "month": "Bu ay",
    }
    period_label = period_labels.get(period, "Bu gün")

    if total == 0:
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="crit_hits_menu"))
        text = f"🔔 {period_label} üçün bildiriş yoxdur."
        try:
            if message:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
            else:
                bot.send_message(chat_id, text, reply_markup=mk)
        except Exception:
            pass
        return

    listings = load_notification_listings(chat_id, "criteria", period)

    if not listings:
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="crit_hits_menu"))
        try:
            if message:
                bot.edit_message_text(
                    f"🔔 {period_label} üçün bildiriş elanları tapılmadı.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
            else:
                bot.send_message(
                    chat_id,
                    f"🔔 {period_label} üçün bildiriş elanları tapılmadı.",
                    reply_markup=mk,
                )
        except Exception:
            pass
        return

    render_notification_listing(
        chat_id,
        message,
        listings,
        notif_type="criteria",
        period=period,
        index=index,
        back_callback="crit_hits_menu",
    )


def set_saved_search_active(chat_id: int, criteria_id: int, active: bool):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE saved_searches SET is_active=? WHERE id=? AND chat_id=?",
        (1 if active else 0, criteria_id, chat_id),
    )
    conn.commit()
    conn.close()


def delete_saved_search_for_user(chat_id: int, criteria_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM saved_searches WHERE id=? AND chat_id=?", (criteria_id, chat_id)
    )
    conn.commit()
    conn.close()


@bot.message_handler(
    func=lambda m: not is_admin(m.chat.id) and m.text == ADMIN_PANEL_BACK_MAIN
)
def public_back_to_main(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    return_to_main_menu(message.chat.id)


@bot.message_handler(
    func=lambda m: m.text in {"🔔 Bildirişlər", "🔔 Bildirişlərim"}
)
def show_saved_notifications(message):
    if message.text and message.text.startswith('/'):
        return

    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    show_notifications_menu(chat_id)


@bot.callback_query_handler(func=lambda c: c.data == "kw_alert_menu")
@callback_guard
def cb_keyword_alert_menu(c):
    if not ensure_allowed_cb(c):
        return
    show_keyword_alert_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "crit_alert_menu")
@callback_guard
def cb_criteria_alert_menu(c):
    if not ensure_allowed_cb(c):
        return
    show_criteria_alert_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "crit_alert_list")
@callback_guard
def cb_criteria_alert_list(c):
    if not ensure_allowed_cb(c):
        return
    send_criteria_list(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "kw_alert_back")
@callback_guard
def cb_keyword_alert_back(c):
    if not ensure_allowed_cb(c):
        return
    show_notifications_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "kw_alert_add")
@callback_guard
def cb_keyword_alert_add(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    keyword_alert_state[chat_id] = {"step": "keyword", "regions": []}
    msg = bot.send_message(chat_id, "🔎 Açar sözü yazın:")
    bot.register_next_step_handler(msg, handle_keyword_alert_keyword)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def handle_keyword_alert_keyword(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if not text:
        msg = bot.send_message(chat_id, "⚠️ Açar söz boş ola bilməz. Yenidən yazın:")
        bot.register_next_step_handler(msg, handle_keyword_alert_keyword)
        return
    state = keyword_alert_state.get(chat_id, {})
    state["keyword"] = text
    state["step"] = "regions"
    keyword_alert_state[chat_id] = state
    send_keyword_alert_rayon_prompt(chat_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("kw_alert_rayon_"))
@callback_guard
def cb_keyword_alert_rayon(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    state = keyword_alert_state.get(chat_id)
    if not state or state.get("step") != "regions":
        return
    action = c.data.split(":", 1)[0].replace("kw_alert_rayon_", "")
    if action == "toggle":
        try:
            rayon = unquote(c.data.split(":", 1)[1])
        except Exception:
            rayon = c.data.split(":", 1)[1]
        selected = state.get("regions", [])
        if rayon in selected:
            selected.remove(rayon)
        else:
            selected.append(rayon)
        state["regions"] = selected
        keyword_alert_state[chat_id] = state
        send_keyword_alert_rayon_prompt(chat_id, message=c.message)
    elif action == "done":
        selected = state.get("regions", [])
        if not selected:
            bot.answer_callback_query(
                c.id, "⚠️ Ən azı bir rayon seçin.", show_alert=True
            )
            return
        keyword = (state.get("keyword") or "").strip()
        keyword_alert_state.pop(chat_id, None)
        alert_id = save_keyword_alert(chat_id, keyword, selected)
        bot.send_message(chat_id, f"✅ Açar söz əlavə edildi (ID: {alert_id}).")
        process_keyword_alerts_for_existing_requests(alert_id)
        show_keyword_alert_menu(chat_id)
    elif action == "back":
        keyword_alert_state.pop(chat_id, None)
        show_keyword_alert_menu(chat_id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("kw_alert_list:"))
@callback_guard
def cb_keyword_alert_list(c):
    if not ensure_allowed_cb(c):
        return
    try:
        page = int(c.data.split(":", 1)[1])
    except Exception:
        page = 1
    show_keyword_alert_list(c.message.chat.id, page=page, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("kw_alert_toggle:"))
@callback_guard
def cb_keyword_alert_toggle(c):
    if not ensure_allowed_cb(c):
        return
    try:
        alert_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    toggle_keyword_alert(c.message.chat.id, alert_id)
    show_keyword_alert_list(c.message.chat.id, page=1, message=c.message)
    try:
        bot.answer_callback_query(c.id, "✅ Yeniləndi")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("kw_alert_delete:"))
@callback_guard
def cb_keyword_alert_delete(c):
    if not ensure_allowed_cb(c):
        return
    try:
        alert_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    deleted = delete_keyword_alert(c.message.chat.id, alert_id)
    if deleted:
        try:
            bot.answer_callback_query(c.id, "🗑 Silindi")
        except Exception:
            pass
    show_keyword_alert_list(c.message.chat.id, page=1, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data == "kw_hits_menu")
@callback_guard
def cb_keyword_hits_menu(c):
    if not ensure_allowed_cb(c):
        return
    keyword_hits_context[c.message.chat.id] = {"back": "kw_alert_menu"}
    show_keyword_hits_filter_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "crit_hits_menu")
@callback_guard
def cb_criteria_hits_menu(c):
    if not ensure_allowed_cb(c):
        return
    show_criteria_hits_filter_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("kw_hits:"))
@callback_guard
def cb_keyword_hits(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split(":")
    if len(parts) < 3:
        return
    period = parts[1]
    try:
        index = int(parts[2])
    except Exception:
        index = 0
    show_keyword_alert_hits(
        c.message.chat.id, period, index=index, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("crit_hits:"))
@callback_guard
def cb_criteria_hits(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split(":")
    if len(parts) < 3:
        return
    period = parts[1]
    try:
        index = int(parts[2])
    except Exception:
        index = 0
    show_criteria_alert_hits(
        c.message.chat.id, period, index=index, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("ss|"))
@callback_guard
def cb_search_select(c):
    if not ensure_allowed_cb(c):
        return
    mode = c.data.split("|", 1)[1]
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
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("save_search|"))
@callback_guard
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


@bot.callback_query_handler(func=lambda c: c.data.startswith("notif_open:"))
@callback_guard
def cb_open_notifications(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split(":")
    period = None
    page = 1
    if len(parts) == 3:
        period = parts[1]
        try:
            page = int(parts[2])
        except Exception:
            page = 1
    else:
        try:
            page = int(parts[1])
        except Exception:
            page = 1
    period = period or notification_menu_state.get(c.message.chat.id, {}).get(
        "period", "today"
    )
    show_notifications_inbox(
        c.message.chat.id, period=period, page=page, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "kw_notif_view")
@callback_guard
def cb_keyword_notification_view(c):
    chat_id = c.message.chat.id if c.message else None
    ctx = keyword_notification_state.get(chat_id or 0, {}) if chat_id else {}
    items = ctx.get("items") or []
    if not items:
        bot.send_message(chat_id, "⚠️ Baxmaq üçün yeni elan yoxdur.")
        return
    send_paginated_results(chat_id, mode="keyword_notif", params={}, page=1)


@bot.callback_query_handler(func=lambda c: c.data == "notif_menu")
@callback_guard
def cb_notifications_menu(c):
    if not ensure_allowed_cb(c):
        return
    show_notifications_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "notif_back")
@callback_guard
def cb_notifications_back(c):
    if not ensure_allowed_cb(c):
        return
    return_to_main_menu(c.message.chat.id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("notif_period:"))
@callback_guard
def cb_notifications_period(c):
    if not ensure_allowed_cb(c):
        return
    period = c.data.split(":", 1)[1]
    notification_menu_state[c.message.chat.id] = {"period": period}
    show_notifications_inbox(
        c.message.chat.id, period=period, page=1, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "notif_kw_hits")
@callback_guard
def cb_notifications_keyword_hits(c):
    if not ensure_allowed_cb(c):
        return
    show_keyword_alert_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "notif_cust_req")
@callback_guard
def cb_notifications_customer_requests(c):
    return
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        try:
            bot.answer_callback_query(
                c.id, "❌ Bu funksiya sizin üçün aktiv deyil", show_alert=True
            )
        except Exception:
            pass
        return
    period = notification_menu_state.get(chat_id, {}).get("period", "today")
    show_customer_request_alerts_inbox(
        chat_id, period=period, page=1, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("notif_view:"))
@callback_guard
def cb_notifications_view_listing(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split(":")
    if len(parts) < 4:
        return
    notif_type = parts[1]
    period = parts[2]
    try:
        index = int(parts[3])
    except Exception:
        index = 0

    if notif_type == "keyword":
        show_keyword_alert_hits(
            c.message.chat.id, period, index=index, message=c.message
        )
    elif notif_type == "criteria":
        show_criteria_alert_hits(
            c.message.chat.id, period, index=index, message=c.message
        )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "notif_noop")
@callback_guard
def cb_notification_noop(c):
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("agent_notif:"))
@callback_guard
def cb_agent_notifications(c):
    if not ensure_allowed_cb(c):
        return
    bot.send_message(c.message.chat.id, "🔔 Sorğu bildirişləri deaktiv edilib.")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "notif_crit")
@callback_guard
def cb_notif_criteria(c):
    if not ensure_allowed_cb(c):
        return
    show_criteria_alert_menu(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "notif_rule_new")
@callback_guard
def cb_notif_rule_new(c):
    if not ensure_allowed_cb(c):
        return
    start_notification_rule_flow(c.message.chat.id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.message_handler(
    func=lambda m: notification_rule_state.get(m.chat.id, {}).get("step") == "operation"
)
def handle_notification_rule_operation(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if text == "⬅️ Geri":
        notification_rule_state.pop(chat_id, None)
        show_notifications_menu(chat_id)
        return
    if text not in {"🏠 Satılır", "🏢 Kirayə"}:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa seçim edin.")
        return
    operation = "sale" if "Satılır" in text else "rent"
    notification_rule_state[chat_id] = {
        "step": "rayon",
        "operation": operation,
        "rayons": [],
    }
    send_notification_rayon_prompt(chat_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("notif_rule_rayon_"))
@callback_guard
def cb_notification_rule_rayon(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    state = notification_rule_state.get(chat_id)
    if not state or state.get("step") != "rayon":
        return
    action = c.data.split(":", 1)[0].replace("notif_rule_rayon_", "")
    if action == "toggle":
        try:
            rayon = unquote(c.data.split(":", 1)[1])
        except Exception:
            rayon = c.data.split(":", 1)[1]
        selected = state.get("rayons", [])
        if rayon in selected:
            selected.remove(rayon)
        else:
            selected.append(rayon)
        state["rayons"] = selected
        send_notification_rayon_prompt(chat_id, message=c.message)
    elif action == "done":
        selected = state.get("rayons", [])
        if not selected:
            bot.answer_callback_query(
                c.id, "⚠️ Ən azı bir rayon seçin.", show_alert=True
            )
            return
        state["step"] = "prop_type"
        send_notification_property_type_prompt(chat_id, message=c.message)
    elif action == "back":
        state["step"] = "operation"
        bot.send_message(
            chat_id,
            "🔔 Bildiriş qaydası üçün əməliyyat növünü seçin:",
            reply_markup=build_notification_rule_operation_keyboard(),
        )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("notif_rule_prop"))
@callback_guard
def cb_notification_rule_prop(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    state = notification_rule_state.get(chat_id)
    if not state or state.get("step") != "prop_type":
        return
    if c.data == "notif_rule_prop_back":
        state["step"] = "rayon"
        send_notification_rayon_prompt(chat_id, message=c.message)
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return
    prop_code = c.data.split(":", 1)[1]
    prop_map = {"m": "Mənzil", "b": "Bağ evi", "t": "Torpaq", "all": None}
    state["prop_type"] = prop_map.get(prop_code)
    state["step"] = "min_price"
    notification_rule_state[chat_id] = state
    bot.send_message(
        chat_id,
        "💰 Minimum qiymət yazın (istəyə görə):",
        reply_markup=build_optional_input_keyboard(),
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.message_handler(
    func=lambda m: notification_rule_state.get(m.chat.id, {}).get("step") == "min_price"
)
def handle_notification_rule_min_price(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    state = notification_rule_state.get(chat_id, {})
    if text == "⬅️ Geri":
        state["step"] = "rayon"
        notification_rule_state[chat_id] = state
        send_notification_rayon_prompt(chat_id)
        return
    if text == "⚪️ Keç":
        state["price_min"] = None
    else:
        value = parse_number(text)
        if value is None:
            bot.send_message(chat_id, "⚠️ Minimum qiyməti rəqəm ilə yazın.")
            return
        state["price_min"] = int(value)
    state["step"] = "max_price"
    notification_rule_state[chat_id] = state
    bot.send_message(
        chat_id,
        "💰 Maksimum qiymət yazın (istəyə görə):",
        reply_markup=build_optional_input_keyboard(),
    )


@bot.message_handler(
    func=lambda m: notification_rule_state.get(m.chat.id, {}).get("step") == "max_price"
)
def handle_notification_rule_max_price(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    state = notification_rule_state.get(chat_id, {})
    if text == "⬅️ Geri":
        state["step"] = "rayon"
        notification_rule_state[chat_id] = state
        send_notification_rayon_prompt(chat_id)
        return
    if text == "⚪️ Keç":
        state["price_max"] = None
    else:
        value = parse_number(text)
        if value is None:
            bot.send_message(chat_id, "⚠️ Maksimum qiyməti rəqəm ilə yazın.")
            return
        state["price_max"] = int(value)
    prop_type = state.get("prop_type")
    prop_type_norm = normalize_property_type_ui_value(prop_type)
    if prop_type_norm in {"Mənzil", "Bağ evi"} or prop_type_norm is None:
        state["step"] = "rooms"
        notification_rule_state[chat_id] = state
        bot.send_message(
            chat_id,
            "🛏 Otaq sayı yazın (istəyə görə):",
            reply_markup=build_optional_input_keyboard(),
        )
        return
    state["rooms"] = None
    finalize_notification_rule(chat_id)


@bot.message_handler(
    func=lambda m: notification_rule_state.get(m.chat.id, {}).get("step") == "rooms"
)
def handle_notification_rule_rooms(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    text = (message.text or "").strip()
    state = notification_rule_state.get(chat_id, {})
    if text == "⬅️ Geri":
        state["step"] = "rayon"
        notification_rule_state[chat_id] = state
        send_notification_rayon_prompt(chat_id)
        return
    if text == "⚪️ Keç":
        state["rooms"] = None
    else:
        value = parse_number(text)
        if value is None:
            bot.send_message(chat_id, "⚠️ Otaq sayını rəqəm ilə yazın.")
            return
        state["rooms"] = int(value)
    finalize_notification_rule(chat_id)


def finalize_notification_rule(chat_id: int):
    state = notification_rule_state.pop(chat_id, {})
    if not state:
        return
    data = {
        "operation": state.get("operation"),
        "rooms": state.get("rooms"),
        "price_min": state.get("price_min"),
        "price_max": state.get("price_max"),
        "rayon": ", ".join(state.get("rayons") or []),
        "prop_type": state.get("prop_type"),
    }
    rule_id = save_notification_rule(chat_id, data)
    bot.send_message(chat_id, f"✅ Bildiriş qaydası yaradıldı (ID: {rule_id}).")
    send_criteria_list(chat_id)
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    send_with_reply_keyboard(
        chat_id,
        "🏠 Əsas menyu açıqdır.",
        build_main_menu(
            is_admin(chat_id),
            has_customer_requests_access(chat_id),
            should_show_bonus_button(chat_id),
        ),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("notif_stopcrit:"))
@callback_guard
def cb_stop_criteria(c):
    if not ensure_allowed_cb(c):
        return
    try:
        criteria_id = int(c.data.split(":")[1])
    except Exception:
        criteria_id = None

    if criteria_id:
        set_saved_search_active(c.message.chat.id, criteria_id, False)
        try:
            bot.send_message(c.message.chat.id, "✅ Kriteriya dayandırıldı.")
        except Exception:
            pass
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("crit_toggle:"))
@callback_guard
def cb_toggle_criteria(c):
    if not ensure_allowed_cb(c):
        return
    try:
        criteria_id = int(c.data.split(":")[1])
    except Exception:
        criteria_id = None

    if criteria_id:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT is_active FROM saved_searches WHERE id=? AND chat_id=?",
            (criteria_id, c.message.chat.id),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            current = row[0] if isinstance(row, tuple) else row["is_active"]
            set_saved_search_active(c.message.chat.id, criteria_id, not bool(current))
    send_criteria_list(c.message.chat.id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("crit_del:"))
@callback_guard
def cb_delete_criteria(c):
    if not ensure_allowed_cb(c):
        return
    try:
        criteria_id = int(c.data.split(":")[1])
    except Exception:
        criteria_id = None

    if criteria_id:
        delete_saved_search_for_user(c.message.chat.id, criteria_id)
    send_criteria_list(c.message.chat.id, message=c.message)
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


def send_keyword_date_prompt(chat_id: int, message=None):
    st = search_state.setdefault(chat_id, {})
    op = st.get("operation")
    mk = types.InlineKeyboardMarkup()
    options = get_date_range_options(op)
    row = []
    for label, code in options:
        row.append(types.InlineKeyboardButton(label, callback_data=f"kwdr|{code}"))
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="kwdr|back"))
    text = "📆 Tarix aralığını seçin:"
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("kwop|"))
@callback_guard
def cb_keyword_operation(c):
    if not ensure_allowed_cb(c):
        return
    action = c.data.split("|")[1]
    chat_id = c.message.chat.id

    if action == "back":
        search_state[chat_id] = {
            "mode": "keyword",
            "operation": None,
            "date_selected": False,
        }
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
    st.update(
        {"mode": "keyword", "operation": normalize_operation_value(action) or action}
    )
    search_state[chat_id] = st
    send_keyword_date_prompt(chat_id, c.message)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("kwdr|"))
@callback_guard
def cb_keyword_date_range(c):
    if not ensure_allowed_cb(c):
        return
    action = c.data.split("|")[1]
    chat_id = c.message.chat.id

    if action == "back":
        search_state[chat_id] = {
            "mode": "keyword",
            "operation": None,
            "date_selected": False,
        }
        send_keyword_operation_prompt(chat_id)
        try:
            bot.answer_callback_query(c.id)
        except:
            pass
        return

    date_days = DATE_RANGE_DAYS.get(action)
    st = search_state.get(chat_id, {})
    st.update({"date_days": date_days, "date_selected": True})
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

PROP_TYPE_MAP = {
    "mənzil": "Mənzil",
    "menzil": "Mənzil",
    "həyət evi": "Həyət evi",
    "ferdi yasayis evi": "Həyət evi",
    "fərdi yaşayış evi": "Həyət evi",
    "obyekt / ofis": "Obyekt / Ofis",
    "obyekt": "Obyekt / Ofis",
    "ofis": "Obyekt / Ofis",
    "qeyri yaşayış sahəsi": "Obyekt / Ofis",
    "bağ evi": "Bağ evi",
    "torpaq": "Torpaq",
}

PROPERTY_TYPE_NORMALIZATION_MAP: Dict[str, str] = {}
PROPERTY_TYPE_VARIANTS: Dict[str, Set[str]] = {}
for variant, canonical in PROP_TYPE_MAP.items():
    key = str(variant).strip().lower()
    if not key:
        continue
    PROPERTY_TYPE_NORMALIZATION_MAP[key] = canonical
    PROPERTY_TYPE_VARIANTS.setdefault(canonical, set()).add(key)

for canonical in set(PROP_TYPE_MAP.values()):
    base_key = str(canonical).strip().lower()
    if base_key:
        PROPERTY_TYPE_NORMALIZATION_MAP.setdefault(base_key, canonical)
        PROPERTY_TYPE_VARIANTS.setdefault(canonical, set()).add(base_key)

PROPERTY_TYPE_MAP = {
    canonical: sorted(list(variants)) for canonical, variants in PROPERTY_TYPE_VARIANTS.items()
}

PROP_TYPES = {
    "all": None,
    "m": "Mənzil",
    "f": "Həyət evi",
    "q": "Obyekt / Ofis",
    "b": "Bağ evi",
    "t": "Torpaq",
    "d": "Digər",
}


def normalize_property_type_ui_value(raw_value: Optional[str]) -> Optional[str]:
    if raw_value is None:
        return None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None
    normalized = PROPERTY_TYPE_NORMALIZATION_MAP.get(cleaned.lower())
    return normalized


def resolve_property_type_from_code(prop_code: Optional[str]) -> Optional[str]:
    if not prop_code or prop_code == "all":
        return None
    mapped = PROP_TYPES.get(prop_code)
    normalized = normalize_property_type_ui_value(mapped or prop_code)
    return normalized


def get_property_type_filter_values(
    prop_code: Optional[str] = None, ui_value: Optional[str] = None
) -> Tuple[Optional[str], List[str]]:
    ui_choice = normalize_property_type_ui_value(ui_value)
    if ui_choice is None:
        ui_choice = resolve_property_type_from_code(prop_code)
    if not ui_choice:
        return None, []
    db_values = PROPERTY_TYPE_MAP.get(ui_choice, [])
    logger.debug("Filtering prop_type UI='%s' → DB_VALUES=%s", ui_choice, db_values)
    normalized_values = [str(v).strip().lower() for v in db_values if str(v).strip()]
    return ui_choice, normalized_values

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


def resolve_operation_filter(op_code: Optional[str], mode: str) -> Optional[str]:
    if not op_code or op_code == "all":
        return None

    if op_code == "sat":
        op_norm = "sale"
    elif op_code == "kir":
        op_norm = "rent"
    else:
        op_norm = normalize_operation_value(op_code) or op_code

    source = "main" if mode == "main" else "local"
    return detect_db_operation_value(op_norm, source)


def build_filters_sql(
    op_code, prop_code, rayon_group, min_price=None, max_price=None, mode="main"
):
    sql = " WHERE 1=1"
    params = []

    # Əməliyyat
    op_value = resolve_operation_filter(op_code, mode)
    if op_value:
        sql += " AND operation = ?"
        params.append(op_value)

    # Əmlak tipi
    _, prop_values = get_property_type_filter_values(prop_code)
    if prop_values:
        placeholders = ",".join(["?"] * len(prop_values))
        sql += f" AND LOWER(prop_type) IN ({placeholders})"
        params.extend(prop_values)

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
ROOM_CODES = [
    ("1", "r1"),
    ("2", "r2"),
    ("3", "r3"),
    ("4", "r4"),
    ("5+", "r5"),
    ("Hamısı", "r0"),
]
FLOOR_PRESETS = {"f13": (1, 3), "f49": (4, 9), "f10": (10, None), "fall": None}
DATE_RANGE_DAYS = {"d7": 7, "d30": 30, "d60": 60, "d90": 90, "all": None}


def structured_send(chat_id, message, text, markup):
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
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


def normalize_date_range_op(op_code: Optional[str]) -> str:
    if op_code in {"kir", "rent"}:
        return "kir"
    if op_code in {"sat", "sale"}:
        return "sat"
    return "sat"


def get_date_range_options(op_code: Optional[str]):
    op = normalize_date_range_op(op_code)
    if op == "kir":
        return [
            ("📆 Son 1 həftə", "d7"),
            ("📆 Son 1 ay", "d30"),
            ("📦 Hamısı", "all"),
        ]
    return [
        ("📆 Son 1 ay", "d30"),
        ("📆 Son 2 ay", "d60"),
        ("📆 Son 3 ay", "d90"),
        ("📦 Hamısı", "all"),
    ]


def is_within_date_range(ev: dict, date_days: Optional[Union[int, str]]) -> bool:
    ev_dt = extract_listing_datetime(ev)
    if date_days == "today":
        if not ev_dt:
            return False
        return ev_dt.date() == datetime.utcnow().date()

    if not date_days:
        return True
    if not ev_dt:
        return False
    cutoff = datetime.utcnow() - timedelta(days=date_days)
    return ev_dt >= cutoff


def build_back_reply_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⬅️ Geri")
    return kb


def parse_floor_value(ev: dict):
    for k in ("floor", "Floor", "Mertebe", "mertebe"):
        if ev.get(k):
            num = parse_number(ev.get(k))
            if num is not None:
                return num
    text = ev.get("summary") or ev.get("Umumi_melumat") or ""
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


def normalize_rayon_name(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def normalize_region(value: str) -> str:
    if not value:
        return ""
    v = value.lower()
    v = re.sub(r"(rayonu|rayon|r\.|r)", "", v)
    v = (
        v.replace("ə", "e")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
        .replace("ı", "i")
    )
    v = re.sub(r"[^a-z0-9 ]", "", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip()


def normalize_rayon(value: str) -> str:
    return normalize_region(value)


def normalize_today_rayon(value: Optional[str]) -> str:
    return normalize_rayon(value or "")


def extract_today_rayon_candidates(ev: dict) -> List[str]:
    address_raw = ev.get("address") or ev.get("Unvan") or ""
    address_first = address_raw.split(",")[0]
    candidates = [
        ev.get("rayon"),
        ev.get("Rayon_Qesebe"),
        ev.get("Rayon"),
        address_first,
    ]
    return [normalize_today_rayon(c) for c in candidates if normalize_today_rayon(c)]


def matches_today_rayon(ev: dict, filters: dict) -> bool:
    rayon = normalize_today_rayon(filters.get("rayon"))
    if not rayon or rayon in {"all", "hamısı"}:
        return True
    candidates = extract_today_rayon_candidates(ev)
    return any(candidate == rayon or rayon in candidate for candidate in candidates)


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


def compute_stats(
    conn: sqlite3.Connection,
    source_table: Optional[str],
    ts_col: Optional[str],
    ts_kind: str,
    op_col: Optional[str],
    type_col: Optional[str],
    window: str,
    stat_context: str = STAT_CONTEXT_USER,
) -> Dict[str, Any]:
    cur = conn.cursor()
    stats: Dict[str, Any] = {
        "total": 0,
        "sale_count": 0,
        "rent_count": 0,
        "apartment_count": 0,
        "house_count": 0,
        "land_count": 0,
        "meta": {
            "table": source_table,
            "ts_col": ts_col,
            "ts_kind": ts_kind or "none",
            "op_col": op_col,
            "type_col": type_col,
        },
    }

    if (
        stat_context == STAT_CONTEXT_USER
        and source_table
        and str(source_table).lower().endswith("_approved")
    ):
        raise RuntimeError("User stats must not use approved tables")

    if not source_table:
        stats["note"] = "Tarix məlumatı yoxdur, son 0 elan"
        return stats

    if ts_col and ts_kind in {"unix", "iso"}:
        try:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{source_table}_{ts_col} ON {source_table}({ts_col})"
            )
        except Exception:
            pass

    op_expr = f"LOWER(TRIM(COALESCE(l.\"{op_col}\", '')))" if op_col else None
    type_expr = f"LOWER(TRIM(COALESCE(l.\"{type_col}\", '')))" if type_col else None

    select_parts = ["COUNT(*) AS total"]
    params: List[Any] = []

    if op_expr:
        sale_vals = STATS_OPERATION_BUCKETS["satilir"]
        rent_vals = STATS_OPERATION_BUCKETS["kiraye"]
        select_parts.append(
            f"SUM(CASE WHEN {op_expr} IN ({','.join(['?']*len(sale_vals))}) THEN 1 ELSE 0 END) AS sale_count"
        )
        params += sale_vals
        select_parts.append(
            f"SUM(CASE WHEN {op_expr} IN ({','.join(['?']*len(rent_vals))}) THEN 1 ELSE 0 END) AS rent_count"
        )
        params += rent_vals
    else:
        select_parts.extend(["0 AS sale_count", "0 AS rent_count"])

    if type_expr:
        apt_vals = STATS_PROPERTY_BUCKETS["menzil"]
        house_vals = STATS_PROPERTY_BUCKETS["heyet_evi"]
        land_vals = STATS_PROPERTY_BUCKETS["torpaq"]
        select_parts.append(
            f"SUM(CASE WHEN {type_expr} IN ({','.join(['?']*len(apt_vals))}) THEN 1 ELSE 0 END) AS apartment_count"
        )
        params += apt_vals
        select_parts.append(
            f"SUM(CASE WHEN {type_expr} IN ({','.join(['?']*len(house_vals))}) THEN 1 ELSE 0 END) AS house_count"
        )
        params += house_vals
        select_parts.append(
            f"SUM(CASE WHEN {type_expr} IN ({','.join(['?']*len(land_vals))}) THEN 1 ELSE 0 END) AS land_count"
        )
        params += land_vals
    else:
        select_parts.extend(
            ["0 AS apartment_count", "0 AS house_count", "0 AS land_count"]
        )

    where_clauses = []
    where_params: List[Any] = []
    use_recent = False
    if window == "24h":
        start, end = get_last_24h_window()
        if ts_col and ts_kind == "unix":
            where_clauses.append(
                f"COALESCE(l.\"{ts_col}\", 0) >= ? AND COALESCE(l.\"{ts_col}\", 0) < ?"
            )
            where_params.extend([int(start.timestamp()), int(end.timestamp())])
        elif ts_col and ts_kind == "iso":
            where_clauses.append(
                f"datetime(l.\"{ts_col}\") >= datetime(?) AND datetime(l.\"{ts_col}\") < datetime(?)"
            )
            where_params.extend(
                [format_sqlite_datetime(start), format_sqlite_datetime(end)]
            )
        else:
            use_recent = True
            stats["note"] = f"Tarix məlumatı yoxdur, son {STATS_RECENT_LIMIT} elan"
    else:
        window_days = {"7d": 7, "30d": 30}.get(window)
        if window != "all" and window_days:
            if ts_col and ts_kind == "unix":
                seconds = window_days * 24 * 3600
                where_clauses.append(
                    f"COALESCE(l.\"{ts_col}\", 0) >= (strftime('%s','now') - ?)"
                )
                where_params.append(seconds)
            elif ts_col and ts_kind == "iso":
                where_clauses.append(
                    f"datetime(l.\"{ts_col}\") >= datetime('now', ?)"
                )
                where_params.append(f"-{window_days} days")
            else:
                use_recent = True
                stats["note"] = f"Tarix məlumatı yoxdur, son {STATS_RECENT_LIMIT} elan"

    cols = get_table_columns(cur, source_table)
    order_col = None
    for candidate in ("id", "listing_id"):
        if candidate in cols:
            order_col = cols[candidate]
            break
    if not order_col:
        order_col = "ROWID"

    from_clause = f"{source_table} l"
    if use_recent:
        from_clause = (
            f"(SELECT * FROM {source_table} ORDER BY {order_col} DESC LIMIT {STATS_RECENT_LIMIT}) l"
        )

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = f"SELECT {', '.join(select_parts)} FROM {from_clause}{where_sql}"

    cur.execute(query, params + where_params)
    row = cur.fetchone() or {}
    stats.update(
        {
            "total": _row_value_safe(row, "total", 0) or 0,
            "sale_count": _row_value_safe(row, "sale_count", 0) or 0,
            "rent_count": _row_value_safe(row, "rent_count", 0) or 0,
            "apartment_count": _row_value_safe(row, "apartment_count", 0) or 0,
            "house_count": _row_value_safe(row, "house_count", 0) or 0,
            "land_count": _row_value_safe(row, "land_count", 0) or 0,
        }
    )
    return stats


def compute_user_statistics(period: str) -> dict:
    key_base = period if period in {"24h", "7d", "30d", "all"} else "all"
    cache_key = f"{STAT_CONTEXT_USER}:{key_base}"
    now_ts = time.time()
    cached = statistics_cache.get(cache_key)
    cache_ts = cached.get("ts", 0) if cached else 0
    if cached and now_ts - cache_ts < STATISTICS_CACHE_TTL_SECONDS:
        return cached.get("data", {})

    stats: Dict[str, Any] = {
        "total": 0,
        "sale_count": 0,
        "rent_count": 0,
        "prop_type_counts": {},
        "meta": {
            "table": "listings",
            "ts_col": "created_at",
            "ts_kind": "iso",
            "op_col": "operation",
            "type_col": "prop_type",
        },
    }

    if not os.path.exists(MAIN_DB):
        logger.warning("User stats DB missing path=%s", MAIN_DB)
        return stats

    conn = None
    try:
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        )
        if not cur.fetchone():
            logger.warning("User stats table not found in besthome.db")
            return stats

        try:
            cur.execute("PRAGMA table_info(listings)")
            col_rows = cur.fetchall() or []
        except Exception:
            col_rows = []

        col_names = {str(r[1]).lower(): r[1] for r in col_rows if len(r) > 1}

        ts_col = col_names.get("created_at")
        if not ts_col:
            logger.warning("User stats timestamp column missing in listings table")

        op_col = col_names.get("operation")
        type_col = col_names.get("prop_type")

        stats["meta"] = {
            "table": "listings",
            "ts_col": ts_col,
            "ts_kind": "iso" if ts_col else "none",
            "op_col": op_col,
            "type_col": type_col,
        }

        logger.info(
            "USER_STATS source_db=%s source_table=listings period=%s",
            MAIN_DB,
            key_base,
        )

        op_expr = f"l.\"{op_col}\"" if op_col else None
        type_expr = f"l.\"{type_col}\"" if type_col else None

        select_parts = ["COUNT(*) AS total"]
        params: List[Any] = []

        if op_expr:
            select_parts.append(
                f"SUM(CASE WHEN {op_expr} = ? THEN 1 ELSE 0 END) AS sale_count"
            )
            select_parts.append(
                f"SUM(CASE WHEN {op_expr} = ? THEN 1 ELSE 0 END) AS rent_count"
            )
            params.extend(["Satılır", "Kirayə verilir"])
        else:
            select_parts.extend(["0 AS sale_count", "0 AS rent_count"])

        where_clauses: List[str] = []
        where_params: List[Any] = []

        if key_base == "24h" and ts_col:
            start, end = get_last_24h_window()
            where_clauses.append(
                f"datetime(l.\"{ts_col}\") >= datetime(?) AND datetime(l.\"{ts_col}\") < datetime(?)"
            )
            where_params.extend(
                [format_sqlite_datetime(start), format_sqlite_datetime(end)]
            )
        elif key_base != "all" and ts_col:
            window_days = {"7d": 7, "30d": 30}.get(key_base)
            if window_days:
                where_clauses.append(
                    f"datetime(l.\"{ts_col}\") >= datetime('now', ?)"
                )
                where_params.append(f"-{window_days} days")
        elif key_base != "all" and not ts_col:
            stats["note"] = "Tarix məlumatı yoxdur, zaman filtri tətbiq edilmədi"

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"SELECT {', '.join(select_parts)} FROM listings l{where_sql}"

        cur.execute(query, params + where_params)
        row = cur.fetchone() or {}
        stats.update(
            {
                "total": _row_value_safe(row, "total", 0) or 0,
                "sale_count": _row_value_safe(row, "sale_count", 0) or 0,
                "rent_count": _row_value_safe(row, "rent_count", 0) or 0,
            }
        )

        if type_expr:
            prop_query = (
                f"SELECT {type_expr} AS prop_type, COUNT(*) AS cnt FROM listings l{where_sql} "
                "GROUP BY prop_type ORDER BY prop_type"
            )
            cur.execute(prop_query, where_params)
            prop_counts = {}
            for r in cur.fetchall() or []:
                key = r["prop_type"] if isinstance(r, dict) else r[0]
                if key in (None, ""):
                    continue
                prop_counts[str(key)] = r["cnt"] if isinstance(r, dict) else r[1]
            stats["prop_type_counts"] = prop_counts
    except Exception:
        logger.exception("Failed to compute user statistics")
        return {}
    finally:
        if conn:
            try:
                close_main_conn(conn)
            except Exception:
                pass

    statistics_cache[cache_key] = {"ts": now_ts, "data": stats}
    logger.debug("USER STATS source=listings prop_type=dynamic operation=exact_string")
    _log_user_stats_consistency()
    return stats


def _log_user_stats_consistency():
    required = ["24h", "7d", "30d", "all"]
    available = {}
    for key in required:
        cached = statistics_cache.get(f"{STAT_CONTEXT_USER}:{key}")
        if cached and cached.get("data"):
            available[key] = cached["data"]
    if set(required).issubset(set(available.keys())):
        total = available["all"].get("total", 0)
        d30 = available["30d"].get("total", 0)
        d7 = available["7d"].get("total", 0)
        d24 = available["24h"].get("total", 0)
        if not (d24 <= d7 <= d30 <= total):
            logger.warning(
                "WARN USER_STATS inconsistency detected total=%s 30d=%s 7d=%s 24h=%s",
                total,
                d30,
                d7,
                d24,
            )


def _build_market_ts_clause(ts_col: Optional[str], ts_kind: str, days: int):
    if not ts_col or days <= 0:
        return "", []
    if ts_kind == "unix":
        return (
            f" AND COALESCE(l.\"{ts_col}\", 0) >= (strftime('%s','now') - ?)",
            [days * 24 * 3600],
        )
    if ts_kind == "iso":
        return (
            f" AND datetime(l.\"{ts_col}\") >= datetime('now', ?)",
            [f"-{days} days"],
        )
    return "", []


def _market_dom_expr(ts_col: Optional[str], ts_kind: str) -> Optional[str]:
    if not ts_col:
        return None
    if ts_kind == "unix":
        return (
            f"((strftime('%s','now') - COALESCE(l.\"{ts_col}\", strftime('%s','now'))) / 86400.0)"
        )
    if ts_kind == "iso":
        return f"(julianday('now') - julianday(l.\"{ts_col}\"))"
    return None


def compute_market_pulse() -> List[Dict[str, Any]]:
    now_ts = time.time()
    cached = market_pulse_cache.get("data")
    cache_ts = market_pulse_cache.get("ts", 0)
    if cached is not None and now_ts - cache_ts < MARKET_PULSE_CACHE_TTL_SECONDS:
        return cached

    results: List[Dict[str, Any]] = []
    started = time.perf_counter()
    status_tag = "ok"

    if not os.path.exists(MAIN_DB):
        status_tag = "missing_db"
    else:
        conn = None
        try:
            conn = get_main_conn()
            cur = conn.cursor()
            meta = detect_stats_source(cur, STAT_CONTEXT_USER)
            table = meta.get("table")
            ts_col = meta.get("ts_col")
            ts_kind = meta.get("ts_kind", "none")
            if not table or not ts_col:
                status_tag = "missing_table"
            else:
                cols = get_table_columns(cur, table) or {}
                rayon_col = next(
                    (cols.get(key) for key in ("rayon", "district", "region", "area") if key in cols),
                    None,
                )
                price_col = next(
                    (
                        cols.get(key)
                        for key in (
                            "price",
                            "qiymet",
                            "qiymət",
                            "price_azn",
                            "price_az",
                            "amount",
                        )
                        if key in cols
                    ),
                    None,
                )
                dom_col = next(
                    (cols.get(key) for key in ("days_on_market", "dom", "market_days") if key in cols),
                    None,
                )
                op_col = meta.get("op_col")
                type_col = meta.get("type_col")

                if not rayon_col:
                    status_tag = "missing_rayon"
                else:
                    seven_clause, seven_params = _build_market_ts_clause(ts_col, ts_kind, 7)
                    thirty_clause, thirty_params = _build_market_ts_clause(ts_col, ts_kind, 30)

                    op_filter = f" AND COALESCE(l.\"{op_col}\", '') != ''" if op_col else ""
                    type_filter = f" AND COALESCE(l.\"{type_col}\", '') != ''" if type_col else ""

                    rayon_base_where = (
                        f"WHERE l.\"{rayon_col}\" IS NOT NULL AND l.\"{rayon_col}\" != ''"
                        f"{thirty_clause}{op_filter}{type_filter}"
                    )
                    cur.execute(
                        f"SELECT DISTINCT l.\"{rayon_col}\" AS rayon FROM {table} l {rayon_base_where} ORDER BY rayon",
                        thirty_params,
                    )
                    rayons = [
                        r[0] if not isinstance(r, dict) else r.get("rayon") for r in cur.fetchall() or []
                    ]

                    dom_expr = _market_dom_expr(ts_col, ts_kind) if not dom_col else None

                    for rayon in rayons:
                        base_params = [rayon]

                        cur.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} l WHERE l.\"{rayon_col}\"=?{seven_clause}{op_filter}{type_filter}",
                            base_params + seven_params,
                        )
                        row = cur.fetchone() or {}
                        new_count = _row_value_safe(row, "cnt", 0) or 0

                        avg_price_7 = None
                        avg_price_30 = None
                        if price_col:
                            cur.execute(
                                f"SELECT AVG(CAST(l.\"{price_col}\" AS REAL)) AS avg_price "
                                f"FROM {table} l WHERE l.\"{rayon_col}\"=?{seven_clause}{op_filter}{type_filter}",
                                base_params + seven_params,
                            )
                            price_row = cur.fetchone() or {}
                            avg_price_7 = _row_value_safe(price_row, "avg_price")

                            cur.execute(
                                f"SELECT AVG(CAST(l.\"{price_col}\" AS REAL)) AS avg_price "
                                f"FROM {table} l WHERE l.\"{rayon_col}\"=?{thirty_clause}{op_filter}{type_filter}",
                                base_params + thirty_params,
                            )
                            price_row = cur.fetchone() or {}
                            avg_price_30 = _row_value_safe(price_row, "avg_price")

                        avg_dom = None
                        if dom_col:
                            cur.execute(
                                f"SELECT AVG(CAST(l.\"{dom_col}\" AS REAL)) AS avg_dom "
                                f"FROM {table} l WHERE l.\"{rayon_col}\"=?{thirty_clause}{op_filter}{type_filter}",
                                base_params + thirty_params,
                            )
                            dom_row = cur.fetchone() or {}
                            avg_dom = _row_value_safe(dom_row, "avg_dom")
                        elif dom_expr:
                            cur.execute(
                                f"SELECT AVG({dom_expr}) AS avg_dom FROM {table} l "
                                f"WHERE l.\"{rayon_col}\"=?{thirty_clause}{op_filter}{type_filter}",
                                base_params + thirty_params,
                            )
                            dom_row = cur.fetchone() or {}
                            avg_dom = _row_value_safe(dom_row, "avg_dom")

                        if avg_dom is None:
                            speed = "orta"
                        elif avg_dom < MARKET_PULSE_SPEED_THRESHOLDS[0]:
                            speed = "yüksək"
                        elif avg_dom < MARKET_PULSE_SPEED_THRESHOLDS[1]:
                            speed = "orta"
                        else:
                            speed = "zəif"

                        price_trend = "→ stabil"
                        if avg_price_7 is not None and avg_price_30 not in (None, 0):
                            if avg_price_7 > avg_price_30:
                                price_trend = "↑ qalxır"
                            elif avg_price_7 < avg_price_30:
                                price_trend = "↓ düşür"

                        results.append(
                            {
                                "rayon": str(rayon) if rayon is not None else "-",
                                "new_count": new_count,
                                "speed": speed,
                                "price_trend": price_trend,
                            }
                        )

        except Exception:
            status_tag = "error"
            logger.exception("Failed to compute market pulse")
        finally:
            if conn:
                try:
                    close_main_conn(conn)
                except Exception:
                    pass

    duration = time.perf_counter() - started
    logger.info(
        "Market pulse computation duration=%.3fs rayons=%s status=%s",
        duration,
        len(results),
        status_tag,
    )
    market_pulse_cache["ts"] = now_ts
    market_pulse_cache["data"] = results
    return results


def fetch_global_statistics(period: str = "all", stat_context: str = STAT_CONTEXT_USER) -> dict:
    key_base = period if period in {"24h", "7d", "30d", "all"} else "all"
    cache_key = f"{stat_context}:{key_base}"

    now_ts = time.time()
    cached = statistics_cache.get(cache_key)
    cache_ts = cached.get("ts", 0) if cached else 0
    if cached and now_ts - cache_ts < STATISTICS_CACHE_TTL_SECONDS:
        return cached.get("data", {})

    started = time.time()
    conn = None
    try:
        if stat_context == STAT_CONTEXT_USER:
            if not os.path.exists(MAIN_DB):
                logger.warning("User stats DB missing path=%s", MAIN_DB)
                return {}
            conn = get_main_conn()
            source_db = MAIN_DB
        else:
            conn = get_local_conn()
            source_db = LOCAL_DB

        cur = conn.cursor()
        if stat_context == STAT_CONTEXT_USER:
            meta = detect_stats_source(cur, "user")
        else:
            meta = detect_stats_source(cur, "admin")
        if stat_context == STAT_CONTEXT_USER:
            logger.info(
                "USER_STATS source_db=%s table=%s", source_db, meta.get("table")
            )
        stats = compute_stats(
            conn,
            meta.get("table"),
            meta.get("ts_col"),
            meta.get("ts_kind", "none"),
            meta.get("op_col"),
            meta.get("type_col"),
            key_base,
            stat_context=stat_context,
        )
    except Exception:
        logger.exception("Failed to compute %s statistics", stat_context)
        return {}
    finally:
        if conn:
            try:
                if stat_context == STAT_CONTEXT_USER:
                    close_main_conn(conn)
                else:
                    conn.close()
            except Exception:
                pass

    elapsed_ms = (time.time() - started) * 1000
    logger.info("Global statistics (%s) computed in %.1f ms", key_base, elapsed_ms)

    statistics_cache[cache_key] = {"ts": now_ts, "data": stats}
    if stat_context == STAT_CONTEXT_USER:
        _log_user_stats_consistency()
    return stats


def _listing_price_value(ev: dict):
    return parse_number(ev.get("price") or ev.get("Qiymet"))


def matches_saved_search(ev: dict, saved: dict) -> bool:
    # Notification matching uses its own rules (not search query parsing).

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
        rayons = [normalize_text(r) for r in str(rayon_filter).split(",") if r.strip()]
        text_block = normalize_text(
            " ".join(
                [
                    str(ev.get("rayon") or ""),
                    str(ev.get("Rayon_Qesebe") or ""),
                    str(ev.get("address") or ""),
                    str(ev.get("Unvan") or ""),
                    str(ev.get("summary") or ""),
                ]
            )
        )
        if rayons and not any(r in text_block for r in rayons):
            return False

    prop_filter = saved.get("prop_type")
    if prop_filter:
        _, prop_values = get_property_type_filter_values(ui_value=prop_filter)
        prop_text = str(ev.get("prop_type") or ev.get("Emlakin_novu") or "").lower()
        if prop_values:
            if prop_text not in prop_values:
                return False
        elif prop_filter.lower() not in prop_text:
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

    now_iso = datetime.utcnow().isoformat()

    for s in searches:
        if str(s.get("is_active", 1)) in {"0", "False", "false"}:
            continue
        since_raw = s.get("last_notified_at") or s.get("created_at")
        try:
            since_dt = (
                datetime.fromisoformat(str(since_raw)) if since_raw else datetime.min
            )
        except Exception:
            since_dt = datetime.min

        candidates = load_recent_listings(since_dt)
        matches = [ev for ev in candidates if matches_saved_search(ev, s)]

        if not matches:
            continue

        listing_ids = []
        for ev in matches:
            listing_id = ev.get("id") or ev.get("ID") or ev.get("Elan_kodu")
            if listing_id is None:
                continue
            try:
                listing_ids.append(int(listing_id))
            except Exception:
                continue

        new_count = ensure_notification_records(s["chat_id"], s.get("id"), listing_ids)

        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE saved_searches SET last_notified_at=? WHERE id=?",
            (now_iso, s.get("id")),
        )
        conn.commit()
        conn.close()

        if new_count <= 0:
            continue

        text = (
            f"🔔 Axtardığınız kriteriyaya uyğun {new_count} yeni elan tapıldı — "
            "Bildirişlər bölməsinə baxın"
        )
        mk = types.InlineKeyboardMarkup()
        mk.row(
            types.InlineKeyboardButton("👀 Elanları gör", callback_data="notif_menu"),
            types.InlineKeyboardButton("⚙️ Kriteriyalar", callback_data="notif_crit"),
        )
        if s.get("id"):
            mk.add(
                types.InlineKeyboardButton(
                    "❌ Bu kriteriyanı dayandır",
                    callback_data=f"notif_stopcrit:{s['id']}",
                )
            )

        try:
            bot.send_message(s["chat_id"], text, reply_markup=mk)
        except Exception as e:
            print("⚠️ Notification send error:", e)


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
    date_days = filters.get("date_days")
    min_p = filters.get("min_price")
    max_p = filters.get("max_price")
    if min_p is None and max_p is None:
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
        date_col = detect_table_date_column(cur, "listings")
        date_sql, date_params = build_date_range_clause(date_col, date_days)
        cur.execute(
            base + flt + date_sql + " ORDER BY date_read DESC, id DESC",
            params + date_params,
        )
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
    date_col = detect_table_date_column(cur, "listings_approved")
    date_sql, date_params = build_date_range_clause(date_col, date_days)
    cur.execute(
        base + flt + date_sql + " ORDER BY date_added DESC, id DESC",
        params + date_params,
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    filtered = []
    for ev in results:
        if not is_within_date_range(ev, date_days):
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


def query_today_results(filters: dict, offset: int = 0, limit: int = None):
    op_code = filters.get("op", "all")
    prop_code = filters.get("prop", "all")
    results = []
    window = get_last_24h_window()
    logger.info(
        "today query start filters=%s offset=%s limit=%s", filters, offset, limit
    )

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        base = "SELECT * FROM listings"
        flt, params = build_filters_sql(op_code, prop_code, None, mode="main")
        date_col = detect_table_date_column(cur, "listings")
        date_sql, date_params = build_today_clause(date_col, window)
        rayon_sql, rayon_params = build_rayon_filter_sql(
            cur, "listings", filters.get("rayon"), ""
        )
        order_col = date_col or "date_read"
        where_sql = flt + date_sql + rayon_sql
        logger.debug(
            "today query main where=%s params=%s",
            where_sql,
            params + date_params + rayon_params,
        )
        cur.execute(
            base + where_sql + f" ORDER BY {order_col} DESC, id DESC",
            params + date_params + rayon_params,
        )
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        close_main_conn(conn)

    conn = get_local_conn()
    cur = conn.cursor()
    base = "SELECT * FROM listings_approved"
    flt, params = build_filters_sql(op_code, prop_code, None, mode="local")
    date_col = detect_table_date_column(cur, "listings_approved")
    date_sql, date_params = build_today_clause(date_col, window)
    rayon_sql, rayon_params = build_rayon_filter_sql(
        cur, "listings_approved", filters.get("rayon"), ""
    )
    order_col = date_col or "date_added"
    where_sql = flt + date_sql + rayon_sql
    logger.debug(
        "today query local where=%s params=%s",
        where_sql,
        params + date_params + rayon_params,
    )
    cur.execute(
        base + where_sql + f" ORDER BY {order_col} DESC, id DESC",
        params + date_params + rayon_params,
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    start, end = window
    filtered = []
    for ev in results:
        ev_dt = safe_date(ev)
        if ev_dt == datetime.min:
            continue
        if not (start <= ev_dt < end):
            continue
        if not matches_today_rayon(ev, filters):
            continue
        filtered.append(ev)

    filtered.sort(key=safe_date, reverse=True)
    total = len(filtered)
    logger.info(
        "today query filtered count=%s op=%s rayon=%s",
        total,
        op_code,
        filters.get("rayon"),
    )
    if limit is not None:
        filtered = filtered[offset : offset + limit]
    return filtered, total


def count_today_filtered(filters: dict) -> int:
    _, total = query_today_results(filters, offset=0, limit=None)
    return total


def is_fts_ready(conn, table_name: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not cur.fetchone():
            return False
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        return (cur.fetchone() or [0])[0] > 0
    except Exception:
        return False


def build_smart_fts_queries(search_text: str) -> Tuple[Optional[str], Optional[str]]:
    return build_fts_queries(search_text)


def build_fts_queries(q: str) -> Tuple[Optional[str], Optional[str]]:
    normalized = normalize_text((q or "").strip())
    if not normalized:
        return None, None
    tokens = [t for t in normalized.split() if t]
    if not tokens:
        return None, None
    phrase = f'"{normalized}"'
    and_query = " AND ".join([f"{t}*" for t in tokens])
    return phrase, and_query


def execute_ranked_fts(
    cur: sqlite3.Cursor,
    table: str,
    fts_table: str,
    where_clause: str,
    params: List[Any],
    phrase_query: Optional[str],
    token_query: Optional[str],
    or_query: Optional[str] = None,
    order_suffix: str = "",
    limit_rows: int = 5000,
) -> List[sqlite3.Row]:
    results: List[sqlite3.Row] = []
    seen: set = set()
    queries = []
    if phrase_query:
        queries.append((0, "phrase", phrase_query))
    if token_query:
        queries.append((1, "and", token_query))
    if or_query:
        queries.append((2, "or", or_query))

    for priority, mode, match_q in queries:
        if results:
            break
        q_started = time.perf_counter()
        base_sql = (
            f"SELECT l.*, ? AS priority_tag, COALESCE(bm25(f), 0) AS rank_score "
            f"FROM {table} l "
            f"JOIN {fts_table} f ON l.id = f.rowid "
            f"WHERE {where_clause} AND {fts_table} MATCH ? "
            f"ORDER BY priority_tag ASC, rank_score ASC{order_suffix} "
            f"LIMIT ?"
        )
        try:
            cur.execute(base_sql, (*params, priority, match_q, limit_rows))
        except Exception:
            fallback_sql = (
                f"SELECT l.*, ? AS priority_tag, 0 AS rank_score "
                f"FROM {table} l "
                f"JOIN {fts_table} f ON l.id = f.rowid "
                f"WHERE {where_clause} AND {fts_table} MATCH ? "
                f"ORDER BY priority_tag ASC{order_suffix} "
                f"LIMIT ?"
            )
            cur.execute(fallback_sql, (*params, priority, match_q, limit_rows))

        for row in cur.fetchall():
            try:
                rid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            except Exception:
                rid = None
            if rid is not None and rid in seen:
                continue
            if rid is not None:
                seen.add(rid)
            results.append(row)
        logger.info(
            "FTS search time_ms=%.2f mode=%s q=%s rows=%s",
            (time.perf_counter() - q_started) * 1000,
            mode,
            match_q,
            len(results),
        )
    return results


def query_keyword_results(
    selected_op: str,
    words: list,
    date_days: Optional[int] = None,
    offset: int = 0,
    limit: int = None,
):
    search_text = " ".join([w for w in words if w])
    tokens = normalize_text(search_text).split()
    if not tokens:
        return [], 0
    phrase_query, token_query = build_fts_queries(search_text)
    or_query = " OR ".join([f"{t}*" for t in tokens]) if tokens else None

    op_main = detect_db_operation_value(selected_op, "main")
    op_local = detect_db_operation_value(selected_op, "local")

    results = []
    search_started_at = time.perf_counter()

    def apply_date_clause_sql(table: str, date_col: Optional[str]) -> Tuple[str, list]:
        if not date_days or not date_col:
            return "", []
        cutoff = datetime.utcnow() - timedelta(days=date_days)
        return f" AND {date_col} >= ?", [cutoff.isoformat()]

    def resolve_keyword_columns(cur: sqlite3.Cursor, table: str) -> List[str]:
        cur.execute("PRAGMA table_info(" + table + ")")
        cols = {row[1].lower(): row[1] for row in cur.fetchall()}
        candidates = [
            "title",
            "description",
            "summary",
            "address",
            "project_name",
            "source_text",
        ]
        return [cols[name] for name in candidates if name in cols]

    def build_normalized_like_clause(columns: List[str]) -> Tuple[str, List[str]]:
        if not columns or not tokens:
            return "", []
        replacements = {
            "ə": "e",
            "ş": "s",
            "ı": "i",
            "ö": "o",
            "ü": "u",
            "ç": "c",
            "ğ": "g",
        }

        def normalize_column_expr(col: str) -> str:
            expr = f"LOWER(COALESCE({col}, ''))"
            for src, dst in replacements.items():
                expr = f"REPLACE({expr}, '{src}', '{dst}')"
            return expr

        clauses = []
        params: List[str] = []
        for token in tokens:
            like = f"%{token}%"
            per_field = [f"{normalize_column_expr(col)} LIKE ?" for col in columns]
            clauses.append("(" + " OR ".join(per_field) + ")")
            params.extend([like] * len(columns))
        return " AND ".join(clauses), params

    def load_results_from_table(
        conn_factory,
        table: str,
        fts_table: str,
        operation_value: Optional[str],
        source: str,
    ):
        conn = conn_factory()
        cur = conn.cursor()
        date_col = detect_table_date_column(cur, table)
        base_where = "1=1"
        params: List[Any] = []
        if operation_value:
            base_where += " AND operation = ?"
            params.append(operation_value)
        if phrase_query or token_query:
            try:
                order_suffix = f", l.{date_col} DESC" if date_col else ""
                rows = execute_ranked_fts(
                    cur,
                    f"{table} l",
                    fts_table,
                    base_where,
                    params,
                    phrase_query,
                    token_query,
                    or_query,
                    order_suffix,
                )
            except Exception:
                rows = []
        else:
            rows = []

        if not rows:
            columns = resolve_keyword_columns(cur, table)
            kw_sql, kw_params = build_normalized_like_clause(columns)
            sql = f"SELECT * FROM {table} WHERE {base_where}"
            params_like = list(params)
            if kw_sql:
                sql += " AND " + kw_sql
                params_like.extend(kw_params)
            date_sql, date_params = apply_date_clause_sql(table, date_col)
            sql += date_sql
            order_col = date_col or "date_read"
            sql += f" ORDER BY {order_col} DESC LIMIT 5000"
            cur.execute(sql, params_like + date_params)
            rows = cur.fetchall()

        for r in rows:
            d = dict(r)
            d["__source"] = source
            results.append(d)
        if conn_factory == get_main_conn:
            close_main_conn(conn)
        else:
            conn.close()

    if os.path.exists(MAIN_DB):
        load_results_from_table(
            get_main_conn, "listings", "listings_fts", op_main, "main"
        )

    load_results_from_table(
        get_local_conn, "listings_approved", "local_listings_fts", op_local, "local"
    )

    filtered: List[dict] = []
    for ev in results:
        if not is_within_date_range(ev, date_days):
            continue
        listing_text = build_listing_text_blob(ev)
        if not listing_text:
            continue
        if all(token in listing_text for token in tokens):
            filtered.append(ev)

    filtered.sort(key=safe_date, reverse=True)
    total = len(filtered)
    if limit is not None:
        filtered = filtered[offset : offset + limit]
    logger.info(
        "keyword search tokens=%s total=%s elapsed=%.3fs",
        tokens,
        total,
        time.perf_counter() - search_started_at,
    )
    return filtered, total


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
            res["price_min"] = int(
                float(range_match.group(1).replace(",", "").replace(".", ""))
            )
            res["price_max"] = int(
                float(range_match.group(2).replace(",", "").replace(".", ""))
            )
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
    search_started_at = time.perf_counter()

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
    joined_keywords = " ".join(keywords)
    fts_phrase, fts_token = build_fts_queries(joined_keywords)
    normalized_tokens = [t for t in normalize_text(joined_keywords).split() if t]
    or_query = (
        " OR ".join([f"{t}*" for t in normalized_tokens]) if normalized_tokens else None
    )

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        where_clause, base_params = build_filters(op_main)
        if keywords and is_fts_ready(conn, "listings_fts"):
            rows = execute_ranked_fts(
                cur,
                "listings l",
                "listings_fts",
                where_clause,
                base_params,
                fts_phrase,
                fts_token,
                or_query,
                ", l.date_read DESC",
            )
        elif keywords:
            sql_where, kw_params = build_multi_like_sql(
                keywords,
                ["summary", "address", "metro", "rayon", "contact_name", "operation"],
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
        if keywords and is_fts_ready(conn, "listings_fts"):
            row_iter = rows
        else:
            row_iter = cur.fetchall()
        for r in row_iter:
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        close_main_conn(conn)

    # LOCAL
    conn = get_local_conn()
    cur = conn.cursor()
    where_clause, base_params = build_filters(op_local)
    if keywords and is_fts_ready(conn, "local_listings_fts"):
        rows = execute_ranked_fts(
            cur,
            "listings_approved l",
            "local_listings_fts",
            where_clause,
            base_params,
            fts_phrase,
            fts_token,
            or_query,
            ", l.date_added DESC",
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
    if keywords and is_fts_ready(conn, "local_listings_fts"):
        row_iter = rows
    else:
        row_iter = cur.fetchall()
    for r in row_iter:
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    filtered = []
    for ev in results:
        if not is_within_date_range(ev, date_days):
            continue
        if not passes_room(ev):
            continue
        filtered.append(ev)

    filtered.sort(key=safe_date, reverse=True)
    total = len(filtered)
    if limit is not None:
        filtered = filtered[offset : offset + limit]
    logger.info(
        "smart search tokens=%s total=%s elapsed=%.3fs",
        keywords,
        total,
        time.perf_counter() - search_started_at,
    )
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

    results.sort(key=safe_date, reverse=True)
    total = len(results)
    if limit is not None:
        results = results[offset : offset + limit]
    return results, total


def query_favorites_page(chat_id: int, offset: int = 0, limit: int = None):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM favorites WHERE chat_id=?", (chat_id,))
    total = cur.fetchone()[0]
    cur.execute(
        """
        SELECT listing_id, source FROM favorites
        WHERE chat_id=?
        ORDER BY added_at DESC
        LIMIT ? OFFSET ?
    """,
        (chat_id, limit if limit is not None else -1, offset),
    )
    rows = cur.fetchall()
    conn.close()

    items = []
    for r in rows:
        ev = fetch_listing_by_source(r["source"], r["listing_id"])
        if ev:
            items.append({"data": ev, "source": r["source"]})
    return items, total


def fetch_page_results(chat_id: int, mode: str, params: dict, page: int):
    offset = (page - 1) * PAGE_SIZE
    if mode == "filter":
        filters = params.get("filters") or params
        return query_structured_results(filters, offset=offset, limit=PAGE_SIZE)
    if mode == "keyword":
        return query_keyword_results(
            params.get("operation"),
            params.get("words", []),
            params.get("date_days"),
            offset=offset,
            limit=PAGE_SIZE,
        )
    if mode == "smart":
        return query_smart_results(
            params.get("criteria", {}), offset=offset, limit=PAGE_SIZE
        )
    if mode == "phone":
        return query_phone_results(
            params.get("digits", ""), offset=offset, limit=PAGE_SIZE
        )
    if mode == "favorites":
        return query_favorites_page(chat_id, offset=offset, limit=PAGE_SIZE)
    if mode == "topviews":
        return query_top_viewed_listings(
            days=params.get("days", 7), offset=offset, limit=PAGE_SIZE
        )
    if mode == "today":
        return query_today_results(
            params.get("filters", {}), offset=offset, limit=PAGE_SIZE
        )
    if mode == "keyword_notif":
        items = keyword_notification_state.get(chat_id, {}).get("items", [])
        seen_ids: Set[str] = set()
        deduped = []
        for item in items:
            item_id = item.get("id") or item.get("ID") or item.get("Elan_kodu")
            if item_id is None:
                deduped.append(item)
                continue
            key = str(item_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            deduped.append(item)
        total = len(deduped)
        return deduped[offset : offset + PAGE_SIZE], total
    return [], 0


def fetch_all_results(chat_id: int, mode: str, params: dict):
    if mode == "filter":
        filters = params.get("filters") or params
        return query_structured_results(filters, offset=0, limit=None)
    if mode == "keyword":
        return query_keyword_results(
            params.get("operation"),
            params.get("words", []),
            params.get("date_days"),
            offset=0,
            limit=None,
        )
    if mode == "smart":
        return query_smart_results(params.get("criteria", {}), offset=0, limit=None)
    if mode == "phone":
        return query_phone_results(params.get("digits", ""), offset=0, limit=None)
    if mode == "favorites":
        return query_favorites_page(chat_id, offset=0, limit=None)
    if mode == "topviews":
        return query_top_viewed_listings(days=params.get("days", 7), offset=0, limit=None)
    if mode == "today":
        filters = params.get("filters", {})
        filters_copy = dict(filters)
        cached = today_results_cache.get(chat_id, {})
        if cached.get("filters") == filters_copy:
            items = cached.get("items") or []
            return items, len(items)
        items, total = query_today_results(filters_copy, offset=0, limit=None)
        today_results_cache[chat_id] = {"filters": filters_copy, "items": items}
        return items, total
    if mode == "keyword_notif":
        items = keyword_notification_state.get(chat_id, {}).get("items", [])
        seen_ids: Set[str] = set()
        deduped = []
        for item in items:
            item_id = item.get("id") or item.get("ID") or item.get("Elan_kodu")
            if item_id is None:
                deduped.append(item)
                continue
            key = str(item_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            deduped.append(item)
        total = len(deduped)
        return deduped, total
    return [], 0


def prepare_listing_session_items(items: List[dict]):
    refs: List[Dict[str, Any]] = []
    cache: Dict[str, dict] = {}
    for item in items:
        norm = normalize_listing_item(item)
        if not norm:
            continue
        ref = {"source": norm["source"], "id": norm["id"]}
        refs.append(ref)
        cache_key = make_listing_ref(norm["source"], norm["id"])
        cache.setdefault(cache_key, norm.get("data", {}))
    return refs, cache


def render_listing_for_user(
    chat_id: int, session_id: Optional[str] = None, target_message=None
):
    session = get_active_listing_session(chat_id)
    if not session or (session_id and session.get("session_id") != session_id):
        return

    refs = session.get("result_ids") or []
    if not refs:
        listing_sessions.pop(chat_id, None)
        return

    idx = max(0, min(session.get("current_index", 0), len(refs) - 1))
    session["current_index"] = idx
    ref = refs[idx]
    cache_key = make_listing_ref(ref["source"], ref["id"])
    listing = session.get("cache", {}).get(cache_key)
    if not listing:
        listing = fetch_listing_by_source(ref["source"], ref["id"])
        if listing:
            listing["__source"] = ref["source"]
            session.setdefault("cache", {})[cache_key] = listing

    if not listing:
        if len(refs) > 1:
            session["result_ids"].pop(idx)
            session["current_index"] = max(0, min(idx, len(session["result_ids"]) - 1))
            return render_listing_for_user(chat_id, session.get("session_id"), target_message)
        listing_sessions.pop(chat_id, None)
        bot.send_message(chat_id, "⚠️ Elan artıq mövcud deyil.")
        return

    progress_text = f"📍 Elan {idx + 1} / {len(refs)}"
    is_fav = is_favorite_entry(chat_id, ref["source"], ref["id"])
    text = build_listing_text(listing, ref["source"], progress_text=progress_text)
    listing_link = listing.get("link") or listing.get("source_link")
    wa_message = build_whatsapp_message(listing)
    wa_phone = listing.get("phone") or listing.get("Elaqe_nomresi")
    wa_url = make_whatsapp_url(wa_phone, wa_message)
    markup = build_listing_navigation_keyboard(
        is_fav, listing_link, wa_url
    )
    try:
        markup_signature = json.dumps(markup.to_dic(), sort_keys=True)
    except Exception:
        try:
            markup_signature = json.dumps(markup.keyboard, default=str, sort_keys=True)
        except Exception:
            markup_signature = str(markup.keyboard)
    signature = (text, markup_signature)

    record_agent_activity(chat_id, metric="views")

    if session.get("track_view"):
        seen_key = make_listing_ref(ref["source"], ref["id"])
        viewed = session.setdefault("viewed", set())
        if seen_key not in viewed:
            record_listing_view(ref["source"], ref["id"], chat_id)
            viewed.add(seen_key)

    session["timestamp"] = time.time()

    if session.get("message_id"):
        if session.get("last_signature") == signature:
            return
        try:
            bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=session["message_id"],
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            session["last_signature"] = signature
            return
        except Exception as e:
            if "message is not modified" in str(e):
                return
            try:
                msg = bot.send_message(
                    chat_id, text, reply_markup=markup, disable_web_page_preview=True
                )
                session["message_id"] = msg.message_id
                session["last_signature"] = signature
                return
            except Exception:
                return

    try:
        msg = bot.send_message(
            chat_id, text, reply_markup=markup, disable_web_page_preview=True
        )
        session["message_id"] = msg.message_id
        session["last_signature"] = signature
    except Exception:
        session.pop("message_id", None)


def start_listing_session(
    chat_id: int,
    mode: str,
    params: dict,
    items: List[dict],
    *,
    start_index: int = 0,
    loading_ref=None,
    track_view: bool = False,
):
    refs, cache = prepare_listing_session_items(items)
    if not refs:
        if not replace_loading_message(loading_ref, "Siyahı boşdur."):
            bot.send_message(chat_id, "Siyahı boşdur.")
        return

    start_index = max(0, min(start_index, len(refs) - 1))
    session_id = f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    listing_sessions[chat_id] = {
        "session_id": session_id,
        "mode": mode,
        "params": params or {},
        "result_ids": refs,
        "current_index": start_index,
        "timestamp": time.time(),
        "message_id": loading_ref[1] if loading_ref else None,
        "cache": cache,
        "track_view": track_view,
        "viewed": set(),
    }
    render_listing_for_user(chat_id, session_id, target_message=loading_ref)


def send_paginated_results(
    chat_id: int,
    mode: str,
    params: dict,
    page: int = 1,
    loading_ref=None,
    show_summary: bool = True,
):
    if mode == "today":
        set_ui_context(chat_id, UI_CONTEXT_TODAY)
    elif mode in {
        "filter",
        "keyword",
        "smart",
        "phone",
        "favorites",
        "topviews",
        "keyword_notif",
    }:
        set_ui_context(chat_id, UI_CONTEXT_SEARCH)
    if mode == "topviews" and not is_admin(chat_id):
        if not replace_loading_message(
            loading_ref, "❌ Bu bölmə yalnız admin üçündür."
        ):
            bot.send_message(chat_id, "❌ Bu bölmə yalnız admin üçündür.")
        return
    items, total = fetch_all_results(chat_id, mode, params)
    if mode == "today":
        today_results_cache[chat_id] = {
            "filters": params.get("filters", {}),
            "items": items,
        }
    if total == 0:
        if not replace_loading_message(loading_ref, "Siyahı boşdur."):
            bot.send_message(chat_id, "Siyahı boşdur.")
        return

    total_pages = compute_total_pages(total) if total else 1
    set_pagination_state(chat_id, mode, params, min(page, total_pages), total_pages)

    start_index = max(0, min((page - 1) * PAGE_SIZE, max(total - 1, 0)))
    track_view = mode == "favorites"
    start_listing_session(
        chat_id,
        mode,
        params,
        items,
        start_index=start_index,
        loading_ref=loading_ref,
        track_view=track_view,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("pg:"))
@callback_guard
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
        set_ui_context(chat_id, UI_CONTEXT_MAIN)
        bot.send_message(chat_id, "⚠️ Axtarış məlumatı tapılmadı. Yeni axtarışa başlayın.")
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


@bot.callback_query_handler(func=lambda c: c.data.startswith("nav:"))
@callback_guard
def cb_listing_nav(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    session = get_active_listing_session(chat_id)
    if not session:
        try:
            bot.answer_callback_query(c.id, "Aktiv siyahı yoxdur.")
        except Exception:
            pass
        return

    if not session.get("result_ids"):
        try:
            bot.answer_callback_query(c.id, "Siyahı boşdur.")
        except Exception:
            pass
        return

    action = c.data.split(":", 1)[1]
    deltas = {"next": 1, "prev": -1, "+5": 5, "-5": -5}
    if action == "home":
        session["timestamp"] = time.time()
        send_main_menu(chat_id, "🏠 Əsas menyu", force=True)
        try:
            bot.answer_callback_query(c.id, "Əsas menyu")
        except Exception:
            pass
        return

    delta = deltas.get(action, 0)
    if delta:
        session["current_index"] = max(
            0, min(session.get("current_index", 0) + delta, len(session["result_ids"]) - 1)
        )
    session["timestamp"] = time.time()
    render_listing_for_user(chat_id, session.get("session_id"))
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "fav:toggle")
@callback_guard
def cb_listing_favorite_toggle(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    session = get_active_listing_session(chat_id)
    if not session or not session.get("result_ids"):
        try:
            bot.answer_callback_query(c.id, "Siyahı bitib.")
        except Exception:
            pass
        return
    ref = session["result_ids"][session.get("current_index", 0)]
    is_fav = is_favorite_entry(chat_id, ref["source"], ref["id"])
    if is_fav:
        removed = remove_favorite_entry(chat_id, ref["source"], ref["id"])
        msg = "❌ Favoritdən çıxarıldı" if removed else "Əvvəlcə əlavə olunmayıb"
    else:
        added = add_favorite_entry(chat_id, ref["source"], ref["id"])
        msg = "❤️ Favoritə əlavə olundu" if added else "Artıq favoritdədir"
    render_listing_for_user(chat_id, session.get("session_id"))
    try:
        bot.answer_callback_query(c.id, msg)
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


def render_date_range_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "date"
    op = st.get("filters", {}).get("op")
    mk = types.InlineKeyboardMarkup()
    options = get_date_range_options(op)
    row = []
    for label, code in options:
        row.append(types.InlineKeyboardButton(label, callback_data=f"fs|dt|{code}"))
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="fs|bk"))
    structured_send(chat_id, message, "📆 Tarix aralığını seç:", mk)


def render_prop_step(chat_id, message=None):
    st = search_state.setdefault(chat_id, {})
    st["step"] = "prop"
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("Mənzil", callback_data="fs|tp|m"),
        types.InlineKeyboardButton("Həyət evi", callback_data="fs|tp|f"),
    )
    mk.add(
        types.InlineKeyboardButton("Obyekt / Ofis", callback_data="fs|tp|q"),
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
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("💰 Qiymət aralığı seç", callback_data="fs|prm"))
    mk.add(types.InlineKeyboardButton("📦 Hamısı", callback_data="fs|pr|all"))
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
        "awaiting_price_min": False,
        "awaiting_price_max": False,
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
    elif step == "date":
        render_date_range_step(chat_id, message)
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
@callback_guard
def cb_structured(c):
    if not ensure_allowed_cb(c):
        return
    parts = c.data.split("|")
    action = parts[1]
    chat_id = c.message.chat.id
    st = search_state.setdefault(
        chat_id,
        {
            "mode": "structured",
            "filters": {},
            "history": [],
            "awaiting_floor_range": False,
            "awaiting_price_min": False,
            "awaiting_price_max": False,
        },
    )

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
        st["history"] = []
        st["awaiting_floor_range"] = False
        st["awaiting_price_min"] = False
        st["awaiting_price_max"] = False
        st["filters"] = {"op": parts[2]}
        structured_push_history(chat_id)
        render_date_range_step(chat_id, c.message)
    elif action == "dt":
        date_code = parts[2]
        date_days = DATE_RANGE_DAYS.get(date_code)
        st.setdefault("filters", {})["date_days"] = date_days
        structured_push_history(chat_id)
        render_prop_step(chat_id, c.message)
    elif action == "tp":
        filters = st.setdefault("filters", {})
        for key in (
            "prop",
            "region",
            "rayon",
            "price",
            "min_price",
            "max_price",
            "rooms",
            "floor_range",
        ):
            filters.pop(key, None)
        filters["prop"] = parts[2]
        structured_push_history(chat_id)
        render_region_step(chat_id, c.message)
    elif action == "rg":
        filters = st.setdefault("filters", {})
        filters["region"] = parts[2]
        filters.pop("rayon", None)
        structured_push_history(chat_id)
        if parts[2] == "sum":
            filters["rayon"] = "Sumqayıt"
            render_price_step(chat_id, c.message)
        elif parts[2] == "abs":
            filters["rayon"] = "Abşeron"
            render_price_step(chat_id, c.message)
        else:
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
        if parts[2] == "all":
            st.setdefault("filters", {})["min_price"] = None
            st.setdefault("filters", {})["max_price"] = None
            st.setdefault("filters", {}).pop("price", None)
            structured_push_history(chat_id)
            prop = st.get("filters", {}).get("prop")
            if prop == "t":
                perform_structured_search(
                    chat_id,
                    offset=0,
                    edit_msg=(c.message.chat.id, c.message.message_id),
                )
            else:
                render_room_step(chat_id, c.message)
    elif action == "prm":
        st.setdefault("filters", {})["min_price"] = None
        st.setdefault("filters", {})["max_price"] = None
        st["awaiting_price_min"] = True
        st["step"] = "price_min"
        bot.send_message(
            chat_id,
            "💰 Minimum qiymət yazın (rəqəm ilə):",
            reply_markup=build_back_reply_keyboard(),
        )
    elif action == "rm":
        filters = st.setdefault("filters", {})
        filters.pop("floor_range", None)
        if parts[2] == "r0":
            filters.pop("rooms", None)
        else:
            filters["rooms"] = parts[2]
        structured_push_history(chat_id)
        prop = st.get("filters", {}).get("prop")
        if prop in {"t", "q"}:
            perform_structured_search(
                chat_id,
                offset=0,
                edit_msg=(c.message.chat.id, c.message.message_id),
            )
        else:
            render_floor_step(chat_id, c.message)
    elif action == "fl":
        filters = st.setdefault("filters", {})
        floor_val = FLOOR_PRESETS.get(parts[2])
        if floor_val is None:
            filters.pop("floor_range", None)
        else:
            filters["floor_range"] = floor_val
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


@bot.message_handler(
    func=lambda m: search_state.get(m.chat.id, {}).get("awaiting_floor_range")
)
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


@bot.message_handler(
    func=lambda m: search_state.get(m.chat.id, {}).get("awaiting_price_min")
)
def handle_price_min_input(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    st = search_state.get(chat_id, {})
    text = (message.text or "").strip()
    if not st:
        return
    if text == "⬅️ Geri":
        st["awaiting_price_min"] = False
        bot.send_message(
            chat_id,
            "↩️ Qiymət seçiminə qayıdıldı.",
        )
        render_price_step(chat_id)
        return
    value = parse_number(text)
    if value is None:
        bot.send_message(
            chat_id,
            "⚠️ Minimum qiyməti rəqəm ilə yazın.",
            reply_markup=build_back_reply_keyboard(),
        )
        return
    st.setdefault("filters", {})["min_price"] = value
    st["awaiting_price_min"] = False
    st["awaiting_price_max"] = True
    bot.send_message(
        chat_id,
        "💰 Maksimum qiymət yazın (rəqəm ilə):",
        reply_markup=build_back_reply_keyboard(),
    )


@bot.message_handler(
    func=lambda m: search_state.get(m.chat.id, {}).get("awaiting_price_max")
)
def handle_price_max_input(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    st = search_state.get(chat_id, {})
    text = (message.text or "").strip()
    if not st:
        return
    if text == "⬅️ Geri":
        st["awaiting_price_max"] = False
        st["awaiting_price_min"] = True
        bot.send_message(
            chat_id,
            "💰 Minimum qiymət yazın (rəqəm ilə):",
            reply_markup=build_back_reply_keyboard(),
        )
        return
    value = parse_number(text)
    if value is None:
        bot.send_message(
            chat_id,
            "⚠️ Maksimum qiyməti rəqəm ilə yazın.",
            reply_markup=build_back_reply_keyboard(),
        )
        return
    min_price = st.get("filters", {}).get("min_price")
    if min_price is not None and value < min_price:
        bot.send_message(
            chat_id,
            "⚠️ Maksimum qiymət minimumdan kiçik ola bilməz.",
            reply_markup=build_back_reply_keyboard(),
        )
        return
    st.setdefault("filters", {})["max_price"] = value
    st["awaiting_price_max"] = False
    st["step"] = "price"
    structured_push_history(chat_id)
    bot.send_message(chat_id, "✅ Qiymət aralığı seçildi.")
    prop = st.get("filters", {}).get("prop")
    if prop == "t":
        perform_structured_search(chat_id, offset=0, edit_msg=None)
    else:
        render_room_step(chat_id)


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
    st["step"] = "results"
    inc_limit(chat_id, "structured", 1)

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

    text = normalize_text(message.text or "")
    if not text:
        bot.send_message(chat_id, "Boş sorğu göndərdiniz.")
        return

    st = search_state.get(chat_id, {})
    selected_op = st.get("operation")
    if selected_op not in ("sale", "rent"):
        send_keyword_operation_prompt(chat_id)
        return
    if not st.get("date_selected"):
        send_keyword_date_prompt(chat_id)
        return

    loading_ref = show_loading_message(chat_id)
    log_search_event(
        chat_id,
        "keyword",
        operation=normalize_operation_value(selected_op) or selected_op,
        query_text=f"{text} | date_days={st.get('date_days')}",
    )

    words = [w for w in text.split() if w]

    inc_limit(chat_id, "keyword", 1)
    send_paginated_results(
        chat_id,
        mode="keyword",
        params={
            "operation": selected_op,
            "words": words,
            "date_days": st.get("date_days"),
        },
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

    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    raw = digits[-9:] if len(digits) > 9 else digits
    if len(raw) < 7:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa düzgün nömrə yazın (min. 7 rəqəm).")
        return

    loading_ref = show_loading_message(chat_id)
    log_search_event(chat_id, "phone", query_text=raw)

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
@callback_guard
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
@callback_guard
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
@callback_guard
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
        WHERE LOWER(COALESCE(Umumi_melumat, '')) LIKE ?
           OR LOWER(COALESCE(Unvan, '')) LIKE ?
           OR LOWER(COALESCE(Rayon_Qesebe, '')) LIKE ?
           OR LOWER(COALESCE(Emlakin_novu, '')) LIKE ?
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
@callback_guard
def cb_agent_phone(c):
    chat_id = c.message.chat.id
    if not is_admin(chat_id):
        return

    msg = bot.send_message(chat_id, "📞 Nömrə daxil et:")
    bot.register_next_step_handler(msg, agent_search_by_phone)


def agent_search_by_phone(message):
    if not is_admin(message.chat.id):
        return

    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    num = digits[-9:] if len(digits) > 9 else digits
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
    buttons = ADMIN_PANEL_BUTTONS
    mk = types.InlineKeyboardMarkup()
    for i in range(0, len(buttons), 2):
        row_buttons = []
        for btn_text in buttons[i : i + 2]:
            callback_data = f"adm_act:{ADMIN_PANEL_ACTION_KEYS[btn_text]}"
            if btn_text == "📊 QR Statistikası":
                callback_data = "admin_qr_stats"
            row_buttons.append(
                types.InlineKeyboardButton(
                    btn_text,
                    callback_data=callback_data,
                )
            )
        mk.row(*row_buttons)
    mk.add(
        types.InlineKeyboardButton(ADMIN_PANEL_BACK_MAIN, callback_data="adm_back:main")
    )
    admin_panel_page_state[chat_id] = 1
    return mk


def send_admin_panel(
    chat_id: int, page: int = 1, text: str = TEXTS_AZ["admin_panel_title"]
):
    mk = build_admin_panel_keyboard(chat_id, page)
    set_user_state(chat_id, "ADMIN")
    bot.send_message(chat_id, text, reply_markup=mk)


@bot.message_handler(func=lambda m: m.text == TEXTS_AZ["admin_panel_button"])
@bot.message_handler(commands=["admin"])
def open_admin_panel(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "❌ Bu bölməyə yalnız admin daxil ola bilər.")
        return

    send_admin_panel(message.chat.id, page=1)


def _handle_admin_panel_action(chat_id: int, action_text: str):
    if action_text in {
        TEXTS_AZ["admin_panel_customer_requests"],
        TEXTS_AZ["admin_panel_customer_requests_access"],
        TEXTS_AZ["admin_panel_archived_requests"],
    }:
        return
    if admin_update_state.get(chat_id) == "awaiting_db_link":
        return

    if action_text == TEXTS_AZ["admin_panel_pending_listings"]:
        show_pending_listings(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_stats"]:
        admin_stats_period[chat_id] = "day"
        show_admin_stats(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_bonus_stats"]:
        show_bonus_stats(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_customer_requests"]:
        show_customer_requests_overview(chat_id, "day")
    elif action_text == "📊 QR Statistikası":
        send_qr_stats_menu(chat_id)
    elif action_text == FINANCIAL_REPORTS_BUTTON:
        send_financial_reports_menu(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_agents_notify"]:
        msg = bot.send_message(chat_id, "✍️ Vasitəçilərə göndəriləcək mətni yaz:")
        bot.register_next_step_handler(msg, admin_agents_broadcast)
    elif action_text == TEXTS_AZ["admin_panel_user_search"]:
        msg = bot.send_message(chat_id, "🔍 İstifadəçi chat_id daxil et:")
        bot.register_next_step_handler(msg, admin_search_by_id_step)
    elif action_text == TEXTS_AZ["admin_panel_promos"]:
        show_admin_promo_menu(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_users"]:
        show_users_menu(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_send_update"]:
        broadcast_bot_update(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_topviews"]:
        reset_search_state(chat_id)
        send_paginated_results(chat_id, "topviews", params={"days": 7}, page=1)
    elif action_text == TEXTS_AZ["admin_panel_db_update"]:
        start_admin_update_db(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_direct_message"]:
        start_direct_user_message_flow(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_customer_requests_access"]:
        show_customer_requests_access_admin(chat_id)
    elif action_text == TEXTS_AZ["admin_panel_archived_requests"]:
        show_archived_requests(chat_id, page=1)


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("adm_act:")
    or c.data.startswith("adm_back:")
    or c.data == "admin_qr_stats"
)
@callback_guard
def cb_admin_panel(c):
    if not is_admin(c.from_user.id):
        return
    chat_id = c.message.chat.id
    if c.data == "admin_qr_stats":
        send_qr_stats_menu(chat_id)
        return

    if c.data == "adm_back:main":
        return_to_main_menu(chat_id)
        return

    if c.data.startswith("adm_act:"):
        action_key = c.data.split(":", 1)[1]
        action_text = ADMIN_PANEL_ACTION_LOOKUP.get(action_key)
        if action_text:
            _handle_admin_panel_action(chat_id, action_text)


@bot.callback_query_handler(func=lambda c: c.data.startswith("bonusprob:"))
@callback_guard
def cb_bonus_probability_controls(c):
    if not is_admin(c.from_user.id):
        return
    action = c.data.split(":", 1)[1] if ":" in c.data else ""
    if action == "edit":
        bonus_probability_edit_state[c.message.chat.id] = True
        bot.send_message(
            c.message.chat.id,
            "Yeni ehtimalları bu formatda göndərin: 0=30,1=30,2=20,3=10,5=7,7=3",
        )
    elif action == "refresh":
        show_bonus_stats(c.message.chat.id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.message_handler(func=lambda m: bonus_probability_edit_state.get(m.chat.id))
def handle_bonus_probability_edit(message):
    if not is_admin(message.chat.id):
        return

    text = (message.text or "").replace("%", "")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    new_weights: Dict[int, int] = {}
    for part in parts:
        if "=" not in part:
            continue
        left, right = part.split("=", 1)
        try:
            day_val = int(left.strip())
            weight_val = int(right.strip())
        except Exception:
            continue
        new_weights[day_val] = weight_val

    bonus_probability_edit_state.pop(message.chat.id, None)

    if not new_weights:
        bot.send_message(
            message.chat.id,
            "⚠️ Düzgün format: 0=30,1=30,2=20,3=10,5=7,7=3",
        )
        show_bonus_stats(message.chat.id)
        return

    update_bonus_probabilities(new_weights)
    bot.send_message(message.chat.id, "✅ Ehtimallar yeniləndi.")
    show_bonus_stats(message.chat.id)


def format_qr_area_label(area_code: Optional[str]) -> str:
    fallback = str(area_code or "-")
    return QR_SOURCE_AREAS.get(str(area_code or "").strip().lower(), fallback)


def get_qr_stats_start_time(range_key: str) -> Optional[datetime]:
    now = datetime.now(timezone.utc)
    if range_key == "24h":
        return now - timedelta(hours=24)
    if range_key == "7d":
        return now - timedelta(days=7)
    if range_key == "30d":
        return now - timedelta(days=30)
    return None


def fetch_qr_stats(range_key: str):
    rows = []
    total = 0
    area_counts: Dict[str, int] = {area: 0 for area in QR_STATS_AREAS}
    conn = None
    try:
        start_time = get_qr_stats_start_time(range_key)
        conn = get_db()
        cur = conn.cursor()
        source_params: List[Any] = list(QR_STATS_AREAS)
        base_where = f"join_source IN ({','.join(['?'] * len(QR_STATS_AREAS))})"
        time_filter = ""
        if start_time:
            time_filter = " AND datetime(created_at) >= datetime(?)"
            source_params.append(start_time.isoformat())

        cur.execute(
            f"""
            SELECT chat_id as user_id, username, join_source, created_at
            FROM users
            WHERE {base_where}{time_filter}
            ORDER BY datetime(created_at) DESC
            LIMIT 50
            """,
            source_params,
        )
        rows = cur.fetchall()

        cur.execute(
            f"""
            SELECT join_source, COUNT(*) as cnt
            FROM users
            WHERE {base_where}{time_filter}
            GROUP BY join_source
            """,
            source_params,
        )
        count_rows = cur.fetchall()
        for count_row in count_rows:
            area_code = (
                count_row["join_source"]
                if "join_source" in count_row.keys()
                else count_row[0]
            )
            cnt = int(count_row["cnt"] if "cnt" in count_row.keys() else count_row[1])
            if area_code in area_counts:
                area_counts[area_code] = cnt

        total_params: List[Any] = []
        total_time_filter = ""
        if start_time:
            total_time_filter = " AND datetime(created_at) >= datetime(?)"
            total_params.append(start_time.isoformat())

        cur.execute(
            f"SELECT COUNT(*) FROM users WHERE join_source IS NOT NULL{total_time_filter}",
            total_params,
        )
        total_row = cur.fetchone()
        total = int(total_row[0]) if total_row else 0
    except Exception:
        logger.exception("Failed to fetch QR stats range=%s", range_key)
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return rows, total, area_counts


def fetch_qr_top_areas():
    rows = []
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        placeholders = ",".join(["?"] * len(QR_STATS_AREAS))
        cur.execute(
            f"SELECT join_source, COUNT(*) as cnt FROM users "
            f"WHERE join_source IN ({placeholders}) "
            "GROUP BY join_source ORDER BY cnt DESC",
            list(QR_STATS_AREAS),
        )
        rows = cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch QR top areas")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return rows


def build_qr_stats_menu() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton("📅 Son 24 saat", callback_data="qr_stats:24h"),
        types.InlineKeyboardButton("📅 Son 7 gün", callback_data="qr_stats:7d"),
    )
    mk.row(
        types.InlineKeyboardButton("📅 Son 30 gün", callback_data="qr_stats:30d"),
        types.InlineKeyboardButton("📊 Ümumi", callback_data="qr_stats:all"),
    )
    mk.add(
        types.InlineKeyboardButton(
            "🏆 Ən çox lead gətirən ərazi", callback_data="qr_stats:top"
        )
    )
    return mk


def send_qr_stats_menu(chat_id: int):
    if not is_admin(chat_id):
        return
    mk = build_qr_stats_menu()
    bot.send_message(chat_id, "📊 QR Statistikası", reply_markup=mk)


def send_qr_stats(chat_id: int, range_key: str):
    if not is_admin(chat_id):
        return
    rows, total, area_counts = fetch_qr_stats(range_key)
    label = QR_STATS_RANGE_LABELS.get(range_key, QR_STATS_RANGE_LABELS["all"])
    lines = [f"📊 QR Statistikası ({label})", ""]
    area_groups: Dict[str, List[Any]] = {area: [] for area in QR_STATS_AREAS}
    for row in rows:
        area_code = row["join_source"] if "join_source" in row.keys() else row[2]
        if area_code in area_groups:
            area_groups[area_code].append(row)

    for area_code in QR_STATS_AREAS:
        area_rows = area_groups.get(area_code, [])
        count = area_counts.get(area_code, 0)
        lines.append(
            f"📍 Mənbə: {format_qr_area_label(area_code)} — {count} istifadəçi"
        )
        if area_rows:
            lines.append("👤 İstifadəçilər:")
            for idx, r in enumerate(area_rows, start=1):
                user_id = r["user_id"] if "user_id" in r.keys() else r[0]
                username = r["username"] if "username" in r.keys() else r[1]
                username_display = f"@{username}" if username else "username yoxdur"
                created_value = r["created_at"] if "created_at" in r.keys() else r[3]
                join_time = str(created_value)
                try:
                    join_dt = datetime.fromisoformat(str(created_value))
                    display_time = join_dt + timedelta(hours=4)
                    join_time = display_time.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
                lines.append(f"{idx}) ID: {user_id}")
                lines.append(f"   👤 {username_display}")
                lines.append(f"   🕒 {join_time}")
                lines.append("")
        else:
            lines.append("👤 İstifadəçi yoxdur.")
            lines.append("")
    lines.append(f"👥 Cəmi QR ilə qoşulanlar: {total}")
    bot.send_message(chat_id, "\n".join(lines))


def send_qr_top_areas(chat_id: int):
    if not is_admin(chat_id):
        return
    rows = fetch_qr_top_areas()
    lines = ["🏆 Ən çox lead gətirən ərazi", ""]
    medals = ["🥇", "🥈", "🥉"]
    if not rows:
        lines.append("Məlumat tapılmadı.")
    else:
        for idx, row in enumerate(rows[:3]):
            medal = medals[idx] if idx < len(medals) else f"{idx + 1}"
            area_code = row["join_source"] if "join_source" in row.keys() else row[0]
            cnt = int(row["cnt"] if "cnt" in row.keys() else row[1])
            lines.append(f"{medal} {format_qr_area_label(area_code)} — {cnt} istifadəçi")
    bot.send_message(chat_id, "\n".join(lines))


@bot.callback_query_handler(func=lambda c: c.data.startswith("qr_stats:"))
@callback_guard
def cb_qr_stats(c):
    if not is_admin(c.from_user.id):
        return
    chat_id = c.message.chat.id
    action = c.data.split(":", 1)[1]
    if action == "top":
        send_qr_top_areas(chat_id)
    else:
        send_qr_stats(chat_id, action)


def admin_customer_requests_access_step(message):
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    try:
        user_id = int(text)
    except Exception:
        bot.send_message(message.chat.id, "❌ Düzgün istifadəçi ID daxil edin.")
        return
    set_customer_requests_enabled(user_id, True)
    bot.send_message(
        message.chat.id,
        f"✅ ID {user_id} üçün müştəri istəkləri icazəsi aktiv edildi.",
    )
    show_customer_requests_access_admin(message.chat.id)


def get_request_period_start(period: str) -> datetime:
    now = datetime.now()
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return datetime(now.year, now.month, 1)
    return datetime(now.year, now.month, now.day)


def fetch_customer_request_stats(period: str):
    start_dt = get_request_period_start(period)
    conn = get_local_conn()
    cur = conn.cursor()
    params = (start_dt.isoformat(),)
    cur.execute(
        """
        SELECT COUNT(*) as total,
               SUM(CASE WHEN request_type='buy' THEN 1 ELSE 0 END) as buy_count,
               SUM(CASE WHEN request_type='rent' THEN 1 ELSE 0 END) as rent_count
        FROM customer_requests
        WHERE status='active' AND datetime(created_at) >= datetime(?)
        """,
        params,
    )
    row = cur.fetchone()
    total = row["total"] if row else 0
    buy_count = (row["buy_count"] or 0) if row else 0
    rent_count = (row["rent_count"] or 0) if row else 0

    cur.execute(
        """
        SELECT COALESCE(rayon, '-') as rayon, COUNT(*) as cnt
        FROM customer_requests
        WHERE status='active' AND datetime(created_at) >= datetime(?)
        GROUP BY rayon
        ORDER BY cnt DESC
        """,
        params,
    )
    rayons = [(r["rayon"], r["cnt"]) for r in cur.fetchall()]
    conn.close()
    return {
        "total": total,
        "buy": buy_count,
        "rent": rent_count,
        "rayons": rayons,
    }


def build_period_tabs(selected: str) -> List[types.InlineKeyboardButton]:
    mapping = {
        "day": TEXTS_AZ["admin_req_period_day"],
        "week": TEXTS_AZ["admin_req_period_week"],
        "month": TEXTS_AZ["admin_req_period_month"],
    }
    buttons = []
    for key, label in mapping.items():
        prefix = "✅ " if key == selected else ""
        buttons.append(
            types.InlineKeyboardButton(
                prefix + label, callback_data=f"adm_req_period:{key}"
            )
        )
    return buttons


def format_admin_request_type(req_type: str) -> str:
    if req_type == "buy":
        return "Satılır"
    if req_type == "rent":
        return "Kirayə verilir"
    return req_type or "-"


def fetch_admin_request_type_counts() -> dict:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT request_type, COUNT(*) as cnt
        FROM customer_requests
        WHERE status='active'
        GROUP BY request_type
        """
    )
    rows = cur.fetchall()
    conn.close()
    counts = {"buy": 0, "rent": 0}
    for row in rows:
        req_type = row["request_type"] if isinstance(row, dict) else row[0]
        cnt = row["cnt"] if isinstance(row, dict) else row[1]
        if req_type in counts:
            counts[req_type] = cnt or 0
    return counts


def show_admin_customer_request_types(
    chat_id: int, message: Optional[types.Message] = None
):
    counts = fetch_admin_request_type_counts()
    buy_count = counts.get("buy", 0)
    rent_count = counts.get("rent", 0)
    mk = types.InlineKeyboardMarkup()
    buttons = []
    if buy_count > 0:
        buttons.append(
            types.InlineKeyboardButton(
                f"{TEXTS_AZ['admin_req_type_sale']} ({buy_count})",
                callback_data="adm_req_type:buy",
            )
        )
    if rent_count > 0:
        buttons.append(
            types.InlineKeyboardButton(
                f"{TEXTS_AZ['admin_req_type_rent']} ({rent_count})",
                callback_data="adm_req_type:rent",
            )
        )

    if not buttons:
        text = "Bu bölmədə aktiv müştəri istəyi yoxdur."
        if message:
            try:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            except Exception:
                bot.send_message(chat_id, text)
        else:
            bot.send_message(chat_id, text)
        return

    if len(buttons) == 2:
        mk.row(*buttons)
    else:
        mk.add(buttons[0])

    text = "📌 Müştəri istəkləri"
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def fetch_admin_request_rayons(req_type: str) -> List[Tuple[str, int]]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rayon, COUNT(*) as cnt
        FROM customer_requests
        WHERE status='active'
          AND request_type=?
          AND rayon IS NOT NULL
          AND TRIM(rayon) != ''
        GROUP BY rayon
        HAVING cnt > 0
        ORDER BY cnt DESC
        """,
        (req_type,),
    )
    rows = [(r["rayon"], r["cnt"]) for r in cur.fetchall()]
    conn.close()
    return rows


def show_admin_request_rayons(
    chat_id: int, req_type: str, message: Optional[types.Message] = None
):
    admin_customer_request_state[chat_id] = {"request_type": req_type}
    rayons = fetch_admin_request_rayons(req_type)
    mk = types.InlineKeyboardMarkup()
    for i in range(0, len(rayons), 2):
        row_buttons = []
        for rayon, cnt in rayons[i : i + 2]:
            row_buttons.append(
                types.InlineKeyboardButton(
                    TEXTS_AZ["admin_req_rayon_item"].format(rayon=rayon, count=cnt),
                    callback_data=f"adm_req_rayon:{req_type}:{quote(rayon, safe='')}:1",
                )
            )
        mk.row(*row_buttons)

    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_types"], callback_data="adm_req_types"
        )
    )

    if not rayons:
        text = "Bu seçim üzrə aktiv müştəri istəyi yoxdur."
        if message:
            try:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
                return
            except Exception:
                pass
        bot.send_message(chat_id, text, reply_markup=mk)
        return

    text = f"📍 {format_admin_request_type(req_type)} üzrə rayonlar:"
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def format_admin_request_list_item(req: dict) -> str:
    created_dt = parse_dt_safe(req.get("created_at"))
    display_time = created_dt + timedelta(hours=4) if created_dt else None
    created_at = display_time.strftime("%Y-%m-%d %H:%M") if display_time else "bilinmir"
    req_type = format_admin_request_type(req.get("request_type"))
    return "\n".join(
        [
            f"🆔 Sorğu ID: {req.get('id')}",
            f"📅 Tarix: {created_at}",
            f"🏠 Tip: {req_type}",
            f"📍 Rayon: {req.get('rayon') or '-'}",
            f"🛏 Otaq: {req.get('rooms') or '-'}",
            f"💰 Büdcə: {req.get('budget') or '-'}",
            f"📞 Telefon: {req.get('phone') or '-'}",
            f"🆔 Müştəri ID: {req.get('chat_id') or '-'}",
        ]
    )


def fetch_admin_requests_by_rayon(
    req_type: str, rayon: str, page: int
) -> Tuple[List[dict], int, int, int]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM customer_requests
        WHERE status='active'
          AND request_type=?
          AND LOWER(rayon) = LOWER(?)
        """,
        (req_type, rayon),
    )
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_REQ)) if total else 1
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE_REQ
    cur.execute(
        """
        SELECT * FROM customer_requests
        WHERE status='active'
          AND request_type=?
          AND LOWER(rayon) = LOWER(?)
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (req_type, rayon, PAGE_SIZE_REQ, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total, total_pages, page


def build_admin_request_list_nav(
    req_type: str, rayon: str, page: int, total_pages: int
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    encoded = quote(rayon, safe="")
    mk.row(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_first_icon"],
            callback_data=f"adm_req_rayon:{req_type}:{encoded}:1",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_prev_icon"],
            callback_data=f"adm_req_rayon:{req_type}:{encoded}:{max(1, page - 1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_page"].format(page=page, total=total_pages),
            callback_data="adm_req_nop:list",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_next_icon"],
            callback_data=f"adm_req_rayon:{req_type}:{encoded}:{min(total_pages, page + 1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_last_icon"],
            callback_data=f"adm_req_rayon:{req_type}:{encoded}:{total_pages}",
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_rayons"],
            callback_data=f"adm_req_rayons:{req_type}",
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_types"], callback_data="adm_req_types"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_main"], callback_data="adm_req_main"
        )
    )
    return mk


def show_admin_requests_by_rayon(
    chat_id: int,
    req_type: str,
    rayon: str,
    page: int = 1,
    message: Optional[types.Message] = None,
):
    admin_customer_request_state[chat_id] = {
        "request_type": req_type,
        "rayon": rayon,
    }
    rows, total, total_pages, current_page = fetch_admin_requests_by_rayon(
        req_type, rayon, page
    )
    if not rows:
        mk = build_admin_request_list_nav(req_type, rayon, 1, 1)
        text = f"📭 {rayon} üzrə aktiv sorğu yoxdur."
        if message:
            try:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk,
                )
                return
            except Exception:
                pass
        bot.send_message(chat_id, text, reply_markup=mk)
        return

    for req in rows:
        bot.send_message(chat_id, format_admin_request_list_item(req))

    mk = build_admin_request_list_nav(req_type, rayon, current_page, total_pages)
    footer = (
        f"📄 Səhifə: {current_page}/{total_pages}\n"
        f"📍 Rayon: {rayon}\n"
        f"🏠 Tip: {format_admin_request_type(req_type)}\n"
        f"Cəmi: {total}"
    )
    if message:
        try:
            bot.edit_message_text(
                footer,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, footer, reply_markup=mk)


def show_customer_requests_overview(chat_id: int, period: str = "day"):
    stats = fetch_customer_request_stats(period)
    text_lines = [
        f"📌 Müştəri istəkləri — {('Bu gün' if period=='day' else 'Bu həftə' if period=='week' else 'Bu ay')}",
        "",
        f"Ümumi istək sayı: {stats['total']}",
        f"Satınalma: {stats['buy']} | Kirayə: {stats['rent']}",
        "",
        "Rayonlara görə dağılım:",
    ]
    if stats["rayons"]:
        text_lines += [f"• {r}: {cnt}" for r, cnt in stats["rayons"]]
    else:
        text_lines.append("• Sorğu yoxdur")

    mk = types.InlineKeyboardMarkup()
    period_buttons = build_period_tabs(period)
    mk.row(*period_buttons)
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_flagged"], callback_data="adm_req_viewflag:1"
        )
    )

    for i in range(0, len(stats["rayons"]), 2):
        row = []
        for rayon, cnt in stats["rayons"][i : i + 2]:
            encoded = quote(rayon, safe="")
            row.append(
                types.InlineKeyboardButton(
                    TEXTS_AZ["admin_req_rayon_item"].format(rayon=rayon, count=cnt),
                    callback_data=f"adm_req:{period}:{encoded}:1",
                )
            )
        mk.row(*row)

    bot.send_message(chat_id, "\n".join(text_lines), reply_markup=mk)


def format_request_type(req_type: str) -> str:
    if req_type == "buy":
        return "Satınalma"
    if req_type == "rent":
        return "Kirayə"
    return req_type or "-"


def format_request_rule_type(req_type: str) -> str:
    if req_type == "buy":
        return "Satılır"
    if req_type == "rent":
        return "Kirayə"
    return req_type or "-"


def build_whatsapp_link(req: dict) -> Optional[str]:
    phone_raw = req.get("phone") or ""
    phone_clean = re.sub(r"\D", "", phone_raw)
    if not phone_clean:
        return None
    if phone_clean.startswith("994"):
        target = phone_clean
    elif len(phone_clean) == 9:
        target = "994" + phone_clean
    elif len(phone_clean) == 10 and phone_clean.startswith("0"):
        target = "994" + phone_clean[1:]
    else:
        target = phone_clean

    msg = (
        "Salam! BestHome-dan sorğunuza görə yazıram. "
        f"Tip: {format_request_type(req.get('request_type'))}; "
        f"Rayon: {req.get('rayon') or '-'}; "
        f"Otaq: {req.get('rooms') or '-'}; "
        f"Büdcə: {req.get('budget') or '-'}; "
        f"Qeyd: {req.get('notes') or '-'}"
    )
    return f"https://wa.me/{target}?text={quote(msg)}"


def build_telegram_user_link(user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    return f"tg://user?id={int(user_id)}"


def format_request_card(req: dict) -> str:
    created_dt = parse_dt_safe(req.get("created_at"))
    display_time = created_dt + timedelta(hours=4) if created_dt else None
    created_at = display_time.strftime("%Y-%m-%d %H:%M") if display_time else "bilinmir"
    req_type = format_request_type(req.get("request_type"))
    return "\n".join(
        [
            f"🆔 Sorğu ID: {req.get('id')}",
            f"📅 Tarix: {created_at}",
            f"📄 Tip: {req_type}",
            f"📍 Rayon: {req.get('rayon') or '-'}",
            f"🚪 Otaq: {req.get('rooms') or '-'}",
            f"💰 Büdcə: {req.get('budget') or '-'}",
            f"📝 Qeyd: {req.get('notes') or '-'}",
            f"📞 Telefon: {req.get('phone') or '-'}",
            f"🆔 Müştəri ID: {req.get('chat_id')}",
            f"📦 Status: {req.get('status')}",
            f"⭐ İşarə: {'Bəli' if req.get('flagged') else 'Xeyr'}",
        ]
    )


def format_public_request_card(req: dict) -> str:
    created_dt = parse_dt_safe(req.get("created_at"))
    display_time = created_dt + timedelta(hours=4) if created_dt else None
    created_at = display_time.strftime("%Y-%m-%d %H:%M") if display_time else "bilinmir"
    req_type = format_request_type(req.get("request_type"))
    return "\n".join(
        [
            "👥 Müştəri istəyi",
            f"🆔 Sorğu ID: {req.get('id')}",
            f"📅 Tarix: {created_at}",
            f"📄 Tip: {req_type}",
            f"📍 Rayon: {req.get('rayon') or '-'}",
            f"🚪 Otaq: {req.get('rooms') or '-'}",
            f"💰 Büdcə: {req.get('budget') or '-'}",
            f"📝 Qeyd: {req.get('notes') or '-'}",
            f"📞 Telefon: {req.get('phone') or '-'}",
            f"🆔 Müştəri ID: {req.get('chat_id') or '-'}",
        ]
    )


def build_public_request_actions(
    user_id: int,
    req: dict,
    extra_buttons: Optional[List[types.InlineKeyboardButton]] = None,
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    wa_link = build_whatsapp_link(req)
    tg_link = build_telegram_user_link(req.get("chat_id"))
    row = []
    if tg_link:
        row.append(types.InlineKeyboardButton("💬 Telegram yaz", url=tg_link))
    if wa_link:
        row.append(types.InlineKeyboardButton("📱 WhatsApp yaz", url=wa_link))
    if row:
        mk.row(*row)
    if tg_link:
        mk.add(
            types.InlineKeyboardButton(
                f"🆔 Müştəri ID: {req.get('chat_id')}",
                url=tg_link,
            )
        )
    mk.add(
        types.InlineKeyboardButton(
            "⭐ Arxivlə", callback_data=f"cust_req_arch:{req.get('id')}"
        )
    )
    if extra_buttons:
        for btn in extra_buttons:
            mk.add(btn)
    return mk


def send_public_request_card(
    chat_id: int,
    req: dict,
    extra_buttons: Optional[List[types.InlineKeyboardButton]] = None,
):
    mk = build_public_request_actions(chat_id, req, extra_buttons=extra_buttons)
    bot.send_message(chat_id, format_public_request_card(req), reply_markup=mk)


def send_request_card(chat_id: int, req: dict):
    mk = types.InlineKeyboardMarkup()
    wa_link = build_whatsapp_link(req)
    customer_id = req.get("chat_id")
    if customer_id:
        mk.add(
            types.InlineKeyboardButton(
                text=TEXTS_AZ["admin_req_user_id"].format(user_id=customer_id),
                callback_data=f"admin_view_profile:{customer_id}",
            )
        )
        mk.row(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_req_user_activate"],
                callback_data=f"toggle_customer_request_user:{customer_id}:on",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_req_user_disable"],
                callback_data=f"toggle_customer_request_user:{customer_id}:off",
            ),
        )
    if wa_link:
        mk.add(types.InlineKeyboardButton(TEXTS_AZ["admin_req_whatsapp"], url=wa_link))
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_flag"], callback_data=f"adm_req_flag:{req['id']}"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_archive"], callback_data=f"adm_req_arch:{req['id']}"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_delete"], callback_data=f"adm_req_del:{req['id']}"
        )
    )
    bot.send_message(chat_id, format_request_card(req), reply_markup=mk)


def build_rayon_pagination(period: str, rayon: str, page: int, total_pages: int):
    mk = types.InlineKeyboardMarkup()
    encoded = quote(rayon, safe="")
    mk.row(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_first_icon"],
            callback_data=f"adm_req:{period}:{encoded}:1",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_prev_icon"],
            callback_data=f"adm_req:{period}:{encoded}:{max(1, page-1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_page"].format(page=page, total=total_pages),
            callback_data="adm_req_nop:page",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_next_icon"],
            callback_data=f"adm_req:{period}:{encoded}:{min(total_pages, page+1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_last_icon"],
            callback_data=f"adm_req:{period}:{encoded}:{total_pages}",
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_rayon_list"],
            callback_data=f"adm_req_period:{period}",
        )
    )
    return mk


def show_customer_requests_by_rayon(
    chat_id: int, period: str, rayon: str, page: int = 1
):
    start_dt = get_request_period_start(period)
    conn = get_local_conn()
    cur = conn.cursor()
    params = [start_dt.isoformat(), rayon]
    cur.execute(
        """
        SELECT COUNT(*) FROM customer_requests
        WHERE status='active' AND datetime(created_at) >= datetime(?)
          AND LOWER(rayon) = LOWER(?)
        """,
        params,
    )
    total = cur.fetchone()[0]
    total_pages = max(1, math.ceil(total / PAGE_SIZE_REQ)) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_REQ
    cur.execute(
        """
        SELECT * FROM customer_requests
        WHERE status='active' AND datetime(created_at) >= datetime(?)
          AND LOWER(rayon) = LOWER(?)
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        params + [PAGE_SIZE_REQ, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        bot.send_message(chat_id, f"📭 Bu rayon üzrə ({rayon}) aktiv sorğu yoxdur.")
        return

    for req in rows:
        send_request_card(chat_id, req)

    mk = build_rayon_pagination(period, rayon, page, total_pages)
    bot.send_message(
        chat_id,
        f"📄 Səhifə {page}/{total_pages} — {rayon} üçün sorğular",
        reply_markup=mk,
    )


def fetch_flagged_requests(page: int = 1):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM customer_requests WHERE flagged=1 AND status='active'"
    )
    total = cur.fetchone()[0]
    total_pages = max(1, math.ceil(total / PAGE_SIZE_REQ)) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_REQ
    cur.execute(
        """
        SELECT * FROM customer_requests
        WHERE flagged=1 AND status='active'
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (PAGE_SIZE_REQ, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total_pages


def show_flagged_requests(chat_id: int, page: int = 1):
    rows, total_pages = fetch_flagged_requests(page)
    if not rows:
        bot.send_message(chat_id, "⭐ İşarələnmiş sorğu yoxdur.")
        return
    for req in rows:
        send_request_card(chat_id, req)

    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_first_icon"], callback_data="adm_req_viewflag:1"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_prev_icon"],
            callback_data=f"adm_req_viewflag:{max(1, page-1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_page"].format(page=page, total=total_pages),
            callback_data="adm_req_nop:flag",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_next_icon"],
            callback_data=f"adm_req_viewflag:{min(total_pages, page+1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_last_icon"],
            callback_data=f"adm_req_viewflag:{total_pages}",
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_rayon_list"], callback_data="adm_req_period:day"
        )
    )
    bot.send_message(chat_id, "⭐ İşarələnən sorğular:", reply_markup=mk)


def format_archived_request_card(req: dict) -> str:
    created_dt = parse_dt_safe(req.get("created_at"))
    display_time = created_dt + timedelta(hours=4) if created_dt else None
    created_at = display_time.strftime("%Y-%m-%d %H:%M") if display_time else "-"
    req_type = format_request_type(req.get("request_type"))
    return "\n".join(
        [
            "🗄 Arxivlənmiş sorğu",
            f"📍 Rayon: {req.get('rayon') or '-'}",
            f"📅 Tarix: {created_at}",
            f"📄 Tip: {req_type}",
            f"🆔 Müştəri ID: {req.get('chat_id') or '-'}",
            f"💰 Büdcə: {req.get('budget') or '-'}",
        ]
    )


def fetch_archived_requests(page: int = 1):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM customer_requests WHERE status='archived'")
    total = cur.fetchone()[0]
    total_pages = max(1, math.ceil(total / PAGE_SIZE_REQ)) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_REQ
    cur.execute(
        """
        SELECT * FROM customer_requests
        WHERE status='archived'
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (PAGE_SIZE_REQ, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total_pages


def show_archived_requests(chat_id: int, page: int = 1):
    rows, total_pages = fetch_archived_requests(page)
    if not rows:
        bot.send_message(chat_id, "🗄 Arxivlənmiş sorğu yoxdur.")
        return
    for req in rows:
        mk = types.InlineKeyboardMarkup()
        mk.row(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_req_restore"],
                callback_data=f"adm_req_restore:{req['id']}",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_req_delete_full"],
                callback_data=f"adm_req_del:{req['id']}",
            ),
        )
        bot.send_message(chat_id, format_archived_request_card(req), reply_markup=mk)

    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_first_icon"], callback_data="adm_req_archived:1"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_prev_icon"],
            callback_data=f"adm_req_archived:{max(1, page-1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_page"].format(page=page, total=total_pages),
            callback_data="adm_req_nop:archived",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_next_icon"],
            callback_data=f"adm_req_archived:{min(total_pages, page+1)}",
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_nav_last_icon"],
            callback_data=f"adm_req_archived:{total_pages}",
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_req_back_requests"],
            callback_data="adm_req_period:day",
        )
    )
    bot.send_message(chat_id, "🗄 Arxivlənmiş sorğular:", reply_markup=mk)


def fetch_user_archived_requests(user_id: int, page: int = 1):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM customer_request_archives ca
        JOIN customer_requests cr ON cr.id = ca.request_id
        WHERE ca.user_id=? AND cr.status!='deleted'
        """,
        (user_id,),
    )
    total = cur.fetchone()[0]
    total_pages = max(1, math.ceil(total / PAGE_SIZE_REQ)) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_REQ
    cur.execute(
        """
        SELECT cr.*, ca.created_at as archived_at
        FROM customer_request_archives ca
        JOIN customer_requests cr ON cr.id = ca.request_id
        WHERE ca.user_id=? AND cr.status!='deleted'
        ORDER BY datetime(ca.created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, PAGE_SIZE_REQ, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total_pages, page


def show_user_archived_requests(chat_id: int, page: int = 1, message=None):
    rows, total_pages, current_page = fetch_user_archived_requests(chat_id, page)
    if not rows:
        mk_empty = types.InlineKeyboardMarkup()
        mk_empty.add(
            types.InlineKeyboardButton(
                "⬅️ Müştəri istəkləri", callback_data="agent_requests"
            )
        )
        try:
            if message:
                bot.edit_message_text(
                    "📦 Arxivlənmiş sorğu yoxdur.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk_empty,
                )
            else:
                bot.send_message(
                    chat_id, "📦 Arxivlənmiş sorğu yoxdur.", reply_markup=mk_empty
                )
        except Exception:
            pass
        return

    header = f"📦 Arxivlənmiş istəklər — Səhifə {current_page}/{total_pages}"
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton("⏮ İlk", callback_data="cust_req_archived:1"),
        types.InlineKeyboardButton(
            "◀️ Geri", callback_data=f"cust_req_archived:{max(1, current_page - 1)}"
        ),
        types.InlineKeyboardButton(
            f"📄 {current_page}/{total_pages}",
            callback_data=f"cust_req_archived:{current_page}",
        ),
        types.InlineKeyboardButton(
            "▶️ İrəli",
            callback_data=f"cust_req_archived:{min(total_pages, current_page + 1)}",
        ),
        types.InlineKeyboardButton(
            "⏭ Son", callback_data=f"cust_req_archived:{total_pages}"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "⬅️ Müştəri istəkləri", callback_data="agent_requests"
        )
    )

    try:
        if message:
            bot.edit_message_text(
                header,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, header, reply_markup=mk)
    except Exception:
        pass

    for row in rows:
        mk_card = types.InlineKeyboardMarkup()
        mk_card.add(
            types.InlineKeyboardButton(
                "♻️ Arxivdən çıxar",
                callback_data=f"cust_req_unarch:{_row_value_safe(row, 'id')}",
            )
        )
        try:
            bot.send_message(
                chat_id, format_public_request_card(row), reply_markup=mk_card
            )
        except Exception:
            continue


def format_customer_request_rule_summary(rule: dict) -> str:
    req_type = format_request_rule_type(rule.get("request_type"))
    rayons_raw = rule.get("rayons") or ""
    rayons_txt = rayons_raw if rayons_raw else "Hamısı"
    price_min = rule.get("price_min")
    price_max = rule.get("price_max")
    if price_min is not None or price_max is not None:
        price_txt = f"{price_min or 0} - {price_max or '∞'}"
    else:
        price_txt = "Məhdudiyyətsiz"
    rooms = rule.get("rooms") or "Hamısı"
    keyword = rule.get("keyword") or "Yoxdur"
    status_txt = "🟢 Aktiv" if rule.get("is_active") else "🔴 Deaktiv"
    return (
        f"{status_txt} | {req_type} | 📍 {rayons_txt} | 💰 {price_txt} | "
        f"🛏 {rooms} | 🔎 {keyword}"
    )


def fetch_customer_request_rules(user_id: int) -> List[dict]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM customer_request_rules
        WHERE user_id=?
        ORDER BY datetime(created_at) DESC
        """,
        (user_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def show_customer_request_rules(chat_id: int, message: Optional[types.Message] = None):
    rules = fetch_customer_request_rules(chat_id)
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ Yeni qayda yarat", callback_data="cust_req_rules_new"
        )
    )
    for rule in rules:
        rule_id = rule.get("id")
        label = format_customer_request_rule_summary(rule)
        mk.add(
            types.InlineKeyboardButton(
                label, callback_data=f"cust_req_rule_toggle:{rule_id}"
            )
        )
    mk.add(
        types.InlineKeyboardButton(
            "⬅️ Müştəri istəkləri", callback_data="agent_requests"
        )
    )
    text = "🔔 Bildiriş qaydaları"
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def build_customer_request_rule_type_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Satılır", "🏢 Kirayə")
    kb.row("⬅️ Geri")
    return kb


def build_customer_request_rule_rayon_markup(
    selected: List[str],
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    row = []
    selected_set = {s.lower() for s in selected}
    for rayon in REQUEST_RAYONS:
        label = f"✅ {rayon}" if rayon.lower() in selected_set else rayon
        row.append(
            types.InlineKeyboardButton(
                label, callback_data=f"cr_rule_rayon_toggle:{quote(rayon)}"
            )
        )
        if len(row) == 3:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(types.InlineKeyboardButton("✅ Bitdi", callback_data="cr_rule_rayon_done"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="cr_rule_rayon_back"))
    return mk


def start_customer_request_rule_flow(chat_id: int):
    customer_request_rule_state[chat_id] = {"step": "type"}
    bot.send_message(
        chat_id,
        "🔔 Bildiriş qaydası üçün tip seçin:",
        reply_markup=build_customer_request_rule_type_keyboard(),
    )


def send_customer_request_rule_rayon_prompt(chat_id: int, message=None):
    selected = customer_request_rule_state.get(chat_id, {}).get("rayons", [])
    mk = build_customer_request_rule_rayon_markup(selected)
    text = "📍 Rayon seçin (bir neçəsini seçə bilərsiniz):"
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)


def build_optional_input_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⚪️ Keç")
    kb.row("⬅️ Geri")
    return kb


def save_customer_request_rule(user_id: int, data: dict) -> Optional[int]:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO customer_request_rules (
            user_id, request_type, rayons, price_min, price_max, rooms, keyword, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            data.get("request_type"),
            data.get("rayons"),
            data.get("price_min"),
            data.get("price_max"),
            data.get("rooms"),
            data.get("keyword"),
        ),
    )
    rule_id = cur.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def set_customer_request_rule_active(user_id: int, rule_id: int, active: bool):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE customer_request_rules
        SET is_active=?
        WHERE id=? AND user_id=?
        """,
        (1 if active else 0, rule_id, user_id),
    )
    conn.commit()
    conn.close()


def delete_customer_request_alert(user_id: int, request_id: int, rule_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM customer_request_alerts
        WHERE user_id=? AND request_id=? AND rule_id=?
        """,
        (user_id, request_id, rule_id),
    )
    conn.commit()
    conn.close()


def fetch_customer_request_alerts(user_id: int, period: str, page: int = 1):
    conn = get_local_conn()
    cur = conn.cursor()
    period_clause, period_params = build_period_filter(period, "ca.created_at")
    where = f"ca.user_id=? AND cr.status!='deleted'{period_clause}"
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM customer_request_alerts ca
        JOIN customer_requests cr ON cr.id = ca.request_id
        WHERE {where}
        """,
        [user_id] + period_params,
    )
    total = cur.fetchone()[0] or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_NOTIFICATIONS)) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_NOTIFICATIONS
    cur.execute(
        f"""
        SELECT ca.request_id, ca.rule_id, ca.created_at, cr.*
        FROM customer_request_alerts ca
        JOIN customer_requests cr ON cr.id = ca.request_id
        WHERE {where}
        ORDER BY datetime(ca.created_at) DESC
        LIMIT ? OFFSET ?
        """,
        [user_id] + period_params + [PAGE_SIZE_NOTIFICATIONS, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows, total, total_pages, page


def format_customer_request_alert_line(idx: int, row: dict) -> str:
    req_type = format_request_type(_row_value_safe(row, "request_type"))
    rayon = _row_value_safe(row, "rayon") or "-"
    rooms = _row_value_safe(row, "rooms") or "-"
    budget = _row_value_safe(row, "budget") or "-"
    req_id = _row_value_safe(row, "request_id") or _row_value_safe(row, "id") or "-"
    return (
        f"{idx}. 🆔 {req_id} | {req_type} | 📍 {rayon} | " f"🚪 {rooms} | 💰 {budget}"
    )


def show_customer_request_alerts_inbox(
    chat_id: int, period: str, page: int = 1, message: Optional[types.Message] = None
):
    rows, total, total_pages, current_page = fetch_customer_request_alerts(
        chat_id, period, page
    )
    if total == 0:
        mk_empty = types.InlineKeyboardMarkup()
        mk_empty.add(
            types.InlineKeyboardButton(
                "🔔 Bildiriş qaydaları", callback_data="cust_req_rules"
            )
        )
        mk_empty.add(
            types.InlineKeyboardButton("⬅️ Bildirişlər", callback_data="notif_menu")
        )
        try:
            if message:
                bot.edit_message_text(
                    "🔔 Yeni bildiriş yoxdur.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk_empty,
                )
            else:
                bot.send_message(
                    chat_id, "🔔 Yeni bildiriş yoxdur.", reply_markup=mk_empty
                )
        except Exception:
            pass
        return

    period_labels = {"today": "Bu gün", "week": "Bu həftə", "month": "Bu ay"}
    header = (
        f"🔔 Müştəri istəkləri ({period_labels.get(period, 'Bu gün')})\n"
        f"Səhifə: {current_page}/{total_pages}\nCəmi: {total}\n"
    )
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(
            "⏮ İlk", callback_data=f"cust_req_alerts:{period}:1"
        ),
        types.InlineKeyboardButton(
            "◀️ Geri",
            callback_data=f"cust_req_alerts:{period}:{max(1, current_page - 1)}",
        ),
        types.InlineKeyboardButton(
            f"📄 {current_page}/{total_pages}",
            callback_data=f"cust_req_alerts:{period}:{current_page}",
        ),
        types.InlineKeyboardButton(
            "▶️ İrəli",
            callback_data=f"cust_req_alerts:{period}:{min(total_pages, current_page + 1)}",
        ),
        types.InlineKeyboardButton(
            "⏭ Son", callback_data=f"cust_req_alerts:{period}:{total_pages}"
        ),
    )
    view_buttons = []
    for idx, row in enumerate(rows, start=1):
        header += format_customer_request_alert_line(idx, row) + "\n"
        req_id = _row_value_safe(row, "request_id") or _row_value_safe(row, "id")
        rule_id = _row_value_safe(row, "rule_id")
        if req_id is not None:
            view_buttons.append(
                types.InlineKeyboardButton(
                    f"👁 {req_id}", callback_data=f"cr_alert_view:{req_id}:{rule_id}"
                )
            )
    if view_buttons:
        for i in range(0, len(view_buttons), 3):
            mk.row(*view_buttons[i : i + 3])
    mk.add(types.InlineKeyboardButton("⬅️ Bildirişlər", callback_data="notif_menu"))
    try:
        if message:
            bot.edit_message_text(
                header.strip(),
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, header.strip(), reply_markup=mk)
    except Exception:
        pass


def send_financial_reports_menu(chat_id: int):
    if not is_admin(chat_id):
        return

    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(
            "📜 Ödəniş tarixçəsi", callback_data="finrep:history"
        ),
        types.InlineKeyboardButton(
            "🤝 Referral statistikası", callback_data="finrep:referral"
        ),
    )
    mk.row(
        types.InlineKeyboardButton(
            "📈 Aylıq gəlir hesabatı", callback_data="finrep:monthly"
        )
    )
    mk.add(
        types.InlineKeyboardButton(FINANCIAL_REPORTS_BACK, callback_data="finrep:back")
    )
    bot.send_message(chat_id, "💰 Maliyyə hesabatları:", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("finrep:"))
@callback_guard
def handle_financial_reports_menu(c):
    if not is_admin(c.from_user.id):
        return
    action = c.data.split(":", 1)[1]
    chat_id = c.message.chat.id
    if action == "history":
        show_payment_history_list(chat_id, page=1)
    elif action == "referral":
        show_referral_stats(chat_id)
    elif action == "monthly":
        show_revenue_report(chat_id)
    elif action == "back":
        page = admin_panel_page_state.get(chat_id, 1)
        send_admin_panel(chat_id, page=page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_period:"))
@callback_guard
def cb_admin_request_period(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    period = c.data.split(":", 1)[1]
    show_customer_requests_overview(c.message.chat.id, period)


@bot.callback_query_handler(func=lambda c: c.data == "adm_req_types")
@callback_guard
def cb_admin_request_types(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    show_admin_customer_request_types(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_type:"))
@callback_guard
def cb_admin_request_type_select(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    req_type = c.data.split(":", 1)[1]
    show_admin_request_rayons(c.message.chat.id, req_type, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_rayons:"))
@callback_guard
def cb_admin_request_rayons(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    req_type = c.data.split(":", 1)[1]
    show_admin_request_rayons(c.message.chat.id, req_type, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_rayon:"))
@callback_guard
def cb_admin_request_rayon_list(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    parts = c.data.split(":", 3)
    if len(parts) < 4:
        return
    _, req_type, encoded_rayon, page_str = parts
    try:
        rayon = unquote(encoded_rayon)
    except Exception:
        rayon = encoded_rayon
    try:
        page = int(page_str)
    except Exception:
        page = 1
    show_admin_requests_by_rayon(
        c.message.chat.id, req_type, rayon, page=page, message=c.message
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_req_main")
@callback_guard
def cb_admin_request_main_menu(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    return_to_main_menu(c.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req:"))
@callback_guard
def cb_admin_request_rayon(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    parts = c.data.split(":", 3)
    if len(parts) < 4:
        return
    _, period, encoded_rayon, page_str = parts
    rayon = encoded_rayon
    try:
        rayon = unquote(encoded_rayon)
    except Exception:
        pass
    page = 1
    try:
        page = int(page_str)
    except Exception:
        pass
    show_customer_requests_by_rayon(c.message.chat.id, period, rayon, page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_flag:"))
@callback_guard
def cb_admin_request_flag(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    req_id = c.data.split(":", 1)[1]
    if not ensure_customer_request_action_allowed(c.message.chat.id, req_id):
        return
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_requests SET flagged=1 WHERE id=?",
        (req_id,),
    )
    conn.commit()
    conn.close()
    bot.send_message(c.message.chat.id, f"⭐ Sorğu işarələndi (ID: {req_id}).")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_arch:"))
@callback_guard
def cb_admin_request_archive(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    req_id = c.data.split(":", 1)[1]
    if not ensure_customer_request_action_allowed(c.message.chat.id, req_id):
        return
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_requests SET status='archived' WHERE id=?",
        (req_id,),
    )
    conn.commit()
    conn.close()
    bot.send_message(c.message.chat.id, f"🗄 Sorğu arxivləndi (ID: {req_id}).")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_restore:"))
@callback_guard
def cb_admin_request_restore(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    req_id = c.data.split(":", 1)[1]
    if not ensure_customer_request_action_allowed(c.message.chat.id, req_id):
        return
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_requests SET status='active' WHERE id=?",
        (req_id,),
    )
    conn.commit()
    conn.close()
    bot.send_message(c.message.chat.id, f"♻️ Sorğu aktiv edildi (ID: {req_id}).")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_del:"))
@callback_guard
def cb_admin_request_delete(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    req_id = c.data.split(":", 1)[1]
    if not ensure_customer_request_action_allowed(c.message.chat.id, req_id):
        return
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE customer_requests SET status='deleted' WHERE id=?",
        (req_id,),
    )
    conn.commit()
    conn.close()
    bot.send_message(c.message.chat.id, f"🗑 Sorğu silindi (ID: {req_id}).")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_archived:"))
@callback_guard
def cb_admin_request_archived(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    page = 1
    try:
        page = int(c.data.split(":", 1)[1])
    except Exception:
        pass
    show_archived_requests(c.message.chat.id, page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_viewflag:"))
@callback_guard
def cb_admin_request_view_flagged(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    page = 1
    try:
        page = int(c.data.split(":", 1)[1])
    except Exception:
        pass
    show_flagged_requests(c.message.chat.id, page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_req_nop"))
@callback_guard
def cb_admin_request_noop(c):
    if not is_admin(c.from_user.id):
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_view_profile:"))
@callback_guard
def admin_open_user_profile(c):
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":", 1)
    if len(parts) < 2:
        return
    try:
        user_id = int(parts[1])
    except Exception:
        return
    show_user_profile(c.message.chat.id, user_id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("toggle_customer_requests:")
)
@callback_guard
def toggle_customer_requests(c):
    return
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":", 1)
    if len(parts) < 2:
        return
    try:
        user_id = int(parts[1])
    except Exception:
        return
    current = get_customer_requests_enabled(user_id)
    set_customer_requests_enabled(user_id, not current)
    bot.send_message(
        c.message.chat.id,
        (
            f"✅ ID {user_id} üçün müştəri istəkləri AKTİV edildi"
            if not current
            else f"🔴 ID {user_id} üçün müştəri istəkləri BAĞLANDI"
        ),
    )
    show_user_profile(c.message.chat.id, user_id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_access:"))
@callback_guard
def cb_customer_requests_access(c):
    return
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":")
    if len(parts) < 3:
        return
    action = parts[1]
    try:
        user_id = int(parts[2])
    except Exception:
        return
    if action == "on":
        set_customer_requests_enabled(user_id, True)
        message = f"✅ ID {user_id} üçün müştəri istəkləri AKTİV edildi"
    elif action == "off":
        set_customer_requests_enabled(user_id, False)
        message = f"🔴 ID {user_id} üçün müştəri istəkləri BAĞLANDI"
    else:
        return
    bot.send_message(c.message.chat.id, message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "cust_req_access_add")
@callback_guard
def cb_customer_requests_access_add(c):
    return
    if not is_admin(c.from_user.id):
        return
    msg = bot.send_message(
        c.message.chat.id, "🆔 Telegram istifadəçi ID-sini daxil edin:"
    )
    bot.register_next_step_handler(msg, admin_customer_requests_access_step)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("cust_req_access_disable:")
)
@callback_guard
def cb_customer_requests_access_disable(c):
    return
    if not is_admin(c.from_user.id):
        return
    try:
        user_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    set_customer_requests_enabled(user_id, False)
    bot.send_message(
        c.message.chat.id, f"🔴 ID {user_id} üçün müştəri istəkləri söndürüldü."
    )
    show_customer_requests_access_admin(c.message.chat.id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("toggle_customer_request_user:")
)
@callback_guard
def toggle_customer_request_user(c):
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":")
    if len(parts) < 2:
        return
    try:
        user_id = int(parts[1])
    except Exception:
        return
    action = parts[2] if len(parts) > 2 else None
    if action in {"on", "enable", "1"}:
        enabled = True
    elif action in {"off", "disable", "0"}:
        enabled = False
    else:
        enabled = not get_customer_requests_enabled(user_id)
    set_customer_requests_enabled(user_id, enabled)
    message = (
        f"✅ ID {user_id} üçün müştəri istəkləri AKTİV edildi"
        if enabled
        else f"🔴 ID {user_id} üçün müştəri istəkləri BAĞLANDI"
    )
    bot.send_message(c.message.chat.id, message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def get_user_phone_for_admin(user_id: int) -> Optional[str]:
    conn = get_local_conn()
    cur = conn.cursor()
    phone = None
    try:
        cur.execute(
            """
            SELECT phone FROM customer_requests
            WHERE chat_id=? AND phone IS NOT NULL AND phone!=''
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            phone = row["phone"] if isinstance(row, dict) else row[0]

        if not phone:
            cur.execute(
                """
                SELECT phone FROM listings_approved
                WHERE chat_id=? AND phone IS NOT NULL AND phone!=''
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                phone = row["phone"] if isinstance(row, dict) else row[0]

        if not phone:
            cur.execute(
                """
                SELECT phone FROM listings_new
                WHERE chat_id=? AND phone IS NOT NULL AND phone!=''
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                phone = row["phone"] if isinstance(row, dict) else row[0]

        if not phone:
            cur.execute(
                "SELECT phone FROM agents WHERE chat_id=?",
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                phone = row["phone"] if isinstance(row, dict) else row[0]
    finally:
        conn.close()

    return phone


def build_admin_whatsapp_url(phone_raw: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", phone_raw or "")
    if not digits:
        return None
    return f"https://wa.me/{digits}"


def show_user_profile(chat_id: int, user_id: int):
    record = get_user_record(user_id) or {}
    profile_url = get_profile_url_for_user(user_id)
    phone_raw = get_user_phone_for_admin(user_id)
    wa_url = build_admin_whatsapp_url(phone_raw)
    customer_requests_enabled = has_customer_requests_access(user_id)
    username = record.get("username")
    computed_status = record.get("computed_status") or get_user_computed_status(user_id)
    effective_raw = record.get("effective_expires_at")
    _, used_today, last_used_at, _ = ensure_chance_usage_state(
        user_id, record, datetime.utcnow()
    )
    display_last_used = last_used_at + timedelta(hours=4) if last_used_at else None
    last_chance_text = (
        display_last_used.strftime("%Y-%m-%d %H:%M") if display_last_used else "-"
    )
    last_spin_text = last_chance_text

    profile_text = "\n".join(
        [
            "👤 Profil",
            f'🆔 ID: <a href="{profile_url}">{user_id}</a>',
            f"👤 Ad: {record.get('full_name') or '-'}",
            f"👤 Username: @{username}" if username else "👤 Username: -",
            f"📞 Telefon: {phone_raw or '-'}",
            f"📦 Status: {computed_status or '-'}",
            f"📅 Bitmə tarixi: {format_effective_expiry_for_ui(effective_raw)}",
            f"⏳ Qalan gün: {format_remaining_days_for_ui(computed_status, effective_raw)}",
            f"🎡 Son klik: {last_spin_text}",
            f"🕒 Son şans istifadəsi: {last_chance_text}",
            "🎁 Şans: 24 saata 1 dəfə",
            f"🚦 Bugün istifadə: {'Bəli' if used_today else 'Xeyr'}",
        ]
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(
            "📨 Botdan mesaj göndər",
            callback_data=f"admin_send_message:{user_id}",
        )
    ]
    if wa_url:
        buttons.append(types.InlineKeyboardButton("💬 WhatsApp-da yaz", url=wa_url))
    toggle_text = (
        "🔴 Müştəri istəkləri: Söndür"
        if customer_requests_enabled
        else "🟢 Müştəri istəkləri: Aktiv et"
    )
    buttons.append(
        types.InlineKeyboardButton(
            toggle_text,
            callback_data=f"toggle_customer_requests:{user_id}",
        )
    )
    markup.add(*buttons)

    bot.send_message(
        chat_id,
        profile_text,
        reply_markup=markup,
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_send_message:"))
@callback_guard
def admin_start_message(c):
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":", 1)
    if len(parts) < 2:
        return
    try:
        user_id = int(parts[1])
    except Exception:
        return
    admin_message_state[c.message.chat.id] = user_id
    bot.send_message(c.message.chat.id, "✍️ Mesajı yazın, göndəriləcək:")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.message_handler(func=lambda m: m.chat.id in admin_message_state)
def admin_send_text(m):
    if m.text and m.text.startswith('/'):
        return

    if not is_admin(m.chat.id):
        return
    target_user_id = admin_message_state.pop(m.chat.id)
    bot.send_message(
        target_user_id,
        f"📩 Admin mesajı:\n\n{m.text}",
    )
    bot.send_message(m.chat.id, "✅ Mesaj göndərildi")


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


def get_demo_profile_url(user_id: int, _username: Optional[str]) -> str:
    return f"tg://user?id={user_id}"


def resolve_demo_expiry(row: sqlite3.Row) -> Optional[datetime]:
    expiry_raw = None
    if isinstance(row, sqlite3.Row):
        row_keys = set(row.keys())
        if "demo_end_at" in row_keys:
            expiry_raw = row["demo_end_at"]
        if not expiry_raw and "demo_expires_at" in row_keys:
            expiry_raw = row["demo_expires_at"]
        if expiry_raw is None and "effective_expires_at" in row_keys:
            expiry_raw = row["effective_expires_at"]
    else:
        try:
            expiry_raw = row["demo_end_at"] or row["demo_expires_at"]
        except Exception:
            expiry_raw = None
    return parse_dt_safe(expiry_raw)


def normalize_demo_user_display(
    full_name: Optional[str], username: Optional[str], user_id: int
) -> str:
    name = full_name or ""
    if username:
        uname = f"@{username}"
        name = f"{name} ({uname})" if name else uname
    if not name:
        name = f"ID: {user_id}"
    return html.escape(name)


def deactivate_expired_demo(
    user_id: int, expiry_dt: Optional[datetime], plan: Optional[str]
):
    if not expiry_dt:
        return


def build_demo_users_view(page: int = 1) -> Tuple[str, types.InlineKeyboardMarkup, int]:
    conn = get_local_conn()
    cur = conn.cursor()
    user_columns = _table_columns(conn, "users")
    base_query = admin_user_status_subquery()
    select_fields = [
        "chat_id",
        "full_name",
        "username",
        "effective_expires_at",
        "computed_status",
        "paid_until",
    ]
    if "demo_end_at" in user_columns:
        select_fields.append("demo_end_at")
    else:
        select_fields.append("NULL AS demo_end_at")
    if "demo_expires_at" in user_columns:
        select_fields.append("demo_expires_at")
    else:
        select_fields.append("NULL AS demo_expires_at")
    query = (
        f"SELECT {', '.join(select_fields)} FROM {base_query} "
        "WHERE (demo_end_at IS NOT NULL OR demo_expires_at IS NOT NULL)"
    )
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()

    logger.info("demo users fetched rows=%s", len(rows))

    if not rows:
        return "❌ Demo istifadəçisi yoxdur.", types.InlineKeyboardMarkup(), 1

    now = datetime.utcnow()
    entries = []

    for row in rows:
        expiry_dt = resolve_demo_expiry(row)
        is_active = bool(expiry_dt and expiry_dt > now)
        if not is_active:
            deactivate_expired_demo(row["chat_id"], expiry_dt, None)

        remaining_days = 0
        if expiry_dt and expiry_dt > now:
            remaining_days = math.ceil((expiry_dt - now).total_seconds() / 86400)

        display_expiry = expiry_dt + timedelta(hours=4) if expiry_dt else None
        expiry_txt = display_expiry.strftime("%Y-%m-%d %H:%M") if display_expiry else "-"
        status_txt = "🟢 Aktiv demo" if is_active else "🔴 Vaxtı bitmiş demo"
        profile_url = get_demo_profile_url(row["chat_id"], row["username"])
        display_name = normalize_demo_user_display(
            row["full_name"], row["username"], row["chat_id"]
        )

        entry_text = (
            f"👤 {display_name}\n"
            f"🆔 <a href=\"{profile_url}\">{row['chat_id']}</a>\n"
            f"⏳ Demo bitmə tarixi: {expiry_txt}\n"
            f"⏱ Qalan gün: {remaining_days} gün\n"
            f"🔄 Status: {status_txt}"
        )
        entries.append(
            {
                "user_id": row["chat_id"],
                "is_active": is_active,
                "expiry": expiry_dt,
                "text": entry_text,
                "profile_url": profile_url,
            }
        )

    active_entries = sorted(
        (e for e in entries if e["is_active"]),
        key=lambda e: e["expiry"] or datetime.max,
    )
    expired_entries = sorted(
        (e for e in entries if not e["is_active"]),
        key=lambda e: e["expiry"] or datetime.min,
        reverse=True,
    )
    ordered_entries = active_entries + expired_entries

    total_pages = max(1, math.ceil(len(ordered_entries) / PAGE_SIZE_DEMO_USERS))
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE_DEMO_USERS
    end = start + PAGE_SIZE_DEMO_USERS
    page_entries = ordered_entries[start:end]

    lines = [
        "🧪 Demo istifadəçilər",
        f"Səhifə: {page} / {total_pages}",
        "",
        "🔹 AKTİV DEMOLAR",
    ]
    active_in_page = [e for e in page_entries if e["is_active"]]
    if not active_in_page:
        lines.append("—")
    else:
        for entry in active_in_page:
            lines.append(entry["text"])
            lines.append("")

    lines.append("🔹 VAXTI BİTMİŞ DEMOLAR")
    expired_in_page = [e for e in page_entries if not e["is_active"]]
    if not expired_in_page:
        lines.append("—")
    else:
        for entry in expired_in_page:
            lines.append(entry["text"])
            lines.append("")

    text = "\n".join(lines)

    mk = types.InlineKeyboardMarkup()
    for entry in page_entries:
        uid = entry["user_id"]
        mk.add(types.InlineKeyboardButton("🧑‍💼 Profilə bax", url=entry["profile_url"]))
        mk.row(
            types.InlineKeyboardButton(
                "➕ +3 gün", callback_data=f"demo_users|add|{uid}|3|{page}"
            ),
            types.InlineKeyboardButton(
                "➕ +5 gün", callback_data=f"demo_users|add|{uid}|5|{page}"
            ),
            types.InlineKeyboardButton(
                "➕ +7 gün", callback_data=f"demo_users|add|{uid}|7|{page}"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "❌ Demo ləğv et", callback_data=f"demo_users|cancel|{uid}|{page}"
            )
        )

    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(
                types.InlineKeyboardButton(
                    "⬅️ Əvvəlki", callback_data=f"demo_users|page|{page - 1}"
                )
            )
        if page < total_pages:
            nav.append(
                types.InlineKeyboardButton(
                    "➡️ Növbəti", callback_data=f"demo_users|page|{page + 1}"
                )
            )
        if nav:
            mk.row(*nav)

    return text, mk, total_pages


def send_demo_users_report(chat_id: int, page: int = 1, message=None):
    if not is_admin(chat_id):
        return

    set_admin_state(chat_id, "admin_users_demo")
    text, mk, _ = build_demo_users_view(page=page)
    if message:
        try:
            bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message.message_id,
                reply_markup=mk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass

    bot.send_message(
        chat_id,
        text,
        reply_markup=mk,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def extend_demo_for_user(user_id: int, days: int) -> Optional[datetime]:
    record = get_user_record(user_id)
    if not record:
        return None
    return admin_grant_demo_days(user_id, days)


def cancel_demo_for_user(user_id: int):
    update_user_demo_end(user_id, None, approve=False)


@bot.callback_query_handler(func=lambda c: c.data.startswith("demo_users|"))
@callback_guard
def cb_demo_users_actions(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    if len(parts) < 2:
        return
    action = parts[1]
    chat_id = c.message.chat.id

    if action == "page" and len(parts) >= 3:
        try:
            page = int(parts[2])
        except Exception:
            page = 1
        send_demo_users_report(chat_id, page=page, message=c.message)
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return

    if action == "add" and len(parts) >= 5:
        try:
            user_id = int(parts[2])
            days = int(parts[3])
            page = int(parts[4])
        except Exception:
            return
        extend_demo_for_user(user_id, days)
        bot.send_message(
            chat_id, f"✅ {user_id} üçün demo müddəti +{days} gün uzadıldı."
        )
        send_demo_users_report(chat_id, page=page, message=c.message)
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass
        return

    if action == "cancel" and len(parts) >= 4:
        try:
            user_id = int(parts[2])
            page = int(parts[3])
        except Exception:
            return
        cancel_demo_for_user(user_id)
        bot.send_message(chat_id, f"✅ {user_id} üçün demo deaktiv edildi.")
        send_demo_users_report(chat_id, page=page, message=c.message)
        try:
            bot.answer_callback_query(c.id)
        except Exception:
            pass


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
    cur.execute("SELECT full_name, username FROM users WHERE chat_id=?", (target_id,))
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

    stale = cleanup_stale_db_updates()
    if stale:
        safe_admin_step(chat_id, "⚠️ Köhnə yenilənmə stuck idi, yenidən başladım.")

    running = get_running_db_update()
    if running:
        running_admin, state = running
        started_at = state.get("started_at", now_utc())
        remaining = DB_UPDATE_TTL_SECONDS - (now_utc() - started_at).total_seconds()
        message = (
            "⏳ Hal-hazırda baza yenilənir"
            f" (qalan: təxmini {format_seconds(remaining)})."
        )
        if callback_id:
            safe_answer_callback_query(callback_id, "⚠️ Baza yenilənir.")
        safe_admin_step(chat_id, message)
        return

    if callback_id:
        safe_answer_callback_query(callback_id, "📦 Baza yeniləmə")

    admin_update_state[chat_id] = "awaiting_db_link"
    set_db_update_state(chat_id, "awaiting_link")
    logger.info("Admin requested db update chat_id=%s", chat_id)
    safe_admin_step(
        chat_id,
        "🔗 Dropbox yükləmə linkini göndərin (besthome.db birbaşa yüklənəcək).",
    )


@bot.callback_query_handler(func=lambda c: c.data == "admin_update_db")
@callback_guard
def cb_admin_update_db(c):
    start_admin_update_db(c.message.chat.id, callback_id=c.id)


@bot.message_handler(
    content_types=["text"],
    func=lambda m: m.from_user
    and is_admin(m.chat.id)
    and admin_update_state.get(m.chat.id) == "awaiting_db_link",
)
def handle_admin_db_upload(message):
    chat_id = message.chat.id
    if message.text and message.text.startswith("/"):
        return
    if not message.from_user or not is_admin(chat_id):
        return

    if admin_update_state.get(chat_id) != "awaiting_db_link":
        return

    stale = cleanup_stale_db_updates()
    if stale:
        safe_admin_step(chat_id, "⚠️ Köhnə yenilənmə stuck idi, yenidən başladım.")

    url = message.text.strip() if message.text else ""
    url_lower = url.lower()
    parts = urlsplit(url)
    if (
        not url
        or parts.scheme.lower() != "https"
        or "dropbox" not in parts.netloc.lower()
    ):
        safe_admin_step(
            chat_id, "❌ Zəhmət olmasa HTTPS Dropbox yükləmə linki göndərin."
        )
        return

    running = get_running_db_update()
    if running:
        started_at = running[1].get("started_at", now_utc())
        remaining = DB_UPDATE_TTL_SECONDS - (now_utc() - started_at).total_seconds()
        safe_admin_step(
            chat_id,
            "⏳ Hal-hazırda baza yenilənir"
            f" (qalan: təxmini {format_seconds(remaining)}).",
        )
        return

    if not acquire_db_update_lock(chat_id):
        safe_admin_step(
            chat_id, "⏳ Hal-hazırda baza yenilənir. Zəhmət olmasa gözləyin."
        )
        return

    safe_admin_step(chat_id, "✅ Link alındı. ⏳ Yenilənir…")
    logger.info("Admin db update link received chat_id=%s url=%s", chat_id, url)
    admin_update_state[chat_id] = "updating_db"
    set_db_update_state(chat_id, "running")
    try:
        threading.Thread(
            target=run_db_update_pipeline,
            args=(chat_id, url),
            daemon=True,
        ).start()
    except Exception:
        release_db_update_lock(chat_id)
        admin_update_state.pop(chat_id, None)
        clear_db_update_state(chat_id)
        logger.exception("Failed to start db update thread chat_id=%s", chat_id)
        safe_admin_step(
            chat_id, "❌ Yenilənmə başladılmadı. Zəhmət olmasa yenidən yoxlayın."
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm|"))
@callback_guard
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
        send_paginated_results(
            c.message.chat.id, "topviews", params={"days": 7}, page=1
        )

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
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_generate_1"], callback_data="prm|gen|1"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_generate_3"], callback_data="prm|gen|3"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_generate_5"], callback_data="prm|gen|5"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_generate_7"], callback_data="prm|gen|7"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_list"], callback_data="prm|list|1"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_stats"], callback_data="prm|stats|1"
        )
    )
    bot.send_message(chat_id, TEXTS_AZ["admin_promo_menu_title"], reply_markup=mk)


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
        mk.add(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_back"], callback_data="adm|promos"
            )
        )
        bot.send_message(chat_id, TEXTS_AZ["admin_promo_empty"], reply_markup=mk)
        return

    lines = [TEXTS_AZ["admin_promo_list_title"]]
    for r in rows:
        status = (
            TEXTS_AZ["admin_promo_status_active"]
            if r["is_active"]
            else TEXTS_AZ["admin_promo_status_inactive"]
        )
        created_txt = "-"
        if r["created_at"]:
            try:
                created_dt = datetime.fromisoformat(str(r["created_at"]).replace(" ", "T"))
                display_time = created_dt + timedelta(hours=4)
                created_txt = display_time.strftime("%d.%m.%Y")
            except Exception:
                created_txt = str(r["created_at"])
        lines.append(f"{r['code']} — {r['days']} gün | {status} | {created_txt}")

    txt = "\n".join(lines) + f"\n\nSəhifə: {page}/{total_pages}"

    mk = types.InlineKeyboardMarkup()
    for r in rows:
        toggle_action = "deact" if r["is_active"] else "act"
        toggle_label = (
            TEXTS_AZ["admin_promo_toggle_inactive"]
            if r["is_active"]
            else TEXTS_AZ["admin_promo_toggle_active"]
        )
        mk.add(
            types.InlineKeyboardButton(
                f"{toggle_label} {r['code']}",
                callback_data=f"prm|{toggle_action}|{r['code']}|{page}",
            )
        )

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_prev"],
                callback_data=f"prm|list|{page-1}",
            )
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_next"],
                callback_data=f"prm|list|{page+1}",
            )
        )
    if nav_buttons:
        mk.add(*nav_buttons)

    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_back"], callback_data="adm|promos"
        )
    )
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
                exp_dt = (
                    datetime.strptime(exp_raw, "%Y-%m-%d %H:%M:%S") if exp_raw else None
                )
            except Exception:
                exp_dt = None

        for pay in payments_by_user.get(u["chat_id"], []):
            appr_raw = pay["approved_at"]
            try:
                appr_dt = datetime.fromisoformat(appr_raw) if appr_raw else None
            except Exception:
                try:
                    appr_dt = (
                        datetime.strptime(appr_raw, "%Y-%m-%d %H:%M:%S")
                        if appr_raw
                        else None
                    )
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
        mk.add(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_back"], callback_data="adm|promos"
            )
        )
        bot.send_message(chat_id, TEXTS_AZ["admin_promo_empty"], reply_markup=mk)
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
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_prev"],
                callback_data=f"prm|stats|{page-1}",
            )
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_next"],
                callback_data=f"prm|stats|{page+1}",
            )
        )
    if nav_buttons:
        mk.add(*nav_buttons)
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_promo_back"], callback_data="adm|promos"
        )
    )

    bot.send_message(chat_id, txt, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("prm|"))
@callback_guard
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
                c.message.chat.id,
                TEXTS_AZ["admin_promo_created"].format(days=days, code=code),
            )
        else:
            bot.send_message(c.message.chat.id, TEXTS_AZ["admin_promo_create_failed"])
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
    admin_show_user_panel(message.chat.id, target_id)


def _build_admin_user_markup(
    target_id: int, record: dict, unlimited: bool, blocked_flag: bool
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    chance_blocked = 1 if record.get("chance_blocked") else 0
    chance_action = "on" if chance_blocked else "off"
    chance_btn_text = "✅ Şansı aktiv et" if chance_blocked else "⛔ Şansı bağla"

    mk.row(
        types.InlineKeyboardButton(
            "🎁 Şansı sıfırla", callback_data=f"admusr|chance_reset|{target_id}"
        ),
        types.InlineKeyboardButton(
            chance_btn_text,
            callback_data=f"chance_toggle:{chance_action}:user:{target_id}",
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "➕ Gün əlavə et",
            callback_data=f"admusr|extend_custom|{target_id}",
        )
    )
    if record.get("blocked"):
        mk.add(
            types.InlineKeyboardButton(
                "✅ Blokdan çıxart",
                callback_data=f"admusr|unblock|{target_id}",
            )
        )
    else:
        mk.add(
            types.InlineKeyboardButton(
                "⛔ Blokla",
                callback_data=f"admusr|block|{target_id}",
            )
        )

    mk.add(
        types.InlineKeyboardButton(
            "❌ Limitsizi ləğv et" if unlimited else "♾️ Limitsiz et",
            callback_data=f"admusr|unlimit|{target_id}",
        )
    )

    mk.add(
        types.InlineKeyboardButton(
            "🗑 İstifadəçini sil", callback_data=f"admusr|delete|{target_id}"
        )
    )
    return mk


def admin_show_user_panel(
    admin_chat_id: int, target_id: int, message: Optional[types.Message] = None
):
    record = get_user_record(target_id) or {}
    if not record:
        bot.send_message(admin_chat_id, "⚠️ İstifadəçi tapılmadı.")
        return
    computed_status = record.get("computed_status") or get_user_computed_status(
        target_id
    )
    unlimited = is_user_unlimited(target_id)
    effective_raw = record.get("effective_expires_at")
    blocked_flag = bool(record.get("blocked") or record.get("is_blocked"))
    blocked_state = "Bəli" if blocked_flag else "Xeyr"
    is_active = record.get("is_active")
    status_text = "Bloklanıb" if blocked_flag else ("Deaktiv" if is_active == 0 else "Aktiv")
    last_error = record.get("last_error")
    _, used_today, last_used_at, _ = ensure_chance_usage_state(
        target_id, record, datetime.utcnow()
    )
    display_last_used = last_used_at + timedelta(hours=4) if last_used_at else None
    last_chance_text = (
        display_last_used.strftime("%Y-%m-%d %H:%M") if display_last_used else "-"
    )
    last_spin_text = last_chance_text
    join_source_code = record.get("join_source")
    join_source_text = (
        f"QR — {format_qr_area_label(join_source_code)}"
        if join_source_code
        else "Birbaşa /start"
    )

    info_txt = (
        f"🆔 İstifadəçi: <a href=\"tg://user?id={target_id}\">{target_id}</a>\n"
        f"🔗 Qoşulma mənbəyi: {html.escape(join_source_text)}\n"
        f"📦 Status: {html.escape(computed_status or '-')}\n"
        f"📅 Bitmə tarixi: {html.escape(format_effective_expiry_for_ui(effective_raw))}\n"
        f"⏳ Qalan gün: {html.escape(format_remaining_days_for_ui(computed_status, effective_raw))}\n"
        f"🔖 Hesab statusu: {html.escape(status_text)}\n"
        f"♾️ Limitsiz: {'Bəli' if unlimited else 'Xeyr'}\n"
        f"⛔ Bloklu: {blocked_state}\n"
        f"🎡 Son klik: {html.escape(last_spin_text)}\n"
        f"🕒 Son şans istifadəsi: {html.escape(last_chance_text)}\n"
        "🎁 Şans: 24 saata 1 dəfə\n"
        f"🚦 Bugün istifadə: {'Bəli' if used_today else 'Xeyr'}\n"
        f"🆔 Ödəniş kodu: {html.escape(subscription_payment_code(target_id))}"
    )
    if last_error:
        info_txt += f"\n⚠️ Son xəta: {html.escape(last_error)}"

    mk = _build_admin_user_markup(target_id, record, unlimited, blocked_flag)

    if message:
        try:
            bot.edit_message_text(
                info_txt,
                chat_id=admin_chat_id,
                message_id=message.message_id,
                reply_markup=mk,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass
    bot.send_message(admin_chat_id, info_txt, reply_markup=mk, parse_mode="HTML")


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
        bot.send_message(chat_id, TEXTS_AZ["admin_payment_history_none"])
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

    lines = [TEXTS_AZ["admin_payment_history_title"]]
    for r in rows:
        last_dt = "-"
        if r["last_payment_date"]:
            try:
                last_dt_value = datetime.fromisoformat(
                    str(r["last_payment_date"]).replace(" ", "T")
                )
                display_time = last_dt_value + timedelta(hours=4)
                last_dt = display_time.strftime("%d.%m.%Y")
            except Exception:
                last_dt = str(r["last_payment_date"])
        lines.append(f"🆔 {r['chat_id']} — {r['total_paid']} AZN (son: {last_dt})")

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
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_prev"], callback_data=f"payhist|{page-1}"
            )
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_next"], callback_data=f"payhist|{page+1}"
            )
        )
    if nav_buttons:
        mk.row(*nav_buttons)

    bot.send_message(chat_id, txt, reply_markup=mk)


def show_user_payment_details(
    admin_chat_id: int, target_id: int, page: int = 1, list_page: int = 1
):
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
                TEXTS_AZ["admin_payment_history_back"],
                callback_data=f"payhist|{list_page}",
            )
        )
        bot.send_message(
            admin_chat_id, "❌ Bu istifadəçinin ödənişləri yoxdur.", reply_markup=mk
        )
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
            last_dt_value = datetime.fromisoformat(
                str(summary["last_dt"]).replace(" ", "T")
            )
            display_time = last_dt_value + timedelta(hours=4)
            last_payment = display_time.strftime("%d.%m.%Y %H:%M")
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
                approved_dt = datetime.fromisoformat(
                    str(p["approved_at"]).replace(" ", "T")
                )
                display_time = approved_dt + timedelta(hours=4)
                pay_date = display_time.strftime("%d.%m.%Y %H:%M")
            except Exception:
                pay_date = str(p["approved_at"])
        lines.append(f"{idx}) {pay_date} — {p['plan']} — {p['amount']} AZN")

    mk = types.InlineKeyboardMarkup()
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_prev"],
                callback_data=f"paydetail|{target_id}|{page-1}|{list_page}",
            )
        )
    if page < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_promo_nav_next"],
                callback_data=f"paydetail|{target_id}|{page+1}|{list_page}",
            )
        )
    if nav_buttons:
        mk.row(*nav_buttons)

    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_payment_history_back"],
            callback_data=f"payhist|{list_page}",
        )
    )

    bot.send_message(
        admin_chat_id,
        header + "\n".join(lines),
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("payhist|"))
@callback_guard
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
@callback_guard
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

    show_user_payment_details(
        c.message.chat.id, target_id, page=page, list_page=list_page
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("subctl|"))
@callback_guard
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
    sub = get_subscription(uid) or {}
    exp_dt = parse_subscription_expiry(sub)

    if action == "add" and len(parts) > 3:
        try:
            days = int(parts[3])
        except Exception:
            days = 0
        if days > 0:
            admin_extend_user_time(uid, days, note=f"extend:{days}")
            try:
                bot.send_message(uid, f"✅ Hesabınız {days} gün uzadıldı")
            except Exception:
                pass
    elif action == "stop":
        block_user(uid)
        try:
            bot.send_message(uid, "🛑 Hesabınız deaktiv edildi")
        except Exception:
            pass
    elif action == "act":
        unblock_user(uid)
        base = resolve_extension_base(uid)
        new_exp = (
            base if base > datetime.utcnow() else datetime.utcnow() + timedelta(days=1)
        )
        insert_subscription(
            uid,
            sub.get("plan") or "manual",
            new_exp,
            is_demo=0,
            note="activated",
        )
        try:
            bot.send_message(uid, "✅ Hesabınız aktivləşdirildi")
        except Exception:
            pass

    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    admin_show_user_panel(c.message.chat.id, uid, message=c.message)


def admin_extend_user_time(
    user_id: int, days: int, note: str = "admin_extend"
) -> Optional[datetime]:
    logger.info(
        "besthome_bot: Admin extend user_id=%s days=%s note=%s", user_id, days, note
    )
    sub = get_subscription(user_id) or {}
    base = resolve_extension_base(user_id)
    new_exp = base + timedelta(days=days)
    plan_name = sub.get("plan") or f"manual {days}g"
    insert_subscription(user_id, plan_name, new_exp, is_demo=0, note=note)
    return new_exp


def bulk_extend_user_time(
    user_ids: List[int], days: int, note: str = "bulk_extend"
) -> int:
    if not user_ids or days <= 0:
        return 0
    started = time.perf_counter()
    conn = get_local_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(user_ids))
    cur.execute(
        f"SELECT chat_id, effective_expires_at FROM users_with_status WHERE chat_id IN ({placeholders})",
        user_ids,
    )
    rows = cur.fetchall()
    now = datetime.utcnow()
    paid_updates = []
    sub_updates = []
    for row in rows:
        uid = _row_value_safe(row, "chat_id", None)
        try:
            uid_int = int(uid)
        except Exception:
            continue
        effective_raw = _row_value_safe(row, "effective_expires_at")
        effective_dt = parse_effective_expires_at(effective_raw)
        base = effective_dt if effective_dt and effective_dt > now else now
        new_exp = base + timedelta(days=days)
        iso_exp = new_exp.isoformat()
        paid_updates.append((iso_exp, uid_int))
        sub_updates.append((uid_int, f"bulk {days}g", iso_exp, 0, f"{note}:{days}"))

    if not paid_updates:
        conn.close()
        return 0

    try:
        cur.executemany("UPDATE users SET paid_until=? WHERE chat_id=?", paid_updates)
        cur.executemany(
            """
            INSERT INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                plan=excluded.plan,
                expires_at=excluded.expires_at,
                is_active=1,
                is_demo=excluded.is_demo,
                last_payment_note=COALESCE(excluded.last_payment_note, subscriptions.last_payment_note)
            """,
            sub_updates,
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(
        "bulk extend completed users=%s days=%s duration=%.3fs",
        len(paid_updates),
        days,
        time.perf_counter() - started,
    )
    return len(paid_updates)


def admin_grant_demo_days(user_id: int, days: int) -> Optional[datetime]:
    logger.info("besthome_bot: Admin demo grant user_id=%s days=%s", user_id, days)
    unblock_user(user_id)
    ensure_subscription_record(user_id)
    sub = get_subscription(user_id) or {}
    base = resolve_extension_base(user_id)
    new_exp = base + timedelta(days=days)
    update_user_demo_end(user_id, new_exp, approve=True)
    if not sub.get("plan") or sub.get("is_demo") or sub.get("plan") == "demo":
        set_subscription(
            user_id, "demo", new_exp, is_active=1, is_demo=1, note="admin_demo"
        )
    return new_exp


def send_demo_update_notification(user_id: int, days: int, new_exp: Optional[datetime], granted: bool):
    if not new_exp:
        return
    display_time = new_exp + timedelta(hours=4)
    date_text = display_time.strftime("%d.%m.%Y %H:%M")
    if granted:
        text = (
            f"🎁 Sizə {days} günlük demo verildi. Pulsuz istifadə edə bilərsiniz.\n"
            f"Yeni bitmə tarixi: {date_text}"
        )
    else:
        text = (
            f"⏳ Demo vaxtınız {days} gün uzadıldı. Yeni bitmə tarixi: {date_text}"
        )
    try:
        bot.send_message(user_id, text)
        logger.info(
            "besthome_bot: User notification sent chat_id=%s type=%s days=%s expires=%s",
            user_id,
            "grant" if granted else "extend",
            days,
            date_text,
        )
    except Exception as e:
        logger.warning(
            "besthome_bot: Failed to notify user about demo change chat_id=%s error=%s",
            user_id,
            e,
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("admusr|"))
@callback_guard
def cb_admin_user_panel_actions(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    if len(parts) < 3:
        return
    action = parts[1]
    try:
        uid = int(parts[2])
    except Exception:
        return
    list_type = admin_user_last_list.get(c.message.chat.id)

    if action == "extend_custom":
        page = get_admin_user_page(c.message.chat.id, list_type or "active")
        admin_pending_action[c.message.chat.id] = {
            "type": "user_extend",
            "user_id": uid,
            "list_type": list_type,
            "page": page,
        }
        set_user_state(c.message.chat.id, "ADMIN_USER_EXTEND")
        bot.send_message(c.message.chat.id, "Neçə gün əlavə etmək istəyirsiniz?")
        safe_answer_callback_query(c.id)
        return

    if action in {"extend", "demo"} and len(parts) > 3:
        try:
            days = int(parts[3])
        except Exception:
            days = 0
        if days <= 0:
            safe_answer_callback_query(c.id, "⚠️ Gün sayı düzgün deyil.")
            return
        if action == "extend":
            new_exp = admin_extend_user_time(uid, days, note=f"admin_extend:{days}")
            if new_exp:
                send_demo_update_notification(uid, days, new_exp, granted=False)
            safe_answer_callback_query(c.id, f"✅ {days} gün uzadıldı.")
        else:
            new_exp = admin_grant_demo_days(uid, days)
            if new_exp:
                send_demo_update_notification(uid, days, new_exp, granted=True)
            safe_answer_callback_query(c.id, f"✅ {days} gün demo verildi.")
    elif action == "chance_reset":
        updated = _reset_chance_usage_for_users([uid])
        safe_answer_callback_query(
            c.id, "✅ Şans sıfırlandı." if updated else "⚠️ Yenilənmə olmadı."
        )
    elif action == "chance_block":
        updated = _block_chance_for_users([uid])
        safe_answer_callback_query(
            c.id, "✅ Şans bağlandı." if updated else "⚠️ Artıq bağlıdır."
        )
    elif action == "block":
        blocked = block_user(uid)
        safe_answer_callback_query(
            c.id, "✅ Bloklandı." if blocked else "⚠️ Artıq blokludur."
        )
    elif action == "unblock":
        unblocked = unblock_user(uid)
        safe_answer_callback_query(
            c.id, "✅ Blokdan çıxarıldı." if unblocked else "⚠️ Blokda deyil."
        )
    elif action == "unlimit":
        sub = get_subscription(uid) or {}
        if is_user_unlimited(uid, sub=sub):
            toggled = disable_user_unlimited(uid)
            safe_answer_callback_query(
                c.id, "❌ Limitsiz söndürüldü" if toggled else "⚠️ Limitsiz deyil"
            )
        else:
            set_user_unlimited(uid)
            safe_answer_callback_query(c.id, "♾️ Limitsiz aktiv edildi")
    elif action == "delete":
        deactivated = deactivate_user(uid)
        safe_answer_callback_query(
            c.id,
            "✅ İstifadəçi silindi" if deactivated else "⚠️ Silmək alınmadı",
        )
    else:
        safe_answer_callback_query(c.id)
        return

    admin_show_user_panel(c.message.chat.id, uid, message=c.message)
    if list_type in {"expired", "pending", "demo", "active", "blocked"}:
        page = get_admin_user_page(c.message.chat.id, list_type)
        show_all_users(
            c.message.chat.id,
            status=list_type,
            page=page,
            message=None,
            force_new=True,
        )


def set_admin_state(chat_id: int, state: Optional[str]):
    if state:
        admin_state[chat_id] = state
    else:
        admin_state.pop(chat_id, None)


def get_admin_state(chat_id: int) -> Optional[str]:
    return admin_state.get(chat_id)


def show_users_menu(chat_id):
    set_admin_state(chat_id, "admin_users_menu")
    set_user_state(chat_id, "ADMIN_USERS")
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_users_menu_active"], callback_data="userlist|active"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_users_menu_demo"], callback_data="userlist|demo"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_users_menu_expired"], callback_data="userlist|expired"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_users_menu_blocked"], callback_data="userlist|blocked"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_users_menu_pending"], callback_data="userlist|unverified"
        )
    )
    bot.send_message(
        chat_id,
        TEXTS_AZ["admin_users_menu_prompt"],
        reply_markup=mk,
    )


def _select_first_column(
    columns: set, choices: List[str], fallback: Optional[str] = None
) -> Optional[str]:
    for col in choices:
        if col in columns:
            return col
    if fallback and fallback in columns:
        return fallback
    return None


def _unverified_filter_from_schema(schema: dict) -> Tuple[str, Tuple]:
    columns = schema.get("columns", set())
    if "approved" in columns:
        return "approved=0", ()
    if "status" in columns:
        return "status=?", (STATUS_PENDING,)
    if "verified" in columns:
        return "verified=0", ()
    return "1=0", ()


def get_admin_users_state(chat_id: int) -> Dict[str, Any]:
    state = admin_navigation_state.get(chat_id) or {}
    if state.get("section") != "users":
        state = {"section": "users", "filter": "active", "page": 1}
    state.setdefault("filter", "active")
    state.setdefault("page", 1)
    admin_navigation_state[chat_id] = state
    return state


def update_admin_users_state(
    chat_id: int,
    *,
    section: str = "users",
    filter_value: Optional[str] = None,
    page: int = None,
) -> Dict[str, Any]:
    state = get_admin_users_state(chat_id)
    state["section"] = section
    if filter_value:
        state["filter"] = filter_value
    if page is not None:
        try:
            state["page"] = max(1, int(page))
        except Exception:
            state["page"] = max(1, state.get("page", 1))

    admin_navigation_state[chat_id] = state
    if state.get("filter"):
        admin_user_last_list[chat_id] = state["filter"]
    return state


def show_unverified_users(
    chat_id: int, page: int = 1, message=None, force_new: bool = False
):
    show_all_users(
        chat_id, status="pending", page=page, message=message, force_new=force_new
    )


def parse_join_datetime(dt_raw: Optional[str]) -> Tuple[str, str]:
    if not dt_raw:
        return "-", "-"
    try:
        join_dt = datetime.fromisoformat(str(dt_raw).replace(" ", "T"))
        display_time = join_dt + timedelta(hours=4)
        return display_time.strftime("%Y-%m-%d"), display_time.strftime("%H:%M")
    except Exception:
        return str(dt_raw), "-"


def format_display_date(dt_raw: Optional[str]) -> str:
    if isinstance(dt_raw, datetime):
        dt = dt_raw
    else:
        dt = parse_dt_safe(dt_raw)
    if not dt:
        return "-"
    display_time = dt + timedelta(hours=4)
    return display_time.strftime("%d.%m.%Y")


def normalize_effective_expiry(
    raw: Optional[Union[str, datetime]],
) -> Optional[datetime]:
    if raw in (None, 0, "0"):
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        dt = parse_effective_expires_at(raw)
    if not dt:
        return None
    epoch = datetime.utcfromtimestamp(0)
    if dt <= epoch:
        return None
    return dt


def format_effective_expiry_for_ui(raw: Optional[Union[str, datetime]]) -> str:
    dt = normalize_effective_expiry(raw)
    if not dt:
        return "—"
    display_time = dt + timedelta(hours=4)
    return display_time.strftime("%d.%m.%Y")


def format_remaining_days_for_ui(
    computed_status: Optional[str], raw: Optional[Union[str, datetime]]
) -> str:
    if computed_status != "ACTIVE":
        return "—"
    expiry = normalize_effective_expiry(raw)
    if not expiry:
        return "—"
    now = datetime.utcnow()
    if expiry <= now:
        return "—"
    days = math.ceil((expiry - now).total_seconds() / 86400)
    return f"{days} gün"


def format_long_date(dt: datetime) -> str:
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


def get_user_view_count(chat_id: int, days: int) -> int:
    conn = get_local_conn()
    cur = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(days=days)
    try:
        cur.execute(
            """
            SELECT COUNT(*) FROM user_view_logs
            WHERE chat_id=? AND created_at >= ?
            """,
            (chat_id, cutoff.isoformat()),
        )
        row = cur.fetchone()
        return (row[0] if row else 0) or 0
    except Exception:
        logger.debug("User view log query failed chat_id=%s", chat_id)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def build_account_status_text(chat_id: int) -> str:
    sub = get_subscription(chat_id) or {}
    record = get_user_record(chat_id) or {}

    expiry = get_effective_expires_at(chat_id)
    now = datetime.utcnow()

    demo_end_raw = record.get("demo_end_at") or record.get("demo_expires_at")
    demo_end = parse_dt_safe(demo_end_raw)
    is_demo_account = bool(sub.get("is_demo")) or (
        demo_end is not None and demo_end > now and not sub.get("plan")
    )

    status_text = "Bitmiş"
    if expiry and expiry > now:
        status_text = "Demo" if is_demo_account else "Aktiv"
    status_emoji = {"Aktiv": "🟢", "Demo": "🟡"}.get(status_text, "🔴")

    remaining_days = 0
    if expiry and expiry > now:
        remaining_days = max(0, math.ceil((expiry - now).total_seconds() / 86400))

    exp_text = format_long_date(expiry) if expiry else "—"
    account_type = "Demo" if is_demo_account else "Ödənişli"
    views_7d = get_user_view_count(chat_id, 7)
    views_30d = get_user_view_count(chat_id, 30)

    lines = [
        "👤 <b>Hesabım</b>",
        "─────────────",
        "",
        f"🆔 ID: {chat_id}",
        f"{status_emoji} Status: {status_text}",
        f"⏳ Qalan gün: {remaining_days} gün",
        f"📅 Bitmə tarixi: {exp_text}",
        f"💳 Paket tipi: {account_type}",
        "",
        "📊 Aktivlik:",
        f"• Son 7 gün baxılan elanlar: {views_7d}",
        f"• Son 30 gün baxılan elanlar: {views_30d}",
    ]

    return "\n".join(lines)


def resolve_admin_user_status(record: Optional[dict]) -> str:
    if not record:
        return "unknown"
    uid = record.get("chat_id") if isinstance(record, dict) else None
    if not uid:
        return "unknown"
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT computed_status AS admin_status FROM users_with_status WHERE chat_id=?",
            (uid,),
        )
        row = cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not row:
        return "unknown"
    return (
        row["admin_status"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
    )


def mark_user_delivery_failure(user_id: int, error_text: str):
    error_text = (error_text or "").strip()
    logger.warning("Failed to deliver message user_id=%s error=%s", user_id, error_text)

    def _is_block_error(msg: str) -> bool:
        msg = msg.lower()
        return "bot was blocked by the user" in msg or "chat not found" in msg

    if not _is_block_error(error_text):
        return
    conn = None
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        schema = detect_users_schema()
        columns = schema.get("columns", set())
        updates = []
        params: List[Any] = []
        now_ts = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if "blocked" in columns:
            updates.append("blocked=1")
        if "is_blocked" in columns:
            updates.append("is_blocked=1")
        if "blocked_at" in columns:
            updates.append("blocked_at=?")
            params.append(now_ts)
        if "last_error" in columns:
            updates.append("last_error=?")
            params.append(error_text)
        if not updates:
            return
        params.append(user_id)
        cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE chat_id=?", params)
        conn.commit()
    except Exception:
        logger.exception("Failed to mark user delivery failure user_id=%s", user_id)
    finally:
        if conn:
            conn.close()


def deactivate_user(user_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM users WHERE chat_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    try:
        cur.execute("DELETE FROM users WHERE chat_id=?", (user_id,))
        cur.execute("DELETE FROM subscriptions WHERE chat_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    for state_map in (
        user_state,
        search_state,
        customer_request_state,
        agent_request_lookup_state,
        admin_customer_request_state,
        USER_STATE,
        listing_sessions,
        user_stats_filter,
        today_results_cache,
    ):
        try:
            state_map.pop(user_id, None)
        except Exception:
            pass

    logger.info("besthome_bot: User permanently deleted chat_id=%s", user_id)
    return True


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

    blocked_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    schema = detect_users_schema()
    columns = schema.get("columns", set())
    updates = ["blocked=1"]
    params = []
    if "is_blocked" in columns:
        updates.append("is_blocked=1")
    if "blocked_at" in columns:
        updates.append("blocked_at=?")
        params.append(blocked_at)
    if "last_error" in columns:
        updates.append("last_error=NULL")
    params.append(chat_id)
    cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE chat_id=?", params)
    conn.commit()
    conn.close()

    logger.info("besthome_bot: User blocked chat_id=%s", chat_id)

    try:
        bot.send_message(chat_id, BLOCKED_MESSAGE_TEXT)
    except Exception:
        pass

    return True


def unblock_user(chat_id: int) -> bool:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT blocked FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    if not row["blocked"]:
        conn.close()
        return False
    schema = detect_users_schema()
    columns = schema.get("columns", set())
    updates = ["blocked=0"]
    params = []
    if "is_blocked" in columns:
        updates.append("is_blocked=0")
    if "blocked_at" in columns:
        updates.append("blocked_at=NULL")
    if "is_active" in columns:
        updates.append("is_active=1")
    if "last_error" in columns:
        updates.append("last_error=NULL")
    params.append(chat_id)
    cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE chat_id=?", params)
    conn.commit()
    conn.close()
    logger.info("besthome_bot: User unblocked chat_id=%s", chat_id)
    return True


def reject_user(chat_id: int):
    update_user_status(chat_id, STATUS_REJECTED)
    try:
        bot.send_message(
            chat_id,
            "Sorğunuz qəbul olunmadı. Daha sonra yenidən müraciət edə bilərsiniz.",
        )
    except Exception:
        pass


def restore_user_to_pending(chat_id: int):
    update_user_status(chat_id, STATUS_PENDING)


def is_user_unlimited(chat_id: int, sub: Optional[dict] = None) -> bool:
    sub = sub or get_subscription(chat_id)
    if not sub:
        return False
    note = (sub.get("last_payment_note") or "").lower()
    plan = (sub.get("plan") or "").lower()
    is_active = bool(sub.get("is_active"))
    if not is_active:
        return False
    return plan == "unlimited" or note.startswith("unlimited:on")


def set_user_unlimited(chat_id: int) -> str:
    ensure_subscription_record(chat_id)
    sub = get_subscription(chat_id) or {}
    prev_plan = sub.get("plan") or ""
    prev_exp = parse_dt_safe(sub.get("expires_at"))
    prev_is_demo = int(sub.get("is_demo") or 0)
    note = (
        f"unlimited:on:{prev_plan}:{prev_exp.isoformat() if prev_exp else ''}:{prev_is_demo}"
    )
    insert_subscription(
        chat_id,
        "unlimited",
        datetime.utcnow() + timedelta(days=3650),
        is_demo=0,
        note=note,
    )
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET approved=1, blocked=0, is_active=1, last_error=NULL WHERE chat_id=?",
        (chat_id,),
    )
    conn.commit()
    conn.close()
    logger.info("besthome_bot: Unlimited enabled chat_id=%s", chat_id)
    return note


def disable_user_unlimited(chat_id: int) -> bool:
    ensure_subscription_record(chat_id)
    sub = get_subscription(chat_id) or {}
    if not is_user_unlimited(chat_id, sub=sub):
        return False
    note = sub.get("last_payment_note") or ""
    prev_plan = ""
    prev_exp_raw = ""
    prev_demo = 0
    if note.startswith("unlimited:on"):
        parts = note.split(":", 4)
        if len(parts) >= 3:
            prev_plan = parts[2]
        if len(parts) >= 4:
            prev_exp_raw = parts[3]
        if len(parts) >= 5:
            try:
                prev_demo = int(parts[4])
            except Exception:
                prev_demo = 0

    prev_exp = parse_dt_safe(prev_exp_raw)
    target_plan = prev_plan or "manual"
    is_active = 1 if prev_exp else 0
    set_subscription(
        chat_id,
        target_plan,
        prev_exp,
        is_active=is_active,
        is_demo=prev_demo,
        note="unlimited:off",
    )
    logger.info("besthome_bot: Unlimited disabled chat_id=%s", chat_id)
    return True


def switch_user_to_paid_flow(chat_id: int):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET approved=0 WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()


def delete_user_fully(chat_id: int):
    record = get_user_record(chat_id)
    if record and record.get("status") not in {STATUS_BLOCKED, STATUS_REJECTED}:
        return False

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM favorites WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM subscriptions WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM agent_activity WHERE chat_id=?", (chat_id,))
    cur.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
    return True


def get_selected_users(chat_id: int) -> set:
    return admin_selected_users.setdefault(chat_id, set())


def clear_selected_users(chat_id: int):
    admin_selected_users.pop(chat_id, None)


def select_page_users(chat_id: int, rows: List[sqlite3.Row]):
    selected = get_selected_users(chat_id)
    for row in rows or []:
        uid = _row_value_safe(row, "chat_id", None)
        try:
            selected.add(int(uid))
        except Exception:
            continue
    admin_selected_users[chat_id] = selected


def show_all_users(
    chat_id, status="active", page: int = 1, message=None, force_new: bool = False
):
    set_ui_context(chat_id, UI_CONTEXT_ADMIN)
    conn = None
    started_at = time.perf_counter()
    try:
        logger.info("show_all_users start status=%s page=%s", status, page)
        state = get_admin_users_state(chat_id)
        page = max(1, int(page or state.get("page", 1) or 1))
        list_status = str(status or state.get("filter") or "active").lower()
        if list_status == "unverified":
            list_status = "pending"

        allowed = {"active", "expired", "pending", "blocked", "demo"}
        if list_status not in allowed:
            list_status = state.get("filter", "active") if state else "active"

        update_admin_users_state(chat_id, filter_value=list_status, page=page)
        state_map = {
            "active": "admin_users_active",
            "demo": "admin_users_demo",
            "expired": "admin_users_expired",
            "blocked": "admin_users_blocked",
            "pending": "admin_users_pending",
        }
        set_admin_state(chat_id, state_map.get(list_status))

        if list_status in ("active", "expired", "demo"):
            logger.info(
                "ADMIN USERLIST OPEN status=%s chat_id=%s", list_status, chat_id
            )

        conn = get_local_conn()
        cur = conn.cursor()

        base_query = admin_user_status_subquery()
        where_clause, params = admin_user_status_where(list_status)

        if list_status in ("active", "expired", "demo"):
            order_clause = "ORDER BY CAST(effective_expires_at AS INTEGER) DESC"
        else:
            order_clause = "ORDER BY chat_id ASC"

        logger.info(
            "users count query start chat_id=%s status=%s where=%s",
            chat_id,
            list_status,
            where_clause,
        )
        count_started = time.perf_counter()
        base_query = admin_user_status_subquery()
        cur.execute(f"SELECT COUNT(*) FROM {base_query} WHERE " + where_clause, params)
        total = cur.fetchone()[0] or 0
        logger.info(
            "users count query done chat_id=%s status=%s total=%s duration=%.3fs",
            chat_id,
            list_status,
            total,
            time.perf_counter() - count_started,
        )

        if total == 0:
            text = TEXTS_AZ["admin_userlist_empty"]
            try:
                bot.send_message(chat_id, text)
            except Exception:
                logger.error("Admin send failed", exc_info=True)
                safe_admin_step(chat_id, text)
            return

        total_pages = max(1, math.ceil(total / PAGE_SIZE_USERS))
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * PAGE_SIZE_USERS
        logger.info(
            "users fetch query start chat_id=%s status=%s page=%s offset=%s",
            chat_id,
            list_status,
            page,
            offset,
        )
        fetch_started = time.perf_counter()
        cur.execute(
            (
                "SELECT chat_id, full_name, username, effective_expires_at, computed_status "
                f"FROM {base_query} WHERE {where_clause} {order_clause} LIMIT ? OFFSET ?"
            ),
            (*params, PAGE_SIZE_USERS, offset),
        )
        rows = cur.fetchall()
        logger.info(
            "show_all_users rows_count=%s duration=%.3fs",
            len(rows),
            time.perf_counter() - fetch_started,
        )
        admin_user_rows_cache[chat_id] = rows

        if not rows:
            try:
                safe_send(chat_id, TEXTS_AZ["admin_userlist_empty"])
            except Exception:
                logger.error("Admin send failed", exc_info=True)
            return

        admin_user_page_state[(chat_id, list_status)] = page

        title_map = {
            "active": TEXTS_AZ["admin_userlist_title_active"],
            "demo": TEXTS_AZ["admin_userlist_title_demo"],
            "expired": TEXTS_AZ["admin_userlist_title_expired"],
            "blocked": TEXTS_AZ["admin_userlist_title_blocked"],
            "pending": TEXTS_AZ["admin_userlist_title_pending"],
        }

        text_lines = [
            f"{title_map.get(list_status, list_status.title())} ({total} nəfər)",
            f"{TEXTS_AZ['admin_userlist_page_label']}: {page} / {total_pages}",
            "",
        ]

        for idx, row in enumerate(rows, start=offset + 1):
            uid = _row_value_safe(row, "chat_id", "-")
            name = _row_value_safe(row, "full_name", "-") or "-"
            username = _row_value_safe(row, "username")
            username_value = f"@{username}" if username else "-"
            computed_status = _row_value_safe(row, "computed_status")
            expiry_raw = _row_value_safe(row, "effective_expires_at")
            if list_status == "pending":
                expiry_text = "—"
                remaining_text = "—"
            else:
                expiry_text = format_effective_expiry_for_ui(expiry_raw)
                remaining_text = format_remaining_days_for_ui(
                    computed_status, expiry_raw
                )

            uid_str = str(uid)
            uid_text = (
                f'🆔 ID: <a href="tg://user?id={uid_str}">{uid_str}</a>'
                if uid_str.isdigit()
                else f"🆔 ID: {uid_str}"
            )

            name_display = html.escape(name)
            username_display = html.escape(username_value)
            expiry_display = html.escape(expiry_text)
            remaining_display = html.escape(remaining_text)

            entry_lines = [
                f"[{idx}]",
                f"{TEXTS_AZ['admin_userlist_entry_name']}: {name_display}",
                uid_text,
                f"{TEXTS_AZ['admin_userlist_entry_username']}: {username_display}",
                f"{TEXTS_AZ['admin_userlist_entry_expiry']}: {expiry_display}",
                f"{TEXTS_AZ['admin_userlist_entry_remaining']}: {remaining_display}",
            ]

            text_lines.append("\n".join(entry_lines))

        nav_buttons = [
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_userlist_nav_first"],
                callback_data=f"adm_u:{list_status}:1",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_userlist_nav_prev"],
                callback_data=f"adm_u:{list_status}:{max(1, page - 1)}",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_userlist_nav_page"].format(
                    page=page, total=total_pages
                ),
                callback_data=f"adm_u:{list_status}:{page}",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_userlist_nav_next"],
                callback_data=f"adm_u:{list_status}:{min(total_pages, page + 1)}",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_userlist_nav_last"],
                callback_data=f"adm_u:{list_status}:{total_pages}",
            ),
        ]

        mk = types.InlineKeyboardMarkup()
        selection_enabled = list_status in {"active", "expired", "demo"}

        if list_status == "pending":
            for row in rows:
                uid = _row_value_safe(row, "chat_id", None)
                try:
                    uid_val = int(uid)
                except Exception:
                    continue
                mk.row(
                    types.InlineKeyboardButton(
                        "✅ Təsdiqlə",
                        callback_data=f"adm_upd:approve:{uid_val}:{list_status}:{page}",
                    ),
                    types.InlineKeyboardButton(
                        "⛔ Blokla",
                        callback_data=f"adm_upd:block:{uid_val}:{list_status}:{page}",
                    ),
                )
        if selection_enabled:
            selected_ids = admin_selected_users.get(chat_id, set())
            for row in rows:
                uid = _row_value_safe(row, "chat_id", None)
                try:
                    uid_int = int(uid)
                except Exception:
                    continue
                computed_status = _row_value_safe(row, "computed_status", "-") or "-"
                expiry_raw = _row_value_safe(row, "effective_expires_at")
                remaining_label = format_remaining_days_for_ui(
                    computed_status, expiry_raw
                )
                label = (
                    f"{'☑' if uid_int in selected_ids else '☐'} {uid_int} | {computed_status} | "
                    f"{remaining_label}"
                )
                mk.add(
                    types.InlineKeyboardButton(
                        label, callback_data=f"adm_sel:{list_status}:{page}:{uid_int}"
                    )
                )
            mk.row(
                types.InlineKeyboardButton(
                    "✅ Hamısını seç", callback_data=f"adm_all:{list_status}:{page}"
                ),
                types.InlineKeyboardButton(
                    "❌ Hamısını sil", callback_data=f"adm_none:{list_status}:{page}"
                ),
            )
            mk.add(
                types.InlineKeyboardButton(
                    "⏱ Seçilənlərə əməliyyat",
                    callback_data=f"adm_ops:{list_status}:{page}",
                )
            )

        mk.row(*nav_buttons)

        text = "\n\n".join(text_lines)

        logger.info(
            "Admin user list sent via send_message status=%s rows=%d",
            list_status,
            len(rows),
        )
        try:
            if message and not force_new:
                bot.edit_message_text(
                    text,
                    chat_id,
                    message.message_id,
                    reply_markup=mk,
                    parse_mode="HTML",
                )
            else:
                bot.send_message(chat_id, text, reply_markup=mk, parse_mode="HTML")
        except Exception:
            logger.error("Admin send failed", exc_info=True)
            safe_admin_step(chat_id, text, reply_markup=mk, parse_mode="HTML")
    except Exception:
        logger.exception("show_all_users fatal error")
        safe_send(chat_id, TEXTS_AZ["admin_userlist_open_error"])
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        logger.info(
            "ADMIN USERLIST CLOSE status=%s page=%s duration=%.3f",
            list_status,
            page,
            time.perf_counter() - started_at,
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("userlist|"))
def cb_userlist(c):
    safe_answer_callback_query(c.id)

    if not is_admin(c.message.chat.id):
        return

    try:
        status = c.data.split("|")[1]
    except Exception:
        safe_send(c.message.chat.id, TEXTS_AZ["admin_userlist_category_missing"])
        return

    if status == "unverified":
        status = "pending"

    allowed = {"active", "demo", "expired", "blocked", "pending"}
    if status not in allowed:
        safe_send(c.message.chat.id, TEXTS_AZ["admin_userlist_category_invalid"])
        return

    logger.info("ADMIN USERLIST CLICK status=%s chat_id=%s", status, c.message.chat.id)
    state = get_admin_users_state(c.message.chat.id)
    current_filter = state.get("filter", "active")
    target_page = state.get("page", 1) if current_filter == status else 1
    update_admin_users_state(c.message.chat.id, filter_value=status, page=target_page)
    loading_message = None
    try:
        loading_message = bot.send_message(
            c.message.chat.id, "⏳ Zəhmət olmasa gözləyin..."
        )
    except Exception:
        loading_message = None
    show_all_users(
        c.message.chat.id,
        status=status,
        page=target_page,
        message=loading_message,
        force_new=False,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_u:"))
@callback_guard
def cb_admin_user_pagination(c):
    safe_answer_callback_query(c.id)
    if not is_admin(c.message.chat.id):
        return
    try:
        _, list_type, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        list_type = "active"
        page = 1
    update_admin_users_state(c.message.chat.id, filter_value=list_type, page=page)
    show_all_users(c.message.chat.id, list_type, page=page, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_upd:"))
@callback_guard
def cb_admin_pending_actions(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, action, uid_raw, list_status, page_raw = c.data.split(":")
        uid = int(uid_raw)
        page = int(page_raw)
    except Exception:
        safe_answer_callback_query(c.id, "Xəta")
        return

    conn = get_local_conn()
    cur = conn.cursor()
    try:
        if action == "approve":
            cur.execute("UPDATE users SET approved=1 WHERE chat_id=?", (uid,))
            logger.info("admin approved user_id=%s by=%s", uid, chat_id)
            bot.send_message(uid, "✅ Hesabınız təsdiqləndi.")
        elif action == "block":
            cur.execute("UPDATE users SET blocked=1 WHERE chat_id=?", (uid,))
            logger.info("admin blocked user_id=%s by=%s", uid, chat_id)
            bot.send_message(uid, "⛔ Hesabınız bloklandı.")
        conn.commit()
    except Exception:
        logger.exception("admin pending action failed uid=%s action=%s", uid, action)
    finally:
        conn.close()

    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(
        chat_id, status=list_status, page=page, message=c.message, force_new=False
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_sel:"))
@callback_guard
def cb_admin_toggle_select(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw, uid_raw = c.data.split(":")
        uid = int(uid_raw)
        page = int(page_raw)
    except Exception:
        return
    selected = get_selected_users(chat_id)
    if uid in selected:
        selected.remove(uid)
    else:
        selected.add(uid)
    admin_selected_users[chat_id] = selected
    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(chat_id, list_status, page=page, message=c.message, force_new=False)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_all:"))
@callback_guard
def cb_admin_select_all(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    select_page_users(chat_id, admin_user_rows_cache.get(chat_id, []))
    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(chat_id, list_status, page=page, message=c.message, force_new=False)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_none:"))
@callback_guard
def cb_admin_select_none(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    clear_selected_users(chat_id)
    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(chat_id, list_status, page=page, message=c.message, force_new=False)


def _build_bulk_action_markup(
    list_status: str, page: int, selected_ids: List[int]
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()

    state_map = _fetch_chance_block_state(selected_ids)
    all_blocked = bool(state_map) and all(v for v in state_map.values())
    toggle_text = "✅ Şansı aktiv et" if all_blocked else "⛔ Şansı bağla"
    toggle_action = "on" if all_blocked else "off"

    mk.row(
        types.InlineKeyboardButton(
            "🎁 Şansı sıfırla",
            callback_data=f"adm_bulk_reset:{list_status}:{page}",
        ),
        types.InlineKeyboardButton(
            toggle_text,
            callback_data=f"chance_toggle:{toggle_action}:bulk:{list_status}:{page}",
        ),
    )
    mk.row(
        types.InlineKeyboardButton(
            "+30 gün", callback_data=f"adm_bulk_do:30:{list_status}:{page}"
        ),
        types.InlineKeyboardButton(
            "+90 gün", callback_data=f"adm_bulk_do:90:{list_status}:{page}"
        ),
    )
    mk.row(
        types.InlineKeyboardButton(
            "➕ Gün əlavə et",
            callback_data=f"adm_bulk_custom:{list_status}:{page}",
        ),
    )
    mk.row(
        types.InlineKeyboardButton(
            "❌ Ləğv et", callback_data=f"adm_bulk_cancel:{list_status}:{page}"
        )
    )
    return mk


def _send_bulk_action_menu(
    chat_id: int, list_status: str, page: int, message: Optional[types.Message] = None
):
    selected_ids = list(admin_selected_users.get(chat_id, set()))
    mk = _build_bulk_action_markup(list_status, page, selected_ids)
    if message:
        try:
            bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message.message_id, reply_markup=mk
            )
            return
        except Exception:
            pass
    bot.send_message(
        chat_id, "Seçilən istifadəçilər üçün müddət seçin:", reply_markup=mk
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ops:"))
@callback_guard
def cb_admin_ops_menu(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    selected = admin_selected_users.get(chat_id, set())
    if not selected:
        safe_answer_callback_query(
            c.id, "⚠️ Əvvəlcə istifadəçiləri seçin", show_alert=True
        )
        return
    _send_bulk_action_menu(chat_id, list_status, page)


def _perform_bulk_extend(chat_id: int, days: int, list_status: str, page: int):
    selected_ids = list(admin_selected_users.get(chat_id, set()))
    if not selected_ids:
        bot.send_message(chat_id, "⚠️ Əvvəlcə istifadəçiləri seçin")
        return
    op_started = time.perf_counter()
    logger.info(
        "ADMIN BULK EXTEND start chat_id=%s count=%s days=%s",
        chat_id,
        len(selected_ids),
        days,
    )
    updated = bulk_extend_user_time(selected_ids, days, note="bulk_extend")
    clear_selected_users(chat_id)
    bot.send_message(chat_id, f"✅ {updated} istifadəçinin vaxtı +{days} gün artırıldı")
    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(
        chat_id, status=list_status, page=page, message=None, force_new=False
    )
    logger.info(
        "ADMIN BULK EXTEND done chat_id=%s updated=%s duration=%.3f",
        chat_id,
        updated,
        time.perf_counter() - op_started,
    )


def _fetch_chance_block_state(user_ids: List[int]) -> Dict[int, int]:
    if not user_ids:
        return {}
    conn = get_local_conn()
    cur = conn.cursor()
    _ensure_chance_columns_exists(conn)
    placeholders = ",".join(["?"] * len(user_ids))
    cur.execute(
        f"SELECT chat_id, chance_blocked FROM users WHERE chat_id IN ({placeholders})",
        user_ids,
    )
    result = {int(row[0]): int(row[1] or 0) for row in cur.fetchall()}
    conn.close()
    return result


def _set_chance_block_state(user_ids: List[int], blocked: int) -> int:
    if not user_ids:
        return 0
    conn = get_local_conn()
    cur = conn.cursor()
    _ensure_chance_columns_exists(conn)
    updated = 0
    for uid in user_ids:
        cur.execute(
            "UPDATE users SET chance_blocked=? WHERE chat_id=?", (blocked, uid)
        )
        try:
            updated += max(cur.rowcount or 0, 0)
        except Exception:
            pass
    conn.commit()
    conn.close()
    return updated


def _reset_chance_usage_for_users(user_ids: List[int]) -> int:
    if not user_ids:
        return 0
    conn = get_local_conn()
    cur = conn.cursor()
    _ensure_chance_columns_exists(conn)
    updated = 0
    for uid in user_ids:
        cur.execute("UPDATE users SET chance_last_used_at=NULL WHERE chat_id=?", (uid,))
        try:
            updated += max(cur.rowcount or 0, 0)
        except Exception:
            pass
    conn.commit()
    conn.close()
    return updated


def _block_chance_for_users(user_ids: List[int]) -> int:
    return _set_chance_block_state(user_ids, 1)


def _perform_bulk_chance_reset(chat_id: int, list_status: str, page: int):
    selected_ids = list(admin_selected_users.get(chat_id, set()))
    if not selected_ids:
        bot.send_message(chat_id, "⚠️ Əvvəlcə istifadəçiləri seçin")
        return
    updated = _reset_chance_usage_for_users(selected_ids)
    clear_selected_users(chat_id)
    bot.send_message(
        chat_id, f"✅ {updated} istifadəçinin şansı sıfırlandı"
    )
    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(chat_id, status=list_status, page=page, message=None, force_new=False)


def _perform_bulk_chance_block(chat_id: int, list_status: str, page: int):
    selected_ids = list(admin_selected_users.get(chat_id, set()))
    if not selected_ids:
        bot.send_message(chat_id, "⚠️ Əvvəlcə istifadəçiləri seçin")
        return
    updated = _block_chance_for_users(selected_ids)
    clear_selected_users(chat_id)
    bot.send_message(chat_id, f"✅ {updated} istifadəçi üçün şans bağlandı")
    update_admin_users_state(chat_id, filter_value=list_status, page=page)
    show_all_users(chat_id, status=list_status, page=page, message=None, force_new=False)


def _ensure_chance_columns_exists(conn: sqlite3.Connection) -> None:
    columns = set()
    for row in conn.execute("PRAGMA table_info(users)"):
        try:
            columns.add(row[1])
        except Exception:
            try:
                columns.add(row.get("name"))
            except Exception:
                continue

    if "chance_last_used_at" not in columns:
        try:
            conn.execute("ALTER TABLE users ADD COLUMN chance_last_used_at DATETIME")
        except sqlite3.OperationalError:
            pass
    if "chance_blocked" not in columns:
        try:
            conn.execute("ALTER TABLE users ADD COLUMN chance_blocked INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
    conn.commit()


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_bulk_do:"))
@callback_guard
def cb_admin_bulk_apply(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, action_raw, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    try:
        days = int(action_raw)
    except Exception:
        return
    _perform_bulk_extend(chat_id, days, list_status, page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_bulk_reset:"))
@callback_guard
def cb_admin_bulk_reset(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    _perform_bulk_chance_reset(chat_id, list_status, page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_bulk_block:"))
@callback_guard
def cb_admin_bulk_block(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    _perform_bulk_chance_block(chat_id, list_status, page)


@bot.callback_query_handler(func=lambda c: c.data.startswith("chance_toggle:"))
@callback_guard
def cb_chance_toggle(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return

    parts = c.data.split(":")
    if len(parts) < 3:
        return

    action = parts[1]
    mode = parts[2]
    target_state = 0 if action == "on" else 1

    if mode == "user" and len(parts) >= 4:
        try:
            uid = int(parts[3])
        except Exception:
            return

        _set_chance_block_state([uid], target_state)
        record = get_user_record(uid) or {}
        mk = _build_admin_user_markup(
            uid,
            record,
            is_user_unlimited(uid),
            bool(record.get("blocked") or record.get("is_blocked")),
        )
        try:
            bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=c.message.message_id, reply_markup=mk
            )
        except Exception:
            pass
        return

    if mode == "bulk" and len(parts) >= 5:
        list_status = parts[3]
        try:
            page = int(parts[4])
        except Exception:
            return
        selected_ids = list(admin_selected_users.get(chat_id, set()))
        if not selected_ids:
            safe_answer_callback_query(
                c.id, "⚠️ Əvvəlcə istifadəçiləri seçin", show_alert=True
            )
            return
        _set_chance_block_state(selected_ids, target_state)
        _send_bulk_action_menu(chat_id, list_status, page, message=c.message)
        return


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_bulk_custom:"))
@callback_guard
def cb_admin_bulk_custom(c):
    chat_id = c.message.chat.id if c.message else None
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        safe_answer_callback_query(c.id)
        return
    state_data = {"type": "bulk_days", "list_status": list_status, "page": page}
    admin_pending_action[chat_id] = state_data
    admin_bulk_action_state[chat_id] = {"list_status": list_status, "page": page}
    set_user_state(chat_id, "ADMIN_BULK_EXTEND")
    bot.send_message(chat_id, "Neçə gün əlavə etmək istəyirsiniz?")
    safe_answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_bulk_cancel:"))
@callback_guard
def cb_admin_bulk_cancel(c):
    chat_id = c.message.chat.id if c.message else None
    safe_answer_callback_query(c.id)
    if not is_admin(chat_id):
        return
    try:
        _, list_status, page_raw = c.data.split(":")
        page = int(page_raw)
    except Exception:
        return
    safe_answer_callback_query(c.id, "❌ Ləğv edildi")
    show_all_users(
        chat_id, status=list_status, page=page, message=c.message, force_new=False
    )


@bot.message_handler(func=lambda m: get_user_state(m.chat.id) == "ADMIN_BULK_EXTEND")
def admin_bulk_custom_input(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not is_admin(chat_id):
        return
    try:
        days = int((message.text or "0").strip())
    except Exception:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa düzgün gün sayı yazın.")
        return
    if days <= 0:
        bot.send_message(chat_id, "⚠️ Gün sayı müsbət olmalıdır.")
        return
    st = (
        admin_pending_action.pop(chat_id, None)
        or admin_bulk_action_state.get(chat_id)
        or {}
    )
    list_status = st.get("list_status", "expired")
    page = int(st.get("page", 1))
    admin_bulk_action_state.pop(chat_id, None)
    set_user_state(chat_id, "ADMIN_USERS")
    _perform_bulk_extend(chat_id, days, list_status, page)


@bot.message_handler(func=lambda m: get_user_state(m.chat.id) == "ADMIN_USER_EXTEND")
def admin_user_custom_extend_input(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    if not is_admin(chat_id):
        return

    try:
        days = int((message.text or "0").strip())
    except Exception:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa düzgün gün sayı yazın.")
        return

    state = admin_pending_action.pop(chat_id, {}) or {}
    target_id = state.get("user_id")
    if not target_id:
        bot.send_message(chat_id, "⚠️ İstifadəçi tapılmadı.")
        set_user_state(chat_id, "ADMIN_USERS")
        return

    list_type = state.get("list_type") or admin_user_last_list.get(chat_id)
    page = state.get("page") or get_admin_user_page(chat_id, list_type or "active")

    new_exp = admin_extend_user_time(target_id, days, note=f"admin_extend:{days}")
    if new_exp:
        send_demo_update_notification(target_id, days, new_exp, granted=False)
    bot.send_message(chat_id, f"✅ {days} gün əlavə edildi.")
    set_user_state(chat_id, "ADMIN_USERS")
    admin_show_user_panel(chat_id, target_id)
    if list_type in {"expired", "pending", "demo", "active", "blocked"}:
        show_all_users(
            chat_id,
            status=list_type,
            page=page,
            message=None,
            force_new=False,
        )


def get_admin_user_page(chat_id: int, list_type: str) -> int:
    if list_type == "unverified":
        list_type = "pending"
    try:
        state = get_admin_users_state(chat_id)
        if state.get("section") == "users" and state.get("filter") == list_type:
            return max(1, int(state.get("page", 1)))
        return max(1, int(admin_user_page_state.get((chat_id, list_type), 1)))
    except Exception:
        return 1


def build_admin_msg_back_markup(
    list_type: str, page: int
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_back_button"],
            callback_data=f"adm_msg_back:{list_type}:{page}",
        )
    )
    return mk


def build_admin_extend_back_markup(
    list_type: str, page: int
) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_back_button"],
            callback_data=f"adm_extend_back:{list_type}:{page}",
        )
    )
    return mk


def send_admin_user_action_menu(chat_id: int, user_id: int, status_text: str):
    text = TEXTS_AZ["admin_user_action_menu"].format(
        user_id=user_id, status=status_text, price_text=ADMIN_PAYMENT_PRICE_TEXT
    )
    try:
        bot.send_message(chat_id, text)
    except Exception:
        logger.error("Admin send failed", exc_info=True)
        safe_admin_step(chat_id, text)


def activate_user_for_days(user_id: int, days: int):
    sub = get_subscription(user_id) or {}
    base = resolve_extension_base(user_id)
    new_exp = base + timedelta(days=days)
    plan_name = sub.get("plan") or f"manual {days}g"
    insert_subscription(
        user_id,
        plan_name,
        new_exp,
        is_demo=0,
        note=f"admin_activate:{days}",
    )
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET approved=1 WHERE chat_id=?", (user_id,))
    conn.commit()
    conn.close()
    logger.info("Admin activated user_id=%s days=%s", user_id, days)
    try:
        bot.send_message(user_id, f"✅ Hesabınız {days} gün aktiv edildi")
    except Exception:
        pass


def show_pending_listings(chat_id):
    return
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
            TEXTS_AZ["admin_pending_listings_none"],
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
                TEXTS_AZ["admin_listing_approve"],
                callback_data=f"admin_approve:{ev['id']}",
            ),
            types.InlineKeyboardButton(
                TEXTS_AZ["admin_listing_delete"],
                callback_data=f"admin_delete:{ev['id']}",
            ),
        )

        bot.send_message(chat_id, txt, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_approve:"))
@callback_guard
def handle_admin_approve(c):
    try:
        ad_id = int(c.data.split(":")[1])

        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM listings_new WHERE id=?", (ad_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise ValueError("Listing not found")
        ev = dict(row)

        cur.execute(
            """
            INSERT INTO listings_approved (
                date_added, created_at, chat_id, role, prop_type, operation,
                rayon, metro, rooms, area_kvm, price, currency,
                phone, contact_name, summary, link
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.get("date_added"),
                ev.get("created_at") or format_sqlite_datetime(datetime.now()),
                ev.get("chat_id"),
                ev.get("role"),
                ev.get("prop_type"),
                ev.get("operation"),
                ev.get("rayon"),
                ev.get("metro"),
                ev.get("rooms"),
                ev.get("area_kvm"),
                ev.get("price"),
                ev.get("currency"),
                ev.get("phone"),
                ev.get("contact_name"),
                ev.get("summary"),
                ev.get("link"),
            ),
        )
        approved_id = cur.lastrowid
        cur.execute("UPDATE listings_new SET approved=1 WHERE id=?", (ad_id,))
        conn.commit()
        conn.close()

        ev["id"] = approved_id
        send_listing_card(
            CHANNEL_ID,
            ev,
            source="local",
            with_fav_button=False,
            track_view=False,
        )
        scan_state = {}
        process_keyword_alerts_for_listing(ev, source="local", scan_state=scan_state)
        if scan_state:
            send_keyword_notification_summaries(scan_state)

        bot.answer_callback_query(c.id, "Elan təsdiqləndi ✅")

        safe_clear_ui(bot, c.message.chat.id, ui_state[c.message.chat.id])
        ui_state[c.message.chat.id].clear()

    except Exception as e:
        bot.answer_callback_query(c.id, "Xəta baş verdi ❌", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_delete:"))
@callback_guard
def handle_admin_delete(c):
    try:
        ad_id = int(c.data.split(":")[1])

        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM listings_new WHERE id=?", (ad_id,))
        conn.commit()
        conn.close()

        bot.answer_callback_query(c.id, "Elan silindi ❌")

        safe_clear_ui(bot, c.message.chat.id, ui_state[c.message.chat.id])
        ui_state[c.message.chat.id].clear()

    except Exception as e:
        bot.answer_callback_query(c.id, "Xəta baş verdi ❌", show_alert=True)


# =============== İSTİFADƏÇİ TƏSDİQİ (ADMIN) ===============


def show_pending_users(chat_id, message=None):
    set_admin_state(chat_id, "admin_users_pending")
    show_all_users(
        chat_id,
        status="pending",
        page=1,
        message=message,
        force_new=message is None,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_approve|"))
@callback_guard
def cb_user_approve_action(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    try:
        uid = int(parts[1])
    except Exception:
        safe_answer_callback_query(c.id, TEXTS_AZ["admin_user_not_found"])
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET approved=1 WHERE chat_id=?", (uid,))
    conn.commit()
    conn.close()

    safe_answer_callback_query(c.id, "✅ İstifadəçi təsdiqləndi.")
    try:
        bot.send_message(
            uid,
            "✅ Hesabınız təsdiqləndi. Admin tərəfindən demo və ya uzatma verilə bilər.",
        )
    except Exception:
        pass

    show_pending_users(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_demo|"))
@callback_guard
def cb_user_demo_action(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    uid = int(parts[1])
    days = 3
    if len(parts) > 2 and parts[2].isdigit():
        days = int(parts[2])
    ensure_subscription_record(uid)
    expires = extend_demo_for_user(uid, days)
    if expires:
        try:
            bot.send_message(
                uid, f"?? Admin tərəfindən {days} günlük demo aktiv edildi!"
            )
        except Exception:
            pass
    safe_answer_callback_query(c.id, TEXTS_AZ["admin_user_demo_given"])
    show_pending_users(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_free|"))
@callback_guard
def cb_user_free_action(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    uid = int(parts[1])
    set_user_unlimited(uid)
    safe_answer_callback_query(c.id, "? Limitsiz edildi")
    try:
        bot.send_message(uid, "? Limitsiz giri? aktiv edildi.")
    except Exception:
        pass
    show_pending_users(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_reject|"))
@callback_guard
def cb_user_reject_action(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    uid = int(parts[1])
    reject_user(uid)
    safe_answer_callback_query(c.id, "? R?dd edildi")
    show_pending_users(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_block|"))
@callback_guard
def cb_user_block_action(c):
    if not is_admin(c.message.chat.id):
        return
    parts = c.data.split("|")
    uid = int(parts[1])
    blocked = block_user(uid)
    safe_answer_callback_query(
        c.id, " Bloklandı" if blocked else " Dəyişiklik olmadə"
    )
    show_pending_users(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("uappr|"))
@callback_guard
def cb_user_approve(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET approved=1 WHERE chat_id=?", (uid,))
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "✅ İstifadəçi təsdiqləndi.")
    try:
        bot.send_message(
            uid,
            "✅ Hesabınız təsdiqləndi.",
        )
    except Exception:
        pass

    show_pending_users(c.message.chat.id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ublock|"))
@callback_guard
def cb_user_block_pending(c):
    if not is_admin(c.message.chat.id):
        return
    uid = int(c.data.split("|")[1])

    block_user(uid)

    bot.answer_callback_query(c.id, "⛔ İstifadəçi bloklandı.")
    show_pending_users(c.message.chat.id, message=c.message)


# =============== BOT YENİLƏMƏ BİLDİRİŞİ ===============


def broadcast_bot_update(admin_chat_id):
    """Admin paneldən 'Yeniləmə göndər' basanda hamıya refresh düyməsi göndər."""
    if not is_admin(admin_chat_id):
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT chat_id FROM users_with_status WHERE computed_status = ?",
        ("ACTIVE",),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(admin_chat_id, "❌ Aktiv istifadəçi tapılmadı.")
        return

    message_text = (
        "🚀 Best Home Əmlak Axtarış Botu yeniləndi! (v10)\n\n"
        "Yeniliklər:\n"
        "🎁 Şansını sına — gündə 1 dəfə pulsuz gün qazan\n"
        "🔍 Ağıllı axtarış — satılan və kirayə evlər\n"
        "💳 Kartla ödəniş — çox yaxında aktiv olacaq\n\n"
        "Yenilikləri görmək üçün yenilə düyməsinə kliklə 👇"
    )

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔄 Yenilə və davam et", callback_data="refresh_bot"))

    sent = 0
    for (uid,) in rows:
        try:
            bot.send_message(
                uid,
                message_text,
                reply_markup=mk,
            )
            sent += 1
        except Exception as exc:
            mark_user_delivery_failure(uid, str(exc))
            continue

    bot.send_message(
        admin_chat_id,
        f"✅ Yeniləmə bildirişi {sent} istifadəçiyə göndərildi.",
    )


def handle_bot_refresh(message):
    chat_id = message.chat.id
    user_state.pop(chat_id, None)
    clear_user_state(chat_id)
    search_state.pop(chat_id, None)
    customer_request_state.pop(chat_id, None)
    customer_request_rule_state.pop(chat_id, None)
    keyword_alert_state.pop(chat_id, None)
    agent_request_lookup_state.pop(chat_id, None)
    today_flow_state.pop(chat_id, None)
    today_results_cache.pop(chat_id, None)
    complaint_flow_state.pop(chat_id, None)
    admin_reply_state.pop(chat_id, None)
    admin_stats_period.pop(chat_id, None)
    admin_direct_message_state.pop(chat_id, None)
    admin_user_message_state.pop(chat_id, None)
    admin_message_state.pop(chat_id, None)
    admin_panel_page_state.pop(chat_id, None)
    admin_user_page_state.pop(chat_id, None)
    admin_navigation_state.pop(chat_id, None)
    admin_state.pop(chat_id, None)
    ui_state.pop(chat_id, None)
    session_interactions.pop(chat_id, None)
    search_reminder_shown.discard(chat_id)
    return_to_main_menu(chat_id)


@bot.message_handler(func=lambda m: m.text == "🔄 Botu yenilə")
def refresh_button_message(message):
    if message.text and message.text.startswith('/'):
        return

    handle_bot_refresh(message)


@bot.callback_query_handler(func=lambda c: c.data == "bot_refresh")
@callback_guard
def cb_bot_refresh(c):
    try:
        bot.answer_callback_query(c.id, "✅ Yeniləndi.")
    except:
        pass
    handle_bot_refresh(c.message)


@bot.callback_query_handler(func=lambda c: c.data == "refresh_bot")
@callback_guard
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
    if message.text and message.text.startswith('/'):
        return

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
    if has_customer_requests_access(message.chat.id):
        mk.add(
            types.InlineKeyboardButton(
                "🎯 Bu ərazidən maraqlanan müştərilər",
                callback_data="agent_requests",
            )
        )
        mk.add(
            types.InlineKeyboardButton(
                "👥 Mənim müştərilərim", callback_data="agt_my:1"
            )
        )
        mk.add(
            types.InlineKeyboardButton(
                "📦 Arxivlənmiş istəklər", callback_data="cust_req_archived:1"
            )
        )
        mk.add(
            types.InlineKeyboardButton(
                "🔔 Bildiriş qaydaları", callback_data="cust_req_rules"
            )
        )
    bot.send_message(
        message.chat.id,
        "🧑‍💼 Vasitəçi elanları bölməsi:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data == "pub_agents_kw")
@callback_guard
def cb_pub_agents_kw(c):
    if not ensure_allowed_cb(c):
        return
    msg = bot.send_message(
        c.message.chat.id,
        "🔎 Vasitəçi elanlarında açar söz yaz:",
    )
    bot.register_next_step_handler(msg, pub_agent_search_by_keyword)


def build_agent_request_rayon_markup():
    mk = types.InlineKeyboardMarkup()
    row = []
    for rayon in REQUEST_RAYONS:
        row.append(
            types.InlineKeyboardButton(rayon, callback_data=f"agt_req:{quote(rayon)}:1")
        )
        if len(row) == 3:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    mk.add(
        types.InlineKeyboardButton("👥 Mənim müştərilərim", callback_data="agt_my:1")
    )
    mk.add(
        types.InlineKeyboardButton(
            "📦 Arxivlənmiş istəklər", callback_data="cust_req_archived:1"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "🔔 Bildiriş qaydaları", callback_data="cust_req_rules"
        )
    )
    mk.add(types.InlineKeyboardButton("🔔 Bildirişlərim", callback_data="notif_menu"))
    return mk


@bot.callback_query_handler(func=lambda c: c.data == "agent_requests")
@callback_guard
def cb_agent_requests(c):
    return
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        try:
            bot.answer_callback_query(
                c.id, "❌ Bu funksiya sizin üçün aktiv deyil", show_alert=True
            )
        except Exception:
            pass
        return
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass
    build_customer_requests_operation_menu(chat_id, message=c.message)


@bot.callback_query_handler(func=lambda c: c.data == "cust_req_ops")
@callback_guard
def cb_customer_requests_ops(c):
    return
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    build_customer_requests_operation_menu(chat_id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "cust_req_back")
@callback_guard
def cb_customer_requests_back(c):
    return
    if not ensure_allowed_cb(c):
        return
    return_to_main_menu(c.message.chat.id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_op:"))
@callback_guard
def cb_customer_requests_op(c):
    return
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    request_type = c.data.split(":", 1)[1]
    if request_type not in {"buy", "rent"}:
        return
    show_customer_request_district_menu(chat_id, request_type, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cr_rule_rayon_"))
@callback_guard
def cb_customer_request_rule_rayon(c):
    return
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    state = customer_request_rule_state.get(chat_id)
    if not state or state.get("step") != "rayon":
        return
    action = c.data.split(":", 1)[0].replace("cr_rule_rayon_", "")
    if action == "toggle":
        try:
            rayon = unquote(c.data.split(":", 1)[1])
        except Exception:
            rayon = c.data.split(":", 1)[1]
        selected = state.get("rayons", [])
        if rayon in selected:
            selected.remove(rayon)
        else:
            selected.append(rayon)
        state["rayons"] = selected
        send_customer_request_rule_rayon_prompt(chat_id, message=c.message)
    elif action == "done":
        state["step"] = "min_price"
        bot.send_message(
            chat_id,
            "💰 Minimum qiymət yazın (istəyə görə):",
            reply_markup=build_optional_input_keyboard(),
        )
    elif action == "back":
        state["step"] = "type"
        bot.send_message(
            chat_id,
            "🔔 Bildiriş qaydası üçün tip seçin:",
            reply_markup=build_customer_request_rule_type_keyboard(),
        )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def show_agent_requests_by_rayon(
    chat_id: int,
    request_type: str,
    rayon: str,
    page: int = 1,
    message: Optional[types.Message] = None,
):
    rows, total, total_pages, current_page = fetch_agent_requests_page(
        rayon, request_type, page, user_id=chat_id
    )
    title = "🏠 Satılır" if request_type == "buy" else "🏢 Kirayə verilir"
    header = (
        f"{title} — {rayon} üzrə aktiv müştəri istəkləri\n"
        f"Səhifə: {current_page} / {total_pages}\n"
        f"Cəmi: {total}"
    )
    mk = types.InlineKeyboardMarkup()
    nav_buttons = [
        types.InlineKeyboardButton(
            "⏮ İlk", callback_data=f"agt_req:{request_type}:{quote(rayon)}:1"
        ),
        types.InlineKeyboardButton(
            "◀️ Geri",
            callback_data=f"agt_req:{request_type}:{quote(rayon)}:{max(1, current_page - 1)}",
        ),
        types.InlineKeyboardButton(
            f"📄 {current_page} / {total_pages}",
            callback_data=f"agt_req:{request_type}:{quote(rayon)}:{current_page}",
        ),
        types.InlineKeyboardButton(
            "▶️ İrəli",
            callback_data=f"agt_req:{request_type}:{quote(rayon)}:{min(total_pages, current_page + 1)}",
        ),
        types.InlineKeyboardButton(
            "⏭ Son",
            callback_data=f"agt_req:{request_type}:{quote(rayon)}:{total_pages}",
        ),
    ]
    mk.row(*nav_buttons)
    mk.add(
        types.InlineKeyboardButton(
            "⬅️ Geri", callback_data=f"cust_req_op:{request_type}"
        )
    )

    try:
        if message:
            bot.edit_message_text(
                header,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, header, reply_markup=mk)
    except Exception:
        pass

    if not rows:
        try:
            bot.send_message(chat_id, "😕 Bu rayonda aktiv müştəri sorğusu yoxdur.")
        except Exception:
            pass
        return

    for row in rows:
        try:
            send_public_request_card(chat_id, row)
        except Exception:
            continue


@bot.callback_query_handler(func=lambda c: c.data.startswith("agt_req:"))
@callback_guard
def cb_agent_request_page(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        try:
            bot.answer_callback_query(
                c.id, "❌ Bu funksiya sizin üçün aktiv deyil", show_alert=True
            )
        except Exception:
            pass
        return
    try:
        _, request_type, rayon_enc, page_raw = c.data.split(":", 3)
    except ValueError:
        build_customer_requests_operation_menu(chat_id, message=c.message)
        return
    if request_type not in {"buy", "rent"}:
        build_customer_requests_operation_menu(chat_id, message=c.message)
        return
    rayon = unquote(rayon_enc)
    try:
        page = int(page_raw)
    except Exception:
        page = 1
    show_agent_requests_by_rayon(
        chat_id, request_type, rayon, page=page, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_save:"))
@callback_guard
def cb_customer_request_save(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    try:
        req_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    if add_customer_request_favorite(chat_id, req_id):
        bot.send_message(chat_id, "⭐ Sorğu yadda saxlanıldı.")
    else:
        bot.send_message(chat_id, "⭐ Bu sorğu artıq yadda saxlanılıb.")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_arch:"))
@callback_guard
def cb_customer_request_archive(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    try:
        req_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    if add_customer_request_archive(chat_id, req_id):
        bot.send_message(chat_id, "📦 Sorğu arxivləndi.")
    else:
        bot.send_message(chat_id, "📦 Sorğu artıq arxivdədir.")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_unarch:"))
@callback_guard
def cb_customer_request_unarchive(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    try:
        req_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    if remove_customer_request_archive(chat_id, req_id):
        bot.send_message(chat_id, "♻️ Sorğu arxivdən çıxarıldı.")
    else:
        bot.send_message(chat_id, "⚠️ Sorğu arxivdə tapılmadı.")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_archived:"))
@callback_guard
def cb_customer_request_archived(c):
    return
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    try:
        page = int(c.data.split(":", 1)[1])
    except Exception:
        page = 1
    show_user_archived_requests(chat_id, page=page, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "cust_req_rules")
@callback_guard
def cb_customer_request_rules(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    show_customer_request_rules(chat_id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "cust_req_rules_new")
@callback_guard
def cb_customer_request_rules_new(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    start_customer_request_rule_flow(chat_id)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_rule_toggle:"))
@callback_guard
def cb_customer_request_rule_toggle(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    try:
        rule_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    rules = fetch_customer_request_rules(chat_id)
    rule = next((r for r in rules if r.get("id") == rule_id), None)
    if not rule:
        return
    set_customer_request_rule_active(chat_id, rule_id, not rule.get("is_active"))
    show_customer_request_rules(chat_id, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cust_req_alerts:"))
@callback_guard
def cb_customer_request_alerts(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    parts = c.data.split(":")
    period = notification_menu_state.get(chat_id, {}).get("period", "today")
    page = 1
    if len(parts) >= 3:
        period = parts[1]
        try:
            page = int(parts[2])
        except Exception:
            page = 1
    else:
        try:
            page = int(parts[1])
        except Exception:
            page = 1
    show_customer_request_alerts_inbox(
        chat_id, period=period, page=page, message=c.message
    )
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cr_alert_view:"))
@callback_guard
def cb_customer_request_alert_view(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    parts = c.data.split(":")
    if len(parts) < 2:
        return
    try:
        req_id = int(parts[1])
    except Exception:
        return
    rule_id = None
    if len(parts) >= 3:
        try:
            rule_id = int(parts[2])
        except Exception:
            rule_id = None
    req_row = fetch_customer_request_by_id(req_id)
    if not req_row or _row_value_safe(req_row, "status") == "deleted":
        bot.send_message(chat_id, "⚠️ Sorğu tapılmadı.")
        return
    extra_buttons = [
        types.InlineKeyboardButton("⬅️ Bildirişlər", callback_data="notif_menu")
    ]
    if rule_id:
        extra_buttons.insert(
            0,
            types.InlineKeyboardButton(
                "🛑 Bu qaydanı dayandır", callback_data=f"cr_rule_stop:{rule_id}"
            ),
        )
        extra_buttons.insert(
            1,
            types.InlineKeyboardButton(
                "🗑 Bildirişi sil", callback_data=f"cr_alert_delete:{req_id}:{rule_id}"
            ),
        )
    send_public_request_card(chat_id, req_row, extra_buttons=extra_buttons)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cr_rule_stop:"))
@callback_guard
def cb_customer_request_rule_stop(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    try:
        rule_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    set_customer_request_rule_active(chat_id, rule_id, False)
    bot.send_message(chat_id, "🛑 Qayda dayandırıldı.")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("cr_alert_delete:"))
@callback_guard
def cb_customer_request_alert_delete(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        return
    parts = c.data.split(":")
    if len(parts) < 3:
        return
    try:
        req_id = int(parts[1])
        rule_id = int(parts[2])
    except Exception:
        return
    delete_customer_request_alert(chat_id, req_id, rule_id)
    bot.send_message(chat_id, "🗑 Bildiriş silindi.")
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def show_agent_my_customers(
    chat_id: int, page: int = 1, message: Optional[types.Message] = None
):
    page = max(1, int(page or 1))
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM customer_request_favorites cf
        JOIN customer_requests cr ON cr.id = cf.request_id
        WHERE cf.user_id=? AND cr.status='active'
          AND cr.id NOT IN (
              SELECT request_id FROM customer_request_archives WHERE user_id=?
          )
        """,
        (chat_id, chat_id),
    )
    total = cur.fetchone()[0] or 0
    if total == 0:
        conn.close()
        mk_empty = types.InlineKeyboardMarkup()
        mk_empty.add(
            types.InlineKeyboardButton(
                "🎯 Müştəri istəkləri", callback_data="agent_requests"
            )
        )
        try:
            if message:
                bot.edit_message_text(
                    "👥 Hələ müştəri seçməmisiniz.",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_markup=mk_empty,
                )
            else:
                bot.send_message(
                    chat_id, "👥 Hələ müştəri seçməmisiniz.", reply_markup=mk_empty
                )
        except Exception:
            pass
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE_REQ))
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PAGE_SIZE_REQ
    cur.execute(
        """
        SELECT cr.*, cf.created_at as favorite_created_at
        FROM customer_request_favorites cf
        JOIN customer_requests cr ON cr.id = cf.request_id
        WHERE cf.user_id=? AND cr.status='active'
          AND cr.id NOT IN (
              SELECT request_id FROM customer_request_archives WHERE user_id=?
          )
        ORDER BY datetime(cf.created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (chat_id, chat_id, PAGE_SIZE_REQ, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    header = (
        "👥 Mənim müştərilərim\n" f"Səhifə: {page} / {total_pages}\n" f"Cəmi: {total}"
    )
    mk = types.InlineKeyboardMarkup()
    nav_buttons = [
        types.InlineKeyboardButton("⏮ İlk", callback_data="agt_my:1"),
        types.InlineKeyboardButton(
            "◀️ Geri", callback_data=f"agt_my:{max(1, page - 1)}"
        ),
        types.InlineKeyboardButton(
            f"📄 {page} / {total_pages}", callback_data=f"agt_my:{page}"
        ),
        types.InlineKeyboardButton(
            "▶️ İrəli", callback_data=f"agt_my:{min(total_pages, page + 1)}"
        ),
        types.InlineKeyboardButton("⏭ Son", callback_data=f"agt_my:{total_pages}"),
    ]
    mk.row(*nav_buttons)
    mk.add(types.InlineKeyboardButton("🎯 Rayon seç", callback_data="agent_requests"))

    try:
        if message:
            bot.edit_message_text(
                header,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=mk,
            )
        else:
            bot.send_message(chat_id, header, reply_markup=mk)
    except Exception:
        pass

    for row in rows:
        try:
            send_public_request_card(chat_id, row)
        except Exception:
            continue


@bot.callback_query_handler(func=lambda c: c.data.startswith("agt_my:"))
@callback_guard
def cb_agent_my_customers(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        try:
            bot.answer_callback_query(
                c.id, "❌ Bu funksiya sizin üçün aktiv deyil", show_alert=True
            )
        except Exception:
            pass
        return
    try:
        page = int(c.data.split(":", 1)[1])
    except Exception:
        page = 1
    show_agent_my_customers(chat_id, page=page, message=c.message)
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("agt_int:"))
@callback_guard
def cb_agent_interest(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    if not has_customer_requests_access(chat_id):
        try:
            bot.answer_callback_query(
                c.id, "❌ Bu funksiya sizin üçün aktiv deyil", show_alert=True
            )
        except Exception:
            pass
        return
    try:
        req_id = int(c.data.split(":", 1)[1])
    except Exception:
        return
    if agent_has_interest(chat_id, req_id):
        try:
            bot.answer_callback_query(
                c.id, "✅ Artıq siyahıya əlavə olunub", show_alert=True
            )
        except Exception:
            pass
        return
    req_row = fetch_customer_request_by_id(req_id)
    if not req_row or _row_value_safe(req_row, "status") not in {None, "active"}:
        try:
            bot.answer_callback_query(c.id, "⚠️ Sorğu tapılmadı və ya aktiv deyil")
        except Exception:
            pass
        return
    if store_agent_interest(chat_id, req_id):
        try:
            bot.answer_callback_query(c.id, "✅ Müştəri siyahınıza əlavə olundu")
        except Exception:
            pass
        try:
            bot.send_message(chat_id, "👥 Yeni müştəri siyahıya əlavə edildi:")
            mk_card = types.InlineKeyboardMarkup()
            wa_url = make_whatsapp_url(
                _row_value_safe(req_row, "phone"),
                "Salam, müştəri sorğunuz ilə maraqlanıram.",
            )
            if wa_url:
                mk_card.add(types.InlineKeyboardButton("💬 WhatsApp yaz", url=wa_url))
            bot.send_message(
                chat_id, format_agent_request_card(req_row), reply_markup=mk_card
            )
        except Exception:
            pass
    else:
        try:
            bot.answer_callback_query(c.id, "⚠️ Əlavə etmək alınmadı")
        except Exception:
            pass


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
        WHERE LOWER(COALESCE(Umumi_melumat, '')) LIKE ?
           OR LOWER(COALESCE(Unvan, '')) LIKE ?
           OR LOWER(COALESCE(Rayon_Qesebe, '')) LIKE ?
           OR LOWER(COALESCE(Emlakin_novu, '')) LIKE ?
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


@bot.message_handler(
    func=lambda m: agent_request_lookup_state.get(m.chat.id, {}).get("step") == "rayon"
)
def handle_agent_request_rayon(message):
    if not ensure_allowed(message):
        return
    chat_id = message.chat.id
    if not has_customer_requests_access(chat_id):
        bot.send_message(chat_id, "❌ Bu funksiya sizin üçün aktiv deyil.")
        agent_request_lookup_state.pop(chat_id, None)
        return
    if message.text == "⬅️ Geri (Əsas menyu)":
        agent_request_lookup_state.pop(chat_id, None)
        return_to_main_menu(chat_id)
        return
    rayon = (message.text or "").strip()
    if not rayon:
        bot.send_message(chat_id, "⚠️ Rayon adı boş ola bilməz.")
        return
    agent_request_lookup_state.pop(chat_id, None)
    entries = fetch_active_requests_by_rayon(
        rayon, include_all_status=True, user_id=chat_id
    )
    if not entries:
        bot.send_message(chat_id, "😕 Bu rayonda aktiv müştəri sorğusu yoxdur.")
        return
    bot.send_message(
        chat_id, f"🎯 {rayon} üzrə aktiv müştəri sorğuları: {len(entries)}"
    )
    for row in entries:
        send_public_request_card(chat_id, row)

# =============== ADMIN STATİSTİKA, AXTARIŞ, BROADCAST ===============


STATS_PERIOD_MAP = {
    "day": TEXTS_AZ["admin_stats_period_day"].replace("📆 ", "").replace("📅 ", ""),
    "week": TEXTS_AZ["admin_stats_period_week"].replace("📆 ", "").replace("📅 ", ""),
    "month": TEXTS_AZ["admin_stats_period_month"].replace("📆 ", "").replace("📅 ", ""),
}


def stats_period_keyboard(selected: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    buttons = [
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_stats_period_day"], callback_data="stats_period:day"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_stats_period_week"], callback_data="stats_period:week"
        ),
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_stats_period_month"], callback_data="stats_period:month"
        ),
    ]
    mk.row(*buttons)
    mk.add(
        types.InlineKeyboardButton(
            TEXTS_AZ["admin_stats_customer_requests"], callback_data="adm_req_types"
        )
    )
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

        name = str(name_raw or "").strip() or "Açar sözlə axtarış"
        count = int(count_raw or 0)
        lines.append(f"{idx}) {name} — {count} axtarış")
    return lines


def normalize_rayon_label(name_raw: str) -> str:
    name = str(name_raw or "").strip()
    return name or "Açar sözlə axtarış"


def format_rayon_stats(rayons):
    lines = []
    for row in rayons:
        try:
            cnt = int(row["cnt"] or 0)
        except Exception:
            cnt = 0
        try:
            rn = row["rn"]
        except Exception:
            rn = row[0] if len(row) > 0 else ""
        lines.append(f"• {normalize_rayon_label(rn)} — {cnt}")
    return lines


def build_profile_url(chat_id: int, username: Optional[str]) -> str:
    username_clean = (username or "").strip().lstrip("@")
    if username_clean:
        return f"https://t.me/{username_clean}"
    return f"tg://user?id={chat_id}"


def get_profile_url_for_user(user_id: int) -> str:
    record = get_user_record(user_id)
    username = record.get("username") if record else None
    return build_profile_url(user_id, username)


def format_active_user_stats(users):
    blocks = []
    buttons = []

    for row in users:
        try:
            chat_id = row["chat_id"]
        except Exception:
            chat_id = None

        if chat_id is None:
            continue

        try:
            cnt = int(row["cnt"] or 0)
        except Exception:
            cnt = 0

        try:
            full_name = str(row["full_name"] or "").strip()
        except Exception:
            full_name = str(row[2] if len(row) > 2 else "").strip()

        try:
            username = str(row["username"] or "").strip()
        except Exception:
            username = str(row[3] if len(row) > 3 else "").strip()
        display_name = full_name or username or "—"
        label_name = display_name
        if len(label_name) > 30:
            label_name = label_name[:27] + "..."

        try:
            profile_url = build_profile_url(chat_id, username)
        except Exception:
            profile_url = None

        block_lines = []
        if display_name != "—":
            block_lines.append(f"👤 {html.escape(display_name)}")
        block_lines.append(
            f'🆔 ID: <a href="tg://user?id={chat_id}">{chat_id}</a>'
        )
        block_lines.append(f"🔍 Axtarış sayı: {cnt}")

        blocks.append("\n".join(block_lines))

    return blocks, buttons


def show_admin_stats(
    chat_id, period: Optional[str] = None, message_id: Optional[int] = None
):
    if not is_admin(chat_id):
        return

    selected_period = period or admin_stats_period.get(chat_id, "day")
    admin_stats_period[chat_id] = selected_period
    start_date, end_date, period_label = stats_period_range(selected_period)
    today_start, today_end = get_today_bounds()
    today_start_str = format_sqlite_datetime(today_start)
    today_end_str = format_sqlite_datetime(today_end)

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

    def detect_date_column(cur, table: str) -> Optional[str]:
        return detect_table_date_column(cur, table)

    def count_today_new(cur, table: str) -> int:
        col = detect_date_column(cur, table)
        if not col:
            return 0
        return safe_count(
            cur,
            (
                f"SELECT COUNT(*) FROM {table} WHERE ((typeof({col})='integer' AND {col} BETWEEN ? AND ?) "
                f"OR datetime({col}) BETWEEN datetime(?) AND datetime(?))"
            ),
            (
                int(today_start.timestamp()),
                int(today_end.timestamp()),
                today_start_str,
                today_end_str,
            ),
        )

    def op_counts(cur, table: str):
        total = (
            safe_count(cur, f"SELECT COUNT(*) FROM {table}")
            if table_exists(cur, table)
            else 0
        )
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

    total_users = active_users = pending_users = expired_users = blocked_users = 0
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
            active_users = admin_user_status_count(cur_local, "active")
            expired_users = admin_user_status_count(cur_local, "expired")
            pending_users = admin_user_status_count(cur_local, "pending")
            blocked_users = admin_user_status_count(cur_local, "blocked")
            demo_users = admin_user_status_count(cur_local, "demo")

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
                           u.full_name,
                           u.username
                    FROM search_logs sl
                    LEFT JOIN users u ON u.chat_id = sl.chat_id
                    WHERE DATE(sl.created_at) BETWEEN ? AND ?
                    GROUP BY sl.chat_id
                    ORDER BY cnt DESC
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
    today_new_main = 0
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
                today_new_main = count_today_new(cur_main, main_table)
        except Exception:
            pass
        finally:
            try:
                close_main_conn(conn_main)
            except Exception:
                pass

    local_total = local_sale = local_rent = 0
    today_new_local = 0
    conn_local_counts = None
    try:
        conn_local_counts = get_local_conn()
        cur_local_counts = conn_local_counts.cursor()
        if table_exists(cur_local_counts, "listings_approved"):
            local_total, local_sale, local_rent = op_counts(
                cur_local_counts, "listings_approved"
            )
            today_new_local = count_today_new(cur_local_counts, "listings_approved")
    finally:
        try:
            conn_local_counts.close()
        except Exception:
            pass

    total_listings = main_total + local_total
    sale_total = main_sale + local_sale
    rent_total = main_rent + local_rent
    today_new_listings = today_new_main + today_new_local

    lines = [f"📊 BestHome Statistikalar — {period_label}", ""]
    lines.append("👥 İstifadəçilər:")
    lines.append(f"• Cəmi: {total_users}")
    lines.append(f"• Aktiv: {active_users}")
    lines.append(f"• Demo: {demo_users}")
    lines.append(f"• Vaxtı bitmiş: {expired_users}")
    lines.append(f"• Bloklanan: {blocked_users}")
    lines.append(f"• Təsdiqsiz: {pending_users}")
    lines.append("")

    lines.append("🏠 Elanlar:")
    lines.append(f"• Ümumi: {total_listings}")
    lines.append(f"• Satılır: {sale_total}")
    lines.append(f"• Kirayə: {rent_total}")
    lines.append(f"📈 Bu gün əlavə olunan yeni elanlar: {today_new_listings}")
    lines.append("")

    lines.append(f"📍 Rayonlar üzrə axtarışlar ({period_label}):")
    if search_stats_available and top_rayons:
        lines.extend(format_rayon_stats(top_rayons))
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
    active_user_blocks, profile_buttons = (
        format_active_user_stats(top_users) if search_stats_available else ([], [])
    )
    if search_stats_available and active_user_blocks:
        for idx, block in enumerate(active_user_blocks):
            lines.append(block)
            if idx != len(active_user_blocks) - 1:
                lines.append("")
    else:
        lines.append("• Məlumat yoxdur")

    text = "\n".join(lines)
    keyboard = stats_period_keyboard(selected_period)
    for btn in profile_buttons:
        try:
            keyboard.add(btn)
        except Exception:
            continue

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


@bot.callback_query_handler(
    func=lambda c: c.data and c.data.startswith("stats_period:")
)
@callback_guard
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



def show_bonus_stats(chat_id: int):
    if not is_admin(chat_id):
        return

    stats = fetch_bonus_stats()
    probabilities = get_bonus_probabilities()

    def build_bar(percent: int, max_percent: int) -> str:
        total_blocks = 10
        if max_percent <= 0:
            max_percent = 1
        filled = 0
        if percent > 0:
            filled = max(1, round((percent / max_percent) * total_blocks))
        filled = min(total_blocks, filled)
        return "▓" * filled + "░" * (total_blocks - filled)

    lines = ["🎁 ŞANS STATİSTİKASI", ""]
    lines.append("📊 Bu gün")
    lines.append(f"• Klik edənlər: {stats.get('today_spins', 0)}")
    lines.append(f"• Verilən bonus: {stats.get('today_days', 0)} gün")
    lines.append("")

    lines.append("📈 Ümumi")
    lines.append(f"• Toplam klik: {stats.get('total_spins', 0)}")
    lines.append(f"• Toplam bonus gün: {stats.get('total_days', 0)}")
    lines.append("")

    lines.append("🎯 Ehtimallar")
    if probabilities:
        max_weight = max(probabilities.values()) if probabilities else 1
        max_weight = max(max_weight, 1)
        for day, weight in sorted(probabilities.items()):
            bar = build_bar(int(weight), int(max_weight))
            lines.append(f"{day} gün  {bar} {weight}%")
    else:
        lines.append("• Ehtimal tapılmadı")

    recent = stats.get("recent") or []
    if recent:
        lines.append("\n🕒 Son kliklər (latest first, limit 5):")
        for row in recent[:5]:
            user_id = _row_value_safe(row, "user_id", "-")
            days_won = _row_value_safe(row, "granted_days", _row_value_safe(row, "days_won", 0)) or 0
            created_raw = _row_value_safe(row, "created_at")
            created_dt = parse_dt_safe(created_raw)
            display_time = created_dt + timedelta(hours=4) if created_dt else None
            time_text = (
                display_time.strftime("%H:%M") if display_time else str(created_raw or "-")
            )
            lines.append(f"• {time_text} — {user_id} → {days_won} gün")

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "⚙️ Ehtimalları dəyiş", callback_data="bonusprob:edit"
        )
    )
    mk.add(
        types.InlineKeyboardButton("🔄 Yenilə", callback_data="bonusprob:refresh")
    )
    mk.add(
        types.InlineKeyboardButton(ADMIN_PANEL_BACK_MAIN, callback_data="adm_back:main")
    )

    bot.send_message(chat_id, "\n".join(lines), reply_markup=mk)



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
        display_time = m_start + timedelta(hours=4)
        history_lines.append(
            f"{display_time.strftime('%B %Y')}: {total} AZN ({cnt} ödəniş)"
        )

    conn.close()

    report_lines = [
        f"📅 {(current_start + timedelta(hours=4)).strftime('%B %Y')} (cari ay):\n"
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

            cur.execute(
                "SELECT COUNT(*) FROM users WHERE blocked=1 OR COALESCE(is_blocked, 0)=1"
            )
            blocked_users = cur.fetchone()[0] or 0

            cur.execute(
                """
                SELECT chat_id
                FROM users
                WHERE COALESCE(blocked, 0)=0
                  AND COALESCE(is_blocked, 0)=0
                  AND COALESCE(is_active, 1)=1
                """
            )
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

    target_ids, total_users, blocked_users, paid_users, demo_users = (
        fetch_targets_and_stats()
    )

    if not target_ids:
        bot.send_message(message.chat.id, "❌ İstifadəçi tapılmadı.")
        return

    def send_broadcast(
        admin_chat_id,
        recipients,
        payload,
        total_users,
        blocked_users,
        paid_users,
        demo_users,
    ):
        success = 0
        failed = 0
        for uid in recipients:
            try:
                bot.send_message(uid, f"📢 Admin bildirişi:\n{payload}")
                success += 1
            except Exception as exc:
                mark_user_delivery_failure(uid, str(exc))
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

    def build_keyword_columns(cur: sqlite3.Cursor, table: str) -> List[str]:
        cur.execute("PRAGMA table_info(" + table + ")")
        cols = {row[1].lower(): row[1] for row in cur.fetchall()}
        if not cols:
            return []
        groups = [
            ["title", "prop_type", "emlakin_novu"],
            ["description", "summary", "umumi_melumat", "text", "details"],
            ["district", "rayon", "rayon_qesebe", "region"],
            ["address", "unvan", "adres"],
        ]
        selected = []
        for group in groups:
            for name in group:
                col = cols.get(name.lower())
                if col:
                    selected.append(col)
                    break
        return list(dict.fromkeys(selected))

    def build_keyword_where(columns: List[str]) -> Tuple[str, List[str]]:
        if not columns:
            return "1=0", []
        conds = ["LOWER(COALESCE(" + col + ", '')) LIKE ?" for col in columns]
        return "(" + " OR ".join(conds) + ")", [like] * len(columns)

    # MAIN DB listings
    if os.path.exists(MAIN_DB):
        try:
            conn = get_main_conn()
            cur = conn.cursor()
            columns = build_keyword_columns(cur, "listings")
            where_clause, params = build_keyword_where(columns)
            sql = (
                "SELECT * FROM listings WHERE "
                + where_clause
                + " ORDER BY date_read DESC LIMIT 30"
            )
            cur.execute(sql, params)
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
    columns = build_keyword_columns(cur, "listings_approved")
    where_clause, params = build_keyword_where(columns)
    sql = (
        "SELECT * FROM listings_approved WHERE "
        + where_clause
        + " ORDER BY date_added DESC LIMIT 30"
    )
    cur.execute(sql, params)
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
            WHERE LOWER(COALESCE(Emlakin_novu, '')) LIKE ?
               OR LOWER(COALESCE(Umumi_melumat, '')) LIKE ?
               OR LOWER(COALESCE(Rayon_Qesebe, '')) LIKE ?
               OR LOWER(COALESCE(Unvan, '')) LIKE ?
            ORDER BY added_at DESC
            LIMIT 30
            """,
            (like, like, like, like),
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


def has_active_text_flow(chat_id: int) -> bool:
    flow_states = [
        user_state,
        complaint_flow_state,
        admin_reply_state,
        admin_direct_message_state,
        admin_user_message_state,
        admin_user_extend_state,
        admin_user_action_state,
        admin_message_state,
        admin_update_state,
        customer_request_state,
        agent_request_lookup_state,
        admin_customer_request_state,
        customer_request_rule_state,
        keyword_alert_state,
        notification_rule_state,
    ]
    if search_state.get(chat_id, {}).get("step"):
        return True
    for state in flow_states:
        entry = state.get(chat_id)
        if not entry:
            continue
        if isinstance(entry, dict):
            if entry.get("step"):
                return True
        else:
            return True
    return False


@bot.message_handler(content_types=["text"])
def guard_idle_messages(message):
    if message.text and message.text.startswith("/"):
        return
    if has_active_text_flow(message.chat.id):
        return
    bot.send_message(
        message.chat.id,
        "Əvvəlcə menudan seçim edin, sonra yazın.\n"
        "Əgər menyu görünmürsə /start yazın, menyu açılacaq.",
    )


# =============== RUN (Render / Lokal) ===============


def api_error_response(message: str, status_code: int = 400):
    logger.warning("API error status=%s message=%s", status_code, message)
    return jsonify({"ok": False, "error": message}), status_code


def api_ok_response(payload: dict, status_code: int = 200):
    body = {"ok": True}
    body.update(payload)
    return jsonify(body), status_code


def _wrap_api(name: str, handler):
    """Execute an API handler with consistent logging and error handling."""

    @wraps(handler)
    def _inner(*args, **kwargs):
        started_at = time.time()
        try:
            return handler(*args, **kwargs)
        except Exception:
            logger.exception("API handler failed name=%s", name)
            return api_error_response("Server error", 500)
        finally:
            duration_ms = int((time.time() - started_at) * 1000)
            logger.info("API %s completed in %sms", name, duration_ms)

    return _inner()


def resolve_user_from_payload(payload: dict):
    uid_raw = (
        payload.get("telegram_user_id")
        or payload.get("user_id")
        or payload.get("chat_id")
    )
    if uid_raw in (None, "", "null"):
        return None, api_error_response("Telegram user_id missing", 400)
    try:
        uid = int(uid_raw)
    except Exception:
        return None, api_error_response("Düzgün telegram_user_id daxil edin", 400)

    if uid == 0:
        return None, api_error_response("Telegram user_id missing", 400)

    user = ensure_user_exists(
        uid,
        username=payload.get("username") or "",
        full_name=payload.get("full_name") or "",
    )
    return uid, None if user else api_error_response("İstifadəçi yaradılmadı", 500)


def map_date_range_to_days(code: Optional[str]) -> Optional[Union[int, str]]:
    if not code:
        return None
    val = str(code).lower()
    mapping = {
        "today": "today",
        "7_days": 7,
        "7d": 7,
        "1_month": 30,
        "month": 30,
        "30d": 30,
        "2_months": 60,
        "60d": 60,
        "3_months": 90,
        "90d": 90,
        "all": None,
    }
    return mapping.get(val)


def compute_besthome_overview_stats():
    stats = {
        "today_total": 0,
        "today_sale": 0,
        "today_rent": 0,
        "last_24h": 0,
        "total_active": 0,
    }

    if not os.path.exists(MAIN_DB):
        return stats

    conn = None
    try:
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        )
        if not cur.fetchone():
            return stats
        cur.execute("SELECT * FROM listings")
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        logger.exception("Failed to compute stats from besthome.db")
        return stats
    finally:
        if conn:
            try:
                close_main_conn(conn)
            except Exception:
                pass

    last_24h_window = get_last_24h_window()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    for row in rows:
        row["__source"] = "main"
        approved_raw = _row_value_safe(row, "approved")
        if approved_raw is None:
            approved_raw = _row_value_safe(row, "is_approved")
        if approved_raw is not None:
            if str(approved_raw).lower() in {
                "0",
                "false",
                "pending",
                "rejected",
                "reject",
            }:
                continue
        stats["total_active"] += 1
        ev_dt = extract_listing_datetime(row)
        if not ev_dt:
            continue

        if last_24h_window[0] <= ev_dt < last_24h_window[1]:
            stats["last_24h"] += 1
        if ev_dt >= today_start:
            stats["today_total"] += 1
            op = normalize_operation_value(
                _row_value_safe(row, "operation") or _row_value_safe(row, "Emeliyyat")
            )
            if op == "sale":
                stats["today_sale"] += 1
            elif op == "rent":
                stats["today_rent"] += 1

    return stats


def filter_results_by_rayon(items: List[dict], rayon: Optional[str]):
    if not rayon or rayon == "all":
        return items
    filters = {"region": "all", "rayon": rayon}
    return [ev for ev in items if matches_region_rayon(ev, filters)]


def parse_int_value(val, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(val)
    except Exception:
        return default


def get_user_id_from_request() -> Optional[int]:
    uid_raw = request.args.get("user_id") if request else None
    if uid_raw in (None, "", "null"):
        payload = request.get_json(silent=True) if request else None
        if isinstance(payload, dict):
            uid_raw = payload.get("user_id")

    if uid_raw in (None, "", "null"):
        return None

    try:
        return int(uid_raw)
    except Exception:
        return None


def clean_field(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in ("-", "—", "–", "None", "null"):
        return ""
    return text


def log_api_call(name: str, user_id: Optional[int], payload: dict):
    logger.info("API call %s user=%s payload=%s", name, user_id, payload)


def subscription_notifier():
    while True:
        try:
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT chat_id, effective_expires_at
                FROM users_with_status
                WHERE computed_status='ACTIVE'
                  AND effective_expires_at IS NOT NULL
                  AND CAST(effective_expires_at AS INTEGER)
                      BETWEEN strftime('%s','now') AND strftime('%s','now','+1 day')
                """
            )
            rows = cur.fetchall()
            conn.close()
            for chat_id, exp_ts in rows:
                key = (chat_id, exp_ts)
                if key not in subscription_warn_cache:
                    try:
                        bot.send_message(
                            chat_id,
                            "⚠️ Hesabınızın bitməsinə 1 gün qalıb",
                        )
                    except Exception:
                        pass
                    subscription_warn_cache.add(key)
        except Exception as e:
            print("Subscription notifier error:", e)
        time.sleep(3600)


def main_menu(chat_id):
    set_ui_context(chat_id, UI_CONTEXT_MAIN)
    send_main_menu(chat_id, "📋 Əsas menyudan seçim et:", force=True)


@bot.callback_query_handler(func=lambda c: True)
def cb_unhandled_callback(c):
    safe_answer_callback_query(c.id)
    logger.warning(
        "UNHANDLED CALLBACK chat_id=%s from=%s data=%s",
        c.message.chat.id if c.message else None,
        c.from_user.id if c.from_user else None,
        c.data,
    )
    if c.message:
        recover_main_menu(c.message.chat.id, c.message)


_app_initialized = False
app: Optional[Flask] = None
__all__ = ["main", "create_flask_app", "app"]

_polling_started = threading.Event()


def _initialize_app_state():
    global _app_initialized
    if _app_initialized:
        return
    logger.info("DATA_DIR resolved to %s", BASE_DATA_DIR)
    _log_db_status()
    init_local_db()
    init_agents_db()
    init_main_db_indices()
    ensure_fts_tables()
    check_favorite_price_drops()
    _app_initialized = True


def create_flask_app():
    global app
    if app is not None:
        return app

    from admin import admin_bp

    _initialize_app_state()
    app = Flask(__name__)
    app.secret_key = (
        os.environ.get("ADMIN_PANEL_SECRET_KEY")
        or os.environ.get("FLASK_SECRET_KEY")
        or os.urandom(32)
    )
    app.config["SESSION_COOKIE_NAME"] = "besthome_admin_session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    if os.environ.get("ENV") == "prod":
        app.config["SESSION_COOKIE_SECURE"] = True

    app.register_blueprint(admin_bp)
    logger.info("Web admin panel registered at /admin")

    @app.route("/")
    def home():
        def _handler():
            resp = send_file(os.path.join(BASE_DIR, "index.html"))
            resp.headers["Cache-Control"] = "no-store"
            return resp

        return _wrap_api("home", _handler)

    @app.route("/api/health", methods=["GET"])
    def api_health():
        def _handler():
            db_status = {"main": False, "local": False}
            try:
                if os.path.exists(MAIN_DB):
                    conn_main = get_main_conn()
                    conn_main.execute("SELECT 1")
                    db_status["main"] = True
                    close_main_conn(conn_main)
            except Exception:
                logger.exception("Health check failed for main DB")
            try:
                conn_local = get_local_conn()
                conn_local.execute("SELECT 1")
                db_status["local"] = True
                conn_local.close()
            except Exception:
                logger.exception("Health check failed for local DB")

            return api_ok_response(
                {"time": datetime.utcnow().isoformat(), "db": db_status}
            )

        return _wrap_api("health", _handler)

    @app.route("/download/local_data.db", methods=["GET"])
    def download_local_db():
        file_path = "/opt/render/project/src/local_data.db"
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        return "File not found", 404

    @app.route("/api/stats/overview", methods=["GET"])
    def api_stats_overview():
        def _handler():
            stats = compute_besthome_overview_stats()
            return api_ok_response(stats)

        return _wrap_api("stats_overview", _handler)

    @app.route("/api/listings/search", methods=["GET"])
    def api_listings_search():
        args = request.args.to_dict() or {}

        def _handler():
            user_id = get_user_id_from_request()
            if user_id is not None:
                if user_id == 0 and ENV != "dev":
                    user_id = None
                else:
                    try:
                        ensure_user_exists(user_id)
                    except Exception:
                        logger.warning(
                            "Invalid user_id passed to listings search: %s", user_id
                        )

            page = _parse_page(args.get("page"))
            page_size = PAGE_SIZE

            query = str(args.get("q") or "").strip()
            op = normalize_operation_value(args.get("op") or args.get("operation"))
            rayon = args.get("district") or args.get("rayon")
            min_price = parse_int_value(args.get("min_price"))
            max_price = parse_int_value(args.get("max_price"))
            rooms = args.get("rooms")
            credit = args.get("credit")
            phone = args.get("phone")
            date_days = map_date_range_to_days(args.get("date_range"))

            filters = {
                "op": op or "all",
                "prop": "all",
                "rayon": rayon,
                "region": "all",
                "min_price": min_price,
                "max_price": max_price,
                "rooms": None,
                "date_days": date_days,
            }

            results = []

            if os.path.exists(MAIN_DB):
                conn = get_main_conn()
                cur = conn.cursor()
                base = "SELECT * FROM listings"
                flt, params = build_filters_sql(
                    filters.get("op"),
                    filters.get("prop"),
                    None,
                    min_price,
                    max_price,
                    mode="main",
                )
                date_col = detect_table_date_column(cur, "listings")
                date_sql, date_params = build_date_range_clause(date_col, date_days)
                cur.execute(
                    base + flt + date_sql + " ORDER BY date_read DESC, id DESC",
                    params + date_params,
                )
                for r in cur.fetchall():
                    d = dict(r)
                    d["__source"] = "main"
                    results.append(d)
                close_main_conn(conn)

            conn = get_local_conn()
            cur = conn.cursor()
            base = "SELECT * FROM listings_approved"
            flt, params = build_filters_sql(
                filters.get("op"),
                filters.get("prop"),
                None,
                min_price,
                max_price,
                mode="local",
            )
            date_col = detect_table_date_column(cur, "listings_approved")
            date_sql, date_params = build_date_range_clause(date_col, date_days)
            cur.execute(
                base + flt + date_sql + " ORDER BY date_added DESC, id DESC",
                params + date_params,
            )
            for r in cur.fetchall():
                d = dict(r)
                d["__source"] = "local"
                results.append(d)
            conn.close()

            filtered = []

            phrase = None
            tokens: List[str] = []
            if '"' in query:
                match = re.search(r"\"([^\"]+)\"", query)
                if match:
                    phrase = match.group(1)
            norm_query = normalize_text(query)
            if norm_query:
                tokens = [tok for tok in norm_query.split() if tok]

            for ev in results:
                if not is_within_date_range(ev, date_days):
                    continue
                if not matches_region_rayon(ev, filters):
                    continue
                if rooms and not _matches_rooms_exact(ev, rooms):
                    continue
                if not _matches_credit(ev, credit):
                    continue
                if phone and not _matches_phone(ev, phone):
                    continue
                if phrase:
                    if not _matches_phrase(ev, phrase):
                        continue
                elif tokens:
                    if not _matches_tokens(ev, tokens):
                        continue
                filtered.append(ev)

            filtered.sort(key=safe_date, reverse=True)
            total = len(filtered)
            start = (page - 1) * page_size
            end = start + page_size
            page_items = filtered[start:end]
            _augment_favorite_flag(user_id, page_items)

            page_items = [_normalize_listing_response(ev) for ev in page_items]

            total_pages = max(1, math.ceil(total / page_size))
            log_api_call(
                "listings_search",
                user_id,
                {
                    "query": query,
                    "op": op,
                    "rayon": rayon,
                    "page": page,
                    "date_days": date_days,
                },
            )

            return api_ok_response(
                {
                    "page": page,
                    "pages": total_pages,
                    "total": total,
                    "items": page_items,
                }
            )

        return _wrap_api("listings_search", _handler)

    @app.route("/api/listings/detail", methods=["GET"])
    def api_listing_detail():
        args = request.args.to_dict() or {}

        def _handler():
            listing_id = parse_int_value(args.get("id"))
            if listing_id is None:
                return api_error_response("id tələb olunur", 400)
            sources = ["main", "local"]
            for src in sources:
                ev = fetch_listing_by_source(src, listing_id)
                if ev:
                    normalized = _normalize_listing_response(ev)
                    logger.info(
                        "Listing detail fetched id=%s source=%s", listing_id, src
                    )
                    if not normalized.get("source_link"):
                        logger.debug(
                            "Listing detail missing source_link id=%s source=%s",
                            listing_id,
                            src,
                        )
                    return api_ok_response({"item": normalized})
            return api_error_response("Elan tapılmadı", 404)

        return _wrap_api("listing_detail", _handler)

    @app.route("/api/agents/search", methods=["GET"])
    def api_agents_search():
        args = request.args.to_dict() or {}

        def _handler():
            page = _parse_page(args.get("page"))
            page_size = PAGE_SIZE
            query = normalize_text(args.get("q") or "")
            phone = args.get("phone") or ""
            district = normalize_text(args.get("district") or args.get("rayon") or "")
            op = normalize_operation_value(args.get("op") or args.get("operation"))

            if not os.path.exists(AGENTS_DB):
                return api_ok_response(
                    {"page": page, "pages": 1, "total": 0, "items": []}
                )

            conn = get_agents_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
            )
            if not cur.fetchone():
                conn.close()
                return api_ok_response(
                    {"page": page, "pages": 1, "total": 0, "items": []}
                )

            cur.execute("SELECT * FROM agents")
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

            filtered = []
            for row in rows:
                blob = normalize_text(
                    " ".join(
                        [
                            str(_row_value_safe(row, "summary") or ""),
                            str(_row_value_safe(row, "rayon") or ""),
                            str(
                                _row_value_safe(row, "agent_name")
                                or _row_value_safe(row, "name")
                                or ""
                            ),
                        ]
                    )
                )
                if query and query not in blob:
                    continue
                if district and district not in blob:
                    continue
                if op and normalize_operation_value(_row_value_safe(row, "operation")) != op:
                    continue
                if phone and phone not in str(_row_value_safe(row, "phone") or ""):
                    continue
                filtered.append(row)

            total = len(filtered)
            start = (page - 1) * page_size
            end = start + page_size
            page_items = filtered[start:end]
            total_pages = max(1, math.ceil(total / page_size))
            log_api_call(
                "agents_search",
                None,
                {"q": query, "district": district, "op": op, "phone": phone},
            )
            return api_ok_response(
                {
                    "page": page,
                    "pages": total_pages,
                    "total": total,
                    "items": page_items,
                }
            )

        return _wrap_api("agents_search", _handler)

    @app.route("/api/favorites/list", methods=["GET"])
    def api_favorites_list():
        args = request.args.to_dict() or {}

        def _handler():
            user_id = get_user_id_from_request()
            if user_id is None or (user_id == 0 and ENV != "dev"):
                return api_error_response("Telegram user_id missing", 400)

            ensure_user_exists(user_id)
            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT listing_id, source, added_at
                FROM favorites
                WHERE chat_id=?
                ORDER BY added_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            conn.close()

            results = []
            for r in rows:
                src = r["source"] or "main"
                if src == "besthome":
                    src = "main"
                listing = fetch_listing_by_source(src, r["listing_id"])
                if not listing and src != "main":
                    listing = fetch_listing_by_source("main", r["listing_id"])

                base_payload = {
                    "listing_id": r["listing_id"],
                    "created_at": r["added_at"],
                    "source": src,
                }

                if listing:
                    normalized = _normalize_listing_response(listing)
                    normalized.update(base_payload)
                    if not normalized.get("id"):
                        normalized["id"] = r["listing_id"]
                    results.append(normalized)
                else:
                    missing_payload = dict(base_payload)
                    missing_payload["missing"] = True
                    results.append(missing_payload)

            log_api_call("favorites_list", user_id, {"count": len(results)})
            return api_ok_response({"items": results, "total": len(results)})

        return _wrap_api("favorites_list", _handler)

    @app.route("/api/favorites/toggle", methods=["POST"])
    def api_favorites_toggle():
        payload = request.get_json(silent=True) or {}

        def _handler():
            user_id = get_user_id_from_request()
            if user_id is None or (user_id == 0 and ENV != "dev"):
                return api_error_response("Telegram user_id missing", 400)

            ensure_user_exists(user_id)
            listing_id = parse_int_value(payload.get("listing_id"))
            if listing_id is None:
                return api_error_response("listing_id tələb olunur", 400)
            source_raw = payload.get("source") or "main"
            source = "main" if source_raw in ("besthome", "main") else source_raw

            conn = get_local_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM favorites WHERE chat_id=? AND listing_id=? AND source=?",
                (user_id, listing_id, source),
            )
            exists = cur.fetchone() is not None

            if exists:
                cur.execute(
                    "DELETE FROM favorites WHERE chat_id=? AND listing_id=? AND source=?",
                    (user_id, listing_id, source),
                )
                is_fav = False
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO favorites (chat_id, listing_id, source, added_at) VALUES (?, ?, ?, ?)",
                    (user_id, listing_id, source, datetime.utcnow().isoformat()),
                )
                is_fav = True
            conn.commit()
            conn.close()

            log_api_call(
                "favorites_toggle",
                user_id,
                {"listing_id": listing_id, "source": source, "is_favorite": is_fav},
            )
            return api_ok_response({"is_favorite": is_fav})

        return _wrap_api("favorites_toggle", _handler)

    @app.route("/api/admin/pending", methods=["GET"])
    def api_admin_pending():
        args = request.args.to_dict() or {}

        def _handler():
            admin_id = parse_int_value(args.get("admin_id"))
            if admin_id is None or not is_admin(admin_id):
                return api_error_response("Admin icazəsi yoxdur", 403)
            ensure_user_exists(admin_id)
            limit = parse_int_value(args.get("limit")) or 200
            offset = parse_int_value(args.get("offset")) or 0
            limit = max(1, min(limit, 500))
            offset = max(0, offset)

            conn = get_local_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT COUNT(1)
                    FROM users
                    WHERE COALESCE(approved,0)=0 AND COALESCE(blocked,0)=0
                    """
                )
                count_row = cur.fetchone()
                total_pending = count_row[0] if count_row else 0
                cur.execute(
                    """
                    SELECT chat_id, full_name, username, first_seen, last_seen, approved, blocked, paid_until
                    FROM users
                    WHERE COALESCE(approved,0)=0 AND COALESCE(blocked,0)=0
                    ORDER BY first_seen DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
                rows = [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

            return api_ok_response({"items": rows, "total": total_pending})

        return _wrap_api("admin_pending", _handler)

    @app.route("/api/admin/approve", methods=["POST"])
    def api_admin_approve():
        payload = request.get_json(silent=True) or {}

        def _handler():
            admin_id = parse_int_value(payload.get("admin_id"))
            user_id = parse_int_value(payload.get("user_id"))
            if admin_id is None or not is_admin(admin_id):
                return api_error_response("Admin icazəsi yoxdur", 403)
            if user_id is None:
                return api_error_response("user_id tələb olunur", 400)
            ensure_user_exists(admin_id)
            ensure_user_exists(user_id)
            conn = get_local_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE users SET approved=1, blocked=0 WHERE chat_id=?",
                    (user_id,),
                )
                conn.commit()
            finally:
                conn.close()
            log_api_call("admin_approve", admin_id, {"user_id": user_id})
            return api_ok_response({})

        return _wrap_api("admin_approve", _handler)

    @app.route("/api/admin/block", methods=["POST"])
    def api_admin_block():
        payload = request.get_json(silent=True) or {}

        def _handler():
            admin_id = parse_int_value(payload.get("admin_id"))
            user_id = parse_int_value(payload.get("user_id"))
            if admin_id is None or not is_admin(admin_id):
                return api_error_response("Admin icazəsi yoxdur", 403)
            if user_id is None:
                return api_error_response("user_id tələb olunur", 400)

            ensure_user_exists(admin_id)
            ensure_user_exists(user_id)
            conn = get_local_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE users SET blocked=1, blocked_at=? WHERE chat_id=?",
                    (datetime.utcnow().isoformat(), user_id),
                )
                conn.commit()
            finally:
                conn.close()
            log_api_call("admin_block", admin_id, {"user_id": user_id})
            return api_ok_response({})

        return _wrap_api("admin_block", _handler)

    @app.route("/api/admin/extend", methods=["POST"])
    def api_admin_extend():
        payload = request.get_json(silent=True) or {}

        def _handler():
            admin_id = parse_int_value(payload.get("admin_id"))
            user_id = parse_int_value(payload.get("user_id"))
            days = parse_int_value(payload.get("days"))
            if admin_id is None or not is_admin(admin_id):
                return api_error_response("Admin icazəsi yoxdur", 403)
            if user_id is None:
                return api_error_response("user_id tələb olunur", 400)
            if days is None or days <= 0:
                return api_error_response("days düzgün deyil", 400)

            ensure_user_exists(admin_id)
            ensure_user_exists(user_id)

            conn = get_local_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT expires_at FROM subscriptions WHERE chat_id=?",
                    (user_id,),
                )
                row = cur.fetchone()
                current_exp = parse_dt_safe(row[0]) if row and row[0] else None
                base_dt = (
                    current_exp
                    if current_exp and current_exp > datetime.utcnow()
                    else datetime.utcnow()
                )
                new_exp = base_dt + timedelta(days=days)

                cur.execute(
                    """
                    INSERT INTO subscriptions (chat_id, plan, expires_at, is_active, is_demo, last_payment_note)
                    VALUES (?, 'admin', ?, 1, 0, 'admin_extend')
                    ON CONFLICT(chat_id) DO UPDATE SET
                        plan='admin', expires_at=excluded.expires_at, is_active=1, is_demo=0,
                        last_payment_note=COALESCE(excluded.last_payment_note, subscriptions.last_payment_note)
                    """,
                    (user_id, new_exp.isoformat()),
                )
                cur.execute(
                    "UPDATE users SET paid_until=?, approved=1, blocked=0 WHERE chat_id=?",
                    (new_exp.isoformat(), user_id),
                )
                conn.commit()
            finally:
                conn.close()

            log_api_call(
                "admin_extend",
                admin_id,
                {"user_id": user_id, "days": days, "expires_at": new_exp.isoformat()},
            )
            return api_ok_response({"expires_at": new_exp.isoformat()})

        return _wrap_api("admin_extend", _handler)

    @app.route("/api/admin/db-update", methods=["POST"])
    def api_admin_db_update():
        payload = request.get_json(silent=True) or {}

        def _handler():
            admin_id = parse_int_value(payload.get("admin_id"))
            if admin_id is None:
                return api_error_response("Admin tələb olunur", 403)
            user_id, error = resolve_user_from_payload({"user_id": admin_id})
            if error:
                return error
            if not is_admin(user_id):
                return api_error_response("Yalnız admin icazəlidir", 403)
            dropbox_url = str(payload.get("dropbox_url") or "").strip()
            if not dropbox_url:
                return api_error_response("dropbox_url tələb olunur", 400)
            parts = urlsplit(dropbox_url)
            if parts.scheme.lower() != "https" or "dropbox" not in parts.netloc.lower():
                return api_error_response(
                    "Yalnız Dropbox HTTPS linki qəbul edilir", 400
                )

            stale = cleanup_stale_db_updates()
            if stale:
                logger.info("Stale DB updates cleaned via API user=%s", user_id)
            running = get_running_db_update()
            if running:
                return api_error_response("Baza yenilənməsi artıq işləyir", 409)
            if not acquire_db_update_lock(user_id):
                return api_error_response("Baza yenilənməsi artıq işləyir", 409)

            try:
                admin_update_state[user_id] = "api_updating_db"
                set_db_update_state(user_id, "running")
                threading.Thread(
                    target=run_db_update_pipeline,
                    args=(user_id, dropbox_url),
                    daemon=True,
                ).start()
            except Exception:
                release_db_update_lock(user_id)
                admin_update_state.pop(user_id, None)
                clear_db_update_state(user_id)
                logger.exception("Failed to start db update via API user=%s", user_id)
                return api_error_response("Yenilənmə başladılmadı", 500)

            log_api_call(
                "admin_db_update",
                user_id,
                {"dropbox_url": dropbox_url, "started": True},
            )
            return api_ok_response({"message": "Baza yenilənməsi başladı"})

        return _wrap_api("admin_db_update", _handler)

    return app


def main():
    if _polling_started.is_set():
        logger.info("Bot polling already running; skipping duplicate start")
        return

    _polling_started.set()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    global BOT_TOKEN, BOT_USERNAME
    BOT_TOKEN = _load_bot_token()

    _initialize_app_state()

    bot.bind(telebot.TeleBot(BOT_TOKEN))
    BOT_USERNAME = bot.get_me().username

    threading.Thread(target=saved_search_worker, daemon=True).start()
    threading.Thread(target=favorite_price_worker, daemon=True).start()
    threading.Thread(target=subscription_notifier, daemon=True).start()
    threading.Thread(target=keepalive_worker, daemon=True).start()

    create_flask_app()
    run_bot()


def run_bot():
    logger.info("🤖 Telegram bot polling started (safe mode)")
    while True:
        try:
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                skip_pending=True,
            )
        except Exception as e:
            logger.error(f"Polling crashed, restarting in 5s: {e}")
            time.sleep(5)


def keepalive_worker(interval_seconds: int = 300):
    while True:
        try:
            logger.info("⏳ Keep-alive heartbeat (DATA_DIR=%s)", BASE_DATA_DIR)
            if os.path.exists(LOCAL_DB):
                conn = get_local_conn()
                try:
                    conn.execute("SELECT 1")
                finally:
                    conn.close()
        except Exception as exc:
            logger.warning("Keep-alive ping failed: %s", exc)
        time.sleep(interval_seconds)
