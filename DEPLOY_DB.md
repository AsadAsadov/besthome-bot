# Direct `besthome.db` deployment to VPS

## Existing flow audit

Old `run.bat` flow was: `estatebase_sync.py --days -3` → `auto_zip.py` → `upload_gdrive.py besthome.zip` → `notify_bot.py`. ZIP, Google Drive, Render-style restore, and PM2 restarts are no longer part of the default runtime flow. `auto_zip.py` and `upload_gdrive.py` may remain as manual legacy fallback scripts, but `run.bat` no longer calls them.

`local_data.db` is user data and must never be uploaded, replaced, or rolled back by this listing DB deployment flow.

## New flow

1. `estatebase_sync.py --days -3` updates local `besthome.db`, creates indexes/FTS, and writes a stable SQLite backup snapshot via the SQLite backup API.
2. `deploy_besthome_db.py` validates local DB, creates `.besthome.deploy.db`, uploads it to `/var/www/besthome-bot/data/.besthome.db.uploading-<timestamp>-<uuid>` using `scp`, then calls the VPS activation script over SSH.
3. `scripts/activate_besthome_db.sh` validates the temporary DB, backs up the current active DB, atomically renames the temporary file to `/var/www/besthome-bot/data/besthome.db`, writes `besthome.db.version.json`, and keeps the newest backups.
4. The bot keeps listing DB connections short-lived and clears listing caches when mtime/size/version marker changes. No PM2 or Telegram bot restart is required.
5. `notify_bot.py` runs only after sync and deploy both succeed.

## Windows `.env`

Copy `.env.example` to `.env` locally and fill values. Do not commit `.env` or private keys.

```env
BESTHOME_DB_PATH=D:\path\to\besthome.db
VPS_HOST=example.com
VPS_PORT=22
VPS_USER=besthomesync
VPS_SSH_KEY=C:\Users\YourUser\.ssh\id_ed25519
VPS_REMOTE_DB_PATH=/var/www/besthome-bot/data/besthome.db

ESTATE_SQL_DRIVER=ODBC Driver 17 for SQL Server
ESTATE_SQL_SERVER=
ESTATE_SQL_DATABASE=
ESTATE_SQL_USER=
ESTATE_SQL_PASSWORD=
ESTATE_SQL_TRUST_CERT=yes
```

Because SQL Server credentials were previously hardcoded, rotate/change that SQL Server password before production use.

## Windows SSH key setup

```powershell
ssh-keygen -t ed25519 -C "besthome-db-sync"
ssh -i $env:USERPROFILE\.ssh\id_ed25519 besthomesync@example.com
```

Never place the private key in the repository.

## VPS setup

Create a restricted sync user and install dependencies:

```bash
sudo adduser --disabled-password --gecos "BestHome DB sync" besthomesync
sudo apt-get update
sudo apt-get install -y openssh-server sqlite3 python3 util-linux coreutils
sudo mkdir -p /var/www/besthome-bot/data/backups
sudo chown -R www-data:www-data /var/www/besthome-bot/data
sudo chmod 775 /var/www/besthome-bot/data
sudo usermod -aG www-data besthomesync
sudo install -o root -g root -m 0755 scripts/activate_besthome_db.sh /var/www/besthome-bot/scripts/activate_besthome_db.sh
sudo install -o root -g root -m 0755 scripts/rollback_besthome_db.sh /var/www/besthome-bot/scripts/rollback_besthome_db.sh
```

Add the Windows public key to `/home/besthomesync/.ssh/authorized_keys`.

Allow only activation/rollback via sudo:

```sudoers
besthomesync ALL=(root) NOPASSWD: /var/www/besthome-bot/scripts/activate_besthome_db.sh, /var/www/besthome-bot/scripts/rollback_besthome_db.sh
```

Use `sudo visudo -f /etc/sudoers.d/besthome-db-sync` and set `0440` permissions.

## First manual deploy

```powershell
python estatebase_sync.py --days -3
python deploy_besthome_db.py
```

Expected success output includes:

```text
DEPLOY_OK
local_sha256=...
remote_sha256=...
size=...
listings_count=...
latest_listing_date=...
duration=...
```

## Task Scheduler

Configure an hourly task:

- Program: `C:\Windows\System32\cmd.exe`
- Arguments: `/c C:\path\to\besthome-bot\run.bat`
- Start in: `C:\path\to\besthome-bot`
- Do not pass `--interactive`; default mode never pauses and returns a proper exit code.

For a manual console run with pause, use:

```cmd
run.bat --interactive
```

## Rollback

Rollback to the latest backup without restarting PM2:

```bash
sudo /var/www/besthome-bot/scripts/rollback_besthome_db.sh
```

Rollback to a specific backup:

```bash
sudo /var/www/besthome-bot/scripts/rollback_besthome_db.sh /var/www/besthome-bot/data/backups/besthome_YYYYMMDD_HHMMSS.db
```

## Verification tests

Local checks:

```bash
python -m py_compile estatebase_sync.py deploy_besthome_db.py listing_db.py besthome_unified_bot.py
bash -n scripts/activate_besthome_db.sh
bash -n scripts/rollback_besthome_db.sh
rg "auto_zip|upload_gdrive|besthome.zip" run.bat
rg "PWD=|byte~~|DATABASE=dbestate3|UID=sa" estatebase_sync.py
```

Production smoke tests:

1. Corrupt local DB and confirm `deploy_besthome_db.py` stops before SCP.
2. Interrupt SCP and confirm active `/var/www/besthome-bot/data/besthome.db` hash is unchanged.
3. Upload a bad SQLite file to a temporary upload name and confirm activation deletes it and leaves active DB unchanged.
4. Deploy a healthy DB and compare local/remote SHA-256.
5. Query the bot before and after deploy without PM2 restart; new listing requests should open new SQLite connections and see the new DB.
6. Confirm `/var/www/besthome-bot/local_data.db` mtime/hash does not change.
7. Start two deploys; one should exit with `DEPLOY_LOCKED` or the VPS flock error.

## Remaining risks

- SCP is not resumable; interrupted uploads are safe but start again on retry. Add optional `rsync` later if needed.
- Existing long-running handlers may finish with data already loaded in memory, but subsequent listing queries use fresh short-lived connections.
- The VPS must have correct sudoers and data directory ownership; misconfiguration causes deploy failure without replacing the active DB.
