import time
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from meta_client import send_instagram_dm

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")

TRIGGER_TYPE_LABELS = {
    "exact": "Tam uyğunluq (dəqiq eynidir)",
    "contains": "Mətnin içində var",
    "regex": "Qaydaya görə (regex)",
    "any": "İstənilən gələn DM",
}


@router.get("")
async def admin_inbox(request: Request):
    threads = db.list_threads(limit=100)
    selected_thread = threads[0]["thread_id"] if threads else None
    messages = db.get_thread_messages(selected_thread) if selected_thread else []
    return templates.TemplateResponse(
        "admin_index.html",
        {
            "request": request,
            "threads": threads,
            "messages": messages,
            "selected_thread": selected_thread,
            "active_page": "inbox",
        },
    )


@router.get("/thread/{thread_id}")
async def admin_thread(request: Request, thread_id: str):
    threads = db.list_threads(limit=100)
    messages = db.get_thread_messages(thread_id)
    return templates.TemplateResponse(
        "thread.html",
        {
            "request": request,
            "threads": threads,
            "messages": messages,
            "selected_thread": thread_id,
            "active_page": "inbox",
        },
    )


@router.post("/message-reply")
async def admin_message_reply(thread_id: str = Form(...), text: str = Form(...)):
    text = text.strip()
    if text:
        send_result = send_instagram_dm(thread_id, text)
        if send_result.get("ok"):
            db.insert_dm_event(
                event_time=int(time.time() * 1000),
                ig_business_id=None,
                message_id=(send_result.get("data") or {}).get("message_id"),
                sender_id="me",
                recipient_id=thread_id,
                thread_id=thread_id,
                direction="outgoing",
                text=text,
                payload_json=str(send_result),
            )
    return RedirectResponse(url=f"/admin/thread/{thread_id}", status_code=303)


@router.get("/templates")
async def admin_templates(request: Request):
    data = db.list_templates()
    return templates.TemplateResponse(
        "templates.html",
        {
            "request": request,
            "templates_data": data,
            "labels": TRIGGER_TYPE_LABELS,
            "active_page": "templates",
        },
    )


@router.post("/templates")
async def create_template(
    name: str = Form(...),
    trigger_type: str = Form(...),
    trigger_value: str = Form(""),
    reply_text: str = Form(...),
    is_active: str = Form("1"),
):
    db.create_template(
        name=name,
        trigger_type=trigger_type,
        trigger_value=trigger_value,
        reply_text=reply_text,
        is_active=1 if is_active == "1" else 0,
    )
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/{template_id}/delete")
async def remove_template(template_id: int):
    db.delete_template(template_id)
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.get("/posts")
async def posts_placeholder(request: Request):
    return templates.TemplateResponse(
        "posts.html",
        {
            "request": request,
            "active_page": "posts",
            "now": datetime.utcnow(),
        },
    )
