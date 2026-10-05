"""Send one message (text, photo, anything) to every signed-in member."""
import asyncio
import logging

from telegram import Update
from telegram.error import Forbidden, RetryAfter
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import admin_only
from bot.keyboards.admin_kb import admin_main_kb, cancel_conv_kb, confirm_kb
from bot.states.states import ADMIN_BROADCAST_CONFIRM, ADMIN_BROADCAST_MESSAGE
from config.settings import ADMIN_IDS
from database.database import AsyncSessionLocal
from database.repositories.user_repo import UserRepository
from services.audit import audit

logger = logging.getLogger(__name__)


@admin_only
async def cb_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "📣 <b>Broadcast</b>\n\n"
        "Send the message to share with every signed-in member — text, a photo with caption, anything.\n"
        "You’ll see the recipient count and confirm before anything is sent.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_BROADCAST_MESSAGE


@admin_only
async def admin_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        recipients = await UserRepository(session).get_broadcast_recipients(exclude=ADMIN_IDS)
    if not recipients:
        await update.message.reply_text("No signed-in members to message yet.", reply_markup=admin_main_kb())
        return ConversationHandler.END
    context.user_data["broadcast"] = (update.message.chat_id, update.message.message_id)
    await update.message.reply_text(
        f"📣 Send the message above to <b>{len(recipients)}</b> member(s)?",
        parse_mode="HTML",
        reply_markup=confirm_kb("adm_broadcast_confirm", "adm_cancel_conv", "📣  Yes, send it"),
    )
    return ADMIN_BROADCAST_CONFIRM


async def _send_broadcast(bot, admin_chat_id: int, from_chat_id: int, message_id: int) -> None:
    async with AsyncSessionLocal() as session:
        recipients = await UserRepository(session).get_broadcast_recipients(exclude=ADMIN_IDS)
    sent = blocked = failed = 0
    for telegram_id in recipients:
        for attempt in range(2):
            try:
                await bot.copy_message(chat_id=telegram_id, from_chat_id=from_chat_id, message_id=message_id)
                sent += 1
                break
            except RetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 1)
            except Forbidden:
                blocked += 1
                break
            except Exception as exc:
                logger.info("Broadcast to %s failed (%s)", telegram_id, type(exc).__name__)
                failed += 1
                break
        await asyncio.sleep(0.05)
    await bot.send_message(
        chat_id=admin_chat_id,
        text=(
            "📣 <b>Broadcast finished</b>\n\n"
            f"Delivered: <b>{sent}</b>\nBlocked the bot: {blocked}\nOther failures: {failed}"
        ),
        parse_mode="HTML",
    )


@admin_only
async def cb_broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    source = context.user_data.pop("broadcast", None)
    if not source:
        await update.callback_query.message.edit_text("Broadcast expired. Start again.", reply_markup=admin_main_kb())
        return ConversationHandler.END
    await update.callback_query.message.edit_text(
        "📣 Sending in the background… I’ll report when it’s done.", reply_markup=admin_main_kb(),
    )
    await audit(update.effective_user, "broadcast", f"message {source[1]}")
    context.application.create_task(
        _send_broadcast(context.bot, update.effective_chat.id, source[0], source[1]),
        name="broadcast",
    )
    return ConversationHandler.END
