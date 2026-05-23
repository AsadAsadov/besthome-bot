"""Render entrypoint for running the Telegram bot worker."""

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
    _start_render_health_server()

    from besthome_unified_bot import main as run_telegram_bot

    run_telegram_bot()
