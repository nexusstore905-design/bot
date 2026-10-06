"""Customer support: customers write in, the team answers by replying to the forwarded message."""
import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.i18n import t
from bot.keyboards.customer_kb import back_to_menu_kb, main_menu_kb, support_cancel_kb
from bot.middlewares.auth_middleware import require_auth, require_callback_auth, user_language
from bot.states.states import SUPPORT_MESSAGE
from config.settings import TEAM_IDS
from database.database import AsyncSessionLocal
from database.repositories.customer_extras_repo import SupportRepository
from database.repositories.order_repo import OrderRepository
from database.repositories.user_repo import UserRepository
from utils.ui import esc, quote

logger = logging.getLogger(__name__)

MAX_SUPPORT_LENGTH = 2000


def _support_prompt(lang: str, order_id: str | None) -> str:
    about = f"\n{t(lang, 'support_about', order_id=order_id)}" if order_id else ""
    return f"{t(lang, 'support_title')}{about}\n\n{t(lang, 'support_prompt')}"


async def cb_support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = user_language(context, update.effective_user)
    parts = update.callback_query.data.split(":", 1)
    order_id = parts[1] if len(parts) == 2 else None
    if order_id:
        # Only attach orders that belong to this customer.
        async with AsyncSessionLocal() as session:
            db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
            order = await OrderRepository(session).get_by_order_id(order_id)
        if not db_user or not order or order.user_id != db_user.id:
            order_id = None
    context.user_data["support_order_id"] = order_id
    await update.callback_query.edit_message_text(
        _support_prompt(lang, order_id), parse_mode="HTML", reply_markup=support_cancel_kb(lang),
    )
    return SUPPORT_MESSAGE


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.message.reply_text(t(user_language(context, update.effective_user), "need_signin"))
        return ConversationHandler.END
    lang = user_language(context, update.effective_user)
    context.user_data["support_order_id"] = None
    await update.message.reply_text(
        _support_prompt(lang, None), parse_mode="HTML", reply_markup=support_cancel_kb(lang),
    )
    return SUPPORT_MESSAGE


async def msg_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = user_language(context, update.effective_user)
    context.user_data.pop("auth_rate_limited", None)
    context.user_data.pop("auth_rejection_sent", None)
    if not await require_auth(update, context):
        throttled = context.user_data.pop("auth_rate_limited", False)
        if not context.user_data.pop("auth_rejection_sent", False):
            await update.message.reply_text(t(lang, "slow_down" if throttled else "need_signin"))
        # Keep the conversation open when only throttled, so the customer can resend.
        return SUPPORT_MESSAGE if throttled else ConversationHandler.END
    text = update.message.text.strip()
    if not text:
        return SUPPORT_MESSAGE
    if len(text) > MAX_SUPPORT_LENGTH:
        await update.message.reply_text(t(lang, "support_too_long", limit=MAX_SUPPORT_LENGTH))
        return SUPPORT_MESSAGE
    user = update.effective_user
    order_id = context.user_data.pop("support_order_id", None)
    who = esc(user.full_name or "Customer") + (f" (@{esc(user.username)})" if user.username else "")
    team_text = (
        "🆘 <b>SUPPORT REQUEST</b>\n\n"
        f"From: {who}\nTelegram ID: <code>{user.id}</code>\nLanguage: {lang}\n"
        + (f"Order: <code>{esc(order_id)}</code>\n" if order_id else "")
        + f"{quote(esc(text))}\n<i>Reply to this message to answer the customer.</i>"
    )
    delivered = 0
    for team_id in TEAM_IDS:
        try:
            sent = await context.bot.send_message(chat_id=team_id, text=team_text, parse_mode="HTML")
            async with AsyncSessionLocal() as session:
                await SupportRepository(session).record(team_id, sent.message_id, user.id, order_id)
            delivered += 1
        except Exception as exc:
            logger.warning("Could not deliver support message to %s (%s)", team_id, type(exc).__name__)
    await update.message.reply_text(
        t(lang, "support_sent" if delivered else "support_failed"),
        parse_mode="HTML", reply_markup=main_menu_kb(lang),
    )
    return ConversationHandler.END


async def cb_support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = user_language(context, update.effective_user)
    context.user_data.pop("support_order_id", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(t(lang, "support_cancelled"), reply_markup=back_to_menu_kb(lang))
    else:
        await update.message.reply_text(t(lang, "support_cancelled"), reply_markup=back_to_menu_kb(lang))
    return ConversationHandler.END


async def team_support_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A team member replying to a SUPPORT REQUEST message answers that customer."""
    message = update.message
    if not message or not message.reply_to_message:
        return
    async with AsyncSessionLocal() as session:
        link = await SupportRepository(session).find(message.chat.id, message.reply_to_message.message_id)
        customer = await UserRepository(session).get_by_telegram_id(link.customer_telegram_id) if link else None
    if link is None:
        return
    lang = customer.language if customer else None
    about = f"\n{t(lang, 'support_about', order_id=link.order_id)}" if link.order_id else ""
    heading = f"{t(lang, 'support_reply')}{about}"
    try:
        if message.text:
            await context.bot.send_message(
                chat_id=link.customer_telegram_id,
                text=f"{heading}\n\n{esc(message.text)}",
                parse_mode="HTML",
                reply_markup=main_menu_kb(lang),
            )
        else:
            await context.bot.send_message(chat_id=link.customer_telegram_id, text=heading, parse_mode="HTML")
            await message.copy(chat_id=link.customer_telegram_id)
        await message.reply_text("✅ Reply sent to the customer.")
    except Exception as exc:
        logger.warning("Could not deliver support reply to %s (%s)", link.customer_telegram_id, type(exc).__name__)
        await message.reply_text("⚠️ The customer could not be reached (they may have blocked the bot).")


def get_support_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_support_start, pattern=r"^support(:[A-Z0-9]+)?$"),
            CommandHandler("support", cmd_support),
        ],
        states={
            SUPPORT_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_support)],
        },
        fallbacks=[
            CallbackQueryHandler(cb_support_cancel, pattern=r"^support_cancel$"),
            CommandHandler("cancel", cb_support_cancel),
        ],
        allow_reentry=True,
        per_message=False,
    )


def get_team_support_reply_handler() -> MessageHandler:
    return MessageHandler(
        filters.ChatType.PRIVATE & filters.REPLY & filters.User(user_id=TEAM_IDS) & ~filters.COMMAND,
        team_support_reply,
    )
