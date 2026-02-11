import os
from typing import Dict

import requests

GRAPH_BASE = "https://graph.facebook.com/v20.0"


def get_verify_token() -> str:
    return os.getenv("META_VERIFY_TOKEN", "dev-verify-token")


def get_page_access_token() -> str:
    return os.getenv("META_PAGE_ACCESS_TOKEN", "")


def send_instagram_dm(recipient_ig_user_id: str, text: str) -> Dict:
    token = get_page_access_token()
    if not token:
        return {"ok": False, "error": "META_PAGE_ACCESS_TOKEN is not set"}

    url = f"{GRAPH_BASE}/me/messages"
    payload = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": recipient_ig_user_id},
        "message": {"text": text},
    }
    response = requests.post(url, params={"access_token": token}, json=payload, timeout=20)
    if not response.ok:
        return {"ok": False, "status_code": response.status_code, "error": response.text}
    return {"ok": True, "data": response.json()}
