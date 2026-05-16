import logging
import os

from supabase import create_client

logger = logging.getLogger("besthome_supabase")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()


class _MissingSupabaseConfig:
    def table(self, table_name: str):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables must be configured "
            f"before accessing Supabase table '{table_name}'."
        )


def _log_init_status() -> None:
    logger.info(
        "[SUPABASE INIT]\nurl_loaded=%s\nservice_key_loaded=%s\nmode=%s",
        bool(SUPABASE_URL),
        bool(SUPABASE_SERVICE_KEY),
        "supabase" if SUPABASE_URL and SUPABASE_SERVICE_KEY else "missing_config",
    )


_log_init_status()

supabase = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    if SUPABASE_URL and SUPABASE_SERVICE_KEY
    else _MissingSupabaseConfig()
)


def validate_supabase_startup() -> bool:
    """Log Supabase configuration and verify that the users table is reachable."""
    _log_init_status()
    try:
        supabase.table("users").select("chat_id").limit(1).execute()
        logger.info("[SUPABASE INIT] users_table_access=True")
        return True
    except Exception as exc:
        logger.error(
            "[SUPABASE ERROR]\ntable=users\naction=startup_validate\ndetails=%s",
            exc,
            exc_info=True,
        )
        return False
