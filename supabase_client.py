import os

from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()


class _MissingSupabaseConfig:
    def table(self, table_name: str):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY environment variables must be configured "
            f"before accessing Supabase table '{table_name}'."
        )


supabase = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY
    else _MissingSupabaseConfig()
)
