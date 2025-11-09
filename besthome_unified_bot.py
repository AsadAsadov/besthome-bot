# ============================================
# 🏠 BestHome Unified Bot — FULL v8
# Elan əlavə • Filtrli axtarış • Açar sözlə axtarış
# Favorilər • Admin Panel • Kanal paylaşımı
# Dual DB: besthome.db (əsas) + local_data.db (bot)
# © 2025 Əsəd Əsədov (@esedovesed)
# ============================================

import os
import sqlite3
import threading
from datetime import datetime
from flask import Flask
import requests
import zipfile
import io

import telebot
from telebot import types

# =============== KONFİQURASİYA ===============
BOT_TOKEN = "6202216323:AAEOWdglrcYTJfCr9oRSJtufjsNAkaLWyTc"
ADMIN_ID = 1311851277
CHANNEL_ID = -1001878623087  # Bot bu kanalda admin OLMALIDIR

MAIN_DB = "besthome.db"  # əsas böyük baza (Dropbox-dan)
LOCAL_DB = "local_data.db"  # botun lokal bazası

DROPBOX_ZIP_URL = "https://www.dropbox.com/scl/fi/08jskiis43hezgl1btim5/besthome.zip?rlkey=jrkmbuv14sal08zpcthb2l7ba&st=h92jg1o7&dl=1"

LOGO_FILE = "besthomelogo.jpeg"

bot = telebot.TeleBot(BOT_TOKEN)
user_state = {}  # yeni elan form addımları
search_state = {}  # açar sözlə axtarış üçün yaddaş


# =============== YARDIMÇI FUNKSİYALAR ===============
def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_ID


def format_price(v):
    if v is None:
        return "-"
    s = str(v).strip()
    try:
        val = int(float(s.replace(" ", "").replace(",", "")))
        return f"{val:,}".replace(",", " ")
    except:
        return s


def safe_date(ev: dict):
    return ev.get("date_read") or ev.get("date_added") or ""


# =============== DB: ƏSAS BAZA (MAIN_DB) ===============
def ensure_main_db():
    if os.path.exists(MAIN_DB):
        print("📦 Mövcud besthome.db tapıldı.")
        return
    if not DROPBOX_ZIP_URL:
        print("⚠️ besthome.db yoxdur və DROPBOX_ZIP_URL verilməyib.")
        return
    print("⬇️ besthome.zip endirilir (Dropbox)...")
    r = requests.get(DROPBOX_ZIP_URL)
    if r.status_code == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                z.extractall(".")
            if os.path.exists(MAIN_DB):
                print("✅ besthome.db ZIP-dən çıxarıldı.")
            else:
                print("⚠️ ZIP açıldı amma besthome.db tapılmadı, adları yoxla.")
        except zipfile.BadZipFile:
            print("❌ ZIP formatı yalnışdır.")
    else:
        print(f"❌ Endirmə alınmadı: {r.status_code}")


def get_main_conn():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_main_indices():
    if not os.path.exists(MAIN_DB):
        return
    conn = get_main_conn()
    cur = conn.cursor()
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_main_op ON listings(operation)")
    except:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_main_price ON listings(price)")
    except:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_main_date ON listings(date_read)")
    except:
        pass
    conn.commit()
    conn.close()


# =============== DB: LOKAL BAZA (LOCAL_DB) ===============
def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_local_db():
    new_db = not os.path.exists(LOCAL_DB)
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

    # Təsdiqlənmiş elanlar (yalnız bot üçün)
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
            source TEXT,      -- 'main' və ya 'local'
            added_at TEXT,
            UNIQUE(chat_id, listing_id, source)
        )
        """
    )

    conn.commit()
    conn.close()

    if new_db:
        print("✅ local_data.db yaradıldı və struktur hazırdır.")
    else:
        print("📦 Mövcud local_data.db tapıldı.")


# =============== ELAN YAZ / OXU FUNKSİYALARI ===============
def save_agent_if_needed(data: dict):
    if data.get("role") != "Vasitəçi":
        return
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


def add_new_listing(data: dict) -> int:
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO listings_new (
            date_added, chat_id, role, prop_type, operation, rayon, metro,
            rooms, area_kvm, price, currency, phone, contact_name, summary, link, approved
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            datetime.today().date().isoformat(),
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
    lid = cur.lastrowid
    conn.commit()
    conn.close()
    return lid


def approve_listing(lid: int):
    """
    Admin təsdiqləyir:
    - listings_new → listings_approved
    - approved=1
    - geri: approved row dict (və ya None)
    """
    conn = get_local_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM listings_new WHERE id=? AND approved=0", (lid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    d = dict(row)

    # Dublikatdan qaçmaq üçün sadə check (telefon+qiymət+təsvir)
    cur.execute(
        """
        SELECT id FROM listings_approved
        WHERE phone=? AND price=? AND summary=?
        LIMIT 1
        """,
        (d.get("phone"), d.get("price"), d.get("summary")),
    )
    if cur.fetchone():
        # yenə də listings_new işarələ
        cur.execute("UPDATE listings_new SET approved=1 WHERE id=?", (lid,))
        conn.commit()
        conn.close()
        return d

    # listings_approved-ə yaz
    cur.execute(
        """
        INSERT INTO listings_approved (
            date_added, chat_id, role, prop_type, operation, rayon, metro,
            rooms, area_kvm, price, currency, phone, contact_name, summary, link
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            d.get("date_added"),
            d.get("chat_id"),
            d.get("role"),
            d.get("prop_type"),
            d.get("operation"),
            d.get("rayon"),
            d.get("metro"),
            d.get("rooms"),
            d.get("area_kvm"),
            d.get("price"),
            d.get("currency"),
            d.get("phone"),
            d.get("contact_name"),
            d.get("summary"),
            d.get("link"),
        ),
    )

    cur.execute("UPDATE listings_new SET approved=1 WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return d


# =============== FAVORİ / CARD GÖSTƏR ===============
def send_listing_card(chat_id, ev: dict, source=None, with_fav_button=True):
    """
    ev: dict (main listings və ya local listings_approved)
    source: 'main' / 'local' → favori üçün lazımdır
    """
    date_val = ev.get("date_read") or ev.get("date_added") or "-"
    txt = (
        f"📅 {date_val}\n"
        f"🏠 {ev.get('prop_type', '-')} | {ev.get('rooms', '-')} otaq\n"
        f"💸 {ev.get('operation', '-')} | 💰 {format_price(ev.get('price'))} {ev.get('currency', 'AZN')}\n"
        f"📍 {ev.get('rayon') or ev.get('address', '-')}"
    )

    if ev.get("metro") and ev.get("metro") not in (ev.get("rayon") or ""):
        txt += f" — {ev.get('metro')}"

    txt += (
        f"\n📞 {ev.get('phone', '-')} ({ev.get('contact_name', '-')})"
        f"\n🧾 {ev.get('summary', '-')}"
    )

    link = ev.get("link") or ev.get("source_link")
    if link:
        txt += f"\n🔗 {link}"

    mk = types.InlineKeyboardMarkup()
    if with_fav_button and ev.get("id") and source in ("main", "local"):
        mk.add(
            types.InlineKeyboardButton(
                "⭐ Favori", callback_data=f"fav|{source}|{ev['id']}"
            )
        )
    if link and link.startswith("http"):
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))

    bot.send_message(chat_id, txt, reply_markup=mk)


# =============== MENYU HELPER ===============
def send_main_menu(chat_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 Yeni elan əlavə et", "📋 Elan axtar")
    kb.add("📞 Nömrə ilə axtarış")
    kb.add("🔎 Açar sözlə axtarış", "⭐ Favorilərim")
    kb.add("📋 Elanlarım", "🔁 Sıfırla")
    kb.add("ℹ️ Haqqında", "📞 Adminlə əlaqə")

    if is_admin(chat_id):
        kb.add("📊 Admin Panel")
    bot.send_message(chat_id, "Seçim edin ⬇️", reply_markup=kb)


# =============== /start ===============
@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    welcome = (
        "👋 *Xoş gəlmisiniz BestHome PRO Bot-a!*\n\n"
        "Bu bot vasitəsilə:\n"
        "• 📝 Yeni elan əlavə edə bilərsiniz\n"
        "• 📋 Filtrlərlə elan axtara bilərsiniz\n"
        "• 🔎 Açar sözlə sürətli axtarış edə bilərsiniz\n"
        "• ⭐ Bəyəndiyiniz elanları favoriyə yığa bilərsiniz\n"
        "• 📋 Öz elanlarınızın statusuna baxa bilərsiniz\n"
        "• 📊 Admin olaraq sistemi idarə edə bilərsiniz\n"
    )

    try:
        if os.path.exists(LOGO_FILE):
            with open(LOGO_FILE, "rb") as f:
                bot.send_photo(chat_id, f, caption=welcome, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, welcome, parse_mode="Markdown")
    except:
        bot.send_message(chat_id, welcome, parse_mode="Markdown")

    send_main_menu(chat_id)


# =============== /myid ===============
@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    bot.send_message(
        message.chat.id,
        f"Sənin Telegram ID-n: `{message.chat.id}`",
        parse_mode="Markdown",
    )


# =============== 🔁 Sıfırla ===============
@bot.message_handler(func=lambda m: m.text == "🔁 Sıfırla")
def reset_all(message):
    chat_id = message.chat.id
    user_state.pop(chat_id, None)
    search_state.pop(chat_id, None)
    bot.send_message(chat_id, "🔁 Bütün seçimlər sıfırlandı.")
    send_main_menu(chat_id)


# =============== 📞 Adminlə əlaqə ===============
@bot.message_handler(func=lambda m: m.text == "📞 Adminlə əlaqə")
def contact_admin(message):
    bot.send_message(
        message.chat.id,
        "📞 Admin ilə əlaqə: @esedovesed\n"
        "Hər hansı texniki problem və ya təklif üçün yaza bilərsiniz.",
    )


# =============== 📝 YENİ ELAN ƏLAVƏ ET ===============
@bot.message_handler(func=lambda m: m.text == "📝 Yeni elan əlavə et")
def start_new_listing(message):
    chat_id = message.chat.id
    user_state[chat_id] = {"step": "role", "chat_id": chat_id}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Vasitəçi", "Əmlak sahibi")
    kb.add("🔁 Sıfırla")
    bot.send_message(
        chat_id, "👤 Siz vasitəçisiniz, yoxsa əmlak sahibi?", reply_markup=kb
    )


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "role")
def step_role(message):
    chat_id = message.chat.id
    if message.text not in ["Vasitəçi", "Əmlak sahibi"]:
        bot.send_message(chat_id, "Zəhmət olmasa seçim edin: Vasitəçi / Əmlak sahibi")
        return
    st = user_state[chat_id]
    st["role"] = message.text

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    props = [
        "Yeni tikili",
        "Köhnə tikili",
        "Həyət evi",
        "Bağ evi",
        "Obyekt",
        "Torpaq",
    ]
    for i in range(0, len(props), 2):
        kb.add(*props[i : i + 2])
    kb.add("🔁 Sıfırla")
    bot.send_message(chat_id, "🏠 Əmlak növünü seç:", reply_markup=kb)
    st["step"] = "prop_type"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "prop_type"
)
def step_prop_type(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["prop_type"] = message.text

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Satılır", "Kirayə", "Günlük")
    kb.add("🔁 Sıfırla")
    bot.send_message(chat_id, "💸 Əməliyyat növü:", reply_markup=kb)
    st["step"] = "operation"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "operation"
)
def step_operation(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["operation"] = message.text

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    rayonlar = [
        "Binəqədi rayonu",
        "Qaradağ rayonu",
        "Xəzər rayonu",
        "Səbail rayonu",
        "Sabunçu rayonu",
        "Suraxanı rayonu",
        "Nərimanov rayonu",
        "Nəsimi rayonu",
        "Nizami rayonu",
        "Pirallahı rayonu",
        "Xətai rayonu",
        "Yasamal rayonu",
        "Abşeron rayonu",
        "Sumqayıt şəhəri",
        "Digər",
    ]
    for i in range(0, len(rayonlar), 2):
        kb.add(*rayonlar[i : i + 2])
    kb.add("🔁 Sıfırla")
    bot.send_message(chat_id, "📍 Rayon seç:", reply_markup=kb)
    st["step"] = "rayon"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "rayon"
)
def step_rayon(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["rayon"] = message.text

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
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
    for i in range(0, len(metros), 2):
        kb.add(*metros[i : i + 2])
    kb.add("🔁 Sıfırla")
    bot.send_message(chat_id, "🚇 Metro seç:", reply_markup=kb)
    st["step"] = "metro"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "metro"
)
def step_metro(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["metro"] = message.text
    bot.send_message(chat_id, "🔢 Otaq sayı:")
    st["step"] = "rooms"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "rooms"
)
def step_rooms(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["rooms"] = message.text
    bot.send_message(chat_id, "📏 Sahə (m²):")
    st["step"] = "area"


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "area")
def step_area(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["area_kvm"] = message.text
    bot.send_message(chat_id, "💰 Qiymət:")
    st["step"] = "price"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "price"
)
def step_price(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["price"] = message.text
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("AZN", "USD")
    kb.add("🔁 Sıfırla")
    bot.send_message(chat_id, "💱 Valyuta:", reply_markup=kb)
    st["step"] = "currency"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "currency"
)
def step_currency(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["currency"] = message.text
    bot.send_message(chat_id, "📞 Əlaqə nömrəsi:")
    st["step"] = "phone"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "phone"
)
def step_phone(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["phone"] = message.text
    bot.send_message(chat_id, "👤 Əlaqədar şəxsin adı:")
    st["step"] = "contact_name"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "contact_name"
)
def step_contact_name(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["contact_name"] = message.text
    bot.send_message(chat_id, "🧾 Qısa təsvir yaz:")
    st["step"] = "summary"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "summary"
)
def step_summary(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["summary"] = message.text

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Link yoxdur, elanı göndər ✅")
    kb.add("🔁 Sıfırla")
    bot.send_message(
        chat_id,
        "🔗 Əgər elan linki varsa (bina.az, tap.az və s.) göndərin.\n"
        "Yoxdursa *Link yoxdur, elanı göndər ✅* seçin.",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    st["step"] = "link"


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "link")
def step_link(message):
    chat_id = message.chat.id
    st = user_state[chat_id]

    if message.text.startswith("http"):
        st["link"] = message.text
    elif message.text != "Link yoxdur, elanı göndər ✅":
        bot.send_message(
            chat_id, "⚠️ Düzgün link yazın və ya 'Link yoxdur, elanı göndər ✅' seçin."
        )
        return

    save_agent_if_needed(st)
    lid = add_new_listing(st)

    # Adminə preview
    txt = (
        f"📢 *Yeni elan (gözləmədə)*\n\n"
        f"ID: {lid}\n"
        f"👤 {st['role']}\n"
        f"🏠 {st['prop_type']} | {st['rooms']}\n"
        f"💸 {st['operation']} | 💰 {format_price(st['price'])} {st['currency']}\n"
        f"📍 {st['rayon']} — {st['metro']}\n"
        f"📞 {st['phone']} ({st['contact_name']})\n"
        f"🧾 {st['summary']}"
    )
    if st.get("link"):
        txt += f"\n🔗 {st['link']}"

    try:
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"aprv_{lid}"),
            types.InlineKeyboardButton("❌ Sil", callback_data=f"del_{lid}"),
        )
        bot.send_message(ADMIN_ID, txt, parse_mode="Markdown", reply_markup=mk)
    except:
        pass

    bot.send_message(
        chat_id,
        "✅ Elan uğurla əlavə olundu.\n" "⏳ Hal-hazırda *admin təsdiqini gözləyir.*",
        parse_mode="Markdown",
    )
    user_state.pop(chat_id, None)


# =============== 📋 ELANLARIM ===============
@bot.message_handler(func=lambda m: m.text == "📋 Elanlarım")
def my_listings(message):
    chat_id = message.chat.id
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM listings_new WHERE chat_id=? ORDER BY id DESC", (chat_id,)
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "Sənin aktiv və ya gözləyən elanların yoxdur.")
        return

    bot.send_message(chat_id, "📋 Sənin elanların:")
    for r in rows:
        ev = dict(r)
        status = "✅ Təsdiqlənib" if ev["approved"] == 1 else "⏳ Gözləmədə"
        txt = (
            f"{status}\n"
            f"🏠 {ev.get('prop_type','-')} | {ev.get('rooms','-')}\n"
            f"💸 {ev.get('operation','-')} | 💰 {format_price(ev.get('price'))} {ev.get('currency','AZN')}\n"
            f"📍 {ev.get('rayon','-')} {('— ' + ev.get('metro')) if ev.get('metro') else ''}\n"
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
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT listing_id, source, added_at FROM favorites WHERE chat_id=? ORDER BY added_at DESC",
        (chat_id,),
    )
    favs = cur.fetchall()
    conn.close()

    if not favs:
        bot.send_message(chat_id, "⭐ Favoridə heç bir elan saxlamamısan.")
        return

    bot.send_message(chat_id, f"⭐ Favorilər ({len(favs)}):")

    # Ayrı-ayrı DB-lərdən çək
    for f in favs:
        lid = f["listing_id"]
        src = f["source"]
        if src == "main" and os.path.exists(MAIN_DB):
            mc = get_main_conn()
            mcur = mc.cursor()
            mcur.execute("SELECT * FROM listings WHERE id=?", (lid,))
            row = mcur.fetchone()
            mc.close()
            if row:
                send_listing_card(
                    message.chat.id, dict(row), source="main", with_fav_button=False
                )
        elif src == "local":
            lc = get_local_conn()
            lcur = lc.cursor()
            lcur.execute("SELECT * FROM listings_approved WHERE id=?", (lid,))
            row = lcur.fetchone()
            lc.close()
            if row:
                send_listing_card(
                    message.chat.id, dict(row), source="local", with_fav_button=False
                )


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav|"))
def cb_favorite(c):
    chat_id = c.message.chat.id
    _, src, lid = c.data.split("|")
    lid = int(lid)

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO favorites (chat_id, listing_id, source, added_at)
        VALUES (?, ?, ?, ?)
        """,
        (chat_id, lid, src, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    bot.answer_callback_query(c.id, "⭐ Favorilərə əlavə olundu")


# =============== 📋 ELAN AXTAR — FİLTRLİ ===============

# Kodlar
OP_CODES = {
    "all": None,
    "sat": ["satılır", "satış"],
    "kir": ["kirayə", "icarə"],
    "gun": ["günlük"],
}

PROP_TYPES = {
    "all": None,
    "nt": "yeni tikili",
    "kt": "köhnə tikili",
    "hey": "həyət",
    "bag": "bağ",
    "ob": "obyekt",
    "tor": "torpaq",
}

RAYON_GROUPS = {
    "all": None,
    "bak": [
        "baki",
        "bakı",
        "binəqədi",
        "qaradağ",
        "xəzər",
        "səbail",
        "sabunçu",
        "suraxanı",
        "nərimanov",
        "nəsimi",
        "nizami",
        "pirallahı",
        "xətai",
        "yasamal",
    ],
    "abs": [
        "abşeron",
        "xırdalan",
        "masazır",
        "mehdiabad",
        "saray",
        "novxanı",
        "fatmayı",
        "hökməli",
        "qobu",
        "güzdək",
        "ceyranbatan",
    ],
    "sum": ["sumqayıt", "sumqayit"],
}


@bot.message_handler(func=lambda m: m.text == "📋 Elan axtar")
def search_menu(message):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("💸 Satılır", callback_data="s_op|sat"),
        types.InlineKeyboardButton("🏢 Kirayə", callback_data="s_op|kir"),
    )
    mk.add(
        types.InlineKeyboardButton("🏡 Günlük", callback_data="s_op|gun"),
        types.InlineKeyboardButton("🌐 Hamısı", callback_data="s_op|all"),
    )
    bot.send_message(
        message.chat.id,
        "🔍 Əməliyyat növü seç:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_op|"))
def cb_search_op(c):
    _, op = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("Yeni tikili", callback_data=f"s_tp|{op}|nt"),
        types.InlineKeyboardButton("Köhnə tikili", callback_data=f"s_tp|{op}|kt"),
    )
    mk.add(
        types.InlineKeyboardButton("Həyət evi", callback_data=f"s_tp|{op}|hey"),
        types.InlineKeyboardButton("Bağ evi", callback_data=f"s_tp|{op}|bag"),
    )
    mk.add(
        types.InlineKeyboardButton("Obyekt", callback_data=f"s_tp|{op}|ob"),
        types.InlineKeyboardButton("Torpaq", callback_data=f"s_tp|{op}|tor"),
    )
    mk.add(
        types.InlineKeyboardButton("Keç (hamısı)", callback_data=f"s_tp|{op}|all"),
    )
    bot.edit_message_text(
        "🏠 Əmlak növü seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_tp|"))
def cb_search_type(c):
    _, op, tp = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "Bütün ərazilər", callback_data=f"s_rn|{op}|{tp}|all"
        )
    )
    mk.add(
        types.InlineKeyboardButton(
            "Bakı rayonları", callback_data=f"s_rn|{op}|{tp}|bak"
        ),
        types.InlineKeyboardButton("Abşeron", callback_data=f"s_rn|{op}|{tp}|abs"),
    )
    mk.add(
        types.InlineKeyboardButton("Sumqayıt", callback_data=f"s_rn|{op}|{tp}|sum"),
    )
    mk.add(
        types.InlineKeyboardButton(
            "Keç (rayon seçmə)", callback_data=f"s_rn|{op}|{tp}|all"
        ),
    )
    bot.edit_message_text(
        "📍 Rayon qrupu seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("s_rn|"))
def cb_search_rayon(c):
    _, op, tp, rn = c.data.split("|")
    # ilk 10 nəticə
    run_structured_search(
        chat_id=c.message.chat.id,
        op_code=op,
        prop_code=tp,
        rayon_group=rn,
        offset=0,
        edit_msg=(c.message.chat.id, c.message.message_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("more|"))
def cb_more_structured(c):
    _, op, tp, rn, off = c.data.split("|")
    run_structured_search(
        chat_id=c.message.chat.id,
        op_code=op,
        prop_code=tp,
        rayon_group=rn,
        offset=int(off),
        edit_msg=None,
    )


def build_filters_sql(op_code, prop_code, rayon_group, table_prefix=""):
    sql = " WHERE 1=1"
    params = []

    op_kws = OP_CODES.get(op_code)
    if op_kws:
        parts = []
        for kw in op_kws:
            parts.append(f"LOWER({table_prefix}operation) LIKE ?")
            params.append(f"%{kw}%")
        sql += " AND (" + " OR ".join(parts) + ")"

    prop_kw = PROP_TYPES.get(prop_code)
    if prop_kw:
        sql += f" AND LOWER({table_prefix}prop_type) LIKE ?"
        params.append(f"%{prop_kw}%")

    kws = RAYON_GROUPS.get(rayon_group)
    if kws:
        conds = []
        for kw in kws:
            like = f"%{kw}%"
            conds.append(f"LOWER({table_prefix}rayon) LIKE ?")
            conds.append(f"LOWER({table_prefix}address) LIKE ?")
            conds.append(f"LOWER({table_prefix}summary) LIKE ?")
            conds.append(f"LOWER({table_prefix}metro) LIKE ?")
            params.extend([like, like, like, like])
        sql += " AND (" + " OR ".join(conds) + ")"

    return sql, params


def run_structured_search(
    chat_id, op_code, prop_code, rayon_group, offset, edit_msg=None
):
    page_size = 10
    results = []

    # MAIN DB
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        base = "SELECT * FROM listings"
        flt, params = build_filters_sql(op_code, prop_code, rayon_group)
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
    flt, params = build_filters_sql(op_code, prop_code, rayon_group)
    sql = base + flt + " ORDER BY date_added DESC, id DESC"
    cur.execute(sql, params)
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    # tarixə görə sort
    results.sort(key=lambda x: safe_date(x), reverse=True)

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
        "kir": "Kirayə",
        "gun": "Günlük",
        "all": "Bütün əməliyyatlar",
    }.get(op_code, "Elanlar")

    title_tp = {
        "nt": "Yeni tikili",
        "kt": "Köhnə tikili",
        "hey": "Həyət evi",
        "bag": "Bağ evi",
        "ob": "Obyekt",
        "tor": "Torpaq",
        "all": "Bütün tiplər",
    }.get(prop_code, "Bütün tiplər")

    title_rn = {
        "all": "Bütün ərazilər",
        "bak": "Bakı rayonları",
        "abs": "Abşeron",
        "sum": "Sumqayıt",
    }.get(rayon_group, "Bütün ərazilər")

    header = f"🔎 {title_op} | {title_tp} | {title_rn}"

    if edit_msg:
        bot.edit_message_text(
            header,
            chat_id=edit_msg[0],
            message_id=edit_msg[1],
        )
    elif offset == 0:
        bot.send_message(chat_id, header)

    for ev in slice_results:
        send_listing_card(
            chat_id,
            ev,
            source=ev.get("__source"),
            with_fav_button=True,
        )

    if offset + page_size < len(results):
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➡️ Daha çox göstər",
                callback_data=f"more|{op_code}|{prop_code}|{rayon_group}|{offset + page_size}",
            )
        )
        bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)


# =============== 🔎 AÇAR SÖZLƏ AXTARIŞ ===============
@bot.message_handler(func=lambda m: m.text == "🔎 Açar sözlə axtarış")
def kw_prompt(message):
    msg = bot.send_message(
        message.chat.id,
        "✍️ Axtarış üçün açar sözlər yaz (məs: *yasamal 2 otaq 600 azn yeni tikili*):",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, do_kw_search_start)


def do_kw_search_start(message):
    chat_id = message.chat.id
    q = (message.text or "").strip().lower()
    if not q:
        bot.send_message(chat_id, "Boş sorğu göndərdin 😅")
        return
    search_state[chat_id] = {"mode": "kw", "query": q}
    run_kw_search(chat_id, offset=0)


@bot.callback_query_handler(func=lambda c: c.data.startswith("morekw|"))
def cb_more_kw(c):
    chat_id = c.message.chat.id
    _, off = c.data.split("|")
    run_kw_search(chat_id, offset=int(off))


def run_kw_search(chat_id, offset):
    if chat_id not in search_state or search_state[chat_id].get("mode") != "kw":
        bot.send_message(chat_id, "Axtarış sorğusu tapılmadı. Yenidən yazın.")
        return

    q = search_state[chat_id]["query"]
    page_size = 10
    results = []

    # MAIN DB
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        like = f"%{q}%"
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
            """,
            (like, like, like, like, like, like),
        )
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    # LOCAL APPROVED
    conn = get_local_conn()
    cur = conn.cursor()
    like = f"%{q}%"
    cur.execute(
        """
        SELECT * FROM listings_approved
        WHERE
            LOWER(prop_type) LIKE ?
            OR LOWER(operation) LIKE ?
            OR LOWER(rayon) LIKE ?
            OR LOWER(metro) LIKE ?
            OR LOWER(rooms) LIKE ?
            OR LOWER(summary) LIKE ?
        ORDER BY date_added DESC, id DESC
        """,
        (like, like, like, like, like, like),
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    results.sort(key=lambda x: safe_date(x), reverse=True)

    if not results and offset == 0:
        bot.send_message(chat_id, "😕 Uyğun elan tapılmadı.")
        return
    if offset >= len(results):
        bot.send_message(chat_id, "✅ Bütün nəticələr göstərildi.")
        return

    if offset == 0:
        bot.send_message(
            chat_id, f"🔍 Nəticələr tapıldı: {len(results)} elan (ilk 10 göstərilir)"
        )

    slice_results = results[offset : offset + page_size]
    for ev in slice_results:
        send_listing_card(
            chat_id,
            ev,
            source=ev.get("__source"),
            with_fav_button=True,
        )

    if offset + page_size < len(results):
        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton(
                "➡️ Daha çox göstər",
                callback_data=f"morekw|{offset + page_size}",
            )
        )
        bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)


# =============== 📞 NÖMRƏ İLƏ AXTARIŞ ===============
@bot.message_handler(func=lambda m: m.text == "📞 Nömrə ilə axtarış")
def phone_search_start(message):
    msg = bot.send_message(
        message.chat.id,
        "📱 Axtarış üçün nömrəni bu formatda yaz: 050xxxxxxx, 070xxxxxxx, 055xxxxxxx və s.",
    )
    bot.register_next_step_handler(msg, phone_search_do)


def phone_search_do(message):
    phone = (message.text or "").strip().replace(" ", "")
    chat_id = message.chat.id

    if not phone.isdigit() or len(phone) not in [9, 10]:
        bot.send_message(
            chat_id,
            "⚠️ Nömrə formatı düzgün deyil. Zəhmət olmasa 050xxxxxxx formatında yaz.",
        )
        return

    # 9 rəqəmli yazılıbsa, 0 əlavə et
    if len(phone) == 9:
        phone = "0" + phone

    results = []

    # Əsas baza
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM listings WHERE REPLACE(phone,' ','') LIKE ? ORDER BY date_read DESC",
            (f"%{phone}%",),
        )
        for r in cur.fetchall():
            d = dict(r)
            d["__source"] = "main"
            results.append(d)
        conn.close()

    # Lokal baza (təsdiqlənmişlər)
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM listings_approved WHERE REPLACE(phone,' ','') LIKE ? ORDER BY date_added DESC",
        (f"%{phone}%",),
    )
    for r in cur.fetchall():
        d = dict(r)
        d["__source"] = "local"
        results.append(d)
    conn.close()

    if not results:
        mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
        mk.add("📞 Nömrə ilə axtarış", "🔎 Açar sözlə axtarış")
        bot.send_message(
            chat_id,
            "❌ Bu nömrə ilə heç bir elan tapılmadı.",
            reply_markup=mk,
        )
        return

    bot.send_message(chat_id, f"✅ {len(results)} elan tapıldı. Aşağıda göstərilir:")
    for ev in results:
        send_listing_card(chat_id, ev, source=ev.get("__source"))


# =============== 📊 ADMIN PANEL ===============
@bot.message_handler(func=lambda m: m.text == "📊 Admin Panel")
def admin_panel(message):
    if not is_admin(message.chat.id):
        return
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "✅ Təsdiqlənməyən elanlar", callback_data="admin_pending"
        )
    )
    mk.add(
        types.InlineKeyboardButton("📊 Statistik hesabat", callback_data="admin_stats")
    )
    mk.add(
        types.InlineKeyboardButton(
            "📤 Vasitəçilərə bildiriş", callback_data="admin_agents_msg"
        )
    )
    mk.add(
        types.InlineKeyboardButton("🔎 Axtar (əsas baza)", callback_data="admin_search")
    )
    bot.send_message(message.chat.id, "🛠 Admin Panel:", reply_markup=mk)


def send_pending_listings(chat_id):
    conn = get_local_conn()
    conn.row_factory = sqlite3.Row
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
            types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"aprv_{ev['id']}"),
            types.InlineKeyboardButton("❌ Sil", callback_data=f"del_{ev['id']}"),
        )
        bot.send_message(chat_id, txt, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data == "admin_pending")
def cb_admin_pending(c):
    if not is_admin(c.message.chat.id):
        return
    send_pending_listings(c.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("aprv_"))
def cb_approve(c):
    if not is_admin(c.message.chat.id):
        return
    lid = int(c.data.split("_")[1])

    row = approve_listing(lid)
    if not row:
        bot.answer_callback_query(c.id, "Tapılmadı və ya artıq təsdiqlənib.")
        return

    # Elan sahibinə xəbər
    try:
        if row.get("chat_id"):
            bot.send_message(
                row["chat_id"],
                "🎉 Elanınız təsdiqləndi və artıq sistemdə aktivdir.",
            )
    except:
        pass

    # Kanala paylaşım
    try:
        if CHANNEL_ID:
            send_listing_card(
                CHANNEL_ID,
                row,
                source="local",
                with_fav_button=False,
            )
    except Exception as e:
        print("Kanal paylaşım xətası:", e)

    bot.answer_callback_query(c.id, "✅ Elan təsdiq olundu.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def cb_delete(c):
    if not is_admin(c.message.chat.id):
        return
    lid = int(c.data.split("_")[1])
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM listings_new WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    bot.answer_callback_query(c.id, "❌ Elan silindi.")


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def cb_admin_stats(c):
    if not is_admin(c.message.chat.id):
        return

    total_main = 0
    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM listings")
            total_main = cur.fetchone()[0]
        except:
            total_main = 0
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM listings_approved")
    total_local = cur.fetchone()[0]

    total = total_main + total_local

    txt = f"📊 *Statistik hesabat*\n\nToplam elan (əsas + lokal): *{total}*\n"
    txt += f"• Əsas baza (besthome.db): {total_main}\n"
    txt += f"• Bot vasitəsilə təsdiqlənənlər: {total_local}\n"

    bot.send_message(c.message.chat.id, txt, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda c: c.data == "admin_agents_msg")
def cb_agents_msg(c):
    if not is_admin(c.message.chat.id):
        return
    msg = bot.send_message(c.message.chat.id, "✍️ Vasitəçilərə göndəriləcək mesajı yaz:")
    bot.register_next_step_handler(msg, do_agents_broadcast)


def do_agents_broadcast(message):
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    if not text:
        bot.send_message(message.chat.id, "Boş mesaj göndərilə bilməz.")
        return

    conn = get_local_conn()
    cur = conn.cursor
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM agents")
    agents = [r[0] for r in cur.fetchall()]
    conn.close()

    sent = 0
    for cid in agents:
        try:
            bot.send_message(cid, f"📢 Yeni bildiriş:\n{text}")
            sent += 1
        except:
            pass

    bot.send_message(message.chat.id, f"✅ Bildiriş {sent} vasitəçiyə göndərildi.")


@bot.callback_query_handler(func=lambda c: c.data == "admin_search")
def cb_admin_search(c):
    if not is_admin(c.message.chat.id):
        return
    msg = bot.send_message(
        c.message.chat.id, "🔎 Əsas bazada açar sözlə axtarış üçün sorğu yaz:"
    )
    bot.register_next_step_handler(msg, do_admin_search)


def do_admin_search(message):
    if not is_admin(message.chat.id):
        return
    kw = (message.text or "").strip().lower()
    if not kw:
        bot.send_message(message.chat.id, "Boş sorğu.")
        return

    if not os.path.exists(MAIN_DB):
        bot.send_message(message.chat.id, "Əsas baza (besthome.db) mövcud deyil.")
        return

    conn = get_main_conn()
    cur = conn.cursor()
    like = f"%{kw}%"
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
        LIMIT 100
        """,
        (like, like, like, like, like, like),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "Heç nə tapılmadı.")
        return

    bot.send_message(message.chat.id, f"🔍 Nəticələr ({len(rows)} ədəd):")
    for r in rows:
        send_listing_card(message.chat.id, dict(r), source="main")


# =============== ℹ️ HAQQINDA ===============
@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    text = (
        "🏠 *Best Home Əmlak Botu*\n"
        "• 📝 Yeni elan əlavə et (vasitəçi / ev sahibi)\n"
        "• 📋 Filtrlərlə elan axtar (əməliyyat + tip + rayon)\n"
        "• 🔎 Açar sözlə axtarış (mətn üzrə)\n"
        "• ⭐ Favorilərim — saxladığın elanlar\n"
        "• 📋 Elanlarım — öz elanlarının statusu\n"
        "• 📊 Admin Panel — yalnız admin üçün\n"
        "• 📢 Təsdiqlənən elanlar avtomatik kanala paylaşılır\n"
        "✅ Tək bot, tam emlak ekosistemi."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


# =============== RUN (Render üçün) ===============
if __name__ == "__main__":
    print("⚙️ BestHome Unified Bot FULL v8 işə düşür...")
    ensure_main_db()
    init_local_db()
    init_main_indices()

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "✅ BestHome Bot is running."

    def run_flask():
        port = int(os.environ.get("PORT", 10000))
        app.run(host="0.0.0.0", port=port)

    def run_bot():
        bot.infinity_polling(skip_pending=True)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=run_bot, daemon=True).start()

    # Render logları üçün blokda saxlayırıq
    threading.Event().wait()
