"""Time out unanswered supplier work and tell everyone exactly once."""
import logging
from datetime import timedelta

from config.settings import SUPPLIER_TIMEOUT_MINUTES
from database.database import AsyncSessionLocal
from database.models import OrderStatus
from database.repositories.order_repo import OrderRepository
from services.notify import announce_order_status, close_supplier_message, notify_admins
from utils.helpers import utcnow
from utils.ui import esc

logger = logging.getLogger(__name__)

SUPPLIER_TIMEOUT = timedelta(minutes=SUPPLIER_TIMEOUT_MINUTES)


async def expire_stale_orders(bot) -> int:
    async with AsyncSessionLocal() as session:
        expired = await OrderRepository(session).expire_stale(utcnow(), SUPPLIER_TIMEOUT)
    if not expired:
        return 0

    minutes = SUPPLIER_TIMEOUT_MINUTES
    summary_lines = []
    for info in expired:
        supplier_text = (
            "⏱️ <b>ORDER PART CLOSED — SUPPLIER TIMEOUT</b>\n\n"
            f"Order <code>{esc(info.order_id)}</code> was not answered within {minutes} minutes.\n"
            "Please do not process it."
        )
        for chat_id, message_id in info.timed_out_messages:
            await close_supplier_message(bot, chat_id, message_id, supplier_text)

        if info.status == OrderStatus.cancelled:
            headline = (
                "⏱️ <b>Order auto-cancelled</b>\n"
                f"The supplier did not respond within {minutes} minutes. You can order again anytime."
            )
        elif info.status == OrderStatus.failed:
            headline = (
                "⚠️ <b>Part of your order timed out</b>\n"
                f"Completed: {esc(', '.join(info.completed_categories))}. "
                "Support has been notified about the rest."
            )
        else:
            headline = (
                "⚠️ <b>One supplier group timed out</b>\n"
                "The rest of your order is still being processed."
            )
        await announce_order_status(bot, info.order_id, headline=headline)

        line = f"• <code>{esc(info.order_id)}</code> → <b>{info.status.value.upper()}</b>"
        line += f" · timed out: {esc(', '.join(info.timed_out_categories) or 'whole order')}"
        if info.completed_categories:
            line += f" · completed: {esc(', '.join(info.completed_categories))}"
        summary_lines.append(line)

    await notify_admins(
        bot,
        f"⏱️ <b>SUPPLIER TIMEOUTS</b> ({len(expired)})\n\n" + "\n".join(summary_lines[:30])
        + ("\n…" if len(summary_lines) > 30 else "")
        + "\n\nFailed orders kept their completed parts; review them in Find order.",
    )
    logger.info("Timed out supplier work on %d order(s)", len(expired))
    return len(expired)
