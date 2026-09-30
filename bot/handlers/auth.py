"""
Auth handler — professional UI with invite-code access system.
"""
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
from config.settings import STORE_NAME, BINANCE_ID

logger = logging.getLogger(__name__)

LOGO = f"""
┌──────────────────────────┐
│   🏪  <b>{STORE_NAME.upper()}</b>   │
└──────────────────────────┘"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_admin(user.id):
        await update.message.reply_text(
            f"{LOGO}\n\n"
            f"👋  Hey <b>{user.first_name}</b>!\n\n"
            f"You are signed in as <b>Administrator</b>.\n"
            f"Use /admin to open the control panel.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)

        if db_user.auth_status == AuthStatus.revoked:
            await update.message.reply_text(
                "⛔  <b>Access Revoked</b>\n\n"
                "Your access to this bot has been revoked.\n"
                "Contact the administrator for assistance.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        if await repo.is_locked(db_user):
            import datetime
            from datetime import timezone
            remaining = int((db_user.locked_until - datetime.datetime.now(timezone.utc)).total_seconds() // 60) + 1
            await update.message.reply_text(
                "🔒  <b>Account Temporarily Locked</b>\n\n"
                f"Too many failed attempts.\n"
                f"Please wait <b>{remaining} minute(s)</b> and try again.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        if await repo.is_session_valid(db_user):
            await _show_main_menu(update, user.first_name, returning=True)
            return ConversationHandler.END

    await update.message.reply_text(
        f"{LOGO}\n\n"
        f"🔐  <b>Private Bot — Access Required</b>\n\n"
        f"This bot is restricted to authorized\n"
        f"members only.\n\n"
        f"┌─────────────────────────┐\n"
        f"│  Enter your <b>access code</b>   │\n"
        f"│  provided by the admin.   │\n"
        f"└─────────────────────────┘\n\n"
        f"<i>Your message will be deleted\n"
        f"immediately for security.</i>",
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
            f"✅  <b>Access Granted!</b>\n\n"
            f"Welcome to <b>{STORE_NAME}</b>, <b>{user.first_name}</b>! 🎉",
            parse_mode="HTML",
        )
        await _show_main_menu_chat(update.effective_chat.id, context, user.first_name)
        return ConversationHandler.END
    else:
        await update.effective_chat.send_message(
            f"❌  <b>Access Denied</b>\n\n"
            f"<code>{message}</code>",
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
        "🔓  <b>Logged Out</b>\n\n"
        "You have been signed out successfully.\n"
        "Send /start to sign in again.",
        parse_mode="HTML",
    )


async def _show_main_menu(update: Update, name: str, returning: bool = False):
    greeting = f"Welcome back, <b>{name}</b>! 👋" if returning else f"Welcome, <b>{name}</b>! 🎉"
    await update.message.reply_text(
        f"{LOGO}\n\n"
        f"{greeting}\n\n"
        f"⚡ Fast Delivery  •  🔒 Secure\n"
        f"💎 Premium UC  •  💳 Binance Pay\n\n"
        f"┌─────────────────────────┐\n"
        f"│  What would you like to do?  │\n"
        f"└─────────────────────────┘",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


async def _show_main_menu_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE, name: str = ""):
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{LOGO}\n\n"
            f"{'👋  ' + name + chr(10) + chr(10) if name else ''}"
            f"⚡ Fast Delivery  •  🔒 Secure\n"
            f"💎 Premium UC  •  💳 Binance Pay\n\n"
            f"┌─────────────────────────┐\n"
            f"│  What would you like to do?  │\n"
            f"└─────────────────────────┘"
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
    user = update.effective_user
    await update.callback_query.message.reply_text(
        f"{LOGO}\n\n"
        f"⚡ Fast Delivery  •  🔒 Secure\n"
        f"💎 Premium UC  •  💳 Binance Pay\n\n"
        f"┌─────────────────────────┐\n"
        f"│  What would you like to do?  │\n"
        f"└─────────────────────────┘",
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
        "🔓  <b>Logged Out Successfully</b>\n\n"
        "See you next time! 👋\n"
        "Send /start to sign in again.",
        parse_mode="HTML",
    )


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.middlewares.auth_middleware import require_auth
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"┌──────────────────────────┐\n"
        f"│   💬  SUPPORT & HELP       │\n"
        f"└──────────────────────────┘\n\n"
        f"<b>📋 How To Order:</b>\n\n"
        f"  1️⃣  Tap <b>Place an Order</b>\n"
        f"  2️⃣  Choose your UC package\n"
        f"  3️⃣  Enter your PUBG Player ID\n"
        f"  4️⃣  Review order summary\n"
        f"  5️⃣  Confirm & submit\n"
        f"  6️⃣  Get notified when delivered ✅\n\n"
        f"<b>📦 Order Statuses:</b>\n"
        f"  ⏳ Pending — received\n"
        f"  ⚙️ Processing — being processed\n"
        f"  ✅ Completed — UC delivered!\n"
        f"  ❌ Failed — issue occurred\n\n"
        f"<b>🔧 Commands:</b>\n"
        f"  /start — Main menu\n"
        f"  /myorders — View your orders\n"
        f"  /logout — Sign out\n",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(),
    )


async def cb_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.middlewares.auth_middleware import require_auth
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return
    from config.settings import BINANCE_ID, CURRENCY
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"┌──────────────────────────┐\n"
        f"│   ℹ️  ABOUT {STORE_NAME.upper()[:12]}   │\n"
        f"└──────────────────────────┘\n\n"
        f"🏪  <b>{STORE_NAME}</b>\n\n"
        f"Your trusted source for PUBG UC\n"
        f"and in-game top-ups.\n\n"
        f"<b>💳 Payment Method:</b>\n"
        f"  Binance Pay\n"
        f"  ID: <code>{BINANCE_ID}</code>\n"
        f"  Currency: <b>{CURRENCY}</b>\n\n"
        f"<b>⚡ Our Promise:</b>\n"
        f"  • Fast delivery\n"
        f"  • 100% secure transactions\n"
        f"  • Trusted service\n"
        f"  • 24/7 availability\n",
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
