# ============================================
# estatebase_sync.py — EstateBase SQL → BestHomeBase
# FINAL (REGION + NORMALIZED + PROGRESS)
# ============================================

import sqlite3
import pyodbc
import pandas as pd
from pathlib import Path
from datetime import datetime
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# -------------------------------------------------
# DB PATH — həmişə skriptin olduğu qovluqda
# -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "besthome.db"


# -------------------------------------------------
# SQL SERVER CONNECTION
# -------------------------------------------------
def get_sql_conn():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=SERVER;"
        "DATABASE=dbestate3;"
        "UID=sa;"
        "PWD=byte~~;"
        "TrustServerCertificate=yes;"
    )


# -------------------------------------------------
# SQLITE INIT
# -------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # REGIONS TABLE
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS regions (
            id_region   INTEGER PRIMARY KEY,
            region_code TEXT,
            region_name TEXT
        )
    """
    )

    # LISTINGS TABLE
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_read TEXT,
            title TEXT,
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
            source_link TEXT UNIQUE,
            region_id INTEGER,
            region_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(region_id) REFERENCES regions(id_region)
        )
    """
    )

    conn.commit()
    conn.close()


# -------------------------------------------------
# SYNC REGIONS FROM SQL SERVER → SQLITE
# -------------------------------------------------
def sync_regions(sql_conn):
    print("[SYNC] Regions sync başlanır...")

    q = """
        SELECT id_region, region_code, region_name
        FROM dbo.region
    """
    df = pd.read_sql(q, sql_conn)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    for r in df.itertuples(index=False):
        c.execute(
            """
            INSERT INTO regions (id_region, region_code, region_name)
            VALUES (?,?,?)
            ON CONFLICT(id_region) DO UPDATE SET
                region_code = excluded.region_code,
                region_name = excluded.region_name
        """,
            (r.id_region, r.region_code, r.region_name),
        )

    conn.commit()
    conn.close()
    print(f"[OK] Regions sync tamamlandı: {len(df)} qeyd")


# -------------------------------------------------
# UTILS
# -------------------------------------------------
def safe(v):
    if v is None:
        return None
    if pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None


def normalize_phone(p):
    if not p:
        return None
    p = str(p).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if p.startswith("+994"):
        p = "0" + p[4:]
    elif not p.startswith("0") and len(p) == 9:
        p = "0" + p
    return p


# -------------------------------------------------
# ADD / UPSERT LISTING
# -------------------------------------------------
def add_listing_row(rec):
    if not rec.get("phone"):
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec.setdefault("created_at", now)
    rec.setdefault("updated_at", now)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    cols = [
        "date_read",
        "title",
        "prop_type",
        "operation",
        "metro",
        "rooms",
        "building",
        "floor",
        "area_kvm",
        "price",
        "currency",
        "phone",
        "contact_name",
        "address",
        "document",
        "summary",
        "source_link",
        "region_id",
        "region_name",
        "created_at",
        "updated_at",
    ]
    vals = [rec.get(k) for k in cols]
    ph = ",".join(["?"] * len(cols))

    sql = f"""
        INSERT INTO listings ({",".join(cols)})
        VALUES ({ph})
        ON CONFLICT(source_link) DO UPDATE SET
            price        = excluded.price,
            operation    = excluded.operation,
            metro        = excluded.metro,
            rooms        = excluded.rooms,
            building     = excluded.building,
            floor        = excluded.floor,
            area_kvm     = excluded.area_kvm,
            currency     = excluded.currency,
            phone        = excluded.phone,
            contact_name = excluded.contact_name,
            address      = excluded.address,
            document     = excluded.document,
            summary      = excluded.summary,
            region_id    = excluded.region_id,
            region_name  = excluded.region_name,
            updated_at   = excluded.updated_at
    """

    try:
        c.execute(sql, vals)
        conn.commit()
    except Exception as e:
        print("[WARN] listing insert error:", e)
        conn.close()
        return False

    conn.close()
    return True


# -------------------------------------------------
# MAIN SYNC
# -------------------------------------------------
def sync_with_progress(date_from, date_to, days):
    print("[SYNC] başlanır...")

    sql_conn = get_sql_conn()

    # 1) əvvəlcə REGIONS sinxron olsun
    sync_regions(sql_conn)

    # -------- DATE FILTER --------
    where = ""
    if date_from and date_to:
        where = f"""
            WHERE CAST(p.insert_date_time AS date)
            BETWEEN '{date_from}' AND '{date_to}'
        """
    elif days and str(days).startswith("-"):
        n = int(days)
        where = f"""
            WHERE CAST(p.insert_date_time AS date)
            >= DATEADD(DAY, {n}, CAST(GETDATE() AS date))
        """

    # -------------------------------------------------
    # PROPERTY + REGION JOIN
    # -------------------------------------------------
    q = f"""
    SELECT 
        p.insert_date_time,
        pt.property_type_name,
        o.operation_type_name,
        m.metro_name,
        rc.room_count_name,
        bt.building_type_name,
        p.floor,
        p.floor_of,
        p.area,
        p.general_area,
        p.price,
        c.currency_name,
        p.owner_phone_number_01,
        p.owner_phone_number_02,
        p.owner_full_name,
        p.address,
        d.document_name,
        p.data,
        p.source_note,
        r.id_region,
        r.region_name
    FROM dbo.property p
    LEFT JOIN dbo.property_type pt ON p.fk_id_property_type = pt.id_property_type
    LEFT JOIN dbo.building_type bt ON p.fk_id_building_type = bt.id_building_type
    LEFT JOIN dbo.operation_type o ON p.fk_id_operation_type = o.id_operation_type
    LEFT JOIN dbo.currency c ON p.fk_id_currency = c.id_currency
    LEFT JOIN dbo.document d ON p.fk_id_document = d.id_document
    LEFT JOIN dbo.metro m ON p.fk_id_metro = m.id_metro
    LEFT JOIN dbo.room_count rc ON p.fk_id_room = rc.id_room_count
    LEFT JOIN dbo.region r ON p.fk_id_city = r.id_region
    {where}
    ORDER BY p.insert_date_time DESC
    """

    df = pd.read_sql(q, sql_conn)
    total = len(df)
    print(f"[INFO] Tapılan ümumi elan: {total}")

    added = 0

    for i, r in enumerate(df.itertuples(index=False), start=1):
        phone = safe(r[12]) or safe(r[13])
        if not phone:
            continue

        rec = {
            "date_read": str(r[0])[:10] if r[0] else None,
            "prop_type": safe(r[1]),
            "operation": safe(r[2]),
            "metro": safe(r[3]),
            "rooms": safe(r[4]),
            "building": safe(r[5]),
            "floor": f"{safe(r[6])}/{safe(r[7])}" if r[6] or r[7] else None,
            "area_kvm": (
                f"{safe(r[8])} sot / {safe(r[9])} kvm" if r[8] or r[9] else None
            ),
            "price": float(r[10]) if r[10] else None,
            "currency": safe(r[11]),
            "phone": normalize_phone(phone),
            "contact_name": safe(r[14]),
            "address": safe(r[15]),
            "document": safe(r[16]),
            "summary": safe(r[17]),
            "source_link": safe(r[18]),
            "region_id": r[19],
            "region_name": safe(r[20]),
        }

        if add_listing_row(rec):
            added += 1

        # -------- PROGRESS LOG --------
        if i % 500 == 0 or i == total:
            remain = total - i
            print(
                f"[PROGRESS] {i}/{total} işlənib | qalıb: {remain} | əlavə edildi: {added}"
            )

    sql_conn.close()
    print(f"[DONE] əlavə edildi: {added}")


# -------------------------------------------------
# ENTRY POINT
# -------------------------------------------------
if __name__ == "__main__":
    import argparse

    # DB avtomatik yaransın
    init_db()

    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    parser.add_argument("--days", default="-1")
    args = parser.parse_args()

    sync_with_progress(args.date_from, args.date_to, args.days)
