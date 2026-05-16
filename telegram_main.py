"""Render entrypoint for running the Telegram bot worker (polling mode)."""

import os
import threading
from flask import Flask

flask_app = Flask(__name__)

# Botun iki dəfə işə düşməsini əngəlləmək üçün flag (bayraq)
_bot_started = False
_lock = threading.Lock()

@flask_app.route("/")
def healthcheck() -> str:
    return "Bot is running"

def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

def bootstrap_env() -> None:
    _require_env("BOT_TOKEN")

    admin_ids = os.getenv("ADMIN_IDS", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()

    if not admin_ids and not admin_id:
        raise RuntimeError(
            "Missing admin configuration. Set ADMIN_ID or ADMIN_IDS environment variable."
        )

    if admin_id and not admin_ids:
        os.environ["ADMIN_IDS"] = admin_id

def run_http_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    # Render mühitində toqquşmanı azaltmaq üçün debug və reloader mütləq False olmalıdır
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, debug=False)

def start_bot_safe():
    global _bot_started
    with _lock:
        if _bot_started:
            print("⚠️ Bot artıq işləyir, ikinci instansiya bloklandı!")
            return
        _bot_started = True

    # Telegram botunu yalnız bir dəfə burada çağırırıq
    from besthome_unified_bot import main as run_telegram_bot
    run_telegram_bot()

if __name__ == "__main__":
    bootstrap_env()

    # HTTP serverini ayrı thread-də başladırıq
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    # Botu təhlükəsiz funksiya ilə başladırıq
    start_bot_safe()