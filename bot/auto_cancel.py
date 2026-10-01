"""Cancel unclaimed pending orders and notify their customers."""

import asyncio
import html
import logging
from datetime import timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from utils.helpers import utcnow

logger = logging.getLogger(__name__)

PENDING_ORDER_TIMEOUT = timedelta(minutes=10)
AUTO_CANCEL_INTERVAL_SECONDS = 60


async def cancel_expired_pending_orders(bot) -> int:
    cutoff = utcnow() - PENDING_ORDER_TIMEOUT
    async with AsyncSessionLocal() as session:
        expired = await OrderRepository(session).cancel_stale_pending(cutoff)

    for order_id, customer_id in expired:
        try:
            await bot.send_message(
                chat_id=customer_id,
                text=(
                    "⏱️  <b>ORDER AUTO-CANCELLED</b>\n\n"
                    f"Order <code>{html.escape(order_id)}</code> was cancelled because "
                    "the supplier did not mark it DONE or ERROR within 10 minutes. "
                    "You can start a new order or view your order list below."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛍  Start a new order", callback_data="order_start")],
                    [InlineKeyboardButton("📋  My orders", callback_data="my_orders")],
                ]),
            )
        except Exception as exc:
            logger.warning(
                "Could not notify customer about auto-cancelled order %s (%s)",
                order_id,
                type(exc).__name__,
            )

    if expired:
        logger.info("Auto-cancelled %d expired pending order(s)", len(expired))
    return len(expired)


async def run_auto_cancel_worker(bot) -> None:
    while True:
        try:
            await cancel_expired_pending_orders(bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Pending-order auto-cancel check failed (%s)", type(exc).__name__)

        await asyncio.sleep(AUTO_CANCEL_INTERVAL_SECONDS)
