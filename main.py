import sys
import os
import threading
import traceback
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ENV
from core.logging import logger
import besthome_unified_bot


_bot_thread_started = threading.Event()


def _start_bot_thread():
    if _bot_thread_started.is_set():
        logger.info("Telegram bot thread already started; skipping")
        return

    logger.info("Starting Telegram bot thread")
    bot_thread = threading.Thread(
        target=besthome_unified_bot.main,
        daemon=True,
        name="telegram-bot-polling",
    )
    bot_thread.start()
    _bot_thread_started.set()


def start_keepalive_server():
    app = Flask("keepalive")

    @app.route("/", methods=["GET", "HEAD"])
    def home():
        return "OK", 200

    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting keepalive HTTP server")
    app.run(host="0.0.0.0", port=port)


def main():
    logger.info("BestHome bot starting env=%s", ENV)
    _start_bot_thread()
    start_keepalive_server()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FATAL ERROR (full traceback):")
        print(traceback.format_exc())
        raise
