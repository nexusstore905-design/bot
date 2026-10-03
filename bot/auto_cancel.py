"""Cancel unclaimed pending orders and notify their customers."""

import asyncio
import html
import logging
from datetime import timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from database.models import OrderStatus
from config.settings import ADMIN_IDS
from bot.keyboards.customer_kb import order_status_kb
from utils.helpers import utcnow

logger = logging.getLogger(__name__)

PENDING_ORDER_TIMEOUT = timedelta(minutes=10)
AUTO_CANCEL_INTERVAL_SECONDS = 15


async def cancel_expired_pending_orders(bot) -> int:
    cutoff = utcnow() - PENDING_ORDER_TIMEOUT
    async with AsyncSessionLocal() as session:
        expired = await OrderRepository(session).cancel_stale_pending(cutoff)

    for order_id, customer_id, status, customer_msg_id, supplier_messages in expired:
        cancelled = status == OrderStatus.cancelled
        customer_text = (
            "⏱️ <b>ORDER AUTO-CANCELLED</b>\n\n"
            f"Order <code>{html.escape(order_id)}</code> was automatically cancelled because the supplier did not choose Done or Error within 10 minutes.\n\n"
            "If you have already paid, contact support about your payment."
            if cancelled else
            "⚠️ <b>ORDER NEEDS SUPPORT</b>\n\n"
            f"Order <code>{html.escape(order_id)}</code> could not be completed because a supplier did not choose Done or Error within 10 minutes. Please contact support."
        )
        supplier_text = (
            "⏱️ <b>ORDER CANCELLED — SUPPLIER TIMEOUT</b>\n\n"
            f"Order <code>{html.escape(order_id)}</code> was closed because no supplier action was received within 10 minutes."
        )
        for supplier_chat_id, message_id in supplier_messages:
            try:
                await bot.edit_message_text(
                    chat_id=supplier_chat_id,
                    message_id=message_id,
                    text=supplier_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except Exception as exc:
                logger.warning(
                    "Could not close supplier message for timed-out order %s (%s)",
                    order_id, type(exc).__name__,
                )

        if customer_msg_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=customer_id,
                    message_id=customer_msg_id,
                    text=customer_text,
                    parse_mode="HTML",
                    reply_markup=order_status_kb(order_id),
                )
            except Exception as exc:
                logger.warning(
                    "Could not update customer order card for %s (%s)",
                    order_id, type(exc).__name__,
                )

        admin_text = (
            "⏱️ <b>SUPPLIER RESPONSE TIMED OUT</b>\n\n"
            f"Order: <code>{html.escape(order_id)}</code>\n"
            f"Order status: <b>{status.value.upper()}</b>\n"
            "One or more supplier groups did not respond within 10 minutes. Please review the order and contact the customer if needed."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
            except Exception as exc:
                logger.warning(
                    "Could not notify admin %s about timed-out order %s (%s)",
                    admin_id, order_id, type(exc).__name__,
                )
        try:
            await bot.send_message(
                chat_id=customer_id,
                text=customer_text + "\n\nYou can start a new order when ready.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄  View updated order status",
                        callback_data=f"refresh_order:{order_id}:0",
                    )],
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
        logger.info("Expired %d stale supplier order(s)", len(expired))
    return len(expired)


async def run_auto_cancel_worker(bot) -> None:
    logger.info(
        "Supplier expiry worker started: timeout=%s minutes, check interval=%s seconds",
        PENDING_ORDER_TIMEOUT.total_seconds() / 60,
        AUTO_CANCEL_INTERVAL_SECONDS,
    )
    while True:
        try:
            await cancel_expired_pending_orders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Pending-order auto-cancel check failed")

        await asyncio.sleep(AUTO_CANCEL_INTERVAL_SECONDS)
