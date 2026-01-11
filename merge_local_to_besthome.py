# ================================================
# 🧩 merge_local_to_besthome.py
# BestHome DB-lərin birləşdirilməsi skripti
# local_data.db → besthome.db (təmizləmədən)
# © 2025 Əsəd Əsədov
# ================================================

import sqlite3
import os
from datetime import datetime

MAIN_DB = "besthome.db"  # Dropbox-dan yüklədiyin əsas baza
LOCAL_DB = "local_data.db"  # Botun istifadə etdiyi lokal baza


def ensure_listings_table(conn):
    """Əgər listings cədvəli mövcud deyilsə, yaradır."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_read TEXT,
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
            address TEXT,
            source_link TEXT,
            is_hidden INTEGER DEFAULT 0
        )
    """
    )
    conn.commit()


def merge_databases():
    """local_data.db → besthome.db daxil edir (təmizləmədən)"""
    if not os.path.exists(MAIN_DB):
        print("❌ Əsas baza (besthome.db) tapılmadı!")
        return
    if not os.path.exists(LOCAL_DB):
        print("❌ Lokal baza (local_data.db) tapılmadı!")
        return

    main_conn = sqlite3.connect(MAIN_DB)
    main_conn.row_factory = sqlite3.Row
    main_cur = main_conn.cursor()

    local_conn = sqlite3.connect(LOCAL_DB)
    local_conn.row_factory = sqlite3.Row
    local_cur = local_conn.cursor()

    # Əsas cədvəl yoxdursa, yaradırıq
    ensure_listings_table(main_conn)

    # Lokal bazadan təsdiqlənmiş elanları çək
    local_cur.execute("SELECT * FROM listings_approved ORDER BY id ASC")
    rows = local_cur.fetchall()

    if not rows:
        print("ℹ️ Köçürüləcək yeni elan yoxdur.")
        main_conn.close()
        local_conn.close()
        return

    added = 0
    skipped = 0

    for r in rows:
        row = dict(r)
        # Dublikat yoxlaması
        main_cur.execute(
            """
            SELECT id FROM listings
            WHERE phone=? AND price=? AND summary=? AND
                  prop_type=? AND operation=?
            LIMIT 1
            """,
            (
                row.get("phone"),
                row.get("price"),
                row.get("summary"),
                row.get("prop_type"),
                row.get("operation"),
            ),
        )
        exists = main_cur.fetchone()
        if exists:
            skipped += 1
            continue

        # Əlavə et
        main_cur.execute(
            """
            INSERT INTO listings (
                date_read, prop_type, operation, rayon, metro, rooms,
                area_kvm, price, currency, phone, contact_name,
                summary, address, source_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("date_added"),
                row.get("prop_type"),
                row.get("operation"),
                row.get("rayon"),
                row.get("metro"),
                row.get("rooms"),
                row.get("area_kvm"),
                row.get("price"),
                row.get("currency"),
                row.get("phone"),
                row.get("contact_name"),
                row.get("summary"),
                row.get("rayon"),  # address olaraq rayon yazırıq
                row.get("link"),
            ),
        )
        added += 1

    main_conn.commit()

    print("✅ Birləşdirmə tamamlandı:")
    print(f"   ➕ Yeni elanlar köçürüldü: {added}")
    print(f"   ⚙️ Dublikat elanlar keçildi: {skipped}")
    print("🕒 Tarix:", datetime.now().strftime("%Y-%m-%d %H:%M"))

    main_conn.close()
    local_conn.close()


if __name__ == "__main__":
    merge_databases()
