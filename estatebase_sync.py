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
import os
import shutil


warnings.filterwarnings("ignore", category=UserWarning)

# -------------------------------------------------
# DB PATH — həmişə skriptin olduğu qovluqda
# -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("BESTHOME_DB_PATH", str(BASE_DIR / "besthome.db"))).expanduser()
SNAPSHOT_PATH = BASE_DIR / ".besthome.deploy.db"
BATCH_SIZE = int(os.getenv("ESTATE_SQLITE_BATCH_SIZE", "500"))


def load_dotenv(path: Path = BASE_DIR / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()


# -------------------------------------------------
# SQL SERVER CONNECTION
# -------------------------------------------------
def get_sql_conn():
    driver = os.getenv("ESTATE_SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    server = os.getenv("ESTATE_SQL_SERVER")
    database = os.getenv("ESTATE_SQL_DATABASE")
    user = os.getenv("ESTATE_SQL_USER")
    password = os.getenv("ESTATE_SQL_PASSWORD")
    trust_cert = os.getenv("ESTATE_SQL_TRUST_CERT", "yes")
    missing = [k for k, v in {
        "ESTATE_SQL_SERVER": server,
        "ESTATE_SQL_DATABASE": database,
        "ESTATE_SQL_USER": user,
        "ESTATE_SQL_PASSWORD": password,
    }.items() if not v]
    if missing:
        raise RuntimeError("SQL Server environment variables missing: " + ", ".join(missing))
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"TrustServerCertificate={trust_cert};"
    )
    return pyodbc.connect(conn_str)


# -------------------------------------------------
# SQLITE INIT
# -------------------------------------------------
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
# SYNC HELPERS
# -------------------------------------------------
LISTING_COLS = [
    "date_read", "title", "prop_type", "operation", "metro", "rooms", "building",
    "floor", "area_kvm", "price", "currency", "phone", "contact_name", "address",
    "document", "summary", "source_link", "region_id", "region_name", "created_at",
    "updated_at",
]
LISTING_UPSERT_SQL = f"""
    INSERT INTO listings ({','.join(LISTING_COLS)})
    VALUES ({','.join(['?'] * len(LISTING_COLS))})
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


def sync_regions(sql_conn, sqlite_conn):
    print("[SYNC] Regions sync başlanır...")
    df = pd.read_sql("SELECT id_region, region_code, region_name FROM dbo.region", sql_conn)
    sqlite_conn.executemany(
        """
        INSERT INTO regions (id_region, region_code, region_name)
        VALUES (?,?,?)
        ON CONFLICT(id_region) DO UPDATE SET
            region_code = excluded.region_code,
            region_name = excluded.region_name
        """,
        [(r.id_region, r.region_code, r.region_name) for r in df.itertuples(index=False)],
    )
    print(f"[OK] Regions sync tamamlandı: {len(df)} qeyd")


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


def listing_values(rec):
    if not rec.get("phone") or not rec.get("source_link"):
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec.setdefault("created_at", now)
    rec.setdefault("updated_at", now)
    return tuple(rec.get(k) for k in LISTING_COLS)


def ensure_local_fts(conn):
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_source_link ON listings(source_link)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_operation ON listings(operation)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_date_read ON listings(date_read)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_region_id ON listings(region_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_region_name ON listings(region_name)")
    cur.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS listings_fts USING fts5(summary, metro, region_name, contact_name, operation, content='listings', content_rowid='id')"
    )
    cur.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS listings_ai AFTER INSERT ON listings BEGIN
          INSERT INTO listings_fts(rowid, summary, metro, region_name, contact_name, operation)
          VALUES (new.id, new.summary, new.metro, new.region_name, new.contact_name, new.operation);
        END;
        CREATE TRIGGER IF NOT EXISTS listings_ad AFTER DELETE ON listings BEGIN
          INSERT INTO listings_fts(listings_fts, rowid, summary, metro, region_name, contact_name, operation)
          VALUES('delete', old.id, old.summary, old.metro, old.region_name, old.contact_name, old.operation);
        END;
        CREATE TRIGGER IF NOT EXISTS listings_au AFTER UPDATE ON listings BEGIN
          INSERT INTO listings_fts(listings_fts, rowid, summary, metro, region_name, contact_name, operation)
          VALUES('delete', old.id, old.summary, old.metro, old.region_name, old.contact_name, old.operation);
          INSERT INTO listings_fts(rowid, summary, metro, region_name, contact_name, operation)
          VALUES (new.id, new.summary, new.metro, new.region_name, new.contact_name, new.operation);
        END;
        """
    )
    cur.execute("SELECT COUNT(*) FROM listings_fts")
    if (cur.fetchone()[0] or 0) == 0:
        cur.execute("INSERT INTO listings_fts(rowid, summary, metro, region_name, contact_name, operation) SELECT id, summary, metro, region_name, contact_name, operation FROM listings")


def create_sqlite_snapshot(source_db=DB_PATH, snapshot_path=SNAPSHOT_PATH):
    source_db = Path(source_db)
    snapshot_path = Path(snapshot_path)
    if snapshot_path.exists():
        snapshot_path.unlink()
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    try:
        src.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.OperationalError:
        pass
    dst = sqlite3.connect(snapshot_path)
    try:
        src.backup(dst)
        row = dst.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"snapshot integrity_check failed: {row}")
        dst.commit()
    finally:
        dst.close(); src.close()
    return snapshot_path


def sync_with_progress(date_from, date_to, days):
    print("[SYNC] başlanır...")
    sql_conn = None
    sqlite_conn = None
    try:
        sql_conn = get_sql_conn()
        sqlite_conn = sqlite3.connect(DB_PATH)
        sqlite_conn.execute("PRAGMA journal_mode=WAL")
        sqlite_conn.execute("BEGIN")
        sync_regions(sql_conn, sqlite_conn)
        where = ""
        if date_from and date_to:
            where = f"WHERE CAST(p.insert_date_time AS date) BETWEEN '{date_from}' AND '{date_to}'"
        elif days and str(days).startswith("-"):
            n = int(days)
            where = f"WHERE CAST(p.insert_date_time AS date) >= DATEADD(DAY, {n}, CAST(GETDATE() AS date))"
        q = f"""
        SELECT p.insert_date_time, pt.property_type_name, o.operation_type_name, m.metro_name, rc.room_count_name,
               bt.building_type_name, p.floor, p.floor_of, p.area, p.general_area, p.price, c.currency_name,
               p.owner_phone_number_01, p.owner_phone_number_02, p.owner_full_name, p.address, d.document_name,
               p.data, p.source_note, r.id_region, r.region_name
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
        total = len(df); print(f"[INFO] Tapılan ümumi elan: {total}")
        added = 0; batch = []
        cur = sqlite_conn.cursor()
        for i, r in enumerate(df.itertuples(index=False), start=1):
            phone = safe(r[12]) or safe(r[13])
            rec = {"date_read": str(r[0])[:10] if r[0] else None, "prop_type": safe(r[1]), "operation": safe(r[2]), "metro": safe(r[3]), "rooms": safe(r[4]), "building": safe(r[5]), "floor": f"{safe(r[6])}/{safe(r[7])}" if r[6] or r[7] else None, "area_kvm": f"{safe(r[8])} sot / {safe(r[9])} kvm" if r[8] or r[9] else None, "price": float(r[10]) if r[10] else None, "currency": safe(r[11]), "phone": normalize_phone(phone), "contact_name": safe(r[14]), "address": safe(r[15]), "document": safe(r[16]), "summary": safe(r[17]), "source_link": safe(r[18]), "region_id": r[19], "region_name": safe(r[20])}
            vals = listing_values(rec)
            if vals:
                batch.append(vals); added += 1
            if len(batch) >= BATCH_SIZE:
                cur.executemany(LISTING_UPSERT_SQL, batch); batch.clear(); sqlite_conn.commit(); sqlite_conn.execute("BEGIN")
            if i % 500 == 0 or i == total:
                print(f"[PROGRESS] {i}/{total} işlənib | qalıb: {total-i} | əlavə edildi: {added}")
        if batch:
            cur.executemany(LISTING_UPSERT_SQL, batch)
        ensure_local_fts(sqlite_conn)
        sqlite_conn.commit()
        create_sqlite_snapshot(DB_PATH, SNAPSHOT_PATH)
        print(f"[SNAPSHOT] {SNAPSHOT_PATH}")
        print(f"[DONE] əlavə edildi: {added}")
    except Exception as exc:
        if sqlite_conn:
            sqlite_conn.rollback()
        print(f"[ERROR] sync failed: {exc}")
        raise
    finally:
        if sqlite_conn:
            sqlite_conn.close()
        if sql_conn:
            sql_conn.close()

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
