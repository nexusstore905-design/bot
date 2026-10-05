"""
Sign-in with invite codes, including one-tap invite links (t.me/<bot>?start=CODE).
"""
import logging

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.home import send_home
from bot.i18n import t
from bot.keyboards.customer_kb import language_kb
from bot.middlewares.auth_middleware import is_admin, is_team, user_language
from bot.states.states import ENTER_PIN
from config.settings import STORE_NAME
from database.database import AsyncSessionLocal
from database.models import AuthStatus
from database.repositories.user_repo import UserRepository
from utils.helpers import utcnow
from utils.ui import esc, header, quote

logger = logging.getLogger(__name__)


def signin_text(lang: str) -> str:
    return (
        f"{header('✨', STORE_NAME.strip())}\n\n"
        f"{t(lang, 'signin_title')}\n{t(lang, 'signin_body')}\n\n"
        f"<i>{t(lang, 'signin_note')}</i>"
    )


async def _attempt_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)
        success, key, params = await repo.try_register_with_code(db_user, code)
        lang = db_user.language or user_language(context, user)
        if success and not db_user.language:
            await repo.set_language(db_user, lang)
    context.user_data["lang"] = lang

    if success:
        await send_home(update, context, returning=False, notice=t(lang, "signin_ok", name=user.first_name or ""))
        return ConversationHandler.END
    await update.effective_chat.send_message(
        f"{t(lang, 'code_failed')}\n{quote(t(lang, key, **params))}", parse_mode="HTML",
    )
    if key in ("code_locked", "access_revoked"):
        return ConversationHandler.END
    return ENTER_PIN


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_team(user.id):
        role = "an <b>owner</b>" if is_admin(user.id) else "<b>staff</b>"
        await update.message.reply_text(
            f"{header('✨', STORE_NAME.strip())}\n\n"
            f"👋 Hello, <b>{esc(user.first_name)}</b>. You're signed in as {role}.\n"
            "Send /admin to open the control panel.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)
        if db_user.language:
            context.user_data["lang"] = db_user.language
        lang = user_language(context, user)

        if db_user.auth_status == AuthStatus.revoked:
            await update.message.reply_text(t(lang, "access_revoked"))
            return ConversationHandler.END
        if await repo.is_locked(db_user):
            minutes = int((db_user.locked_until - utcnow()).total_seconds() // 60) + 1
            await update.message.reply_text(t(lang, "account_locked", minutes=minutes))
            return ConversationHandler.END
        signed_in = await repo.is_session_valid(db_user)

    if signed_in:
        await send_home(update, context)
        return ConversationHandler.END

    # One-tap invite link: /start CODE
    if context.args:
        return await _attempt_code(update, context, context.args[0])

    await update.message.reply_text(signin_text(lang), parse_mode="HTML", reply_markup=language_kb())
    return ENTER_PIN


async def handle_code_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code_input = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    return await _attempt_code(update, context, code_input)


async def _sign_out(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user = update.effective_user
    lang = user_language(context, user)
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_by_telegram_id(user.id)
        if db_user:
            await repo.logout(db_user)
    context.user_data.clear()
    context.user_data["lang"] = lang
    return t(lang, "signed_out")


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_team(update.effective_user.id):
        await update.message.reply_text("ℹ️  The team doesn't use sign-in sessions.")
        return
    await update.message.reply_text(await _sign_out(update, context), parse_mode="HTML")


async def cb_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(await _sign_out(update, context), parse_mode="HTML")


def get_auth_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ENTER_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_entry)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
        per_message=False,
    )
