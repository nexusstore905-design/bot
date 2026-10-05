"""Shared helpers for the admin panel."""
import functools
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.keyboards.admin_kb import admin_advanced_kb
from bot.middlewares.auth_middleware import is_admin, is_team
from services import app_settings

logger = logging.getLogger("bot.handlers.admin")

STATUS_ICONS = {"pending": "⏳", "processing": "⚙️", "completed": "✅", "failed": "❌", "cancelled": "🚫"}
PAKISTAN_TZ = ZoneInfo("Asia/Karachi")


def _guard(allowed, denial: str):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            if user is None or not allowed(user.id):
                if update.callback_query:
                    await update.callback_query.answer(denial, show_alert=True)
                elif update.effective_message:
                    await update.effective_message.reply_text(denial)
                return ConversationHandler.END
            return await func(update, context)
        return wrapper
    return decorator


# Owners: everything. Staff: orders, customers (read-only payments), dashboard.
admin_only = _guard(is_admin, "⛔ Owners only.")
team_only = _guard(is_team, "⛔ Admins only.")


def local_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PAKISTAN_TZ).strftime("%Y-%m-%d %H:%M PKT")


def admin_label(user) -> str:
    return f"admin:{user.full_name or user.id}"


async def advanced_kb():
    return admin_advanced_kb(is_on=not await app_settings.is_maintenance())


def parse_chat_id_or_forward(message) -> tuple[int | None, str | None]:
    """Read a supplier group ID from a forwarded group message or a typed negative ID."""
    origin = getattr(message, "forward_origin", None)
    source_chat = getattr(origin, "chat", None) or getattr(message, "forward_from_chat", None)
    if source_chat is not None:
        if getattr(source_chat, "type", None) in ("group", "supergroup"):
            return int(source_chat.id), None
        return None, "Forward a message from the supplier group itself (not a person or channel)."
    text = (message.text or "").strip().replace(" ", "")
    try:
        chat_id = int(text)
    except ValueError:
        return None, "Send the negative group ID (for example <code>-1001234567890</code>) or forward a message from the group."
    if chat_id >= 0:
        return None, "Group IDs are negative, usually starting with <code>-100</code>."
    return chat_id, None
