import json
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import db
from meta_client import get_verify_token, send_instagram_dm

router = APIRouter()


def _extract_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for entry in payload.get("entry", []):
        ig_business_id = str(entry.get("id") or "")
        for event in entry.get("messaging", []):
            msg = event.get("message") or {}
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            sender_id = str((event.get("sender") or {}).get("id") or "")
            recipient_id = str((event.get("recipient") or {}).get("id") or "")
            if not sender_id or not recipient_id:
                continue

            is_incoming = sender_id != ig_business_id
            thread_id = sender_id if is_incoming else recipient_id
            direction = "incoming" if is_incoming else "outgoing"
            messages.append(
                {
                    "event_time": int(event.get("timestamp") or int(time.time() * 1000)),
                    "ig_business_id": ig_business_id,
                    "message_id": msg.get("mid"),
                    "sender_id": sender_id,
                    "recipient_id": recipient_id,
                    "thread_id": thread_id,
                    "direction": direction,
                    "text": text,
                    "payload_json": json.dumps(event, ensure_ascii=False),
                }
            )
    return messages


@router.get("/webhook")
async def webhook_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    verify_token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and verify_token == get_verify_token() and challenge:
        return PlainTextResponse(challenge)
    return JSONResponse({"error": "verification_failed"}, status_code=403)


@router.post("/webhook")
async def webhook_events(request: Request):
    payload = await request.json()
    events = _extract_messages(payload)

    for event in events:
        db.insert_dm_event(**event)

        if event["direction"] == "incoming":
            matched = db.find_matching_template(event["text"])
            if matched:
                reply = matched["reply_text"]
                send_result = send_instagram_dm(event["thread_id"], reply)
                if send_result.get("ok"):
                    db.insert_dm_event(
                        event_time=int(time.time() * 1000),
                        ig_business_id=event["ig_business_id"],
                        message_id=(send_result.get("data") or {}).get("message_id"),
                        sender_id=event["recipient_id"],
                        recipient_id=event["sender_id"],
                        thread_id=event["thread_id"],
                        direction="outgoing",
                        text=reply,
                        payload_json=json.dumps(send_result, ensure_ascii=False),
                    )

    return JSONResponse({"ok": True})
