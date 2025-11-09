# ============================================
# 🏠 BestHome Unified Bot v4
# Elan əlavə + Axtarış + Favorilər + Admin Panel + Kanal paylaşımı
# Əsəd Əsədov ©️ 2025 | BestHome Systems
# ============================================

import os
import io
import zipfile
import requests
import telebot
from telebot import types
import sqlite3
import datetime

# ================== KONFİQURASİYA ==================
BOT_TOKEN = "6202216323:AAEOWdglrcYTJfCr9oRSJtufjsNAkaLWyTc"
ADMIN_ID = 1311851277  # yalnız sən
CHANNEL_ID = -1001878623087  # təsdiqlənən elanların gedəcəyi kanal
DB_PATH = "besthome.db"

# Dropbox ZIP (besthome.db bu ZIP-in içində olmalıdır)
DROPBOX_ZIP_URL = (
    "https://www.dropbox.com/scl/fi/56yi62s82zj0lv7ouebyj/besthome.zip"
    "?rlkey=qm33d26daqx74evsr1x2tso0x&st=xhffgc2g&dl=1"
)

bot = telebot.TeleBot(BOT_TOKEN)
user_state = {}  # elan əlavə proses state
admin_tmp = {}  # admin üçün müvəqqəti məlumat


# ================== DB YÜKLƏMƏ (DROPBOX) ==================
def ensure_database():
    if not os.path.exists(DB_PATH):
        print("⬇️ besthome.zip Dropbox-dan endirilir və açılır...")
        r = requests.get(DROPBOX_ZIP_URL)
        if r.status_code == 200:
            try:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    z.extractall(".")
                if os.path.exists(DB_PATH):
                    print("✅ besthome.db ZIP-dən uğurla çıxarıldı.")
                else:
                    print("⚠️ ZIP açıldı, amma besthome.db tapılmadı. ZIP-i yoxla.")
            except zipfile.BadZipFile:
                print("❌ Xəta: Fayl ZIP formatında deyil. Dropbox linkini yoxla.")
        else:
            print(f"⚠️ Fayl endirilə bilmədi, status: {r.status_code}")
    else:
        print("📦 Mövcud baza tapıldı, yenidən endirməyə ehtiyac yoxdur.")


ensure_database()


# ================== AXTARIŞ KOD XƏRİTƏLƏRİ ==================
RAYON_CODES = {
    "all": "Bütün ərazilər",
    "bin": "Binəqədi",
    "qar": "Qaradağ",
    "xez": "Xəzər",
    "seb": "Səbail",
    "sab": "Sabunçu",
    "sur": "Suraxanı",
    "nar": "Nərimanov",
    "nes": "Nəsimi",
    "niz": "Nizami",
    "pir": "Pirallahı",
    "xet": "Xətai",
    "yas": "Yasamal",
    "abs": "Abşeron",
    "sum": "Sumqayıt",
}

PRICE_CODES = {
    "p0": (None, None),
    "p1": (0, 500),
    "p2": (500, 1000),
    "p3": (1000, 2000),
    "p4": (2000, None),
}

OP_CODES = {
    "all": None,
    "sat": "satıl",
    "kir": "kiray",
    "gun": "günlük",
}


# ================== DB INIT + MİQRASİYA ==================
def column_exists(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Əsas baza (əgər yoxdursa)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_read TEXT,
            prop_type TEXT,
            operation TEXT,
            metro TEXT,
            rooms TEXT,
            building TEXT,
            floor TEXT,
            area_kvm TEXT,
            price TEXT,
            currency TEXT,
            phone TEXT,
            contact_name TEXT,
            address TEXT,
            document TEXT,
            summary TEXT,
            source_link TEXT
        )
        """
    )

    # Təsdiq gözləyən elanlar
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

    # Vasitəçilər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            chat_id INTEGER PRIMARY KEY
        )
        """
    )
    if not column_exists(cur, "agents", "role"):
        cur.execute("ALTER TABLE agents ADD COLUMN role TEXT")
    if not column_exists(cur, "agents", "phone"):
        cur.execute("ALTER TABLE agents ADD COLUMN phone TEXT")
    if not column_exists(cur, "agents", "name"):
        cur.execute("ALTER TABLE agents ADD COLUMN name TEXT")

    # Favorilər
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            listing_id INTEGER,
            added_at TEXT,
            UNIQUE(chat_id, listing_id)
        )
        """
    )

    # İndekslər
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_op ON listings(operation)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_date ON listings(date_read)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_addr ON listings(address)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_new_approved ON listings_new(approved)")

    conn.commit()
    conn.close()


# ================== UTIL FUNKSİYALAR ==================
def format_price(v):
    if v is None:
        return "-"
    s = str(v).strip()
    try:
        val = int(float(s.replace(" ", "").replace(",", "")))
        return f"{val:,}".replace(",", " ")
    except:
        return s


def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_ID


def reset_user(chat_id: int):
    user_state.pop(chat_id, None)


def send_main_menu(chat_id: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 Yeni elan əlavə et")
    kb.add("📋 Elan axtar", "🔎 Açar sözlə axtarış")
    kb.add("⭐ Favorilərim", "📋 Elanlarım")
    kb.add("📞 Adminlə əlaqə", "🔄 Sıfırla")
    kb.add("ℹ️ Haqqında")
    if is_admin(chat_id):
        kb.add("📊 Admin Panel")
    bot.send_message(
        chat_id,
        "Seçim edin 👇",
        reply_markup=kb,
    )


def save_agent_if_needed(data):
    if data.get("role") != "Vasitəçi":
        return
    conn = sqlite3.connect(DB_PATH)
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


def add_listing_new(data):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO listings_new (
            date_added, chat_id, role, prop_type, operation, rayon, metro,
            rooms, area_kvm, price, currency, phone, contact_name, summary, link, approved
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            datetime.date.today().isoformat(),
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
    conn.commit()
    conn.close()


def move_to_listings(row):
    """
    listings_new -> listings
    Dublikat nəzarəti; yeni id qaytarır (yoxdursa None)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Dublikat yoxlaması
    cur.execute(
        """
        SELECT id FROM listings
        WHERE phone = ? AND price = ? AND summary = ?
        LIMIT 1
        """,
        (row["phone"], row["price"], row["summary"]),
    )
    exists = cur.fetchone()
    if exists:
        conn.close()
        return None  # artıq var, təkrar yazılmasın

    addr = (row["rayon"] or "") + ((", " + row["metro"]) if row["metro"] else "")

    # Yeni elan yazılır
    cur.execute(
        """
        INSERT INTO listings (
            date_read, prop_type, operation, metro, rooms, building, floor,
            area_kvm, price, currency, phone, contact_name, address,
            document, summary, source_link
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["date_added"],
            row["prop_type"],
            row["operation"],
            row["metro"],
            row["rooms"],
            "",  # building
            "",  # floor
            row["area_kvm"],
            row["price"],
            row["currency"],
            row["phone"],
            row["contact_name"],
            addr,
            "",  # document
            row["summary"],
            row["link"] if row.get("link", "").startswith("http") else "",
        ),
    )

    new_id = cur.lastrowid  # 🔹 yeni ID alınır
    conn.commit()
    conn.close()
    return new_id


def send_listing_card(chat_id, ev, with_fav_button=True):
    date_val = ev.get("date_read") or ev.get("date_added") or "-"
    txt = (
        f"📅 {date_val}\n"
        f"🏠 {ev.get('prop_type','-')} | {ev.get('rooms','-')}\n"
        f"💸 {ev.get('operation','-')} | 💰 {format_price(ev.get('price'))} {ev.get('currency','AZN')}\n"
    )

    addr = ev.get("address") or ev.get("rayon") or "-"
    if ev.get("metro"):
        addr += f" — {ev.get('metro')}"
    txt += f"📍 {addr}\n"

    txt += f"📞 {ev.get('phone','-')} ({ev.get('contact_name','-')})\n"
    txt += f"🧾 {ev.get('summary','-')}"

    link = ev.get("link") or ev.get("source_link")
    mk = types.InlineKeyboardMarkup()
    if link:
        txt += f"\n🔗 {link}"
        mk.add(types.InlineKeyboardButton("🌐 Elana bax", url=link))

    if with_fav_button and ev.get("id"):
        mk.add(types.InlineKeyboardButton("⭐ Saxla", callback_data=f"fav|{ev['id']}"))

    bot.send_message(chat_id, txt, reply_markup=mk if mk.keyboard else None)


# ================== /myid ==================
@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    bot.send_message(
        message.chat.id,
        f"Sənin Telegram ID-n: `{message.chat.id}`",
        parse_mode="Markdown",
    )


# ================== /start ==================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    reset_user(chat_id)

    # Logo + təlimat
    caption = (
        "🏠 *BestHome Unified Bot*\n\n"
        "• Satılır / Kirayə / Günlük elanlara bax\n"
        "• Vasitəçi və ya ev sahibi kimi elan əlavə et\n"
        "• Favori elanlarını saxla\n"
        "• Öz elanlarının statusuna bax\n"
        "• Çətinlik olsa, 📞 Adminlə əlaqə bölməsindən yaz\n"
    )
    try:
        if os.path.exists("besthomelogo.jpeg"):
            with open("besthomelogo.jpeg", "rb") as f:
                bot.send_photo(chat_id, f, caption=caption, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, caption, parse_mode="Markdown")
    except:
        bot.send_message(chat_id, caption, parse_mode="Markdown")

    send_main_menu(chat_id)


# ================== 🔄 Sıfırla ==================
@bot.message_handler(func=lambda m: m.text == "🔄 Sıfırla")
def reset_all(message):
    reset_user(message.chat.id)
    bot.send_message(message.chat.id, "🔄 Bütün proseslər sıfırlandı.")
    send_main_menu(message.chat.id)


# ================== 📞 Adminlə əlaqə ==================
@bot.message_handler(func=lambda m: m.text == "📞 Adminlə əlaqə")
def contact_admin(message):
    bot.send_message(
        message.chat.id,
        "Hər hansı texniki və ya elanla bağlı sualın varsa, birbaşa adminə yaza bilərsən:\n"
        "👉 @esedovesed",
    )


# ================== 📝 Yeni elan əlavə et ==================
@bot.message_handler(func=lambda m: m.text == "📝 Yeni elan əlavə et")
def start_new_listing(message):
    chat_id = message.chat.id
    reset_user(chat_id)
    user_state[chat_id] = {"step": "role", "chat_id": chat_id}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Vasitəçi", "Əmlak sahibi")
    kb.add("🔄 Sıfırla")
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
        "Obyekt",
        "Torpaq",
        "Villa",
        "Bağ evi",
    ]
    for i in range(0, len(props), 2):
        kb.add(*props[i : i + 2])
    kb.add("🔄 Sıfırla")
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
    kb.add("🔄 Sıfırla")
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
    kb.add("🔄 Sıfırla")
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
    kb.add("🔄 Sıfırla")
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
    kb.add("🔄 Sıfırla")
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
    kb.add("🔄 Sıfırla")
    bot.send_message(
        chat_id,
        "🔗 Əgər elan linki varsa (tap.az, bina.az və s.) yaz.\n"
        "Yoxdursa *Link yoxdur, elanı göndər ✅* seç:",
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
            chat_id,
            "⚠️ Düzgün link yaz və ya 'Link yoxdur, elanı göndər ✅' seç.",
        )
        return

    save_agent_if_needed(st)
    add_listing_new(st)

    txt = (
        f"📢 *Yeni elan (gözləmədə)*\n\n"
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
        bot.send_message(ADMIN_ID, txt, parse_mode="Markdown")
    except:
        pass

    bot.send_message(
        chat_id,
        "✅ Elan uğurla əlavə olundu.\n📌 Hal-hazırda *admin təsdiqini gözləyir.*",
        parse_mode="Markdown",
    )
    reset_user(chat_id)
    send_main_menu(chat_id)


# ================== 📋 Elanlarım ==================
@bot.message_handler(func=lambda m: m.text == "📋 Elanlarım")
def my_listings(message):
    chat_id = message.chat.id
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM listings_new WHERE chat_id = ? ORDER BY id DESC",
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


# ================== ⭐ Favorilərim ==================
@bot.message_handler(func=lambda m: m.text == "⭐ Favorilərim")
def show_favorites(message):
    chat_id = message.chat.id
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.*
        FROM favorites f
        JOIN listings l ON l.id = f.listing_id
        WHERE f.chat_id = ?
        ORDER BY f.added_at DESC
        LIMIT 50
        """,
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "⭐ Favoridə heç bir elan saxlamamısan.")
        return

    bot.send_message(chat_id, f"⭐ Favorilər ({len(rows)} ədəd):")
    for r in rows:
        send_listing_card(chat_id, dict(r), with_fav_button=False)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav|"))
def cb_favorite(c):
    chat_id = c.message.chat.id
    _, lid = c.data.split("|")
    listing_id = int(lid)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO favorites (chat_id, listing_id, added_at)
        VALUES (?, ?, ?)
        """,
        (chat_id, listing_id, datetime.datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    bot.answer_callback_query(c.id, "⭐ Favorilərə əlavə olundu")


# ================== 🔎 Açar sözlə axtarış (istifadəçi) ==================
@bot.message_handler(func=lambda m: m.text == "🔎 Açar sözlə axtarış")
def keyword_search_prompt(message):
    msg = bot.send_message(
        message.chat.id,
        "✍️ Axtarmaq istədiyin sözü yaz:\nMəs: `Yasamal 2 otaqlı 600 AZN`",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, do_keyword_search)


def do_keyword_search(message):
    chat_id = message.chat.id
    kw = message.text.strip()
    if not kw:
        bot.send_message(chat_id, "Boş sorğu göndərdin.")
        return
    run_keyword_search(chat_id, kw, 0)


def run_keyword_search(chat_id, keyword, offset):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    like = f"%{keyword.lower()}%"
    limit = 10
    cur.execute(
        """
        SELECT * FROM listings
        WHERE
            LOWER(prop_type) LIKE ?
            OR LOWER(operation) LIKE ?
            OR LOWER(metro) LIKE ?
            OR LOWER(rooms) LIKE ?
            OR LOWER(area_kvm) LIKE ?
            OR LOWER(price) LIKE ?
            OR LOWER(address) LIKE ?
            OR LOWER(summary) LIKE ?
        ORDER BY date_read DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (like, like, like, like, like, like, like, like, limit, offset),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows and offset == 0:
        bot.send_message(chat_id, "😕 Uyğun elan tapılmadı.")
        return
    elif not rows:
        bot.send_message(chat_id, "✅ Bütün nəticələr göstərildi.")
        return

    if offset == 0:
        bot.send_message(
            chat_id,
            f"🔎 `{keyword}` üzrə nəticələr:",
            parse_mode="Markdown",
        )

    for r in rows:
        send_listing_card(chat_id, dict(r))

    # Daha çox göstər
    safe_kw = keyword.replace(" ", "_")
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ Daha çox göstər",
            callback_data=f"morekw|{safe_kw}|{offset + limit}",
        )
    )
    bot.send_message(chat_id, "⬇️ Daha çox nəticə:", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("morekw|"))
def cb_more_keyword(c):
    _, safe_kw, offset = c.data.split("|")
    kw = safe_kw.replace("_", " ")
    run_keyword_search(c.message.chat.id, kw, int(offset))


# ================== 📋 Elan axtar (filtrlə) ==================
@bot.message_handler(func=lambda m: m.text == "📋 Elan axtar")
def search_menu(message):
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("💸 Satılır", callback_data="sop|sat"),
        types.InlineKeyboardButton("🏢 Kirayə", callback_data="sop|kir"),
    )
    mk.add(
        types.InlineKeyboardButton("🏡 Günlük", callback_data="sop|gun"),
        types.InlineKeyboardButton("🌐 Hamısı", callback_data="sop|all"),
    )
    bot.send_message(
        message.chat.id,
        "🔍 Axtarış üçün əməliyyat növü seç:",
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("sop|"))
def cb_search_op(c):
    _, op = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("Bütün ərazilər", callback_data=f"sray|{op}|all"))
    mk.add(
        types.InlineKeyboardButton("Bakı rayonları", callback_data=f"sray|{op}|bak"),
        types.InlineKeyboardButton("Abşeron/Sumqayıt", callback_data=f"sray|{op}|abs"),
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
        mk.add(
            types.InlineKeyboardButton("Bütün Bakı", callback_data=f"sray2|{op}|all")
        )
        mk.add(
            types.InlineKeyboardButton("Binəqədi", callback_data=f"sray2|{op}|bin"),
            types.InlineKeyboardButton("Qaradağ", callback_data=f"sray2|{op}|qar"),
        )
        mk.add(
            types.InlineKeyboardButton("Xəzər", callback_data=f"sray2|{op}|xez"),
            types.InlineKeyboardButton("Səbail", callback_data=f"sray2|{op}|seb"),
        )
        mk.add(
            types.InlineKeyboardButton("Sabunçu", callback_data=f"sray2|{op}|sab"),
            types.InlineKeyboardButton("Suraxanı", callback_data=f"sray2|{op}|sur"),
        )
        mk.add(
            types.InlineKeyboardButton("Nərimanov", callback_data=f"sray2|{op}|nar"),
            types.InlineKeyboardButton("Nəsimi", callback_data=f"sray2|{op}|nes"),
        )
        mk.add(
            types.InlineKeyboardButton("Nizami", callback_data=f"sray2|{op}|niz"),
            types.InlineKeyboardButton("Pirallahı", callback_data=f"sray2|{op}|pir"),
        )
        mk.add(
            types.InlineKeyboardButton("Xətai", callback_data=f"sray2|{op}|xet"),
            types.InlineKeyboardButton("Yasamal", callback_data=f"sray2|{op}|yas"),
        )
    elif area == "abs":
        mk.add(
            types.InlineKeyboardButton(
                "Abşeron rayonu", callback_data=f"sray2|{op}|abs"
            ),
            types.InlineKeyboardButton("Sumqayıt", callback_data=f"sray2|{op}|sum"),
        )

    bot.edit_message_text(
        "📍 Dəqiq rayon seç:",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("sray2|"))
def cb_search_rayon2(c):
    _, op, rayon = c.data.split("|")
    mk = types.InlineKeyboardMarkup()
    add_price_buttons(mk, op, rayon)
    title = RAYON_CODES.get(rayon, "Bütün ərazilər")
    bot.edit_message_text(
        f"💰 Qiymət aralığı seç ({title}):",
        chat_id=c.message.chat.id,
        message_id=c.message.message_id,
        reply_markup=mk,
    )


def add_price_buttons(mk, op, rayon):
    mk.add(
        types.InlineKeyboardButton("Hamısı", callback_data=f"spr|{op}|{rayon}|p0"),
        types.InlineKeyboardButton("0-500", callback_data=f"spr|{op}|{rayon}|p1"),
    )
    mk.add(
        types.InlineKeyboardButton("500-1000", callback_data=f"spr|{op}|{rayon}|p2"),
        types.InlineKeyboardButton("1000-2000", callback_data=f"spr|{op}|{rayon}|p3"),
    )
    mk.add(
        types.InlineKeyboardButton("2000+", callback_data=f"spr|{op}|{rayon}|p4"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("spr|"))
def cb_search_price(c):
    _, op, rayon, pcode = c.data.split("|")
    run_search(
        chat_id=c.message.chat.id,
        op_code=op,
        rayon_code=rayon,
        price_code=pcode,
        offset=0,
        edit_msg=(c.message.chat.id, c.message.message_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("more|"))
def cb_search_more(c):
    _, op, rayon, pcode, offset = c.data.split("|")
    run_search(
        chat_id=c.message.chat.id,
        op_code=op,
        rayon_code=rayon,
        price_code=pcode,
        offset=int(offset),
        edit_msg=None,
    )


def run_search(chat_id, op_code, rayon_code, price_code, offset, edit_msg=None):
    limit = 10
    rows = search_listings(op_code, rayon_code, price_code, offset, limit)

    title_rayon = RAYON_CODES.get(rayon_code, "Bütün ərazilər")
    op_title = {
        "sat": "Satılır",
        "kir": "Kirayə",
        "gun": "Günlük",
        "all": "Bütün əməliyyatlar",
    }.get(op_code, "Elanlar")

    if not rows and offset == 0:
        text = f"😕 {op_title} | {title_rayon} üzrə uyğun elan tapılmadı."
        if edit_msg:
            bot.edit_message_text(
                text,
                chat_id=edit_msg[0],
                message_id=edit_msg[1],
            )
        else:
            bot.send_message(chat_id, text)
        return
    elif not rows:
        bot.send_message(chat_id, "✅ Bütün uyğun elanlar göstərildi.")
        return

    header = f"🔎 {op_title} | {title_rayon} — nəticələr:"
    if edit_msg:
        bot.edit_message_text(
            header,
            chat_id=edit_msg[0],
            message_id=edit_msg[1],
        )
    elif offset == 0:
        bot.send_message(chat_id, header)

    for r in rows:
        send_listing_card(chat_id, r)

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(
            "➕ Daha çox göstər",
            callback_data=f"more|{op_code}|{rayon_code}|{price_code}|{offset + limit}",
        )
    )
    bot.send_message(chat_id, "⬇️ Daha çox elan üçün:", reply_markup=mk)


def search_listings(op_code, rayon_code, price_code, offset, limit):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    sql = "SELECT * FROM listings WHERE 1=1"
    params = []

    op_kw = OP_CODES.get(op_code)
    if op_kw:
        sql += " AND LOWER(operation) LIKE ?"
        params.append(f"%{op_kw}%")

    if rayon_code != "all":
        rayon_name = RAYON_CODES.get(rayon_code, "").lower()
        if rayon_name:
            like = f"%{rayon_name}%"
            sql += " AND (LOWER(address) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(metro) LIKE ?)"
            params.extend([like, like, like])

    min_p, max_p = PRICE_CODES.get(price_code, (None, None))
    if min_p is not None:
        sql += " AND CAST(price AS INTEGER) >= ?"
        params.append(min_p)
    if max_p is not None:
        sql += " AND CAST(price AS INTEGER) <= ?"
        params.append(max_p)

    sql += " ORDER BY date_read DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ================== 📊 Admin Panel ==================
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_new WHERE approved=0 ORDER BY id DESC LIMIT 20")
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

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings_new WHERE id=? AND approved=0", (lid,))
    row = cur.fetchone()
    if not row:
        bot.answer_callback_query(c.id, "Tapılmadı və ya artıq təsdiqlənib.")
        conn.close()
        return

    new_id = move_to_listings(row)
    cur.execute("UPDATE listings_new SET approved=1 WHERE id=?", (lid,))
    conn.commit()
    conn.close()

    # Elan sahibinə xəbər
    try:
        bot.send_message(
            row["chat_id"],
            "🎉 Elanınız təsdiqləndi və artıq sistemdə aktivdir.",
        )
    except:
        pass

    # Kanala paylaşım
    if CHANNEL_ID and new_id:
        conn2 = sqlite3.connect(DB_PATH)
        conn2.row_factory = sqlite3.Row
        cur2 = conn2.cursor()
        cur2.execute("SELECT * FROM listings WHERE id=?", (new_id,))
        lrow = cur2.fetchone()
        conn2.close()
        if lrow:
            send_listing_card(CHANNEL_ID, dict(lrow), with_fav_button=False)

    bot.answer_callback_query(c.id, "✅ Təsdiq olundu.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def cb_delete(c):
    if not is_admin(c.message.chat.id):
        return
    lid = int(c.data.split("_")[1])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM listings_new WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    bot.answer_callback_query(c.id, "❌ Elan silindi.")


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def cb_admin_stats(c):
    if not is_admin(c.message.chat.id):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM listings")
    total = cur.fetchone()[0]

    cur.execute(
        "SELECT LOWER(operation), COUNT(*) FROM listings GROUP BY LOWER(operation)"
    )
    ops = cur.fetchall()

    types_stats = {}
    for key, label in [
        ("yeni", "Yeni tikili"),
        ("köhnə", "Köhnə tikili"),
        ("həyət", "Həyət evi"),
        ("bağ", "Bağ evi"),
        ("villa", "Villa"),
        ("obyekt", "Obyekt"),
        ("torpaq", "Torpaq"),
    ]:
        cur.execute(
            "SELECT COUNT(*) FROM listings WHERE LOWER(prop_type) LIKE ?",
            (f"%{key}%",),
        )
        types_stats[label] = cur.fetchone()[0]

    conn.close()

    txt = f"📊 *Statistik hesabat*\n\nToplam elan: *{total}*\n\n💸 Əməliyyat növləri:\n"
    for op, cnt in ops:
        if op:
            txt += f"• {op.capitalize()}: {cnt}\n"

    txt += "\n🏠 Əmlak tipləri:\n"
    for label, cnt in types_stats.items():
        txt += f"• {label}: {cnt}\n"

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
    text = message.text.strip()
    if not text:
        bot.send_message(message.chat.id, "Boş mesaj göndərilə bilməz.")
        return

    conn = sqlite3.connect(DB_PATH)
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
        c.message.chat.id, "🔎 Əsas bazada axtarış üçün açar söz yaz:"
    )
    bot.register_next_step_handler(msg, do_admin_search)


def do_admin_search(message):
    if not is_admin(message.chat.id):
        return
    kw = message.text.strip().lower()
    if not kw:
        bot.send_message(message.chat.id, "Boş sorğu.")
        return

    like = f"%{kw}%"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        LIMIT 50
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
        send_listing_card(message.chat.id, dict(r))


# ================== ℹ️ Haqqında ==================
@bot.message_handler(func=lambda m: m.text == "ℹ️ Haqqında")
def about(message):
    text = (
        "🏠 *BestHome Unified Bot*\n"
        "• 📝 Yeni elan əlavə et (vasitəçi / ev sahibi)\n"
        "• 📋 Elan axtar — filtrli + Daha çox göstər\n"
        "• 🔎 Açar sözlə axtarış — sərbəst mətnlə\n"
        "• ⭐ Favorilərim — saxladığın elanlar\n"
        "• 📋 Elanlarım — öz elanlarının statusu\n"
        "• 📊 Admin Panel — yalnız admin üçün\n"
        "• 📞 Adminlə əlaqə — @esedovesed\n"
        "✅ Tək bot, tam emlak sistemi."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


# ================== RUN (Render üçün uyğun versiya) ==================
if __name__ == "__main__":
    import threading
    from flask import Flask
    import os
    import logging

    print("⚙️ BestHome Unified Bot işə düşür...")
    init_db()

    # --- Flask (keep-alive port üçün) ---
    app = Flask(__name__)

    @app.route("/")
    def home():
        return "✅ BestHome Bot is running on Render!"

    # Telebot səhvləri terminalda göstərsin
    telebot.logger.setLevel(logging.INFO)

    def run_flask():
        port = int(os.environ.get("PORT", 10000))
        # host və threaded=True botun paralel işləməsinə kömək edir
        app.run(host="0.0.0.0", port=port, threaded=True)

    def run_bot():
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            print(f"❌ Bot polling xəta verdi: {e}")
            # Render avtomatik yenidən işə salacaq, amma yenə də 5 san fasilə et
            import time

            time.sleep(5)
            run_bot()  # retry

    # --- İki prosesi paralel işə salırıq ---
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=run_bot).start()

    # proqramın dayanmaması üçün
    while True:
        import time

        time.sleep(60)
