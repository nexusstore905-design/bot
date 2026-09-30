"""
Auth handler — professional UI with invite-code access system.
"""
import html
import logging
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, MessageHandler, filters,
)

from database.database import AsyncSessionLocal
from database.repositories.user_repo import UserRepository
from database.models import AuthStatus
from bot.states.states import ENTER_PIN
from bot.keyboards.customer_kb import main_menu_kb, back_to_menu_kb
from bot.middlewares.auth_middleware import is_admin
from config.settings import STORE_NAME

logger = logging.getLogger(__name__)

BRAND = f"✨ <b>{html.escape(STORE_NAME.strip().upper(), quote=False)}</b> ✨"


def _welcome_text(name: str, returning: bool = False) -> str:
    safe_name = html.escape(name, quote=False)
    greeting = "Welcome back" if returning else "Welcome"
    return (
        f"{BRAND}\n"
        "<i>Your order and tracking center</i>\n\n"
        f"👋 {greeting}, <b>{safe_name}</b>!\n\n"
        "Choose an option below to get started."
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_admin(user.id):
        await update.message.reply_text(
            f"{BRAND}\n\n"
            f"👋  Hello, <b>{html.escape(user.first_name, quote=False)}</b>!\n\n"
            "You are signed in as an <b>administrator</b>.\n"
            "Send /admin to open the control panel.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)

        if db_user.auth_status == AuthStatus.revoked:
            await update.message.reply_text(
                "⛔ <b>Access unavailable</b>\n\n"
                "Your account cannot use this bot right now.\n"
                "Please contact the administrator.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        if await repo.is_locked(db_user):
            import datetime
            from datetime import timezone
            remaining = int((db_user.locked_until - datetime.datetime.now(timezone.utc)).total_seconds() // 60) + 1
            await update.message.reply_text(
                "🔒 <b>Try again later</b>\n\n"
                f"There were too many incorrect codes. Try again in <b>{remaining} minute(s)</b>.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        if await repo.is_session_valid(db_user):
            await _show_main_menu(update, user.first_name, returning=True)
            return ConversationHandler.END

    await update.message.reply_text(
        f"{BRAND}\n\n"
        "🔐 <b>Member sign in</b>\n\n"
        "This bot is available to invited members.\n"
        "Enter the one-time access code from your administrator.\n\n"
        "<i>Your code message is deleted after submission.</i>",
        parse_mode="HTML",
    )
    return ENTER_PIN


async def handle_code_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    code_input = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)
        success, message = await repo.try_register_with_code(db_user, code_input)

    if success:
        await update.effective_chat.send_message(
            f"✅ <b>You're in!</b>\n\n"
            f"Welcome to <b>{html.escape(STORE_NAME, quote=False)}</b>, "
            f"<b>{html.escape(user.first_name, quote=False)}</b>.",
            parse_mode="HTML",
        )
        await _show_main_menu_chat(update.effective_chat.id, context, user.first_name)
        return ConversationHandler.END
    else:
        await update.effective_chat.send_message(
            "❌ <b>That code did not work</b>\n\n"
            f"<code>{html.escape(message, quote=False)}</code>",
            parse_mode="HTML",
        )
        if "locked" in message.lower() or "revoked" in message.lower():
            return ConversationHandler.END
        return ENTER_PIN


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_admin(user.id):
        await update.message.reply_text("ℹ️  Admins do not use sessions.")
        return

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_by_telegram_id(user.id)
        if db_user:
            await repo.logout(db_user)

    context.user_data.clear()
    await update.message.reply_text(
        "🔓 <b>Signed out</b>\n\n"
        "Send /start whenever you want to sign in again.",
        parse_mode="HTML",
    )


async def _show_main_menu(update: Update, name: str, returning: bool = False):
    await update.message.reply_text(
        _welcome_text(name, returning),
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


async def _show_main_menu_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE, name: str = ""):
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            _welcome_text(name) if name else
            f"{BRAND}\n\nChoose an option below to get started."
        ),
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.middlewares.auth_middleware import require_auth
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        _welcome_text(update.effective_user.first_name, returning=True),
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


async def cb_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_by_telegram_id(user.id)
        if db_user:
            await repo.logout(db_user)
    context.user_data.clear()
    await update.callback_query.message.reply_text(
        "🔓 <b>Signed out</b>\n\n"
        "Send /start whenever you want to sign in again.",
        parse_mode="HTML",
    )


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.middlewares.auth_middleware import require_auth
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "💬 <b>Help and order guide</b>\n\n"
        "<b>Place an order</b>\n"
        "1. Choose a category and package.\n"
        "2. Select the quantity.\n"
        "3. Enter your PUBG Player ID.\n"
        "4. Review the details and submit.\n\n"
        "We will message you when the order status changes.\n\n"
        "<b>Status guide</b>\n"
        "⏳ Pending · waiting for processing\n"
        "⚙️ Processing · supplier is working on it\n"
        "✅ Completed · order is finished\n"
        "❌ Failed · contact support for help\n\n"
        "Commands: /start · /myorders · /logout",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(),
    )


async def cb_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.middlewares.auth_middleware import require_auth
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"🏪 <b>About {html.escape(STORE_NAME, quote=False)}</b>\n\n"
        "Use this bot to submit orders and follow their progress.\n\n"
        "📦 Choose a product and enter the correct player ID.\n"
        "📬 Order updates arrive here in Telegram.\n"
        "🤝 Orders are sent to the supplier assigned to the product category.",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(),
    )


def get_auth_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ENTER_PIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_entry)
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
        per_message=False,
    )
