"""Customer support: customers write in, admins answer by replying to the forwarded message."""
import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

from bot.keyboards.customer_kb import back_to_menu_kb, main_menu_kb, support_cancel_kb
from bot.middlewares.auth_middleware import require_auth, require_callback_auth
from bot.states.states import SUPPORT_MESSAGE
from config.settings import ADMIN_IDS
from database.database import AsyncSessionLocal
from database.repositories.customer_extras_repo import SupportRepository
from database.repositories.order_repo import OrderRepository
from database.repositories.user_repo import UserRepository
from utils.ui import esc

logger = logging.getLogger(__name__)

MAX_SUPPORT_LENGTH = 2000


async def cb_support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
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
    about = f" about order <code>{esc(order_id)}</code>" if order_id else ""
    await update.callback_query.edit_message_text(
        f"🆘 <b>Contact support</b>{about}\n\n"
        "Send your message in one text. An admin will reply here in this chat.",
        parse_mode="HTML",
        reply_markup=support_cancel_kb(),
    )
    return SUPPORT_MESSAGE


async def msg_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.message.reply_text("🔐 Please /start to sign in first.")
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text:
        return SUPPORT_MESSAGE
    if len(text) > MAX_SUPPORT_LENGTH:
        await update.message.reply_text(f"Please keep it under {MAX_SUPPORT_LENGTH} characters.")
        return SUPPORT_MESSAGE
    user = update.effective_user
    order_id = context.user_data.pop("support_order_id", None)
    who = esc(user.full_name or "Customer")
    if user.username:
        who += f" (@{esc(user.username)})"
    admin_text = (
        "🆘 <b>SUPPORT REQUEST</b>\n\n"
        f"From: {who}\nTelegram ID: <code>{user.id}</code>\n"
        + (f"Order: <code>{esc(order_id)}</code>\n" if order_id else "")
        + f"\n{esc(text)}\n\n<i>Reply to this message to answer the customer.</i>"
    )
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            sent = await context.bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
            async with AsyncSessionLocal() as session:
                await SupportRepository(session).record(admin_id, sent.message_id, user.id, order_id)
            delivered += 1
        except Exception as exc:
            logger.warning("Could not deliver support message to admin %s (%s)", admin_id, type(exc).__name__)
    if delivered:
        reply = "✅ <b>Message sent.</b>\n\nAn admin will reply here as soon as possible."
    else:
        reply = "⚠️ <b>We couldn’t reach support right now.</b> Please try again later."
    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=main_menu_kb())
    return ConversationHandler.END


async def cb_support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("support_order_id", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "✖ Support request cancelled.", reply_markup=back_to_menu_kb(),
        )
    else:
        await update.message.reply_text("✖ Support request cancelled.", reply_markup=back_to_menu_kb())
    return ConversationHandler.END


async def admin_support_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """An admin replying to a SUPPORT REQUEST message answers that customer."""
    message = update.message
    if not message or not message.reply_to_message:
        return
    async with AsyncSessionLocal() as session:
        link = await SupportRepository(session).find(message.chat.id, message.reply_to_message.message_id)
    if link is None:
        return
    about = f" about order <code>{esc(link.order_id)}</code>" if link.order_id else ""
    try:
        if message.text:
            await context.bot.send_message(
                chat_id=link.customer_telegram_id,
                text=f"💬 <b>Support reply</b>{about}\n\n{esc(message.text)}",
                parse_mode="HTML",
                reply_markup=main_menu_kb(),
            )
        else:
            await context.bot.send_message(
                chat_id=link.customer_telegram_id,
                text=f"💬 <b>Support reply</b>{about}",
                parse_mode="HTML",
            )
            await message.copy(chat_id=link.customer_telegram_id)
        await message.reply_text("✅ Reply sent to the customer.")
    except Exception as exc:
        logger.warning("Could not deliver support reply to %s (%s)", link.customer_telegram_id, type(exc).__name__)
        await message.reply_text("⚠️ The customer could not be reached (they may have blocked the bot).")


def get_support_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_support_start, pattern=r"^support(:[A-Z0-9]+)?$")],
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


def get_admin_support_reply_handler() -> MessageHandler:
    return MessageHandler(
        filters.ChatType.PRIVATE & filters.REPLY & filters.User(user_id=ADMIN_IDS) & ~filters.COMMAND,
        admin_support_reply,
    )
