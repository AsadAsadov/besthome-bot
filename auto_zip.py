import zipfile
import os
from datetime import datetime

DB_PATH = r"D:\Proyekt\31.10.2025 18.10\besthome.db"
ZIP_PATH = r"D:\Proyekt\31.10.2025 18.10\besthome.zip"


def make_zip():
    if not os.path.exists(DB_PATH):
        print("❌ DB tapılmadı")
        return

    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(DB_PATH, arcname="besthome.db")

    print("✅ ZIP yaradıldı:", ZIP_PATH)


if __name__ == "__main__":
    make_zip()
