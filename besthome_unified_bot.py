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
import json
import re
import zipfile
import sqlite3
import threading
from datetime import datetime, date
from urllib.parse import quote, unquote

import requests
from flask import Flask
import telebot
from telebot import types

# =============== KONFİQURASİYA ===============
BOT_TOKEN = "7938311608:AAHmzsTqnVJ7cVtStp2lmzGe2-1oj9LN1JM"
ADMIN_ID = 1311851277
CHANNEL_ID = -1001878623087  # Bot bu kanalda admin olmalıdır

MAIN_DB = "besthome.db"  # Əsas gündəlik baza (Dropbox-dan)
LOCAL_DB = "local_data.db"  # Yeni elanlar, təsdiqlər, users, favorilər, limitlər
AGENTS_DB = "agents.db"  # Vasitəçi elanları (parserdən gələn)

DROPBOX_ZIP_URL = "https://www.dropbox.com/scl/fi/7ne0n5havbzihjvgi2w44/besthome.zip?rlkey=e3p9zaxxpzqpa1xpsac72tygv&st=ajk8n1hu&dl=1"
DROPBOX_LOCAL_URL = "https://www.dropbox.com/scl/fi/byg4ioywhkmk7qs18zb73/local_data.zip?rlkey=jvq1x3klk0b04mprk08e3ibcq&st=ft5d9x78&dl=1"
DROPBOX_AGENTS_URL = "https://www.dropbox.com/scl/fi/a4q28aq343ncgf89mcb4g/agents.zip?rlkey=iu5kgmpxv19k993fkc3l054uf&st=1tasdhg8&dl=1"

bot = telebot.TeleBot(BOT_TOKEN)
user_state = {}  # Yeni elan proses state
search_state = {}  # Açar sözlə axtarış paging state


# =============== BESTHOME DROPBOX YÜKLƏNMƏSİ ===============


def ensure_local_db():
    if os.path.exists(LOCAL_DB):
        print("✅ local_data.db mövcuddur, yenidən yüklənmir.")
        return

    print("⬇️ Dropbox-dan local_data.zip yüklənir...")
    try:
        r = requests.get(DROPBOX_LOCAL_URL)
        if r.status_code != 200:
            print("⚠️ Dropbox cavab kodu:", r.status_code)
            return

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for name in z.namelist():
                if name.endswith(".db"):
                    z.extract(name, ".")
                    os.rename(name, LOCAL_DB)
                    print("✅ local_data.db çıxarıldı!")
                    return

        print("⚠️ ZIP-də local_data.db tapılmadı!")

    except Exception as e:
        print("❌ local_data.zip yükləmə xətası:", e)


def ensure_agents_db():
    if os.path.exists(AGENTS_DB):
        print("✅ agents.db mövcuddur, yenidən yüklənmir.")
        return

    print("⬇️ Dropbox-dan agents.zip yüklənir...")
    try:
        r = requests.get(DROPBOX_AGENTS_URL)
        if r.status_code != 200:
            print("⚠️ Dropbox cavab kodu:", r.status_code)
            return

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for name in z.namelist():
                if name.endswith(".db"):
                    z.extract(name, ".")
                    os.rename(name, AGENTS_DB)
                    print("✅ agents.db çıxarıldı!")
                    return

        print("⚠️ ZIP-də agents.db tapılmadı!")

    except Exception as e:
        print("❌ agents.zip yükləmə xətası:", e)


# =============== DB HELPERS ===============


def ensure_main_db():
    """Əsas besthome.db yoxdursa Dropbox ZIP-dən endir."""
    if os.path.exists(MAIN_DB):
        print("📦 Mövcud besthome.db tapıldı.")
        return
    if not DROPBOX_ZIP_URL:
        print("⚠️ besthome.db yoxdur və DROPBOX_ZIP_URL boşdur.")
        return
    print("⬇️ besthome.zip endirilir...")
    try:
        r = requests.get(DROPBOX_ZIP_URL, timeout=60)
        if r.status_code != 200:
            print("❌ Endirmə alınmadı:", r.status_code)
            return
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(".")
        print("✅ besthome.db ZIP-dən çıxarıldı.")
    except Exception as e:
        print("❌ ZIP xətası:", e)


def get_main_conn():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_agents_conn():
    path = os.path.join(os.path.dirname(__file__), "agents.db")
    conn = sqlite3.connect(path)
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

    # İstifadəçi aktivliyi və meta məlumatlar üçün əlavə sütunlar
    ensure_column_exists(cur, "users", "first_seen", "TEXT")
    ensure_column_exists(cur, "users", "is_admin", "INTEGER DEFAULT 0")
    ensure_column_exists(cur, "users", "last_version", "TEXT")
    ensure_column_exists(cur, "users", "last_seen", "TEXT")

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

    # Axtarış tarixçəsi
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            search_type TEXT,
            query TEXT,
            filters TEXT,
            created_at TEXT
        )
    """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(chat_id)"
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


# =============== ÜMUMİ UTIL FUNKSİYALAR ===============


def ensure_column_exists(cursor, table: str, column: str, col_def: str):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cursor.fetchall()]
    if column not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except Exception as e:
            print(f"⚠️ {table}.{column} əlavə edilə bilmədi:", e)


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


def safe_date(row: dict):
    for key in ("date_read", "date_added", "Elanin_tarixi", "added_at", "created_at"):
        v = row.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace(" ", "T"))
            except:
                pass
    return datetime.min


def update_last_seen(chat_id: int):
    ts = datetime.utcnow().isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (chat_id, date_joined, approved, blocked, last_seen)
        VALUES (?, ?, 0, 0, ?)
        ON CONFLICT(chat_id) DO UPDATE SET last_seen=excluded.last_seen
    """,
        (chat_id, ts, ts),
    )
    conn.commit()
    conn.close()


def save_search_history(chat_id: int, search_type: str, query: str = "", filters=None):
    payload = ""
    if filters is not None:
        try:
            payload = json.dumps(filters, ensure_ascii=False)
        except Exception:
            payload = str(filters)
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO search_history (chat_id, search_type, query, filters, created_at)
        VALUES (?, ?, ?, ?, ?)
    """,
        (chat_id, search_type, query or "", payload, datetime.utcnow().isoformat()),
    )
    # Yalnız son 30 qeydi saxla
    cur.execute(
        "DELETE FROM search_history WHERE id NOT IN ("
        "SELECT id FROM search_history WHERE chat_id=? ORDER BY created_at DESC LIMIT 30)"
        " AND chat_id=?",
        (chat_id, chat_id),
    )
    conn.commit()
    conn.close()


def get_last_searches(chat_id: int, limit: int = 5):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, search_type, query, filters, created_at
        FROM search_history
        WHERE chat_id=?
        ORDER BY created_at DESC
        LIMIT ?
    """,
        (chat_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


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
        update_last_seen(chat_id)
        return True
    if not is_user_allowed(chat_id):
        bot.send_message(
            chat_id,
            "🛑 Botdan istifadə üçün admin təsdiqi tələb olunur.\n"
            "Zəhmət olmasa icazə verilməsini gözləyin.",
        )

        return False
    update_last_seen(chat_id)
    return True


def ensure_allowed_cb(c) -> bool:
    chat_id = c.message.chat.id
    if is_admin(chat_id):
        update_last_seen(chat_id)
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
    update_last_seen(chat_id)
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
    allow_unfav: bool = False,
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

    text = (
        f"📅 {date_val}\n"
        f"🏠 {title} | {rooms}\n"
        f"💸 {op} | 💰 {price} {cur}\n"
        f"📍 {location or '-'}\n"
        f"📞 {phone} ({cname})\n"
        f"🧾 {summary}"
    )

    link = ev.get("link") or ev.get("source_link")
    if link:
        text += f"\n🔗 {link}"

    mk = types.InlineKeyboardMarkup()

    if with_fav_button and ev.get("id"):
        mk.add(
            types.InlineKeyboardButton(
                "⭐ Favoriyə əlavə et",
                callback_data=f"fav|{source}|{ev['id']}",
            )
        )

    if allow_unfav and ev.get("id"):
        mk.add(
            types.InlineKeyboardButton(
                "🗑 Favorilərdən çıxart",
                callback_data=f"favdel|{source}|{ev['id']}",
            )
        )

    wa_url = make_whatsapp_url(phone)
    if wa_url:
        mk.add(types.InlineKeyboardButton("💬 WhatsApp-da yaz", url=wa_url))

    if link:
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))

    bot.send_message(chat_id, text, reply_markup=mk)


@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_id = message.chat.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""
    first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT chat_id, approved, is_admin, last_version FROM users WHERE chat_id=?",
        (chat_id,),
    )
    row = cur.fetchone()

    # 🧩 Əgər user bazada yoxdursa, əlavə et
    if not row:
        cur.execute(
            "INSERT INTO users (chat_id, username, full_name, first_seen, approved, is_admin, last_version, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                username,
                full_name,
                first_seen,
                0,
                0,
                CURRENT_VERSION,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()

    # 🧩 Admin üçün avtomatik təsdiq
    if chat_id == ADMIN_ID:
        cur.execute(
            "UPDATE users SET approved=1, is_admin=1 WHERE chat_id=?", (chat_id,)
        )
        conn.commit()
        conn.close()
        update_last_seen(chat_id)
        main_menu(chat_id)
        bot.send_message(chat_id, "✅ Admin kimi daxil oldun.")
        return

    # 🧩 İstifadəçi təsdiqlənməyibsə
    cur.execute("SELECT approved FROM users WHERE chat_id=?", (chat_id,))
    approved = cur.fetchone()[0]
    conn.close()

    if not approved:
        bot.send_message(
            chat_id, "❌ Admin icazə verməyib. Zəhmət olmasa təsdiq gözləyin."
        )
        return

    # 🧩 Təsdiqlənmiş istifadəçi üçün menyunu aç
    update_last_seen(chat_id)
    main_menu(chat_id)
    bot.send_message(chat_id, "👋 Xoş gəlmisiniz! Menyudan seçim edin:")


# =============== ℹ️ Haqqında ===============


@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    text = (
        "🏠 *Best Home Əmlak Botu*\n"
        "• 🔎 Filtrlə, açar sözlə və nömrə ilə axtarış\n"
        "• ⭐ Favorilər, 📋 Elanlarım, 🛠 Admin panel funksiyaları\n"
        "• 👥 Yalnız admin təsdiqli istifadəçilər üçün təhlükəsiz giriş\n"
        "• 💬 WhatsApp-a bir toxunuşla keçid\n"
        "📞 Admin: @esedovesed"
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
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT listing_id, source FROM favorites
        WHERE chat_id=?
        ORDER BY added_at DESC
    """,
        (chat_id,),
    )
    favs = cur.fetchall()
    conn.close()

    if not favs:
        bot.send_message(chat_id, "⭐ Favorilər siyahınız boşdur.")
        return

    bot.send_message(chat_id, "⭐ Favori elanlarınız:")
    for f in favs:
        lid = f["listing_id"]
        src = f["source"]
        ev = None
        if src == "main" and os.path.exists(MAIN_DB):
            conn = get_main_conn()
            c2 = conn.cursor()
            c2.execute("SELECT * FROM listings WHERE id=?", (lid,))
            r = c2.fetchone()
            conn.close()
            if r:
                ev = dict(r)
        elif src == "local":
            conn = get_local_conn()
            c2 = conn.cursor()
            c2.execute("SELECT * FROM listings_approved WHERE id=?", (lid,))
            r = c2.fetchone()
            conn.close()
            if r:
                ev = dict(r)
        if ev:
            send_listing_card(
                chat_id,
                ev,
                source=src,
                with_fav_button=False,
                allow_unfav=True,
            )


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
    bot.answer_callback_query(c.id, "⭐ Favoriyə əlavə olundu.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("favdel|"))
def cb_remove_favorite(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    _, src, sid = c.data.split("|")
    lid = int(sid)
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM favorites WHERE chat_id=? AND listing_id=? AND source=?",
        (chat_id, lid, src),
    )
    conn.commit()
    conn.close()
    try:
        bot.delete_message(chat_id, c.message.message_id)
    except Exception:
        pass
    bot.answer_callback_query(c.id, "❌ Favoridən silindi.")


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
        types.InlineKeyboardButton(
            "🔍 Açar sözlə və nömrə ilə axtar", callback_data="ss|text"
        )
    )
    mk.add(types.InlineKeyboardButton("🕘 Son axtarışlar", callback_data="ss|history"))
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
        msg = bot.send_message(
            chat_id,
            "🔍 Açar söz və ya bir neçə söz yazın (məs: *yasamal 3 otaqlı 600 azn*):",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, keyword_search_handler)

    elif mode == "phone":
        if not check_limit(chat_id, "phone", 50):
            bot.answer_callback_query(
                c.id, "Günlük nömrə ilə axtarış limitiniz bitib.", show_alert=True
            )
            return
        msg = bot.send_message(chat_id, "☎️ Axtarmaq istədiyiniz nömrəni yazın:")
        bot.register_next_step_handler(msg, phone_search_handler)

    elif mode == "text":
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "🔍 Açar sözlə axtar", callback_data="ss|keyword"
            )
        )
        mk.add(
            types.InlineKeyboardButton("☎️ Nömrə ilə axtar", callback_data="ss|phone")
        )
        bot.edit_message_text(
            "Axtarış növünü seç:",
            chat_id=chat_id,
            message_id=c.message.message_id,
            reply_markup=mk,
        )

    elif mode == "history":
        rows = get_last_searches(chat_id, 5)
        if not rows:
            bot.answer_callback_query(c.id, "Hələ axtarış tarixçəsi yoxdur.", show_alert=True)
            return
        mk = types.InlineKeyboardMarkup()
        for r in rows:
            try:
                filters = json.loads(r[3] or "{}")
            except Exception:
                filters = {}
            title = r[2] or filters.get("rayon_group", "") or r[1]
            ts = r[4][:16] if r[4] else ""
            mk.add(
                types.InlineKeyboardButton(
                    f"{title} ({ts})", callback_data=f"hist|{r[0]}"
                )
            )
        bot.edit_message_text(
            "🕘 Son 5 axtarış:",
            chat_id=c.message.chat.id,
            message_id=c.message.message_id,
            reply_markup=mk,
        )

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("hist|"))
def cb_search_history(c):
    if not ensure_allowed_cb(c):
        return
    hid = int(c.data.split("|")[1])
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT search_type, query, filters FROM search_history WHERE id=? AND chat_id=?",
        (hid, c.message.chat.id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(c.id, "Tarixçə tapılmadı.", show_alert=True)
        return
    stype, query, filters_raw = row
    try:
        filters = json.loads(filters_raw or "{}")
    except Exception:
        filters = {}
    if stype == "structured":
        run_structured_search(
            chat_id=c.message.chat.id,
            op_code=filters.get("op_code", "all"),
            prop_code=filters.get("prop_code", "all"),
            rayon_group=filters.get("rayon_group", "all"),
            metro_name=filters.get("metro"),
            street_name=filters.get("street"),
            price_code=filters.get("price_code", "s0"),
            room_code=filters.get("room_code", "all"),
            floor_code=filters.get("floor_code", "all"),
            offset=0,
            edit_msg=None,
        )
    elif stype == "keyword":
        perform_keyword_search(c.message.chat.id, query)
    elif stype == "phone":
        perform_phone_search(c.message.chat.id, query)
    else:
        bot.answer_callback_query(c.id, "Axtarış növü dəstəklənmir.", show_alert=True)


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
    "h": "həyət evi",
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

BAKU_DISTRICTS = [
    ("bn", "Binəqədi rayonu", ["binəqədi", "bineqedi", "binaqedi"]),
    ("qd", "Qaradağ rayonu", ["qaradağ", "qaradag"]),
    ("xz", "Xəzər rayonu", ["xəzər", "xezər", "xezar", "xazar"]),
    ("sb", "Səbail rayonu", ["səbail", "sabail"]),
    ("sn", "Sabunçu rayonu", ["sabunçu", "sabuncu", "sabunchu"]),
    ("sr", "Suraxanı rayonu", ["suraxanı", "suraxani", "surakhani"]),
    ("nr", "Nərimanov rayonu", ["nərimanov", "nerimanov", "narimanov"]),
    ("ns", "Nəsimi rayonu", ["nəsimi", "nesimi", "nasimi"]),
    ("nz", "Nizami rayonu", ["nizami", "nizami rayonu"]),
    ("pr", "Pirallahı rayonu", ["pirallahı", "pirallahi"]),
    ("xt", "Xətai rayonu", ["xətai", "xetai", "khatai"]),
    ("ys", "Yasamal rayonu", ["yasamal", "yasamal rayonu"]),
]

for code, name, aliases in BAKU_DISTRICTS:
    RAYON_GROUPS[f"bak_{code}"] = aliases + [name.lower()]


def detect_baku_rayon(text: str):
    low = (text or "").lower()
    for code, name, aliases in BAKU_DISTRICTS:
        for kw in aliases + [name.lower()]:
            if kw in low:
                return kw
    return None


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


def decode_room_code(code: str):
    mapping = {
        "r1": (1, 1, "1 otaq"),
        "r2": (2, 2, "2 otaq"),
        "r3": (3, 3, "3 otaq"),
        "r4": (4, 4, "4 otaq"),
        "r5": (5, None, "5+ otaq"),
    }
    return mapping.get(code, (None, None, "Hamısı"))


def decode_floor_code(code: str):
    mapping = {
        "f1": (1, 3, "1-3"),
        "f2": (4, 9, "4-9"),
        "f3": (10, None, "10+"),
    }
    return mapping.get(code, (None, None, "Hamısı"))


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


def build_rayon_text_filter(rayon_group: str, mode: str):
    sql = ""
    params = []
    kws = RAYON_GROUPS.get(rayon_group)
    if not kws:
        return sql, params
    conds = []
    for kw in kws:
        like = f"%{kw}%"
        if mode == "main":
            conds.append("LOWER(COALESCE(address,'')) LIKE ?")
            conds.append("LOWER(COALESCE(summary,'')) LIKE ?")
        else:
            conds.append("LOWER(COALESCE(rayon,'')) LIKE ?")
            conds.append("LOWER(COALESCE(summary,'')) LIKE ?")
        params.extend([like, like])
    sql = " AND (" + " OR ".join(conds) + ")"
    return sql, params


def fetch_metros_by_rayon(rayon_group: str, limit: int = 20):
    metros = {}
    filt_main, params_main = build_rayon_text_filter(rayon_group, "main")
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT metro, COUNT(*) as cnt FROM listings "
            "WHERE COALESCE(metro,'')<>''" + filt_main +
            " GROUP BY metro ORDER BY cnt DESC LIMIT ?",
            (*params_main, limit),
        )
        for name, cnt in cur.fetchall():
            if name:
                metros[name] = metros.get(name, 0) + (cnt or 0)
        conn.close()

    filt_local, params_local = build_rayon_text_filter(rayon_group, "local")
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT metro, COUNT(*) as cnt FROM listings_approved "
        "WHERE COALESCE(metro,'')<>''" + filt_local +
        " GROUP BY metro ORDER BY cnt DESC LIMIT ?",
        (*params_local, limit),
    )
    for name, cnt in cur.fetchall():
        if name:
            metros[name] = metros.get(name, 0) + (cnt or 0)
    conn.close()

    if not metros and rayon_group and rayon_group != "all":
        # fallback: ən populyar metroları götür
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT metro, COUNT(*) FROM listings_approved WHERE COALESCE(metro,'')<>'' "
            "GROUP BY metro ORDER BY COUNT(*) DESC LIMIT ?",
            (limit,),
        )
        for name, cnt in cur.fetchall():
            metros[name] = metros.get(name, 0) + (cnt or 0)
        conn.close()

    return [m for m, _ in sorted(metros.items(), key=lambda x: x[1], reverse=True)]


def fetch_streets_by_metro(metro_name: str, limit: int = 20):
    if not metro_name or metro_name == "all":
        return []
    streets = []
    m_like = f"%{metro_name.lower()}%"

    def collect_from_rows(rows, field):
        for (addr,) in rows:
            if not addr:
                continue
            part = str(addr).split(",")[0].strip()
            if part and part.lower() not in [s.lower() for s in streets]:
                streets.append(part)

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT address FROM listings WHERE LOWER(COALESCE(metro,'')) LIKE ? "
            "OR LOWER(COALESCE(summary,'')) LIKE ? LIMIT ?",
            (m_like, m_like, limit),
        )
        collect_from_rows(cur.fetchall(), "address")
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT summary FROM listings_approved WHERE LOWER(COALESCE(metro,'')) LIKE ? "
        "OR LOWER(COALESCE(summary,'')) LIKE ? LIMIT ?",
        (m_like, m_like, limit),
    )
    collect_from_rows(cur.fetchall(), "summary")
    conn.close()

    return streets[:limit]


def send_structured_start(chat_id, message=None):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("💸 Satılır", callback_data="s_op|sat"),
        types.InlineKeyboardButton("🏢 Kirayə verilir", callback_data="s_op|kir"),
    )
    mk.add(types.InlineKeyboardButton("🌐 Hamısı", callback_data="s_op|all"))
    bot.send_message(chat_id, "🔍 Əməliyyat növünü seç:", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_op|"))
def cb_s_op(c):
    if not ensure_allowed_cb(c):
        return
    _, op = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("Mənzil", callback_data=f"s_tp|{op}|m"),
        types.InlineKeyboardButton("Fərdi yaşayış evi", callback_data=f"s_tp|{op}|f"),
    )
    mk.add(
        types.InlineKeyboardButton(
            "Qeyri-yaşayış sahəsi", callback_data=f"s_tp|{op}|q"
        ),
        types.InlineKeyboardButton("Bağ evi", callback_data=f"s_tp|{op}|b"),
    )
    mk.add(
        types.InlineKeyboardButton("Həyət evi", callback_data=f"s_tp|{op}|h"),
        types.InlineKeyboardButton("Torpaq", callback_data=f"s_tp|{op}|t"),
    )
    mk.add(
        types.InlineKeyboardButton("Hamısı", callback_data=f"s_tp|{op}|all"),
    )
    bot.edit_message_text(
        "🏠 Əmlak tipini seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_tp|"))
def cb_s_tp(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "Bütün ərazilər", callback_data=f"s_rg|{op}|{tp}|all"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "Bakı rayonları", callback_data=f"s_rg|{op}|{tp}|bak"
        ),
        types.InlineKeyboardButton("Abşeron", callback_data=f"s_rg|{op}|{tp}|abs"),
    )
    mk.add(types.InlineKeyboardButton("Sumqayıt", callback_data=f"s_rg|{op}|{tp}|sum"))
    bot.edit_message_text(
        "📍 Rayon qrupunu seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_rg|"))
def cb_s_rg(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg = c.data.split("|")
    if rg == "bak":
        mk = types.InlineKeyboardMarkup()
        row = []
        for code, name, _ in BAKU_DISTRICTS:
            row.append(
                types.InlineKeyboardButton(
                    name, callback_data=f"s_bak|{op}|{tp}|{code}"
                )
            )
            if len(row) == 2:
                mk.add(*row)
                row = []
        if row:
            mk.add(*row)
        bot.edit_message_text(
            "📍 Bakı rayonunu seç:",
            chat_id=c.message.chat.id,
            message_id=c.message.message_id,
            reply_markup=mk,
        )
        return

    send_metro_selection(c.message.chat.id, c.message.message_id, op, tp, rg)


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_bak|"))
def cb_s_baku_district(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, code = c.data.split("|")
    rg = f"bak_{code}"
    send_metro_selection(c.message.chat.id, c.message.message_id, op, tp, rg)


def send_metro_selection(chat_id, message_id, op, tp, rg):
    mk = types.InlineKeyboardMarkup()
    metros = fetch_metros_by_rayon(rg)
    if metros:
        for name in metros[:12]:
            mk.add(
                types.InlineKeyboardButton(
                    name, callback_data=f"smt|{op}|{tp}|{rg}|{quote(name)}"
                )
            )
    mk.add(types.InlineKeyboardButton("Hamısı", callback_data=f"smt|{op}|{tp}|{rg}|all"))
    bot.edit_message_text(
        "🚇 Rayona uyğun metro seçin:",
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("smt|"))
def cb_s_metro(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg, metro = c.data.split("|", 4)
    metro = unquote(metro)
    send_street_selection(c.message.chat.id, c.message.message_id, op, tp, rg, metro)


def send_street_selection(chat_id, message_id, op, tp, rg, metro):
    mk = types.InlineKeyboardMarkup()
    streets = fetch_streets_by_metro(metro)
    if streets:
        for st in streets[:12]:
            mk.add(
                types.InlineKeyboardButton(
                    st, callback_data=f"sst|{op}|{tp}|{rg}|{quote(metro)}|{quote(st)}"
                )
            )
    mk.add(
        types.InlineKeyboardButton(
            "Hamısı", callback_data=f"sst|{op}|{tp}|{rg}|{quote(metro)}|all"
        )
    )
    bot.edit_message_text(
        "🛣 Küçə seçin (metroya görə):",
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("sst|"))
def cb_s_street(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg, metro, street = c.data.split("|", 5)
    metro = unquote(metro)
    street = unquote(street)
    send_price_selection(
        c.message.chat.id, c.message.message_id, op, tp, rg, metro, street
    )


def send_price_selection(chat_id, message_id, op, tp, rg, metro, street):
    mk = types.InlineKeyboardMarkup()
    if op == "kir":
        mk.add(
            types.InlineKeyboardButton("0-500", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|k1"),
            types.InlineKeyboardButton(
                "520-1000", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|k2"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "1050-1500", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|k3"
            ),
            types.InlineKeyboardButton(
                "1550-2000", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|k4"
            ),
        )
        mk.add(
            types.InlineKeyboardButton("2000+", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|k5"),
        )
    else:
        mk.add(
            types.InlineKeyboardButton(
                "Limitsiz", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|s0"
            ),
            types.InlineKeyboardButton(
                "0-50,000", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|s1"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "50,000-100,000", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|s2"
            ),
            types.InlineKeyboardButton(
                "100,000-200,000", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|s3"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "200,000+", callback_data=f"spr|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|s4"
            ),
        )
    bot.edit_message_text(
        "💰 Qiymət aralığını seç:",
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("spr|"))
def cb_s_price(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg, metro, street, pc = c.data.split("|", 6)
    send_room_selection(
        c.message.chat.id,
        c.message.message_id,
        op,
        tp,
        rg,
        unquote(metro),
        unquote(street),
        pc,
    )


def send_room_selection(chat_id, message_id, op, tp, rg, metro, street, pc):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "1 otaq", callback_data=f"srm|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|r1"
        ),
        types.InlineKeyboardButton(
            "2 otaq", callback_data=f"srm|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|r2"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "3 otaq", callback_data=f"srm|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|r3"
        ),
        types.InlineKeyboardButton(
            "4 otaq", callback_data=f"srm|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|r4"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "5+ otaq", callback_data=f"srm|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|r5"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "Hamısı", callback_data=f"srm|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|all"
        )
    )
    bot.edit_message_text(
        "🚪 Otaq sayını seç:",
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("srm|"))
def cb_s_room(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg, metro, street, pc, room = c.data.split("|", 7)
    send_floor_selection(
        c.message.chat.id,
        c.message.message_id,
        op,
        tp,
        rg,
        unquote(metro),
        unquote(street),
        pc,
        room,
    )


def send_floor_selection(chat_id, message_id, op, tp, rg, metro, street, pc, room):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "1-3", callback_data=f"sfl|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|{room}|f1"
        ),
        types.InlineKeyboardButton(
            "4-9", callback_data=f"sfl|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|{room}|f2"
        ),
    )
    mk.add(
        types.InlineKeyboardButton(
            "10+", callback_data=f"sfl|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|{room}|f3"
        ),
        types.InlineKeyboardButton(
            "Hamısı", callback_data=f"sfl|{op}|{tp}|{rg}|{quote(metro)}|{quote(street)}|{pc}|{room}|all"
        ),
    )
    bot.edit_message_text(
        "🧱 Mərtəbə aralığını seç:",
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("sfl|"))
def cb_s_floor(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg, metro, street, pc, room, floor = c.data.split("|", 8)
    run_structured_search(
        chat_id=c.message.chat.id,
        op_code=op,
        prop_code=tp,
        rayon_group=rg,
        metro_name=unquote(metro),
        street_name=unquote(street),
        price_code=pc,
        room_code=room,
        floor_code=floor,
        offset=0,
        edit_msg=(c.message.chat.id, c.message.message_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("more|"))
def cb_more(c):
    if not ensure_allowed_cb(c):
        return
    _, op, tp, rg, metro, street, pc, room, floor, off = c.data.split("|", 9)
    run_structured_search(
        chat_id=c.message.chat.id,
        op_code=op,
        prop_code=tp,
        rayon_group=rg,
        metro_name=unquote(metro),
        street_name=unquote(street),
        price_code=pc,
        room_code=room,
        floor_code=floor,
        offset=int(off),
        edit_msg=None,
    )


def extract_first_int(text: str):
    if not text:
        return None
    m = re.search(r"(\d+)", str(text))
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def matches_room_filter(ev: dict, room_code: str):
    min_r, max_r, _ = decode_room_code(room_code)
    if min_r is None:
        return True
    num = extract_first_int(ev.get("rooms") or ev.get("summary") or "")
    if num is None:
        return True
    if num < min_r:
        return False
    if max_r is not None and num > max_r:
        return False
    return True


def matches_floor_filter(ev: dict, floor_code: str):
    min_f, max_f, _ = decode_floor_code(floor_code)
    if min_f is None:
        return True
    text = (
        ev.get("floor")
        or ev.get("Mertebe")
        or ev.get("summary")
        or ev.get("address")
        or ""
    )
    num = extract_first_int(text)
    if num is None:
        return True
    if num < min_f:
        return False
    if max_f is not None and num > max_f:
        return False
    return True


def run_structured_search(
    chat_id,
    op_code,
    prop_code,
    rayon_group,
    price_code,
    offset,
    edit_msg=None,
    metro_name=None,
    street_name=None,
    room_code="all",
    floor_code="all",
):
    page_size = 20
    min_p, max_p = decode_price_range(price_code)
    _, _, room_label = decode_room_code(room_code)
    _, _, floor_label = decode_floor_code(floor_code)
    results = []

    metro_filter = metro_name and metro_name != "all"
    street_filter = street_name and street_name != "all"

    # MAIN DB
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        base = "SELECT * FROM listings"
        flt, params = build_filters_sql(
            op_code,
            prop_code,
            rayon_group,
            min_price=min_p,
            max_price=max_p,
            mode="main",
        )
        sql = base + flt + " ORDER BY date_read DESC, id DESC"
        cur.execute(sql, params)
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    # LOCAL APPROVED
    conn = get_local_conn()
    cur = conn.cursor()
    base = "SELECT * FROM listings_approved"
    flt, params = build_filters_sql(
        op_code,
        prop_code,
        rayon_group,
        min_price=min_p,
        max_price=max_p,
        mode="local",
    )
    sql = base + flt + " ORDER BY date_added DESC, id DESC"
    cur.execute(sql, params)
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    def match_any(text):
        if not text:
            return False
        low = text.lower()
        return (
            (metro_filter and metro_name.lower() in low)
            or (street_filter and street_name.lower() in low)
        )

    filtered = []
    for ev in results:
        if metro_filter:
            if not match_any(
                (ev.get("metro") or "")
                + " "
                + (ev.get("address") or "")
                + " "
                + (ev.get("summary") or "")
            ):
                continue
        if street_filter:
            if not match_any(
                (ev.get("address") or "") + " " + (ev.get("summary") or "")
            ):
                continue
        if not matches_room_filter(ev, room_code):
            continue
        if not matches_floor_filter(ev, floor_code):
            continue
        filtered.append(ev)

    results = filtered

    results.sort(key=safe_date, reverse=True)

    if offset == 0:
        save_search_history(
            chat_id,
            "structured",
            query="",
            filters={
                "op_code": op_code,
                "prop_code": prop_code,
                "rayon_group": rayon_group,
                "metro": metro_name,
                "street": street_name,
                "price_code": price_code,
                "room_code": room_code,
                "floor_code": floor_code,
            },
        )

    if not results and offset == 0:
        if edit_msg:
            bot.edit_message_text(
                "😕 Uyğun elan tapılmadı.",
                chat_id=edit_msg[0],
                message_id=edit_msg[1],
            )
        else:
            bot.send_message(chat_id, "😕 Uyğun elan tapılmadı.")
        return

    if offset >= len(results):
        bot.send_message(chat_id, "✅ Bütün uyğun elanlar göstərildi.")
        return

    slice_results = results[offset : offset + page_size]

    title_op = {
        "sat": "Satılır",
        "kir": "Kirayə verilir",
        "all": "Bütün əməliyyatlar",
    }.get(op_code, "Elanlar")

    title_tp = {
        "m": "Mənzil",
        "f": "Fərdi yaşayış evi",
        "q": "Qeyri-yaşayış sahəsi",
        "b": "Bağ evi",
        "h": "Həyət evi",
        "t": "Torpaq",
        "all": "Bütün tiplər",
    }.get(prop_code, "Bütün tiplər")

    title_rn_map = {
        "all": "Bütün ərazilər",
        "bak": "Bakı rayonları",
        "abs": "Abşeron",
        "sum": "Sumqayıt",
    }
    for code, name, _ in BAKU_DISTRICTS:
        title_rn_map[f"bak_{code}"] = name

    title_rn = title_rn_map.get(rayon_group, "Bütün ərazilər")
    metro_title = metro_name if metro_filter else "Bütün metrolar"
    street_title = street_name if street_filter else "Bütün küçələr"
    room_title = room_label
    floor_title = floor_label

    if offset == 0:
        header = (
            f"🔎 {title_op} | {title_tp} | {title_rn}\n"
            f"🚇 {metro_title} | 🛣 {street_title}\n"
            f"🚪 {room_title} | 🧱 {floor_title}"
        )
        if edit_msg:
            bot.edit_message_text(
                header,
                chat_id=edit_msg[0],
                message_id=edit_msg[1],
            )
        else:
            bot.send_message(chat_id, header)

    for ev in slice_results:
        send_listing_card(
            chat_id,
            ev,
            source=ev.get("__source", "main"),
            with_fav_button=True,
        )

    if offset + page_size < len(results):
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➡️ Daha çox göstər",
                callback_data=
                f"more|{op_code}|{prop_code}|{rayon_group}|{quote(metro_name or 'all')}|{quote(street_name or 'all')}|{price_code}|{room_code}|{floor_code}|{offset + page_size}",
            )
        )
        bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)


# ===== AÇAR SÖZLƏ AXTARIŞ (paging ilə) =====


def perform_keyword_search(chat_id, raw_text: str, apply_limit: bool = True):
    if apply_limit and not check_limit(chat_id, "keyword", 30):
        bot.send_message(chat_id, "Günlük açar sözlə axtarış limitiniz bitib.")
        return

    text = (raw_text or "").strip().lower()
    if not text:
        bot.send_message(chat_id, "Boş sorğu göndərdiniz.")
        return

    words = [w for w in text.split() if w]
    rayon_hint = detect_baku_rayon(text)

    results = []

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

    FIELDS_MAIN = ["prop_type", "operation", "metro", "rooms", "address", "summary"]
    FIELDS_LOCAL = ["prop_type", "operation", "metro", "rooms", "rayon", "summary"]

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()

        sql_where, params = build_multi_like_sql(FIELDS_MAIN)
        if rayon_hint:
            extra = "(" + " OR ".join([
                "LOWER(address) LIKE ?",
                "LOWER(summary) LIKE ?",
                "LOWER(COALESCE(rayon,'')) LIKE ?",
                "LOWER(COALESCE(metro,'')) LIKE ?",
            ]) + ")"
            sql_where = f"{sql_where} AND {extra}"
            params.extend([f"%{rayon_hint}%"] * 4)
        sql = f"SELECT * FROM listings WHERE {sql_where} ORDER BY date_read DESC LIMIT 5000"

        cur.execute(sql, params)
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()

    sql_where, params = build_multi_like_sql(FIELDS_LOCAL)
    if rayon_hint:
        extra = "(" + " OR ".join([
            "LOWER(rayon) LIKE ?",
            "LOWER(summary) LIKE ?",
            "LOWER(COALESCE(metro,'')) LIKE ?",
        ]) + ")"
        sql_where = f"{sql_where} AND {extra}"
        params.extend([f"%{rayon_hint}%"] * 3)
    sql = f"SELECT * FROM listings_approved WHERE {sql_where} ORDER BY date_added DESC LIMIT 5000"

    cur.execute(sql, params)
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    if not results:
        bot.send_message(chat_id, "😕 Uyğun elan tapılmadı.")
        return

    results.sort(key=safe_date, reverse=True)

    sale_results = []
    rent_results = []
    other_results = []
    for ev in results:
        op = (ev.get("operation") or "").lower()
        if "sat" in op:
            sale_results.append(ev)
        elif "kir" in op or "icar" in op:
            rent_results.append(ev)
        else:
            other_results.append(ev)

    flat_results = sale_results + rent_results + other_results
    boundaries = []
    idx = 0
    if sale_results:
        boundaries.append((0, f"📌 Satılır elanları ({len(sale_results)})"))
        idx += len(sale_results)
    if rent_results:
        boundaries.append((idx, f"📌 Kirayə elanları ({len(rent_results)})"))
        idx += len(rent_results)
    if other_results:
        boundaries.append((idx, f"📌 Digər elanlar ({len(other_results)})"))

    if apply_limit:
        inc_limit(chat_id, "keyword", 1)

    save_search_history(chat_id, "keyword", text, {"rayon_hint": rayon_hint})

    search_state[chat_id] = {
        "mode": "kw",
        "results": flat_results,
        "boundaries": boundaries,
    }

    _send_keyword_page(chat_id, 0)


def keyword_search_handler(message):
    if not ensure_allowed(message):
        return
    perform_keyword_search(message.chat.id, message.text, apply_limit=True)


def _send_keyword_page(chat_id, offset):
    state = search_state.get(chat_id)
    if not state or state.get("mode") != "kw":
        bot.send_message(chat_id, "Sessiya tapılmadı. Yenidən axtarın.")
        return
    results = state["results"]
    boundaries = state.get("boundaries", [])
    page_size = 20

    if offset == 0:
        bot.send_message(
            chat_id, f"🔍 Tapıldı: {len(results)} elan. İlk {page_size} göstərilir:"
        )

    slice_results = results[offset : offset + page_size]
    for idx, ev in enumerate(slice_results, start=offset):
        for start_idx, title in boundaries:
            if idx == start_idx:
                bot.send_message(chat_id, title)
        send_listing_card(
            chat_id,
            ev,
            source=ev.get("__source", "main"),
            with_fav_button=True,
        )

    if offset + page_size < len(results):
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➡️ Daha çox göstər",
                callback_data=f"kwmore|{offset + page_size}",
            )
        )
        bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)
    else:
        bot.send_message(chat_id, "✅ Bütün uyğun elanlar göstərildi.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("kwmore|"))
def cb_kw_more(c):
    if not ensure_allowed_cb(c):
        return
    chat_id = c.message.chat.id
    _, off = c.data.split("|")
    offset = int(off)
    try:
        bot.answer_callback_query(c.id)
    except:
        pass
    _send_keyword_page(chat_id, offset)


# ===== NÖMRƏ İLƏ AXTARIŞ =====


def perform_phone_search(chat_id, raw_text: str, apply_limit: bool = True):
    if apply_limit and not check_limit(chat_id, "phone", 50):
        bot.send_message(chat_id, "Günlük nömrə ilə axtarış limitiniz bitib.")
        return

    raw = "".join(ch for ch in (raw_text or "") if ch.isdigit())
    if len(raw) < 7:
        bot.send_message(chat_id, "⚠️ Zəhmət olmasa düzgün nömrə yazın (min. 7 rəqəm).")
        return

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
            LIMIT 200
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
        LIMIT 200
    """,
        (like,),
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    if not results:
        bot.send_message(chat_id, "❌ Bu nömrə ilə heç bir elan tapılmadı.")
        return

    if apply_limit:
        inc_limit(chat_id, "phone", 1)

    save_search_history(chat_id, "phone", raw)
    results.sort(key=safe_date, reverse=True)

    bot.send_message(chat_id, f"☎️ Bu nömrə ilə {len(results)} elan tapıldı:")
    for ev in results[:50]:
        send_listing_card(
            chat_id,
            ev,
            source=ev.get("__source", "main"),
            with_fav_button=True,
        )


def phone_search_handler(message):
    if not ensure_allowed(message):
        return
    perform_phone_search(message.chat.id, message.text, apply_limit=True)


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
        types.InlineKeyboardButton("📊 Statistik hesabat", callback_data="adm|stats")
    )
    mk.add(
        types.InlineKeyboardButton(
            "📤 Vasitəçilərə bildiriş", callback_data="adm|agents_broadcast"
        )
    )
    mk.add(
        types.InlineKeyboardButton("📢 Hamıya bildiriş", callback_data="adm|broadcast_all")
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

    elif cmd == "broadcast_all":
        msg = bot.send_message(c.message.chat.id, "📝 Hamıya göndəriləcək mətni yaz:")
        bot.register_next_step_handler(msg, admin_broadcast_all)

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
            "SELECT chat_id, full_name, username, approved, blocked, last_seen "
            "FROM users WHERE approved=1 AND blocked=0 "
            "ORDER BY date_joined DESC"
        )
        title = "✅ Aktiv istifadəçilər"
    elif status == "blocked":
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked, last_seen "
            "FROM users WHERE blocked=1 "
            "ORDER BY date_joined DESC"
        )
        title = "🚫 Bloklanmış istifadəçilər"
    elif status == "pending":
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked, last_seen "
            "FROM users WHERE approved=0 "
            "ORDER BY date_joined DESC"
        )
        title = "⏳ Təsdiqlənməmiş istifadəçilər"
    else:
        cur.execute(
            "SELECT chat_id, full_name, username, approved, blocked, last_seen "
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
        chat_id_u, full_name, username, approved, blocked, last_seen = r
        uname = f"@{username}" if username else "—"
        status_text = (
            "✅ Aktiv"
            if approved and not blocked
            else "🚫 Bloklanıb" if blocked else "⏳ Təsdiqlənməyib"
        )

        online_status = ""
        try:
            if last_seen:
                ts = datetime.fromisoformat(str(last_seen))
                delta = datetime.utcnow() - ts
                minutes = int(delta.total_seconds() // 60)
                if minutes <= 5:
                    online_status = " (Online)"
                else:
                    online_status = f" (Offline, {minutes} dəq əvvəl)"
        except Exception:
            pass

        txt = (
            f"👤 {full_name or 'Ad yoxdur'}\n"
            f"💬 {uname}\n"
            f"🆔 <code>{chat_id_u}</code>\n"
            f"📊 Status: {status_text}{online_status}\n"
            f"⏱ Son aktivlik: {last_seen or '-'}"
        )

        mk = types.InlineKeyboardMarkup()
        if blocked:
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
            "🎉 Admin tərəfindən təsdiqləndiniz. Artıq botdan istifadə edə bilərsiniz.",
        )
    except:
        pass


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


@bot.message_handler(func=lambda m: m.text == "🏠 Əmlak Sahibləri")
def owners_button(message):
    if not ensure_allowed(message):
        return
    # Əmlak sahibləri üçün sadəcə mövcud axtarış sistemini açırıq
    search_system_menu(message)


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

    sale_main = rent_main = total_main = 0
    if os.path.exists(MAIN_DB):
        conn_m = get_main_conn()
        cur_m = conn_m.cursor()
        cur_m.execute("SELECT COUNT(*) FROM listings")
        total_main = cur_m.fetchone()[0] or 0
        cur_m.execute("SELECT COUNT(*) FROM listings WHERE LOWER(operation) LIKE ?", ("%sat%",))
        sale_main = cur_m.fetchone()[0] or 0
        cur_m.execute(
            "SELECT COUNT(*) FROM listings WHERE LOWER(operation) LIKE ?",
            ("%kir%",),
        )
        rent_main = cur_m.fetchone()[0] or 0
        conn_m.close()

    # Local DB
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

    cur.execute(
        "SELECT COUNT(*) FROM listings_approved WHERE LOWER(operation) LIKE ?",
        ("%sat%",),
    )
    sale_local = cur.fetchone()[0] or 0

    cur.execute(
        "SELECT COUNT(*) FROM listings_approved WHERE LOWER(operation) LIKE ?",
        ("%kir%",),
    )
    rent_local = cur.fetchone()[0] or 0

    conn.close()

    total_sale = sale_main + sale_local
    total_rent = rent_main + rent_local
    total_all_listings = total_main + total_local

    # Agents DB
    try:
        conn_a = get_agents_conn()
        cur_a = conn_a.cursor()
        cur_a.execute("SELECT COUNT(*) FROM arenda_data")
        total_agents = cur_a.fetchone()[0] or 0
        conn_a.close()
    except:
        total_agents = 0

    # Ən çox axtarılan Bakı rayonları
    top_rayon_counts = {}
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT filters FROM search_history WHERE search_type='structured'")
    for (raw,) in cur.fetchall():
        try:
            data = json.loads(raw or "{}")
            rg = data.get("rayon_group")
            if rg and rg.startswith("bak_"):
                top_rayon_counts[rg] = top_rayon_counts.get(rg, 0) + 1
        except Exception:
            continue
    conn.close()

    top_lines = []
    if top_rayon_counts:
        for code, name, _ in BAKU_DISTRICTS:
            key = f"bak_{code}"
            if key in top_rayon_counts:
                top_lines.append((name, top_rayon_counts[key]))
        top_lines = sorted(top_lines, key=lambda x: x[1], reverse=True)[:5]

    text = (
        "📊 *Admin Statistikası*\n"
        f"👥 Ümumi istifadəçi: {total_users}\n"
        f"✅ Aktiv: {active_users}\n"
        f"🏠 Satılır elanları: {total_sale}\n"
        f"🏢 Kirayə elanları: {total_rent}\n"
        f"📦 Cəmi elan (əsas + lokal): {total_all_listings}\n"
        f"📢 Yeni elanlar (cəmi): {total_new}\n"
        f"⏳ Gözləyən elanlar: {pending_new}\n"
        f"📂 Təsdiqlənmiş lokal elanlar: {total_local}\n"
        f"🏢 Vasitəçi elanları: {total_agents}\n"
    )

    if top_lines:
        text += "\n🔥 Ən çox axtarılan Bakı rayonları:\n"
        for name, cnt in top_lines:
            text += f"• {name}: {cnt} dəfə\n"

    bot.send_message(chat_id, text, parse_mode="Markdown")


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


def admin_broadcast_all(message):
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    if not text:
        bot.send_message(message.chat.id, "⚠️ Boş mətni göndərə bilmərəm.")
        return

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM users WHERE blocked=0")
    users = cur.fetchall()
    conn.close()

    if not users:
        bot.send_message(message.chat.id, "❌ Aktiv istifadəçi tapılmadı.")
        return

    sent = 0
    for (uid,) in users:
        try:
            bot.send_message(uid, f"📢 Admin bildirişi:\n{text}")
            sent += 1
            time.sleep(0.05)
        except Exception:
            continue

    bot.send_message(
        message.chat.id,
        f"✅ Bildiriş {sent} istifadəçiyə göndərildi.",
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
    mk.add("🏠 Əmlak Sahibləri")
    mk.add("📝 Yeni elan əlavə et")
    mk.add("🔎 Axtarış sistemi")
    mk.add("⭐ Favorilərim", "📋 Elanlarım")
    mk.add("ℹ️ Haqqında")

    # 🌐 Buraya MiniApp düyməsini əlavə edirik
    miniapp_btn = types.KeyboardButton(
        text="🌐 MiniApp aç",
        web_app=types.WebAppInfo(url="https://besthome-bot-144q.onrender.com"),
    )
    mk.add(miniapp_btn)

    if is_admin(chat_id):
        mk.add("📊 Admin Panel")

    bot.send_message(chat_id, "📋 Əsas menyudan seçim et:", reply_markup=mk)


@bot.message_handler(func=lambda m: m.text == "🌐 MiniApp aç")
def open_miniapp(message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn = types.KeyboardButton(
        text="🌐 MiniApp aç",
        web_app=types.WebAppInfo(url="https://besthome-bot-144q.onrender.com"),
    )
    kb.add(btn)
    bot.send_message(message.chat.id, "🌐 MiniApp:", reply_markup=kb)


if __name__ == "__main__":
    print("⚙️ BestHome Unified Bot FULL v9 işə düşür...")
    ensure_main_db()
    ensure_local_db()  # 🔥 bunu əlavə et
    ensure_agents_db()  # 🔥 agents üçün
    init_local_db()
    init_agents_db()
    init_main_db_indices()

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "✅ BestHome Bot işləyir."

    threading.Thread(target=run_bot, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
