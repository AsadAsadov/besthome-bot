import atexit
import fcntl
import sys

LOCK_FILE = "/tmp/besthome_telegram_bot.lock"

lock_fp = open(LOCK_FILE, "w")

try:
    fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("⚠️ Another bot instance already running")
    sys.exit(0)


def release_lock():
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        lock_fp.close()
    except:
        pass


atexit.register(release_lock)

"""Render entrypoint for running the Telegram bot worker (STRICT POLLING MODE)."""

import logging
import os
import threading

from flask import Flask
from werkzeug.serving import make_server


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


def _start_render_health_server() -> threading.Thread:
    """Start a minimal HTTP server so Render Web Services can detect an open port."""
    host = "0.0.0.0"
    port = int(os.getenv("PORT", 10000))

    health_app = Flask("render_health")

    @health_app.get("/")
    def root_health() -> tuple[str, int]:
        return "Bot is running", 200

    server = make_server(host, port, health_app)

    def _serve_forever() -> None:
        logging.info("[BOOT] Render health server listening on %s:%s", host, port)
        server.serve_forever()

    thread = threading.Thread(target=_serve_forever, daemon=True, name="render-health-server")
    thread.start()
    return thread


if __name__ == "__main__":
    bootstrap_env()

    print("🚀 Bot tamamilə TƏK PROSES rejimində başladılır...")

    # Təhlükəsizlik addımı: əgər nəsə arxa fonda ilişibsə, Render mühitini təmizləyirik
    sys.stdout.flush()

    _start_render_health_server()

    # Telegram botunu daxildə təhlükəsiz şəkildə çağırırıq
    from besthome_unified_bot import main as run_telegram_bot

    # main() funksiyasını çağırırıq - daxildə sync_loop olmadığı üçün
    # artıq CPU-nu yükləmədən təmiz polling başlayacaq
    run_telegram_bot()
