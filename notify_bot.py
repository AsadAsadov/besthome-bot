import sys
import requests
from pathlib import Path

BOT_TOKEN = "6202216323:AAG5GqXLAUCem3_4879Neqb59dmu61uE7qw"
ADMIN_ID = 1311851277

BASE_DIR = Path(__file__).resolve().parent
LINK_FILE = BASE_DIR / "last_db_link.txt"


def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": ADMIN_ID,
        "text": text,
        "disable_web_page_preview": False,
    }
    r = requests.post(url, data=data, timeout=20)
    print("✅ Telegram cavabı:", r.text)


def notify_bot():
    # 1️⃣ Əgər argument gəlibsə – ONU GÖNDƏR
    if len(sys.argv) > 1:
        message = sys.argv[1]
        send_message(message)
        return

    # 2️⃣ Əks halda last_db_link.txt-dən oxu (fallback)
    if not LINK_FILE.exists():
        print("[ERR] last_db_link.txt tapılmadı")
        return

    link = LINK_FILE.read_text(encoding="utf-8").strip()
    if not link:
        print("[ERR] last_db_link.txt boşdur")
        return

    send_message(f"/auto_update_db {link}")


if __name__ == "__main__":
    notify_bot()
