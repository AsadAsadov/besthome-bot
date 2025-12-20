from functools import wraps
from typing import Optional

from config import PRIMARY_ADMIN_ID
from core.bot_instance import bot
from core.logging import logger


def safe_answer_callback_query(callback_id: Optional[str], text: Optional[str] = None, **kwargs):
    if not callback_id:
        return
    try:
        bot.answer_callback_query(callback_id, text, **kwargs)
    except Exception:
        logger.exception("answer_callback_query failed callback_id=%s", callback_id)


def callback_guard(handler):
    @wraps(handler)
    def wrapper(call):
        safe_answer_callback_query(call.id)
        logger.info(
            "callback entry handler=%s chat_id=%s from=%s data=%s",
            handler.__name__,
            getattr(getattr(call, "message", None), "chat", None).id
            if getattr(call, "message", None)
            else None,
            getattr(getattr(call, "from_user", None), "id", None),
            getattr(call, "data", None),
        )
        try:
            return handler(call)
        except Exception as exc:
            logger.exception("Callback failed data=%s", getattr(call, "data", None))
            chat_id = None
            if call and getattr(call, "message", None):
                chat_id = call.message.chat.id
            notify_chat_id = PRIMARY_ADMIN_ID
            if notify_chat_id is not None:
                try:
                    bot.send_message(
                        notify_chat_id,
                        f"⚠️ Xəta oldu: {exc} (chat_id={chat_id})",
                    )
                except Exception:
                    logger.exception("Admin send failed chat_id=%s", notify_chat_id)

    return wrapper
