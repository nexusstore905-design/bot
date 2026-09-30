"""
Auth middleware — verifies every update before it reaches handlers.
Unauthenticated users are redirected to the PIN entry flow.
"""
from telegram import Update
from telegram.ext import ContextTypes
import time

from database.database import AsyncSessionLocal
from database.repositories.user_repo import UserRepository
from database.models import AuthStatus
from config.settings import ADMIN_IDS

# Inbound Anti-Flood memory (User ID -> Timestamp)
USER_LAST_REQUEST = {}

async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check authentication for any update.
    Returns True if authenticated, False otherwise.
    If not authenticated, sends an appropriate message.
    """
    user = update.effective_user
    if user is None:
        return False
        
    # ─── Anti-Flood Check (max 2 actions per second per user) ───
    now = time.time()
    last = USER_LAST_REQUEST.get(user.id, 0)
    if now - last < 0.5:
        # User is clicking too fast — silently drop to prevent load
        return False
    USER_LAST_REQUEST[user.id] = now

    # Admins bypass PIN auth (they are identified by Telegram ID in ADMIN_IDS)
    if user.id in ADMIN_IDS:
        return True

    import os
    if os.path.exists("maintenance.flag"):
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if msg:
            await msg.reply_text(
                "🚧 <b>MAINTENANCE MODE</b>\n\n"
                "The bot is currently turned OFF for maintenance.\n"
                "Please check back later! 🙏",
                parse_mode="HTML"
            )
        return False

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)

        if db_user.auth_status == AuthStatus.revoked:
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if msg:
                await msg.reply_text("⛔  Your access has been revoked. Contact admin.")
            return False

        if await repo.is_locked(db_user):
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if msg:
                from datetime import timezone
                import datetime
                remaining = (db_user.locked_until - datetime.datetime.now(timezone.utc)).seconds // 60 + 1
                await msg.reply_text(f"🔒  Account locked. Try again in {remaining} minutes.")
            return False

        if await repo.is_session_valid(db_user):
            context.user_data["db_user_id"] = db_user.id
            context.user_data["tg_user_id"] = user.id
            return True

    return False


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS
