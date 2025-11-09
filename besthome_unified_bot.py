# ============================================
# 🏠 BestHome Unified Bot — Full v7
# Elan əlavə • Elan axtarış • Favori • Admin Panel
# Dual DB (besthome.db + local_data.db)
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

# ================== KONFİQURASİYA ==================
BOT_TOKEN = "6202216323:AAEOWdglrcYTJfCr9oRSJtufjsNAkaLWyTc"
ADMIN_ID = 1311851277
CHANNEL_ID = -1001878623087  # Kanal ID (bot kanalda admin olmalıdır!)

MAIN_DB = "besthome.db"  # Gündəlik yenilənən böyük baza
LOCAL_DB = "local_data.db"  # Sabit baza (yeni elanlar, təsdiqlər, agentlər, favorilər)

# Əgər Render-də main db zip şəklindədirsə, burdan çəkə bilərsən (istəyirsənsə istifadə et)
DROPBOX_ZIP_URL = None  # "https://.../besthome.zip?dl=1"

bot = telebot.TeleBot(BOT_TOKEN)
user_state = {}  # istifadəçi addım prosesi (yeni elan)
search_state = {}  # "daha çox göstər" üçün yaddaş


# ================== DB YARDIMÇI ==================
def ensure_main_db():
    if os.path.exists(MAIN_DB):
        print("📦 Mövcud besthome.db tapıldı.")
        return
    if not DROPBOX_ZIP_URL:
        print("⚠️ besthome.db yoxdur və DROPBOX_ZIP_URL təyin edilməyib.")
        return
    print("⬇️ besthome.zip endirilir...")
    r = requests.get(DROPBOX_ZIP_URL)
    if r.status_code == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                z.extractall(".")
            print("✅ besthome.db ZIP-dən çıxarıldı.")
        except zipfile.BadZipFile:
            print("❌ ZIP formatı səhvdir.")
    else:
        print(f"❌ Endirmə alınmadı: {r.status_code}")


def get_main_conn():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_local_conn():
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_local_db():
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

    # Təsdiqlənmiş elanlar (lokal yaddaş üçün)
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

    conn.commit()
    conn.close()


def init_main_db_indices():
    """Əsas bazada axtarışı sürətləndirmək üçün indexlər (yoxdursa yaradır)."""
    if not os.path.exists(MAIN_DB):
        return
    conn = get_main_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_main_operation ON listings(operation)"
        )
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


# ================== UTIL FUNKSİYALAR ==================
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


def clean_text(x):
    return (x or "").strip()


def send_logo(chat_id):
    if os.path.exists("besthomelogo.jpeg"):
        try:
            with open("besthomelogo.jpeg", "rb") as f:
                bot.send_photo(chat_id, f)
        except:
            pass


def build_main_menu(chat_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 Yeni elan əlavə et", "📋 Elan axtar")
    kb.add("🔎 Açar sözlə axtarış", "⭐ Favorilərim")
    kb.add("📋 Elanlarım", "♻️ Sıfırla")
    kb.add("ℹ️ Haqqında", "📞 Adminlə əlaqə")
    if is_admin(chat_id):
        kb.add("📊 Admin Panel")
    return kb


def send_listing_card(chat_id, ev: dict, source: str = None, show_fav: bool = True):
    """
    ev — dict (main listings, listings_approved və ya listings_new)
    source — 'main' və ya 'local' (favorit üçün)
    """
    date_val = clean_text(
        ev.get("date_read")
        or ev.get("date_added")
        or datetime.now().strftime("%Y-%m-%d")
    )

    prop = clean_text(ev.get("prop_type"))
    rooms = clean_text(ev.get("rooms"))
    op = clean_text(ev.get("operation"))
    price = format_price(ev.get("price"))
    cur = clean_text(ev.get("currency") or "AZN")
    metro = clean_text(ev.get("metro"))
    rayon = clean_text(ev.get("rayon"))
    addr = clean_text(ev.get("address"))
    phone = clean_text(ev.get("phone"))
    name = clean_text(ev.get("contact_name"))
    summ = clean_text(ev.get("summary"))
    link = clean_text(ev.get("link") or ev.get("source_link"))

    loc = ""
    if rayon:
        loc += rayon
    if metro:
        if loc:
            loc += " — "
        loc += metro
    if not loc and addr:
        loc = addr

    txt = (
        f"📅 {date_val}\n"
        f"🏠 {rooms} {prop}\n"
        f"💸 {op} | 💰 {price} {cur}\n"
        f"📍 {loc or '-'}\n"
        f"📞 {phone} ({name})\n"
        f"🧾 {summ or '-'}"
    )

    mk = types.InlineKeyboardMarkup()
    if link:
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))
    if show_fav and source and ev.get("id"):
        mk.add(
            types.InlineKeyboardButton(
                "⭐ Favorilərə əlavə et",
                callback_data=f"fav|{source}|{ev['id']}",
            )
        )

    bot.send_message(chat_id, txt, reply_markup=mk)


# ================== /start + xoş gəldin ==================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    send_logo(chat_id)
    text = (
        "👋 *BestHome Unified Bot-a xoş gəlmisən!*\n\n"
        "Bu bot vasitəsilə:\n"
        "• 📝 Elan əlavə edə bilərsən (vasitəçi və ya ev sahibi kimi)\n"
        "• 📋 Filtrlərlə elan axtara bilərsən\n"
        "• 🔎 Açar sözlə istənilən formada axtarış edə bilərsən\n"
        "• ⭐ Seçilmiş elanları Favorilərim-də saxlaya bilərsən\n"
        "• 📋 Öz elanlarını və statuslarını izləyə bilərsən\n"
        "• 📊 Admin üçün ayrıca idarəetmə paneli mövcuddur\n\n"
        "Başlamaq üçün menyudan seçim et ⬇️"
    )
    bot.send_message(
        chat_id, text, parse_mode="Markdown", reply_markup=build_main_menu(chat_id)
    )


# ================== ♻️ Sıfırla ==================
@bot.message_handler(func=lambda m: m.text == "♻️ Sıfırla")
def reset_flow(message):
    chat_id = message.chat.id
    user_state.pop(chat_id, None)
    search_state.pop(chat_id, None)
    bot.send_message(
        chat_id,
        "✅ Bütün aktiv proseslər sıfırlandı. Yenidən seçim edə bilərsən.",
        reply_markup=build_main_menu(chat_id),
    )


# ================== 📞 Adminlə əlaqə ==================
@bot.message_handler(func=lambda m: m.text == "📞 Adminlə əlaqə")
def contact_admin(message):
    bot.send_message(
        message.chat.id,
        "📞 Adminlə əlaqə üçün: @esedovesed\n"
        "Hər hansı texniki problem, təklif və ya əməkdaşlıq üçün yaza bilərsən.",
    )


# ================== 📝 Yeni elan əlavə et (FSM) ==================
@bot.message_handler(func=lambda m: m.text == "📝 Yeni elan əlavə et")
def new_listing_start(message):
    chat_id = message.chat.id
    user_state[chat_id] = {"step": "role", "chat_id": chat_id}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Vasitəçi", "Əmlak sahibi")
    kb.add("♻️ Sıfırla")
    bot.send_message(
        chat_id, "👤 Siz vasitəçisiniz, yoxsa əmlak sahibi?", reply_markup=kb
    )


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "role")
def step_role(message):
    chat_id = message.chat.id
    choice = message.text.strip()
    if choice not in ["Vasitəçi", "Əmlak sahibi"]:
        bot.send_message(chat_id, "Zəhmət olmasa seçim edin: Vasitəçi / Əmlak sahibi")
        return
    st = user_state[chat_id]
    st["role"] = choice

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    props = [
        "Yeni tikili",
        "Köhnə tikili",
        "Həyət evi",
        "Obyekt",
        "Torpaq",
        "Villa",
        "Bağ evi",
    ]
    for i in range(0, len(props), 2):
        kb.add(*props[i : i + 2])
    kb.add("♻️ Sıfırla")
    bot.send_message(chat_id, "🏠 Əmlak növünü seç:", reply_markup=kb)
    st["step"] = "prop_type"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "prop_type"
)
def step_prop_type(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["prop_type"] = message.text.strip()

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Satılır", "Kirayə", "Günlük")
    kb.add("♻️ Sıfırla")
    bot.send_message(chat_id, "💸 Əməliyyat növünü seç:", reply_markup=kb)
    st["step"] = "operation"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "operation"
)
def step_operation(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["operation"] = message.text.strip()

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
    kb.add("♻️ Sıfırla")
    bot.send_message(chat_id, "📍 Rayon seç:", reply_markup=kb)
    st["step"] = "rayon"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "rayon"
)
def step_rayon(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["rayon"] = message.text.strip()

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
    kb.add("♻️ Sıfırla")
    bot.send_message(chat_id, "🚇 Metro seç:", reply_markup=kb)
    st["step"] = "metro"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "metro"
)
def step_metro(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["metro"] = message.text.strip()
    bot.send_message(chat_id, "🔢 Otaq sayı (məs: 2, 3, 4):")
    st["step"] = "rooms"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "rooms"
)
def step_rooms(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["rooms"] = message.text.strip()
    bot.send_message(chat_id, "📏 Sahə (m²):")
    st["step"] = "area_kvm"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "area_kvm"
)
def step_area(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["area_kvm"] = message.text.strip()
    bot.send_message(chat_id, "💰 Qiymət:")
    st["step"] = "price"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "price"
)
def step_price(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["price"] = message.text.strip()

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("AZN", "USD")
    kb.add("♻️ Sıfırla")
    bot.send_message(chat_id, "💱 Valyuta:", reply_markup=kb)
    st["step"] = "currency"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "currency"
)
def step_currency(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["currency"] = message.text.strip()
    bot.send_message(chat_id, "📞 Əlaqə nömrəsi:")
    st["step"] = "phone"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "phone"
)
def step_phone(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["phone"] = message.text.strip()
    bot.send_message(chat_id, "👤 Əlaqədar şəxsin adı:")
    st["step"] = "contact_name"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "contact_name"
)
def step_contact_name(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["contact_name"] = message.text.strip()
    bot.send_message(chat_id, "🧾 Qısa təsvir yaz:")
    st["step"] = "summary"


@bot.message_handler(
    func=lambda m: user_state.get(m.chat.id, {}).get("step") == "summary"
)
def step_summary(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    st["summary"] = message.text.strip()

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Link yoxdur, elanı göndər ✅")
    kb.add("♻️ Sıfırla")
    bot.send_message(
        chat_id,
        "🔗 Əgər elan linki varsa (bina.az, tap.az və s.) göndər.\n"
        "Yoxdursa *Link yoxdur, elanı göndər ✅* seç:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    st["step"] = "link"


@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "link")
def step_link(message):
    chat_id = message.chat.id
    st = user_state[chat_id]
    txt = message.text.strip()

    if txt.startswith("http"):
        st["link"] = txt
    elif txt != "Link yoxdur, elanı göndər ✅":
        bot.send_message(
            chat_id, "⚠️ Düzgün link yaz və ya 'Link yoxdur, elanı göndər ✅' seç."
        )
        return

    # Vasitəçini qeyd al
    if st.get("role") == "Vasitəçi":
        conn = get_local_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO agents (chat_id, role, phone, name)
            VALUES (?, ?, ?, ?)
        """,
            (chat_id, st.get("role"), st.get("phone"), st.get("contact_name")),
        )
        conn.commit()
        conn.close()

    # listings_new-ə yaz
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
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            chat_id,
            st.get("role"),
            st.get("prop_type"),
            st.get("operation"),
            st.get("rayon"),
            st.get("metro"),
            st.get("rooms"),
            st.get("area_kvm"),
            st.get("price"),
            st.get("currency"),
            st.get("phone"),
            st.get("contact_name"),
            st.get("summary"),
            st.get("link", ""),
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Adminə preview
    preview = (
        f"📢 *Yeni elan (GÖZLƏMƏDƏ)* [ID: {new_id}]\n\n"
        f"👤 {st['role']}\n"
        f"🏠 {st['prop_type']} | {st['rooms']}\n"
        f"💸 {st['operation']} | 💰 {format_price(st['price'])} {st['currency']}\n"
        f"📍 {st['rayon']} — {st['metro']}\n"
        f"📞 {st['phone']} ({st['contact_name']})\n"
        f"🧾 {st['summary']}"
    )
    if st.get("link"):
        preview += f"\n🔗 {st['link']}"

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"aprv_{new_id}"),
        types.InlineKeyboardButton("❌ Sil", callback_data=f"del_{new_id}"),
    )
    try:
        bot.send_message(ADMIN_ID, preview, parse_mode="Markdown", reply_markup=mk)
    except:
        pass

    bot.send_message(
        chat_id,
        "✅ Elan uğurla əlavə olundu.\n" "⏳ Hal-hazırda *admin təsdiqini gözləyir.*",
        parse_mode="Markdown",
        reply_markup=build_main_menu(chat_id),
    )

    user_state.pop(chat_id, None)


# ================== 📋 Elanlarım ==================
@bot.message_handler(func=lambda m: m.text == "📋 Elanlarım")
def my_listings(message):
    chat_id = message.chat.id
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM listings_new WHERE chat_id=? ORDER BY id DESC",
        (chat_id,),
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
        ev_txt = (
            f"{status}\n"
            f"🏠 {ev.get('prop_type','-')} | {ev.get('rooms','-')}\n"
            f"💸 {ev.get('operation','-')} | 💰 {format_price(ev.get('price'))} {ev.get('currency','AZN')}\n"
            f"📍 {ev.get('rayon','-')} — {ev.get('metro','')}\n"
            f"🧾 {ev.get('summary','-')}"
        )
        if ev.get("link"):
            ev_txt += f"\n🔗 {ev['link']}"
        bot.send_message(chat_id, ev_txt)


# ================== ⭐ Favorilərim ==================
@bot.message_handler(func=lambda m: m.text == "⭐ Favorilərim")
def show_favorites(message):
    chat_id = message.chat.id
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT listing_id, source, added_at
        FROM favorites
        WHERE chat_id=?
        ORDER BY added_at DESC
        LIMIT 50
    """,
        (chat_id,),
    )
    favs = cur.fetchall()
    conn.close()

    if not favs:
        bot.send_message(chat_id, "⭐ Favori siyahın boşdur.")
        return

    bot.send_message(chat_id, f"⭐ Favorilər ({len(favs)} ədəd):")

    for f in favs:
        lid = f["listing_id"]
        src = f["source"]

        if src == "main":
            if not os.path.exists(MAIN_DB):
                continue
            conn_m = get_main_conn()
            cur_m = conn_m.cursor()
            cur_m.execute("SELECT * FROM listings WHERE id=?", (lid,))
            row = cur_m.fetchone()
            conn_m.close()
            if not row:
                continue
            send_listing_card(chat_id, dict(row), source="main", show_fav=False)

        elif src == "local":
            conn_l = get_local_conn()
            cur_l = conn_l.cursor()
            cur_l.execute("SELECT * FROM listings_approved WHERE id=?", (lid,))
            row = cur_l.fetchone()
            conn_l.close()
            if not row:
                continue
            send_listing_card(chat_id, dict(row), source="local", show_fav=False)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav|"))
def cb_favorite(c):
    try:
        _, src, sid = c.data.split("|")
        listing_id = int(sid)
    except:
        bot.answer_callback_query(c.id, "Xəta.")
        return

    chat_id = c.from_user.id

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO favorites (chat_id, listing_id, source, added_at)
        VALUES (?, ?, ?, ?)
    """,
        (chat_id, listing_id, src, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "✅ Favorilərə əlavə olundu.")


# ================== 📋 Elan axtar (filtrlə + daha çox) ==================
@bot.message_handler(func=lambda m: m.text == "📋 Elan axtar")
def search_menu(message):
    if not os.path.exists(MAIN_DB):
        bot.send_message(message.chat.id, "⚠️ Əsas baza hazır deyil.")
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("💸 Satılır", callback_data="sop|sat"),
        types.InlineKeyboardButton("🏢 Kirayə", callback_data="sop|kir"),
    )
    mk.add(
        types.InlineKeyboardButton("🏡 Günlük", callback_data="sop|gun"),
        types.InlineKeyboardButton("🌐 Hamısı", callback_data="sop|all"),
    )
    bot.send_message(message.chat.id, "🔍 Əməliyyat növü seç:", reply_markup=mk)


OP_MAP = {
    "sat": "satıl",
    "kir": "kiray",
    "gun": "günlük",
    "all": None,
}


@bot.callback_query_handler(func=lambda c: c.data.startswith("sop|"))
def cb_search_op(c):
    _, op = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Bütün ərazilər", callback_data=f"sray|{op}|all"))
    mk.add(types.InlineKeyboardButton("Bakı rayonları", callback_data=f"sray|{op}|bak"))
    mk.add(
        types.InlineKeyboardButton("Abşeron / Sumqayıt", callback_data=f"sray|{op}|abs")
    )
    bot.edit_message_text(
        "📍 Ərazi seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("sray|"))
def cb_search_rayon(c):
    _, op, area = c.data.split("|")
    mk = types.InlineKeyboardMarkup()

    if area == "all":
        add_price_buttons(mk, op, "all")
    elif area == "bak":
        # Bakı rayonları
        for code, name in [
            ("yas", "Yasamal"),
            ("nes", "Nəsimi"),
            ("nar", "Nərimanov"),
            ("xet", "Xətai"),
            ("seb", "Səbail"),
            ("bin", "Binəqədi"),
            ("sab", "Sabunçu"),
            ("sur", "Suraxanı"),
            ("niz", "Nizami"),
            ("xez", "Xəzər"),
            ("qar", "Qaradağ"),
            ("pir", "Pirallahı"),
        ]:
            mk.add(types.InlineKeyboardButton(name, callback_data=f"sray2|{op}|{code}"))
    elif area == "abs":
        mk.add(types.InlineKeyboardButton("Abşeron", callback_data=f"sray2|{op}|abs"))
        mk.add(types.InlineKeyboardButton("Sumqayıt", callback_data=f"sray2|{op}|sum"))

    bot.edit_message_text(
        "📍 Dəqiq rayon seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


RAYON_FILTER = {
    "yas": "yasamal",
    "nes": "nəsimi",
    "nar": "nərimanov",
    "xet": "xətai",
    "seb": "səbail",
    "bin": "binəqədi",
    "sab": "sabunçu",
    "sur": "suraxanı",
    "niz": "nizami",
    "xez": "xəzər",
    "qar": "qaradağ",
    "pir": "pirallahı",
    "abs": "abşeron",
    "sum": "sumqayıt",
    "all": None,
}

PRICE_RANGES = {
    "p0": (None, None),
    "p1": (0, 500),
    "p2": (500, 1000),
    "p3": (1000, 2000),
    "p4": (2000, None),
}


def add_price_buttons(mk, op, rayon_code):
    mk.add(
        types.InlineKeyboardButton("Hamısı", callback_data=f"spr|{op}|{rayon_code}|p0"),
        types.InlineKeyboardButton("0-500", callback_data=f"spr|{op}|{rayon_code}|p1"),
    )
    mk.add(
        types.InlineKeyboardButton(
            "500-1000", callback_data=f"spr|{op}|{rayon_code}|p2"
        ),
        types.InlineKeyboardButton(
            "1000-2000", callback_data=f"spr|{op}|{rayon_code}|p3"
        ),
    )
    mk.add(
        types.InlineKeyboardButton("2000+", callback_data=f"spr|{op}|{rayon_code}|p4"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("spr|"))
def cb_search_price(c):
    _, op, rayon_code, pcode = c.data.split("|")
    run_filtered_search(
        c.message.chat.id,
        op,
        rayon_code,
        pcode,
        0,
        edit_msg=(c.message.chat.id, c.message.message_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("more|"))
def cb_search_more(c):
    _, op, rayon_code, pcode, off = c.data.split("|")
    run_filtered_search(
        c.message.chat.id, op, rayon_code, pcode, int(off), edit_msg=None
    )


def run_filtered_search(
    chat_id, op_code, rayon_code, price_code, offset, edit_msg=None, limit=10
):
    if not os.path.exists(MAIN_DB):
        if edit_msg:
            bot.edit_message_text(
                "⚠️ Əsas baza yoxdur.", chat_id=edit_msg[0], message_id=edit_msg[1]
            )
        else:
            bot.send_message(chat_id, "⚠️ Əsas baza yoxdur.")
        return

    conn = get_main_conn()
    cur = conn.cursor()

    sql = "SELECT * FROM listings WHERE 1=1"
    params = []

    # operation
    op_kw = OP_MAP.get(op_code)
    if op_kw:
        sql += " AND LOWER(operation) LIKE ?"
        params.append(f"%{op_kw}%")

    # rayon filter (address/summary/metro daxilində)
    rayon_kw = RAYON_FILTER.get(rayon_code)
    if rayon_kw:
        like = f"%{rayon_kw}%"
        sql += " AND (LOWER(address) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(metro) LIKE ?)"
        params.extend([like, like, like])

    # price
    mn, mx = PRICE_RANGES.get(price_code, (None, None))
    if mn is not None:
        sql += " AND CAST(price AS INTEGER) >= ?"
        params.append(mn)
    if mx is not None:
        sql += " AND CAST(price AS INTEGER) <= ?"
        params.append(mx)

    sql += " ORDER BY date_read DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    if not rows and offset == 0:
        msg = "😕 Uyğun elan tapılmadı."
        if edit_msg:
            bot.edit_message_text(msg, chat_id=edit_msg[0], message_id=edit_msg[1])
        else:
            bot.send_message(chat_id, msg)
        return
    elif not rows:
        bot.send_message(chat_id, "✅ Bütün uyğun elanlar göstərildi.")
        return

    if edit_msg:
        bot.edit_message_text(
            "🔎 Axtarış nəticələri:", chat_id=edit_msg[0], message_id=edit_msg[1]
        )
    elif offset == 0:
        bot.send_message(chat_id, "🔎 Axtarış nəticələri:")

    for r in rows:
        send_listing_card(chat_id, dict(r), source="main", show_fav=True)

    # Daha çox göstər
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ Daha çox göstər",
            callback_data=f"more|{op_code}|{rayon_code}|{price_code}|{offset + limit}",
        )
    )
    bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)


# ================== 🔎 Açar sözlə axtarış ==================
@bot.message_handler(func=lambda m: m.text == "🔎 Açar sözlə axtarış")
def ask_keyword_search(message):
    msg = bot.send_message(
        message.chat.id,
        "✍️ Axtarış üçün açar söz(lər) yaz:\nMəsələn: `yasamal 2 otaq 600 azn yeni tikili`",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, do_keyword_search)


def do_keyword_search(message):
    chat_id = message.chat.id
    query = message.text.strip().lower()
    if not query:
        bot.send_message(chat_id, "Boş sorğu göndərdin.")
        return

    like = f"%{query}%"

    results = []

    if os.path.exists(MAIN_DB):
        conn = get_main_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM listings
            WHERE LOWER(prop_type || ' ' || operation || ' ' || metro || ' ' ||
                        rooms || ' ' || address || ' ' || summary || ' ' ||
                        IFNULL(price,'') || ' ' || IFNULL(currency,''))
                  LIKE ?
            ORDER BY date_read DESC, id DESC
            LIMIT 30
        """,
            (like,),
        )
        results += [("main", dict(r)) for r in cur.fetchall()]
        conn.close()

    # lokal təsdiqlənmişlər
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM listings_approved
        WHERE LOWER(prop_type || ' ' || operation || ' ' || metro || ' ' ||
                    rooms || ' ' || rayon || ' ' || summary || ' ' ||
                    IFNULL(price,'') || ' ' || IFNULL(currency,''))
              LIKE ?
        ORDER BY id DESC
        LIMIT 20
    """,
        (like,),
    )
    results += [("local", dict(r)) for r in cur.fetchall()]
    conn.close()

    if not results:
        bot.send_message(chat_id, "😕 Heç nə tapılmadı.")
        return

    bot.send_message(chat_id, f"🔍 Tapıldı: {len(results)} elan.")

    for src, ev in results:
        send_listing_card(chat_id, ev, source=src, show_fav=True)


# ================== 📊 Admin Panel ==================
@bot.message_handler(func=lambda m: m.text == "📊 Admin Panel")
def admin_panel(message):
    if not is_admin(message.chat.id):
        return
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("⏳ Gözləyən elanlar", callback_data="admin_pending")
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
        types.InlineKeyboardButton("🔎 Əsas bazada axtar", callback_data="admin_search")
    )
    bot.send_message(message.chat.id, "🛠 Admin Panel:", reply_markup=mk)


def send_pending_listings(chat_id):
    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_new WHERE approved=0 ORDER BY id DESC LIMIT 30")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "✅ Gözləyən elan yoxdur.")
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
def cb_admin_approve(c):
    if not is_admin(c.message.chat.id):
        return
    lid = int(c.data.split("_")[1])

    conn = get_local_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_new WHERE id=? AND approved=0", (lid,))
    row = cur.fetchone()
    if not row:
        bot.answer_callback_query(c.id, "Tapılmadı və ya artıq təsdiqlənib.")
        conn.close()
        return

    ev = dict(row)

    # listings_approved-ə əlavə et
    cur.execute(
        """
        INSERT INTO listings_approved (
            date_added, chat_id, role, prop_type, operation,
            rayon, metro, rooms, area_kvm, price, currency,
            phone, contact_name, summary, link
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            ev["date_added"],
            ev["chat_id"],
            ev["role"],
            ev["prop_type"],
            ev["operation"],
            ev["rayon"],
            ev["metro"],
            ev["rooms"],
            ev["area_kvm"],
            ev["price"],
            ev["currency"],
            ev["phone"],
            ev["contact_name"],
            ev["summary"],
            ev["link"],
        ),
    )

    cur.execute("UPDATE listings_new SET approved=1 WHERE id=?", (lid,))
    conn.commit()
    conn.close()

    # sahibinə mesaj
    try:
        bot.send_message(ev["chat_id"], "🎉 Elanınız təsdiqləndi və artıq aktivdir.")
    except:
        pass

    # Kanala paylaşım
    try:
        send_listing_card(CHANNEL_ID, ev, source="local", show_fav=False)
    except:
        pass

    bot.answer_callback_query(c.id, "✅ Elan təsdiqləndi.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def cb_admin_delete(c):
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
        cur.execute("SELECT COUNT(*) FROM listings")
        total_main = cur.fetchone()[0]
        conn.close()

    conn = get_local_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM listings_approved")
    total_local = cur.fetchone()[0]

    (
        cur.execute(
            """
        SELECT LOWER(operation), COUNT(*) FROM listings
        GROUP BY LOWER(operation)
    """
        )
        if os.path.exists(MAIN_DB)
        else None
    )

    txt = (
        "📊 *Statistik hesabat*\n\n"
        f"Əsas baza elanları: *{total_main}*\n"
        f"Bot vasitəsilə təsdiqlənən elanlar: *{total_local}*\n"
    )

    bot.send_message(c.message.chat.id, txt, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda c: c.data == "admin_agents_msg")
def cb_admin_agents_msg(c):
    if not is_admin(c.message.chat.id):
        return
    msg = bot.send_message(c.message.chat.id, "✍️ Vasitəçilərə göndəriləcək mesajı yaz:")
    bot.register_next_step_handler(msg, do_agents_broadcast)


def do_agents_broadcast(message):
    if not is_admin(message.chat.id):
        return
    text = message.text.strip()
    if not text:
        bot.send_message(message.chat.id, "Boş mesaj göndərilə bilməz.")
        return

    conn = get_local_conn()
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
    msg = bot.send_message(c.message.chat.id, "🔎 Əsas bazada açar sözlə axtar:")
    bot.register_next_step_handler(msg, do_admin_search)


def do_admin_search(message):
    if not is_admin(message.chat.id):
        return
    kw = message.text.strip().lower()
    if not kw:
        bot.send_message(message.chat.id, "Boş sorğu.")
        return
    if not os.path.exists(MAIN_DB):
        bot.send_message(message.chat.id, "Əsas baza yoxdur.")
        return

    like = f"%{kw}%"
    conn = get_main_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM listings
        WHERE LOWER(prop_type || ' ' || operation || ' ' || metro || ' ' ||
                    rooms || ' ' || address || ' ' || summary)
              LIKE ?
        ORDER BY date_read DESC, id DESC
        LIMIT 50
    """,
        (like,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "Heç nə tapılmadı.")
        return

    bot.send_message(message.chat.id, f"🔍 {len(rows)} nəticə tapıldı:")
    for r in rows:
        send_listing_card(message.chat.id, dict(r), source="main", show_fav=True)


# ================== ℹ️ Haqqında ==================
@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    text = (
        "🏠 *BestHome Unified Bot*\n"
        "• 📝 Yeni elan əlavə et (vasitəçi / ev sahibi)\n"
        "• 📋 Filtrlərlə elan axtar (əməliyyat, rayon, qiymət, daha çox göstər)\n"
        "• 🔎 Açar sözlə axtarış (yasamal 2 otaq 600 azn və s.)\n"
        "• ⭐ Favorilərim — saxladığın elanlar\n"
        "• 📋 Elanlarım — öz göndərdiyin elanlar və statuslar\n"
        "• 📊 Admin Panel — yalnız admin ID üçün aktivdir\n"
        "• 📢 Təsdiqlənən elanlar avtomatik kanalına göndərilir\n"
        "• 📞 Admin: @esedovesed\n"
        "✅ Tək bot, tam emlak idarəetmə sistemi."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


# ================== Flask + Bot (Render üçün) ==================
app = Flask(__name__)


@app.route("/")
def home():
    return "✅ BestHome Bot is running!"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def run_bot():
    ensure_main_db()
    init_local_db()
    init_main_db_indices()
    print("🤖 Telegram bot start...")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    print("⚙️ BestHome Unified Bot FULL v7 işə düşür...")
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()
