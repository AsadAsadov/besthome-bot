"""Legacy migration helper disabled.

User data now lives in Supabase and listing data remains in besthome.db.
This script intentionally does not migrate or modify besthome.db.
"""

if __name__ == "__main__":
    print("Disabled: user data is stored in Supabase; besthome.db listings are untouched.")
