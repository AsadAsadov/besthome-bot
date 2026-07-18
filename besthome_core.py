# ============================================
# besthome_core.py — Database və Query modulu (Stable Final)
# ============================================

import sqlite3
from pathlib import Path
from datetime import datetime, date

DB_PATH = Path("besthome.db")

# ---------- DB Setup ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
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
            price REAL,
            currency TEXT,
            phone TEXT,
            contact_name TEXT,
            address TEXT,
            document TEXT,
            summary TEXT,
            source_link TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sold (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            color TEXT
        )
    """)
    conn.commit()
    conn.close()


def ensure_tables():
    """Baza yoxdursa yaradır, varsa toxunmur"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    required_cols = {
        "listings": {
            "sql_id": "INTEGER",
            "source_link": "TEXT",
        },
    }

    for table, cols in required_cols.items():
        for col, col_type in cols.items():
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};")
                print(f"✅ '{col}' sütunu əlavə edildi ({table})")
            except Exception as e:
                if "duplicate column name" in str(e).lower():
                    pass  # artıq var
                else:
                    print(f"⚠️ '{col}' əlavə edilə bilmədi: {e}")

    conn.commit()
    conn.close()


# ---------- Əlavə və təmizlik ----------
def clear_search_history():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM search_history")
    conn.commit()
    conn.close()


def add_listing_row(rec):
    """Yeni elan əlavə et (təkrarlanmaya qarşı yoxlama ilə)"""
    if not rec.get("phone"):
        return False

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute(
        "SELECT id FROM listings WHERE phone=? AND price=?",
        (rec.get("phone"), rec.get("price")),
    )
    exists = c.fetchone()
    if exists:
        conn.close()
        return False

    cols = list(rec.keys())
    vals = [rec[k] for k in cols]
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO listings ({','.join(cols)}) VALUES ({placeholders})"
    try:
        c.execute(sql, vals)
        conn.commit()
    except Exception as e:
        print(f"[⚠️ Əlavə edilə bilmədi] {e}")
    finally:
        conn.close()
    return True


# ---------- Fərqləndirilənlər / Satılanlar ----------
def set_favorite_phone(phone, color="#e8f2ff"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO favorites (phone, color) VALUES (?,?)", (phone, color)
    )
    conn.commit()
    conn.close()


def get_favorites_phones_map():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT phone, color FROM favorites")
    data = {row[0]: row[1] for row in c.fetchall()}
    conn.close()
    return data


def add_sold(phone):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO sold (phone) VALUES (?)", (phone,))
    conn.commit()
    conn.close()


def remove_sold(phone):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM sold WHERE phone=?", (phone,))
    conn.commit()
    conn.close()


def get_sold_set():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT phone FROM sold")
    rows = {r[0] for r in c.fetchall()}
    conn.close()
    return rows


# ---------- Əsas Query ----------
def query_phones_summary(
    keyword=None,
    limit=500,
    date_from=None,
    date_to=None,
    exclude_sold=False,
    only_sold=False,
    only_favorites=False,
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    base = """
        SELECT
            phone,
            MAX(date_read) AS date_read,
            MAX(created_at) AS created_at,
            MAX(prop_type) AS prop_type,
            MAX(building) AS building,
            MAX(operation) AS operation,
            MAX(metro) AS metro,
            MAX(rooms) AS rooms,
            MAX(floor) AS floor,
            MAX(area_kvm) AS area_kvm,
            MAX(price) AS price,
            MAX(currency) AS currency,
            COUNT(*) AS ad_count,
            MAX(contact_name) AS contact_name,
            MAX(address) AS address,
            MAX(document) AS document,
            MAX(summary) AS summary,
            MAX(source_link) AS source_link
        FROM listings
        WHERE 1=1
    """
    params = []

    # 🔍 Axtarış sözü varsa
    if keyword:
        kw = f"%{keyword.lower()}%"
        base += " AND (LOWER(phone) LIKE ? OR LOWER(metro) LIKE ? OR LOWER(address) LIKE ?)"
        params += [kw, kw, kw]

    # 📅 Tarix filtrləri
    if date_from:
        base += " AND date(created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        base += " AND date(created_at) <= date(?)"
        params.append(date_to)

    # ⚙️ Satılan / favorit filtrləri
    if only_sold:
        base += " AND phone IN (SELECT phone FROM sold)"
    elif only_favorites:
        base += " AND phone IN (SELECT phone FROM favorites)"
    elif exclude_sold:
        base += " AND phone NOT IN (SELECT phone FROM sold)"

    base += " GROUP BY phone ORDER BY MAX(created_at) DESC LIMIT ?"
    params.append(limit)

    cur.execute(base, params)
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- Dəstək funksiyalar ----------
def get_distinct_values(col):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        f"SELECT DISTINCT {col} FROM listings WHERE {col} IS NOT NULL AND TRIM({col}) != '' ORDER BY {col} ASC"
    )
    vals = [r[0] for r in c.fetchall()]
    conn.close()
    return vals


def get_listings_by_phone(phone):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM listings WHERE phone=? ORDER BY date_read DESC", (phone,))
    rows = c.fetchall()
    conn.close()
    return rows


def phone_stats(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        SELECT 
            MIN(date_read), MAX(date_read),
            COUNT(*), AVG(price), MIN(price), MAX(price)
        FROM listings WHERE phone=?
    """,
        (phone,),
    )
    r = c.fetchone()
    conn.close()
    if not r:
        return {}
    min_d, max_d, cnt, avg_p, min_p, max_p = r
    trend = None
    if min_p and max_p and min_p != 0:
        trend = ((max_p - min_p) / min_p) * 100
    return {
        "first_date": min_d,
        "last_date": max_d,
        "count": cnt,
        "avg_price": avg_p,
        "min_price": min_p,
        "max_price": max_p,
        "trend_pct": trend,
    }


def normalize_phone(p):
    if not p:
        return None
    p = str(p)
    p = p.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if p.startswith("+994"):
        p = "0" + p[4:]
    elif not p.startswith("0") and len(p) == 9:
        p = "0" + p
    return p.strip()
