# ============================================
# 🏠 BestHome Unified Bot — FULL v8
# Elan əlavə • Filtrlə axtarış • Açar sözlə axtarış
# Nömrə ilə axtarış • Favorilər • Admin Panel
# Dual DB (besthome.db + local_data.db)
# © 2025 Əsəd Əsədov (@esedovesed)
# ============================================

import os
import sqlite3
import threading
from datetime import datetime, date
from flask import Flask
import requests
import zipfile
import io

import telebot
from telebot import types

# =============== KONFİQURASİYA ===============
BOT_TOKEN = "6202216323:AAEOWdglrcYTJfCr9oRSJtufjsNAkaLWyTc"  # <-- MUTLƏQ DƏYİŞ
ADMIN_ID = 1311851277
CHANNEL_ID = -1001878623087  # kanal ID (bot orda admin olmalıdır!)

MAIN_DB = "besthome.db"  # əsas böyük baza (Dropbox-dan gəlir)
LOCAL_DB = "local_data.db"  # sabit baza (yeni elanlar, təsdiqlər, agentlər, favorilər, limitlər)

# Əsas baza ZIP linki
DROPBOX_ZIP_URL = "https://www.dropbox.com/scl/fi/08jskiis43hezgl1btim5/besthome.zip?rlkey=jrkmbuv14sal08zpcthb2l7ba&st=h92jg1o7&dl=1"

bot = telebot.TeleBot(BOT_TOKEN)
user_state = {}  # yeni elan əlavə axını
search_state = {}  # gələcək üçün (əgər lazım olsa)


# =============== DB YARDIMÇI FUNKSİYALAR ===============
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
    except Exception as e:
        print("❌ Endirmə xətası:", e)
        return

    if r.status_code == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                z.extractall(".")
            print("✅ besthome.db ZIP-dən uğurla çıxarıldı.")
        except zipfile.BadZipFile:
            print("❌ ZIP faylı korlanıb və ya düzgün deyil.")
    else:
        print(f"❌ Endirmə alınmadı: HTTP {r.status_code}")


def get_main_conn():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_local_db():
    """local_data.db daxilində bütün cədvəlləri hazırla."""
    conn = get_local_conn()
    cur = conn.cursor()

    # Gözləyən elanlar
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

    # Vasitəçilər
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
            source TEXT,        -- 'main' və ya 'local'
            added_at TEXT,
            UNIQUE(chat_id, listing_id, source)
        )
        """
    )

    # İstifadəçilər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            first_seen TEXT,
            username TEXT,
            is_premium INTEGER DEFAULT 0
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

    conn.commit()
    conn.close()
    print("✅ local_data.db hazırdır.")


def init_main_db_indices():
    """Əsas bazada indekslər (əgər listings cədvəli varsa)."""
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


# =============== ÜMUMİ YARDIMÇILAR ===============
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
    for key in ("date_read", "date_added"):
        v = row.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v))
            except:
                pass
    return datetime.min


def register_user(message):
    try:
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO users (chat_id, first_seen, username)
            VALUES (?, ?, ?)
            """,
            (
                message.chat.id,
                datetime.utcnow().isoformat(),
                (message.from_user.username or "") if message.from_user else "",
            ),
        )
        conn.commit()
        conn.close()
    except:
        pass


def check_limit(chat_id: int, key_type: str, daily_limit: int) -> bool:
    if daily_limit <= 0:
        return True
    today = date.today().isoformat()
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT used FROM search_limits
        WHERE chat_id=? AND date=? AND key_type=?
        """,
        (chat_id, today, key_type),
    )
    row = cur.fetchone()
    used = row["used"] if row else 0
    conn.close()
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


def send_main_menu(chat_id: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📝 Yeni elan əlavə et")
    kb.row("🔎 Axtarış sistemi")
    kb.row("⭐ Favorilərim", "📋 Elanlarım")
    kb.row("ℹ️ Haqqında")
    if is_admin(chat_id):
        kb.row("📊 Admin Panel")
    bot.send_message(chat_id, "🏠 Əsas menyu:", reply_markup=kb)


def send_logo_if_exists(chat_id: int):
    try:
        if os.path.exists("besthomelogo.jpeg"):
            with open("besthomelogo.jpeg", "rb") as f:
                bot.send_photo(chat_id, f)
    except:
        pass


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


def send_listing_card(
    chat_id: int, ev: dict, source: str = "main", with_fav_button: bool = True
):
    date_val = ev.get("date_read") or ev.get("date_added") or "-"
    title = ev.get("prop_type", "-")
    rooms = ev.get("rooms") or "-"
    op = ev.get("operation") or "-"
    price = format_price(ev.get("price"))
    cur = ev.get("currency") or "AZN"
    rayon = ev.get("rayon") or ""
    metro = ev.get("metro") or ""
    addr = ev.get("address") or ""
    phone = ev.get("phone") or "-"
    cname = ev.get("contact_name") or "-"
    summary = ev.get("summary") or ""

    location = addr or rayon
    if metro:
        location += f" — {metro}"

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
                "⭐ Favoriyə əlavə et", callback_data=f"fav|{source}|{ev['id']}"
            )
        )
    if link:
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))

    bot.send_message(chat_id, text, reply_markup=mk)


# =============== /start ===============
@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    register_user(message)
    reset_user_state(chat_id)

    send_logo_if_exists(chat_id)

    welcome = (
        "👋 *BestHome Unified Bot-a xoş gəldiniz!*\n\n"
        "Bu bot vasitəsilə:\n"
        "• 📝 Yeni elan əlavə edə bilərsiniz\n"
        "• 🔎 Filtrlə, açar sözlə və nömrə ilə axtarış edə bilərsiniz\n"
        "• ⭐ Favorilərə elan əlavə edib sonradan rahat tapa bilərsiniz\n"
        "• 📋 Öz elanlarınızın statusuna baxa bilərsiniz\n\n"
        "Başlamaq üçün aşağıdakı menyudan seçim edin."
    )
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📝 Yeni elan əlavə et")
    kb.row("🔎 Axtarış sistemi")
    kb.row("⭐ Favorilərim", "📋 Elanlarım")
    kb.row("ℹ️ Haqqında")
    if is_admin(chat_id):
        kb.row("📊 Admin Panel")

    bot.send_message(chat_id, welcome, parse_mode="Markdown", reply_markup=kb)


# =============== ℹ️ Haqqında ===============
@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    text = (
        "🏠 *Best Home Əmlak Botu*\n"
        "• 📝 Yeni elan əlavə (vasitəçi / ev sahibi)\n"
        "• 🔎 Axtarış sistemi: filtrlə, açar sözlə, nömrə ilə\n"
        "• ⭐ Favorilərim: saxladığın elanlar\n"
        "• 📋 Elanlarım: öz elanlarınız və statusu\n"
        "• 📞 Admin: @esedovesed\n"
        "✅ Tək bot — peşəkar emlak idarəetmə sistemi."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


# =============== 📝 YENİ ELAN ƏLAVƏ ET ===============
CANCEL_CMDS = ["❌ Ləğv et", "🏠 Əsas menyu"]
BACK_CMD = "◀️ Geri"


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
    if txt == BACK_CMD:
        bot.send_message(chat_id, "◀️ Bu versiyada geri funksiyası məhduddur.")
        return True
    return False


@bot.message_handler(func=lambda m: m.text == "📝 Yeni elan əlavə et")
def start_new_listing(message):
    chat_id = message.chat.id
    reset_user_state(chat_id)

    instr = (
        "📝 *Yeni elan əlavə etmə qaydası:*\n"
        "1️⃣ Rol seçin (vasitəçi / əmlak sahibi)\n"
        "2️⃣ Əməliyyat növü (Satılır / Kirayə verilir)\n"
        "3️⃣ Əmlak tipi (Mənzil / Fərdi yaşayış evi / Qeyri yaşayış sahəsi / Bağ evi / Torpaq)\n"
        "4️⃣ Otaq sayı, rayon, metro, sahə, qiymət və əlaqə məlumatları\n"
        "5️⃣ Elan admin təsdiqindən sonra kanal + bazaya düşəcək."
    )
    bot.send_message(chat_id, instr, parse_mode="Markdown")

    kb = new_listing_keyboard(extra=[["Vasitəçi", "Əmlak sahibi"]])
    user_state[chat_id] = {"step": "role", "chat_id": chat_id}
    bot.send_message(chat_id, "👤 Rolunuzu seçin:", reply_markup=kb)


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "role")
def step_role(message):
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
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    choice = message.text.strip()
    if choice not in ["Satılır", "Kirayə verilir"]:
        bot.send_message(chat_id, "Zəhmət olmasa Satılır / Kirayə verilir seçin.")
        return
    st = user_state[chat_id]
    st["operation"] = choice

    extra = [
        ["Mənzil", "Fərdi yaşayış evi"],
        ["Qeyri yaşayış sahəsi", "Bağ evi"],
        ["Torpaq"],
    ]
    kb = new_listing_keyboard(extra=extra)
    st["step"] = "prop_type"
    bot.send_message(chat_id, "🏠 Əmlak tipini seçin:", reply_markup=kb)


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "prop_type"
)
def step_prop_type(message):
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    choice = message.text.strip()
    valid = ["Mənzil", "Fərdi yaşayış evi", "Qeyri yaşayış sahəsi", "Bağ evi", "Torpaq"]
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
    chat_id = message.chat.id
    if handle_common_nav(message):
        return
    val = message.text.strip().upper()
    if val not in ["AZN", "USD"]:
        bot.send_message(chat_id, "Zəhmət olmasa AZN və ya USD seçin.")
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
        "Yoxdursa *Link yoxdur, elanı göndər ✅* seçin.",
        parse_mode="Markdown",
        reply_markup=kb,
    )


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
    return new_id


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "link")
def step_link(message):
    chat_id = message.chat.id
    if handle_common_nav(message):
        return

    st = user_state[chat_id]
    txt = message.text.strip()

    if txt.startswith("http"):
        st["link"] = txt
    elif txt != "Link yoxdur, elanı göndər ✅":
        bot.send_message(
            chat_id,
            "⚠️ Düzgün link yazın və ya 'Link yoxdur, elanı göndər ✅' seçin.",
        )
        return

    save_agent_if_needed(st)
    new_id = add_listing_new(st)

    # Adminə məlumat
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
        "✅ Elanınız uğurla əlavə olundu və *admin təsdiqini gözləyir.*",
        parse_mode="Markdown",
    )

    reset_user_state(chat_id)


# =============== 📋 ELANLARIM ===============
@bot.message_handler(func=lambda m: m.text == "📋 Elanlarım")
def my_listings(message):
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
            send_listing_card(message.chat.id, ev, source=src, with_fav_button=False)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav|"))
def cb_add_favorite(c):
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
    bot.answer_callback_query(c.id, "⭐ Favoriyə əlavə olundu")


# =============== 🔎 AXTARIŞ SİSTEMİ MENYUSU ===============
@bot.message_handler(func=lambda m: m.text == "🔎 Axtarış sistemi")
def search_system_menu(message):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("📋 Filtrlə axtar", callback_data="ss|structured"),
    )
    mk.add(
        types.InlineKeyboardButton("🔍 Açar sözlə axtar", callback_data="ss|keyword"),
    )
    mk.add(
        types.InlineKeyboardButton("☎️ Nömrə ilə axtar", callback_data="ss|phone"),
    )
    bot.send_message(
        message.chat.id,
        "🔎 Axtarış metodunu seçin:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("ss|"))
def cb_search_select(c):
    mode = c.data.split("|")[1]
    chat_id = c.message.chat.id

    if mode == "structured":
        if not check_limit(chat_id, "structured", 200):
            bot.answer_callback_query(
                c.id,
                "Günlük filtrli axtarış limitiniz bitib.",
                show_alert=True,
            )
            return
        search_menu(c.message)

    elif mode == "keyword":
        if not check_limit(chat_id, "keyword", 30):
            bot.answer_callback_query(
                c.id,
                "Günlük açar sözlə axtarış limitiniz bitib.",
                show_alert=True,
            )
            return
        msg = bot.send_message(
            chat_id,
            "🔍 Açar söz və ya bir neçə söz yazın (məs: *Kristal Abşeron*):",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, keyword_search_handler)

    elif mode == "phone":
        if not check_limit(chat_id, "phone", 50):
            bot.answer_callback_query(
                c.id,
                "Günlük nömrə ilə axtarış limitiniz bitib.",
                show_alert=True,
            )
            return
        msg = bot.send_message(
            chat_id,
            "☎️ Axtarmaq istədiyiniz nömrəni yazın (məs: 0708468585):",
        )
        bot.register_next_step_handler(msg, phone_search_handler)

    try:
        bot.answer_callback_query(c.id)
    except:
        pass


# =============== 📋 ELAN AXTAR — FİLTRLİ SİSTEM ===============

# Əməliyyat kodları
OP_CODES = {
    "all": None,
    "sat": ["satılır", "satış"],
    "kir": ["kirayə verilir", "kirayə", "icarə"],
}

# Əmlak tipləri (sənin DB-yə uyğun)
PROP_TYPES = {
    "all": None,
    "m": "mənzil",
    "f": "fərdi yaşayış evi",
    "q": "qeyri yaşayış sahəsi",
    "b": "bağ evi",
    "t": "torpaq",
}

# Rayon qrupları
RAYON_GROUPS = {
    "all": None,
    "bak": [
        "baki",
        "bakı",
        "yasamal",
        "xətai",
        "xetai",
        "nizami",
        "sabail",
        "səbail",
        "binəqədi",
        "bineqedi",
        "nərimanov",
        "nerimanov",
        "nəsimi",
        "nasimi",
        "sabunçu",
        "sabuncu",
        "suraxanı",
        "suraxani",
        "xəzər",
        "xezər",
        "qaradağ",
        "qaradag",
        "pirallahı",
        "buzovna",
        "əhmədli",
        "ehmedli",
        "28 may",
        "elmler",
        "memar əcəmi",
        "20 yanvar",
        "inşaatçılar",
        "xalqlar",
        "nəriman nərimanov",
        "bakixanov",
        "hövsan",
        "biləcəri",
        "bileceri",
        "bina",
        "maştağa",
        "mastaga",
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
        "mehdi abad",
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
    "sum": ["sumqayıt", "sumqayit"],
}


def safe_date(row: dict):
    for key in ("date_read", "date_added"):
        v = row.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v))
            except:
                pass
    return datetime.min


def decode_price_range(code: str):
    # Kirayə
    if code == "k1":
        return 0, 500
    if code == "k2":
        return 600, 1000
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


def build_filters_sql(op_code, prop_code, rayon_group, min_price=None, max_price=None):
    sql = " WHERE 1=1"
    params = []

    # Əməliyyat (satılır / kirayə)
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

    # Rayon (address və summary üzərindən axtar)
    kws = RAYON_GROUPS.get(rayon_group)
    if kws:
        conds = []
        for kw in kws:
            like = f"%{kw}%"
            # address sütunu varsa onunla, yoxdursa summary ilə işləyəcək
            conds.append("LOWER(COALESCE(address, '')) LIKE ?")
            conds.append("LOWER(COALESCE(summary, '')) LIKE ?")
            params.extend([like, like])
        sql += " AND (" + " OR ".join(conds) + ")"

    # Qiymət filtri
    if min_price is not None:
        sql += " AND CAST(REPLACE(REPLACE(price, ',', ''), ' ', '') AS INTEGER) >= ?"
        params.append(min_price)
    if max_price is not None:
        sql += " AND CAST(REPLACE(REPLACE(price, ',', ''), ' ', '') AS INTEGER) <= ?"
        params.append(max_price)

    return sql, params


# ----------- Əsas menyu -----------
@bot.message_handler(func=lambda m: m.text == "📋 Elan axtar")
def search_menu(m):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("💸 Satılır", callback_data="s_op|sat"),
        types.InlineKeyboardButton("🏢 Kirayə verilir", callback_data="s_op|kir"),
    )
    mk.add(types.InlineKeyboardButton("🌐 Hamısı", callback_data="s_op|all"))
    bot.send_message(m.chat.id, "🔍 Əməliyyat növünü seç:", reply_markup=mk)


# ----------- Əmlak tipi seçimi -----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("s_op|"))
def cb_s_op(c):
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
        types.InlineKeyboardButton("Torpaq", callback_data=f"s_tp|{op}|t"),
        types.InlineKeyboardButton("Hamısı", callback_data=f"s_tp|{op}|all"),
    )
    bot.edit_message_text(
        "🏠 Əmlak tipini seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


# ----------- Rayon qrupu seçimi -----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("s_tp|"))
def cb_s_tp(c):
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


# ----------- Qiymət seçimi -----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("s_rg|"))
def cb_s_rg(c):
    _, op, tp, rg = c.data.split("|")
    mk = types.InlineKeyboardMarkup()

    if op == "kir":
        mk.add(
            types.InlineKeyboardButton("0-500", callback_data=f"spr|{op}|{tp}|{rg}|k1"),
            types.InlineKeyboardButton(
                "600-1000", callback_data=f"spr|{op}|{tp}|{rg}|k2"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "1050-1500", callback_data=f"spr|{op}|{tp}|{rg}|k3"
            ),
            types.InlineKeyboardButton(
                "1550-2000", callback_data=f"spr|{op}|{tp}|{rg}|k4"
            ),
        )
        mk.add(
            types.InlineKeyboardButton("2000+", callback_data=f"spr|{op}|{tp}|{rg}|k5")
        )
    else:
        mk.add(
            types.InlineKeyboardButton(
                "Limitsiz", callback_data=f"spr|{op}|{tp}|{rg}|s0"
            ),
            types.InlineKeyboardButton(
                "0-50,000", callback_data=f"spr|{op}|{tp}|{rg}|s1"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "50,000-100,000", callback_data=f"spr|{op}|{tp}|{rg}|s2"
            ),
            types.InlineKeyboardButton(
                "100,000-200,000", callback_data=f"spr|{op}|{tp}|{rg}|s3"
            ),
        )
        mk.add(
            types.InlineKeyboardButton(
                "200,000+", callback_data=f"spr|{op}|{tp}|{rg}|s4"
            )
        )

    bot.edit_message_text(
        "💰 Qiymət aralığını seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


# ----------- Axtarış nəticələrinin işlənməsi -----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("spr|"))
def cb_s_price(c):
    _, op, tp, rg, pc = c.data.split("|")
    run_structured_search(
        chat_id=c.message.chat.id,
        op_code=op,
        prop_code=tp,
        rayon_group=rg,
        price_code=pc,
        offset=0,
        edit_msg=(c.message.chat.id, c.message.message_id),
    )


# ----------- Daha çox nəticə -----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("more|"))
def cb_more(c):
    _, op, tp, rg, pc, off = c.data.split("|")
    run_structured_search(
        chat_id=c.message.chat.id,
        op_code=op,
        prop_code=tp,
        rayon_group=rg,
        price_code=pc,
        offset=int(off),
        edit_msg=None,
    )


def run_structured_search(
    chat_id, op_code, prop_code, rayon_group, price_code, offset, edit_msg=None
):
    page_size = 10
    min_p, max_p = decode_price_range(price_code)
    results = []

    # MAIN DB
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        flt, params = build_filters_sql(op_code, prop_code, rayon_group, min_p, max_p)
        sql = "SELECT * FROM listings" + flt + " ORDER BY date_read DESC, id DESC"
        cur.execute(sql, params)
        for r in cur.fetchall():
            results.append(dict(r))
        conn.close()

    # LOCAL DB (address sütunu olmayan)
    conn = get_local_conn()
    cur = conn.cursor()
    try:
        flt, params = build_filters_sql(op_code, prop_code, rayon_group, min_p, max_p)
        sql = (
            "SELECT * FROM listings_approved"
            + flt
            + " ORDER BY date_added DESC, id DESC"
        )
        cur.execute(sql, params)
        for r in cur.fetchall():
            results.append(dict(r))
    except sqlite3.OperationalError:
        # address sütunu yoxdursa, summary ilə axtar
        flt, params = build_filters_sql(op_code, prop_code, "all", min_p, max_p)
        sql = "SELECT * FROM listings_approved WHERE LOWER(summary) LIKE ? ORDER BY date_added DESC, id DESC"
        cur.execute(sql, ["%%"])
        for r in cur.fetchall():
            results.append(dict(r))
    conn.close()

    if not results:
        bot.send_message(chat_id, "😕 Uyğun elan tapılmadı.")
        return

    results.sort(key=safe_date, reverse=True)
    slice_results = results[offset : offset + page_size]

    if offset == 0:
        bot.send_message(
            chat_id, f"🔎 {len(results)} elan tapıldı. İlk {page_size} göstərilir:"
        )

    for ev in slice_results:
        send_listing_card(chat_id, ev, source="main", with_fav_button=True)

    if offset + page_size < len(results):
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➡️ Daha çox göstər",
                callback_data=f"more|{op_code}|{prop_code}|{rayon_group}|{price_code}|{offset + page_size}",
            )
        )
        bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)


# === 🔍 AÇAR SÖZLƏ AXTARIŞ (səhifələmə ilə) ===
@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"))
def keyword_search_handler(message):
    chat_id = message.chat.id
    if not check_limit(chat_id, "keyword", 30):
        bot.send_message(chat_id, "Günlük açar sözlə axtarış limitiniz bitib.")
        return

    query = (message.text or "").strip().lower()
    if not query:
        bot.send_message(chat_id, "Boş sorğu göndərdiniz.")
        return

    like = f"%{query}%"
    results = []

    # Əsas baza
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM listings
            WHERE
                LOWER(prop_type) LIKE ?
                OR LOWER(operation) LIKE ?
                OR LOWER(metro) LIKE ?
                OR LOWER(rooms) LIKE ?
                OR LOWER(address) LIKE ?
                OR LOWER(summary) LIKE ?
            ORDER BY date_read DESC, id DESC
            LIMIT 200
            """,
            (like, like, like, like, like, like),
        )
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    # Lokal baza
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM listings_approved
        WHERE
            LOWER(prop_type) LIKE ?
            OR LOWER(operation) LIKE ?
            OR LOWER(metro) LIKE ?
            OR LOWER(rooms) LIKE ?
            OR LOWER(summary) LIKE ?
        ORDER BY date_added DESC, id DESC
        LIMIT 200
        """,
        (like, like, like, like, like),
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    if not results:
        bot.send_message(chat_id, "😕 Uyğun elan tapılmadı.")
        return

    inc_limit(chat_id, "keyword", 1)
    results.sort(key=lambda x: safe_date(x), reverse=True)

    # Nəticələri yadda saxla (müvəqqəti RAM-də)
    global search_results_cache
    search_results_cache[chat_id] = results

    send_keyword_page(chat_id, offset=0)


# === 🔁 Daha çox düyməsinə cavab ===
@bot.callback_query_handler(func=lambda c: c.data.startswith("kw_more|"))
def cb_kw_more(c):
    chat_id = c.message.chat.id
    offset = int(c.data.split("|")[1])
    send_keyword_page(chat_id, offset)
    try:
        bot.delete_message(chat_id, c.message.message_id)
    except:
        pass


# === 📄 Nəticə səhifələyici ===
search_results_cache = {}


def send_keyword_page(chat_id, offset=0):
    results = search_results_cache.get(chat_id, [])
    page_size = 10

    if not results:
        bot.send_message(chat_id, "⚠️ Axtarış məlumatı tapılmadı. Yenidən axtarın.")
        return

    end_index = offset + page_size
    slice_results = results[offset:end_index]

    if offset == 0:
        bot.send_message(
            chat_id, f"🔍 Tapıldı: {len(results)} elan. İlk {page_size} göstərilir:"
        )

    for ev in slice_results:
        send_listing_card(
            chat_id, ev, source=ev.get("__source", "main"), with_fav_button=True
        )

    # Daha çox düyməsi
    if end_index < len(results):
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➡️ Daha çox göstər", callback_data=f"kw_more|{end_index}"
            )
        )
        bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)
    else:
        bot.send_message(chat_id, "✅ Bütün elanlar göstərildi.")


# === 3) NÖMRƏ İLƏ AXTARIŞ ===
def phone_search_handler(message):
    chat_id = message.chat.id
    if not check_limit(chat_id, "phone", 50):
        bot.send_message(chat_id, "Günlük nömrə ilə axtarış limitiniz bitib.")
        return

    raw = "".join(ch for ch in (message.text or "") if ch.isdigit())
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
            LIMIT 100
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
        LIMIT 100
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

    inc_limit(chat_id, "phone", 1)
    results.sort(key=lambda x: safe_date(x), reverse=True)

    bot.send_message(chat_id, f"☎️ Bu nömrə ilə {len(results)} elan tapıldı:")
    for ev in results[:50]:
        send_listing_card(
            chat_id, ev, source=ev.get("__source", "main"), with_fav_button=True
        )


# =============== 📊 ADMIN PANEL ===============
@bot.message_handler(func=lambda m: m.text == "📊 Admin Panel")
def admin_panel(message):
    if not is_admin(message.chat.id):
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
    mk.add(types.InlineKeyboardButton("🔍 Bazada axtar", callback_data="adm|search"))
    mk.add(
        types.InlineKeyboardButton(
            "♻️ Limitləri sıfırla", callback_data="adm|reset_limits"
        )
    )
    bot.send_message(message.chat.id, "🛠 Admin Panel:", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm|"))
def cb_admin(c):
    if not is_admin(c.message.chat.id):
        return
    cmd = c.data.split("|")[1]
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
    try:
        bot.answer_callback_query(c.id)
    except:
        pass


def show_pending_listings(chat_id):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_new WHERE approved=0 ORDER BY id DESC LIMIT 30")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "⛔ Təsdiq gözləyən elan yoxdur.")
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
            types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"aprv|{ev['id']}"),
            types.InlineKeyboardButton("❌ Sil", callback_data=f"del|{ev['id']}"),
        )
        bot.send_message(chat_id, txt, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("aprv|"))
def cb_approve(c):
    if not is_admin(c.message.chat.id):
        return
    lid = int(c.data.split("|")[1])

    conn = get_local_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_new WHERE id=? AND approved=0", (lid,))
    row = cur.fetchone()
    if not row:
        bot.answer_callback_query(c.id, "Tapılmadı və ya artıq təsdiqlənib.")
        conn.close()
        return

    r = dict(row)

    cur.execute(
        """
        INSERT INTO listings_approved (
            date_added, chat_id, role, prop_type, operation,
            rayon, metro, rooms, area_kvm, price, currency,
            phone, contact_name, summary, link
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            r["date_added"],
            r["chat_id"],
            r["role"],
            r["prop_type"],
            r["operation"],
            r["rayon"],
            r["metro"],
            r["rooms"],
            r["area_kvm"],
            r["price"],
            r["currency"],
            r["phone"],
            r["contact_name"],
            r["summary"],
            r["link"],
        ),
    )
    new_id = cur.lastrowid

    cur.execute("UPDATE listings_new SET approved=1 WHERE id=?", (lid,))
    conn.commit()
    conn.close()

    try:
        bot.send_message(r["chat_id"], "🎉 Elanınız təsdiqləndi və sistemdə aktivdir.")
    except:
        pass

    if CHANNEL_ID and new_id:
        conn2 = get_local_conn()
        cur2 = conn2.cursor()
        cur2.execute("SELECT * FROM listings_approved WHERE id=?", (new_id,))
        lrow = cur2.fetchone()
        conn2.close()
        if lrow:
            send_listing_card(
                CHANNEL_ID, dict(lrow), source="local", with_fav_button=False
            )

    bot.answer_callback_query(c.id, "✅ Elan təsdiqləndi.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("del|"))
def cb_delete(c):
    if not is_admin(c.message.chat.id):
        return
    lid = int(c.data.split("|")[1])
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM listings_new WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    bot.answer_callback_query(c.id, "❌ Elan silindi.")


def show_admin_stats(chat_id):
    conn_l = get_local_conn()
    c = conn_l.cursor()
    c.execute("SELECT COUNT(*) FROM listings_new")
    total_new = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings_new WHERE approved=0")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings_approved")
    approved_local = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM agents")
    agents = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    conn_l.close()

    total_main = 0
    if os.path.exists(MAIN_DB):
        conn_m = get_main_conn()
        cm = conn_m.cursor()
        try:
            cm.execute("SELECT COUNT(*) FROM listings")
            total_main = cm.fetchone()[0]
        except:
            total_main = 0
        conn_m.close()

    txt = (
        "📊 *BestHome Statistikası*\n\n"
        f"📁 Əsas baza elanları: *{total_main}*\n"
        f"🆕 Yeni elanlar (ümumi): *{total_new}*\n"
        f"⏳ Gözləmədə olanlar: *{pending}*\n"
        f"✅ Təsdiqlənmiş lokal elanlar: *{approved_local}*\n"
        f"👥 Vasitəçilər: *{agents}*\n"
        f"👤 İstifadəçilər: *{users}*\n"
    )
    bot.send_message(chat_id, txt, parse_mode="Markdown")


def admin_agents_broadcast(message):
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    if not text:
        bot.send_message(message.chat.id, "Boş mesaj göndərilə bilməz.")
        return
    conn = get_local_conn()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM agents")
    agents = [r[0] for r in c.fetchall()]
    conn.close()

    sent = 0
    for cid in agents:
        try:
            bot.send_message(cid, f"📢 Admin bildirişi:\n{text}")
            sent += 1
        except:
            pass

    bot.send_message(message.chat.id, f"✅ Bildiriş {sent} vasitəçiyə göndərildi.")


def admin_search_handler(message):
    if not is_admin(message.chat.id):
        return
    kw = (message.text or "").strip().lower()
    if not kw:
        bot.send_message(message.chat.id, "Boş sorğu.")
        return
    like = f"%{kw}%"
    results = []

    if os.path.exists(MAIN_DB):
        conn_m = get_main_conn()
        cm = conn_m.cursor()
        cm.execute(
            """
            SELECT * FROM listings
            WHERE
                LOWER(prop_type) LIKE ?
                OR LOWER(operation) LIKE ?
                OR LOWER(metro) LIKE ?
                OR LOWER(rooms) LIKE ?
                OR LOWER(address) LIKE ?
                OR LOWER(summary) LIKE ?
            ORDER BY date_read DESC, id DESC
            LIMIT 100
            """,
            (like, like, like, like, like, like),
        )
        for r in cm.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn_m.close()

    conn_l = get_local_conn()
    cl = conn_l.cursor()
    cl.execute(
        """
        SELECT * FROM listings_approved
        WHERE
            LOWER(prop_type) LIKE ?
            OR LOWER(operation) LIKE ?
            OR LOWER(metro) LIKE ?
            OR LOWER(rooms) LIKE ?
            OR LOWER(rayon) LIKE ?
            OR LOWER(summary) LIKE ?
        ORDER BY date_added DESC, id DESC
        LIMIT 100
        """,
        (like, like, like, like, like, like),
    )
    for r in cl.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn_l.close()

    if not results:
        bot.send_message(message.chat.id, "Heç nə tapılmadı.")
        return

    results.sort(key=lambda x: safe_date(x), reverse=True)
    bot.send_message(message.chat.id, f"🔍 Admin üçün nəticələr: {len(results)}")
    for ev in results[:50]:
        send_listing_card(
            message.chat.id,
            ev,
            source=ev.get("__source", "main"),
            with_fav_button=False,
        )


# =============== RUN (Render üçün) ===============
if __name__ == "__main__":
    print("⚙️ BestHome Unified Bot FULL v8 işə düşür...")
    ensure_main_db()
    init_local_db()
    init_main_db_indices()

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "✅ BestHome Bot işləyir."

    import threading, time

    def run_bot():
        while True:
            try:
                print("🤖 Bot polling başladı...")
                bot.infinity_polling(
                    timeout=10, long_polling_timeout=20, skip_pending=True
                )
            except Exception as e:
                print("⚠️ Polling error:", e)
                time.sleep(5)

    threading.Thread(target=run_bot, daemon=True).start()

    # 🔹 BU HİSSƏ MÜTLƏQ BELƏ OLMALIDIR
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
