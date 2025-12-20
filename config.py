import os
from typing import List

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN env dəyişəni tapılmadı. Zəhmət olmasa BOT_TOKEN dəyərini təyin edin."
    )

ENV = os.getenv("ENV", "dev")

_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: List[int] = [
    int(item)
    for item in (value.strip() for value in _raw_admin_ids.split(","))
    if item
]

PRIMARY_ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else None
