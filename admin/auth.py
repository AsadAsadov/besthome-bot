import os
import hashlib
import hmac
from typing import Optional

ADMIN_USERNAME_ENV = "ADMIN_USERNAME"
ADMIN_PASSWORD_HASH_ENV = "ADMIN_PASSWORD_HASH"


def _verify_password_hash(password: str, stored_hash: str) -> bool:
    try:
        algo, iter_str, salt, digest = str(stored_hash).split("$")
        iterations = int(iter_str)
    except Exception:
        return False
    if algo != "pbkdf2_sha256":
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return hmac.compare_digest(computed, digest)


def load_admin_credentials() -> Optional[tuple[str, str]]:
    username = os.environ.get(ADMIN_USERNAME_ENV)
    pwd_hash = os.environ.get(ADMIN_PASSWORD_HASH_ENV)
    if not username or not pwd_hash:
        return None
    return username, pwd_hash


def authenticate(username: str, password: str) -> bool:
    creds = load_admin_credentials()
    if not creds:
        return False
    stored_username, pwd_hash = creds
    if username != stored_username:
        return False
    return _verify_password_hash(password, pwd_hash)
