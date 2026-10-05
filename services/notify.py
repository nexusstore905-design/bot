"""Customer and admin notifications.

A customer gets one live order card (edited in place, silently) plus at most one
new message per meaningful change, so they are notified without duplicates.
"""
import logging

from telegram.error import BadRequest

from bot.keyboards.customer_kb import order_status_kb
from config.settings import ADMIN_IDS
from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from services import app_settings
from services.messages import is_terminal, order_card_text, status_parts
from utils.ui import esc

logger = logging.getLogger(__name__)

DEFAULT_HEADLINES = {
    "completed": "🎉 <b>Your order is complete</b>",
    "failed": "❌ <b>Your order needs support</b>\nA supplier could not complete it. Our team has been notified.",
    "cancelled": "🚫 <b>Your order was cancelled</b>",
}


async def notify_admins(bot, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not notify admin %s (%s)", admin_id, type(exc).__name__)


async def refresh_customer_card(bot, order, show_prices: bool) -> None:
    if not order.customer_msg_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=order.user.telegram_id,
            message_id=order.customer_msg_id,
            text=order_card_text(order, show_prices),
            parse_mode="HTML",
            reply_markup=order_status_kb(order.order_id, terminal=is_terminal(order)),
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.info("Could not update order card for %s (%s)", order.order_id, exc)
    except Exception as exc:
        logger.warning("Could not update order card for %s (%s)", order.order_id, type(exc).__name__)


async def announce_order_status(bot, order_id: str, headline: str | None = None) -> None:
    """Refresh the customer's order card and send one notice when it matters.

    A notice is sent for terminal statuses (default headline) or whenever a
    headline is given explicitly.
    """
    async with AsyncSessionLocal() as session:
        order = await OrderRepository(session).get_by_order_id(order_id)
    if order is None:
        return
    show_prices = await app_settings.show_prices()
    await refresh_customer_card(bot, order, show_prices)

    if headline is None and is_terminal(order):
        headline = DEFAULT_HEADLINES.get(order.status.value)
    if not headline:
        return
    icon, label, _ = status_parts(order.status.value)
    try:
        await bot.send_message(
            chat_id=order.user.telegram_id,
            text=f"{headline}\n\n🧾 Order <code>{esc(order.order_id)}</code> · {icon} <b>{label}</b>",
            parse_mode="HTML",
            reply_markup=order_status_kb(order.order_id, terminal=is_terminal(order)),
        )
    except Exception as exc:
        logger.warning("Could not notify customer about %s (%s)", order.order_id, type(exc).__name__)


async def close_supplier_message(bot, chat_id: int, message_id: int | None, text: str) -> None:
    """Replace a supplier task message (removing its buttons); send a new one if editing fails."""
    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                parse_mode="HTML", reply_markup=None,
            )
            return
        except Exception as exc:
            logger.info("Could not edit supplier message %s in %s (%s)", message_id, chat_id, type(exc).__name__)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Could not notify supplier chat %s (%s)", chat_id, type(exc).__name__)
