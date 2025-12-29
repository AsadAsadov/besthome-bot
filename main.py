import sys
import os
import threading
from flask import Flask


def start_keepalive_server():
    app = Flask("keepalive")

    @app.route("/")
    def home():
        return "OK", 200

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


threading.Thread(target=start_keepalive_server, daemon=True).start()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ENV
from core.logging import logger
import traceback

import besthome_unified_bot


def main():
    logger.info("BestHome bot starting env=%s", ENV)
    besthome_unified_bot.main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FATAL ERROR (full traceback):")
        print(traceback.format_exc())
        raise
