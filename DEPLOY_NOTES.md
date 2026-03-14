# Render deployment notes

## Why Telegram bot stopped responding
The Render deployment was starting `main.py`, which is a **FastAPI Instagram/Meta webhook + admin app**, not the Telegram polling runner. Because of this, Telegram updates were never consumed, so `/start` and other Telegram commands did nothing.

## Wrong vs correct entrypoint
- Incorrect entrypoint for Telegram: `main.py`
- Correct Telegram entrypoint now: `telegram_main.py`
- Telegram runtime mode: **polling** (uses `bot.infinity_polling(...)` in `besthome_unified_bot.py`)

## Service layout on Render
Use **two services** so Instagram webhook and Telegram bot can run independently:

1. **Instagram/Admin API**
   - Service type: **Web Service**
   - Build Command:
     ```bash
     pip install -r requirements.txt
     ```
   - Start Command:
     ```bash
     uvicorn main:app --host 0.0.0.0 --port $PORT
     ```

2. **Telegram Bot Worker**
   - Service type: **Background Worker**
   - Build Command:
     ```bash
     pip install -r requirements.txt
     ```
   - Start Command:
     ```bash
     python telegram_main.py
     ```

## Required environment variables (Telegram worker)
- `BOT_TOKEN` (required)
- `ADMIN_ID` (required unless `ADMIN_IDS` is provided)
- `ADMIN_IDS` (optional alternative to `ADMIN_ID`, comma-separated IDs)
- `AUTO_DB_URL` (optional, preferred) Google Drive ZIP URL for automatic DB restore
- `AUTO_DB_FILE_ID` (optional fallback) Google Drive file id used when `AUTO_DB_URL` is not set

`telegram_main.py` validates these settings and fails fast with clear errors if they are missing.

## Automatic DB restore on startup (ephemeral disk safe)
`besthome.db` lives on ephemeral storage by default (`/tmp/besthome`). To survive Render restarts/deploys without paid persistent disk, the Telegram bot now restores from Google Drive at startup:

1. On startup, if local `besthome.db` exists, restore is skipped.
2. If local DB is missing, bot reads restore source from:
   - `AUTO_DB_URL` (preferred), or
   - `AUTO_DB_FILE_ID` (converted to direct Google Drive download URL).
3. Bot downloads `besthome.zip`, safely extracts `besthome.db`, validates SQLite content, and atomically places it at `BESTHOME_DB_PATH` / `BASE_DATA_DIR`.
4. If restore fails, startup logs include a clear failure trace.

This keeps admin/manual DB update flows intact while adding automatic boot-time recovery.

## Required environment variables (Instagram/Admin web service)
- `META_VERIFY_TOKEN`
- `META_PAGE_ACCESS_TOKEN`
- `BOT_TOKEN` (still required by shared config imports)
- `ADMIN_ID` or `ADMIN_IDS`

## Health check
For the web service (`main.py`):
```bash
curl https://<your-render-web-service>.onrender.com/health
```
Expected response:
```json
{"ok": true}
```

## How to verify Telegram bot works
1. Deploy/start the Background Worker with `python telegram_main.py`.
2. Open Telegram chat with your bot.
3. Send `/start`.
4. Confirm the bot replies.
5. Check worker logs for successful polling start.

## Webhook commands for Telegram
Not applicable in current setup because Telegram runs in **polling mode**, not webhook mode.
- `setWebhook` is **not required**.
- `getWebhookInfo` should normally show no active webhook URL when polling is used.


## Storage paths (Render-safe defaults)
The Telegram bot no longer hard-requires `/data` at import/startup.

- Base directory env: `BASE_DATA_DIR` (recommended)
- Backward-compatible alias: `DATA_DIR`
- Default when unset: `/tmp/besthome`
- Main DB default when `BESTHOME_DB_PATH` is unset: `/tmp/besthome/besthome.db`

This prevents `PermissionError` on platforms where `/data` is not writable. Local SQLite files are now created only when related features initialize.
