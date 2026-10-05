"""
Auth middleware — verifies every update before it reaches handlers.
Unauthenticated users are redirected to the access-code sign-in flow.
"""
import time
from collections import OrderedDict

from telegram import Update
from telegram.ext import ContextTypes

from bot.i18n import guess_language, normalize, t
from config.settings import ADMIN_IDS, STAFF_IDS
from database.database import AsyncSessionLocal
from database.models import AuthStatus
from database.repositories.user_repo import UserRepository
from services import app_settings
from utils.helpers import utcnow

# Inbound anti-flood memory (user ID -> timestamp)
USER_LAST_REQUEST: OrderedDict[int, float] = OrderedDict()
REQUEST_WINDOW_SECONDS = 60.0
REQUEST_CACHE_MAX_USERS = 4096
MIN_SECONDS_BETWEEN_ACTIONS = 0.5


def is_admin(telegram_id: int) -> bool:
    """Owners: full control of the bot."""
    return telegram_id in ADMIN_IDS


def is_staff(telegram_id: int) -> bool:
    return telegram_id in STAFF_IDS


def is_team(telegram_id: int) -> bool:
    """Owners and staff."""
    return telegram_id in ADMIN_IDS or telegram_id in STAFF_IDS


def user_language(context: ContextTypes.DEFAULT_TYPE, telegram_user=None) -> str:
    """The cached language for this chat, guessed from Telegram until the user picks one."""
    lang = context.user_data.get("lang")
    if lang:
        return normalize(lang)
    return guess_language(getattr(telegram_user, "language_code", None))


def _throttled(user_id: int) -> bool:
    now = time.monotonic()
    last = USER_LAST_REQUEST.get(user_id, 0)
    if now - last < MIN_SECONDS_BETWEEN_ACTIONS:
        return True
    USER_LAST_REQUEST[user_id] = now
    USER_LAST_REQUEST.move_to_end(user_id)
    while USER_LAST_REQUEST:
        _, oldest = next(iter(USER_LAST_REQUEST.items()))
        if now - oldest <= REQUEST_WINDOW_SECONDS:
            break
        USER_LAST_REQUEST.popitem(last=False)
    while len(USER_LAST_REQUEST) > REQUEST_CACHE_MAX_USERS:
        USER_LAST_REQUEST.popitem(last=False)
    return False


async def _reject(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        context.user_data["auth_rejection_sent"] = True
        await message.reply_text(text, parse_mode="HTML")


async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check authentication for any update.
    Returns True if authenticated, False otherwise (sending an explanation when useful).
    """
    user = update.effective_user
    if user is None:
        return False
    if _throttled(user.id):
        context.user_data["auth_rate_limited"] = True
        return False

    # The team (owners and staff) is identified by Telegram ID and skips sign-in.
    if is_team(user.id):
        context.user_data.setdefault("lang", "en")
        return True

    lang = user_language(context, user)
    if await app_settings.is_maintenance():
        await _reject(update, context, t(lang, "maintenance"))
        return False

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)
        if db_user.language:
            context.user_data["lang"] = db_user.language
            lang = db_user.language

        if db_user.auth_status == AuthStatus.revoked:
            await _reject(update, context, t(lang, "access_revoked"))
            return False

        if await repo.is_locked(db_user):
            minutes = int((db_user.locked_until - utcnow()).total_seconds() // 60) + 1
            await _reject(update, context, t(lang, "account_locked", minutes=minutes))
            return False

        if await repo.is_session_valid(db_user):
            context.user_data["db_user_id"] = db_user.id
            context.user_data["tg_user_id"] = user.id
            return True

    return False


async def require_callback_auth(update: Update, context: ContextTypes.DEFAULT_TYPE, toast: str | None = None) -> bool:
    """Acknowledge a button press immediately (optionally with a toast), then authenticate."""
    query = update.callback_query
    if query:
        try:
            await query.answer(toast)
        except Exception:
            pass

    context.user_data.pop("auth_rate_limited", None)
    context.user_data.pop("auth_rejection_sent", None)
    if await require_auth(update, context):
        return True

    throttled = context.user_data.pop("auth_rate_limited", False)
    rejection_sent = context.user_data.pop("auth_rejection_sent", False)
    if query and query.message and not rejection_sent:
        lang = user_language(context, update.effective_user)
        await query.message.reply_text(t(lang, "slow_down" if throttled else "need_signin"))
    return False
