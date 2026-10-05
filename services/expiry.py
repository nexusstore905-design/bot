"""Time out unanswered supplier work and tell everyone exactly once."""
import logging
from datetime import timedelta

from config.settings import SUPPLIER_TIMEOUT_MINUTES
from database.database import AsyncSessionLocal
from database.models import OrderStatus
from database.repositories.order_repo import OrderRepository
from services.messages import supplier_notice
from services.notify import announce_order_status, close_supplier_message, notify_team
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
        supplier_text = supplier_notice(
            "⏱️", "CLOSED — NO RESPONSE", info.order_id,
            f"Not answered within {minutes} min.\n🚫 <b>Do not process this order.</b>",
        )
        for chat_id, message_id in info.timed_out_messages:
            await close_supplier_message(bot, chat_id, message_id, supplier_text)

        if info.status == OrderStatus.cancelled:
            await announce_order_status(bot, info.order_id, "n_auto_cancel", minutes=minutes)
        elif info.status == OrderStatus.failed:
            await announce_order_status(
                bot, info.order_id, "n_partial_timeout", done=", ".join(info.completed_categories),
            )
        else:
            await announce_order_status(bot, info.order_id, "n_group_timeout")

        line = f"• <code>{esc(info.order_id)}</code> → <b>{info.status.value.upper()}</b>"
        line += f" · timed out: {esc(', '.join(info.timed_out_categories) or 'whole order')}"
        if info.completed_categories:
            line += f" · completed: {esc(', '.join(info.completed_categories))}"
        summary_lines.append(line)

    await notify_team(
        bot,
        f"⏱️ <b>SUPPLIER TIMEOUTS</b> ({len(expired)})\n\n" + "\n".join(summary_lines[:30])
        + ("\n…" if len(summary_lines) > 30 else "")
        + "\n\nFailed orders kept their completed parts; review them in Find order.",
    )
    logger.info("Timed out supplier work on %d order(s)", len(expired))
    return len(expired)
