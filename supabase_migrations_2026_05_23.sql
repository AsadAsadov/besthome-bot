-- BestHome Supabase hardening migration (2026-05-23)

-- 1) search_logs upsert compatibility (on_conflict=chat_id)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'search_logs_chat_id_unique'
  ) THEN
    ALTER TABLE search_logs
    ADD CONSTRAINT search_logs_chat_id_unique UNIQUE (chat_id);
  END IF;
END $$;

-- 2) optional users columns used by bot payloads
ALTER TABLE users
ADD COLUMN IF NOT EXISTS first_name TEXT;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

-- 3) favorites data integrity for toggle/upsert flow
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'favorites_user_listing_unique'
  ) THEN
    ALTER TABLE favorites
    ADD CONSTRAINT favorites_user_listing_unique UNIQUE (chat_id, listing_id, source);
  END IF;
END $$;
