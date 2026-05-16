import logging
import os

from supabase import create_client

logger = logging.getLogger("besthome_supabase")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
)
SUPABASE_ANON_KEY = (
    os.getenv("SUPABASE_ANON_KEY", "").strip()
    or os.getenv("ANON_KEY", "").strip()
)
SUPABASE_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY


class _MissingSupabaseConfig:
    def table(self, table_name: str):
        raise RuntimeError(
            "SUPABASE_URL and a Supabase key (SUPABASE_SERVICE_ROLE_KEY, "
            "SUPABASE_SERVICE_KEY, or SUPABASE_ANON_KEY) must be configured "
            f"before accessing Supabase table '{table_name}'."
        )


def _log_init_status() -> None:
    logger.info(
        "[SUPABASE INIT]\nurl_loaded=%s\nservice_role_key_loaded=%s\nanon_key_loaded=%s\nmode=%s",
        bool(SUPABASE_URL),
        bool(SUPABASE_SERVICE_ROLE_KEY),
        bool(SUPABASE_ANON_KEY),
        (
            "service_role"
            if SUPABASE_SERVICE_ROLE_KEY
            else ("anon" if SUPABASE_ANON_KEY else "missing_config")
        ),
    )


_log_init_status()

supabase = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY
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
