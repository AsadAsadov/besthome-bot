"""Render entrypoint for running the Telegram bot worker (STRICT POLLING MODE)."""

import os

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

if __name__ == "__main__":
    bootstrap_env()
    
    print("🚀 Bot tamamilə TƏK PROSES rejimində başladılır...")
    
    # Telegram botunu birbaşa və yalqız şəkildə çağırırıq
    from besthome_unified_bot import main as run_telegram_bot
    run_telegram_bot()