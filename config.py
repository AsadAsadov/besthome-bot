import os
from typing import List

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN env dəyişəni tapılmadı. Zəhmət olmasa BOT_TOKEN dəyərini təyin edin."
    )

ENV = os.getenv("ENV", "dev")

_raw_admin_ids = os.getenv("ADMIN_IDS", "1311851277,899663909")
_raw_admin_id = os.getenv("ADMIN_ID", "")
_raw_admin_values = _raw_admin_ids or _raw_admin_id


def _parse_admin_ids(raw_value: str) -> List[int]:
    admin_ids: List[int] = []
    for item in (value.strip() for value in raw_value.split(",")):
        if not item:
            continue
        try:
            admin_ids.append(int(item))
        except ValueError:
            continue
    return admin_ids


ADMIN_IDS: List[int] = _parse_admin_ids(_raw_admin_values)
ADMINS: List[int] = ADMIN_IDS

PRIMARY_ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else None
