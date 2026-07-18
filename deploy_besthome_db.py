"""Deploy a validated local besthome.db snapshot directly to the VPS.

Uses only SSH/SCP with key authentication. Never writes to the active remote DB
path directly; upload goes to a hidden temporary file and the VPS activation
script validates and atomically renames it.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_REMOTE_DB = "/var/www/besthome-bot/data/besthome.db"
SNAPSHOT_NAME = ".besthome.deploy.db"
LOCK_PATH = BASE_DIR / ".besthome_deploy.lock"
SSH_TIMEOUT = int(os.getenv("BESTHOME_SSH_TIMEOUT", "180"))
SCP_TIMEOUT = int(os.getenv("BESTHOME_SCP_TIMEOUT", "900"))
SCP_RETRIES = int(os.getenv("BESTHOME_SCP_RETRIES", "2"))


def log(msg: str) -> None:
    print(msg, flush=True)


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def acquire_lock():
    if os.name == "nt":
        import msvcrt
        f = open(LOCK_PATH, "a+b")
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            f.seek(0); f.truncate(); f.write(str(os.getpid()).encode()); f.flush()
            return f
        except OSError:
            log(f"DEPLOY_LOCKED lock={LOCK_PATH}")
            sys.exit(75)
    import fcntl
    f = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush()
        return f
    except BlockingIOError:
        log(f"DEPLOY_LOCKED lock={LOCK_PATH}")
        sys.exit(75)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_sqlite(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(f"Local DB not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"quick_check failed: {row}")
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listings'").fetchone()
        if not exists:
            raise RuntimeError("listings table missing")
        count = int(conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] or 0)
        if count <= 0:
            raise RuntimeError("listings table is empty")
        return count
    finally:
        conn.close()


def create_sqlite_snapshot(source_db: Path, snapshot_path: Path) -> Path:
    if snapshot_path.exists():
        snapshot_path.unlink()
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    dst = sqlite3.connect(snapshot_path)
    try:
        try:
            src.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass
        src.backup(dst)
        dst.commit()
    finally:
        dst.close(); src.close()
    validate_sqlite(snapshot_path)
    return snapshot_path


def run_cmd(args: Iterable[str], timeout: int) -> subprocess.CompletedProcess:
    args = [str(a) for a in args]
    log("RUN " + " ".join(args[:2]) + " ...")
    cp = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if cp.stdout:
        print(cp.stdout, end="")
    if cp.stderr:
        print(cp.stderr, end="", file=sys.stderr)
    if cp.returncode != 0:
        raise subprocess.CalledProcessError(cp.returncode, args, cp.stdout, cp.stderr)
    return cp


def ssh_base(host: str, port: str, user: str, key: Optional[str]) -> list[str]:
    args = ["ssh", "-p", str(port), "-o", "BatchMode=yes"]
    if key:
        args += ["-i", key]
    args.append(f"{user}@{host}")
    return args


def scp_base(port: str, key: Optional[str]) -> list[str]:
    args = ["scp", "-P", str(port), "-o", "BatchMode=yes"]
    if key:
        args += ["-i", key]
    return args


def main() -> int:
    started = time.time()
    load_dotenv()
    lock = acquire_lock()
    snapshot = BASE_DIR / SNAPSHOT_NAME
    try:
        source_db = Path(os.getenv("BESTHOME_DB_PATH", str(BASE_DIR / "besthome.db"))).expanduser()
        host = os.getenv("VPS_HOST")
        port = os.getenv("VPS_PORT", "22")
        user = os.getenv("VPS_USER")
        key = os.getenv("VPS_SSH_KEY")
        remote_db = os.getenv("VPS_REMOTE_DB_PATH", DEFAULT_REMOTE_DB)
        if not host or not user:
            raise RuntimeError("VPS_HOST and VPS_USER are required")
        log(f"Validating local DB: {source_db}")
        create_sqlite_snapshot(source_db, snapshot)
        count = validate_sqlite(snapshot)
        size = snapshot.stat().st_size
        digest = sha256_file(snapshot)
        remote_dir = os.path.dirname(remote_db.rstrip("/"))
        remote_tmp = f"{remote_dir}/.besthome.db.uploading-{int(time.time())}-{uuid.uuid4().hex}"
        remote = f"{user}@{host}:{remote_tmp}"
        for attempt in range(1, SCP_RETRIES + 2):
            try:
                log(f"Uploading temporary DB attempt={attempt} path={remote_tmp}")
                run_cmd(scp_base(port, key) + [str(snapshot), remote], SCP_TIMEOUT)
                break
            except Exception:
                if attempt > SCP_RETRIES:
                    raise
                time.sleep(3)
        activate_cmd = f"sudo /var/www/besthome-bot/scripts/activate_besthome_db.sh {remote_tmp} {digest}"
        run_cmd(ssh_base(host, port, user, key) + [activate_cmd], SSH_TIMEOUT)
        verify_cmd = (
            "python3 - <<'PYR'\n"
            f"import hashlib,json,os,sqlite3; p='{remote_db}';\n"
            "h=hashlib.sha256(open(p,'rb').read()).hexdigest();\n"
            "c=sqlite3.connect(p); n=c.execute('select count(*) from listings').fetchone()[0];\n"
            "mx=c.execute('select max(date_read) from listings').fetchone()[0];\n"
            "up=c.execute('select max(updated_at) from listings').fetchone()[0] if 'updated_at' in [r[1] for r in c.execute('pragma table_info(listings)')] else None; c.close();\n"
            "print(json.dumps({'remote_sha256':h,'listings_count':n,'latest_listing_date':mx,'latest_updated_at':up,'size':os.path.getsize(p)}))\nPYR"
        )
        cp = run_cmd(ssh_base(host, port, user, key) + [verify_cmd], SSH_TIMEOUT)
        remote_info = json.loads(cp.stdout.strip().splitlines()[-1])
        if remote_info["remote_sha256"] != digest:
            raise RuntimeError("Remote SHA-256 mismatch")
        log("DEPLOY_OK")
        log(f"local_sha256={digest}")
        log(f"remote_sha256={remote_info['remote_sha256']}")
        log(f"size={size}")
        log(f"listings_count={count}")
        log(f"latest_listing_date={remote_info.get('latest_listing_date')}")
        log(f"duration={time.time() - started:.1f}s")
        return 0
    finally:
        try:
            if snapshot.exists():
                snapshot.unlink()
        finally:
            lock.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"DEPLOY_FAILED {exc}")
        raise SystemExit(1)
