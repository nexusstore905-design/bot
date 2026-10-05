"""Customer, staff, and owner notifications.

A customer gets one live order card (edited in place, silently) plus at most one
new message per meaningful change, so they are notified without duplicates.
"""
import logging

from telegram.error import BadRequest

from bot.i18n import t
from bot.keyboards.customer_kb import order_status_kb
from config.settings import ADMIN_IDS, TEAM_IDS
from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from services.messages import is_terminal, order_card_text, status_label
from utils.ui import esc

logger = logging.getLogger(__name__)

DEFAULT_NOTICES = {
    "completed": "n_completed",
    "failed": "n_failed",
    "cancelled": "n_cancelled",
}


async def _broadcast(bot, recipients: list[int], text: str) -> None:
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not notify %s (%s)", chat_id, type(exc).__name__)


async def notify_admins(bot, text: str) -> None:
    """Owners only: errors and business-level events."""
    await _broadcast(bot, ADMIN_IDS, text)


async def notify_team(bot, text: str) -> None:
    """Owners and staff: operational alerts about orders and suppliers."""
    await _broadcast(bot, TEAM_IDS, text)


async def refresh_customer_card(bot, order) -> None:
    if not order.customer_msg_id:
        return
    lang = order.user.language
    try:
        await bot.edit_message_text(
            chat_id=order.user.telegram_id,
            message_id=order.customer_msg_id,
            text=order_card_text(order, lang),
            parse_mode="HTML",
            reply_markup=order_status_kb(order.order_id, lang, terminal=is_terminal(order)),
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.info("Could not update order card for %s (%s)", order.order_id, exc)
    except Exception as exc:
        logger.warning("Could not update order card for %s (%s)", order.order_id, type(exc).__name__)


async def announce_order_status(bot, order_id: str, notice: str | None = None, **params) -> None:
    """Refresh the customer's order card and send one notice when it matters.

    `notice` is an i18n key. Terminal statuses get a default notice; open
    statuses only notify when a notice is given explicitly.
    """
    async with AsyncSessionLocal() as session:
        order = await OrderRepository(session).get_by_order_id(order_id)
    if order is None:
        return
    await refresh_customer_card(bot, order)

    if notice is None and is_terminal(order):
        notice = DEFAULT_NOTICES.get(order.status.value)
    if not notice:
        return
    lang = order.user.language
    try:
        await bot.send_message(
            chat_id=order.user.telegram_id,
            text=(
                f"{t(lang, notice, **params)}\n\n"
                f"🧾 <code>{esc(order.order_id)}</code> · {status_label(order.status.value, lang)}"
            ),
            parse_mode="HTML",
            reply_markup=order_status_kb(order.order_id, lang, terminal=is_terminal(order)),
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
