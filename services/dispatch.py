"""Deliver order parts to supplier groups, with automatic retries.

Used by the bot (customer checkout and the background worker) and by the API.
A part is claimed atomically before sending, so two processes never deliver
the same part. Failed deliveries are retried by the bot worker; after the last
attempt the part fails and admins and the customer are told.
"""
import json
import logging
from dataclasses import dataclass

from bot.keyboards.admin_kb import supplier_done_error_kb
from config.settings import SUPPLIER_TIMEOUT_MINUTES
from database.database import AsyncSessionLocal
from database.repositories.order_repo import MAX_DISPATCH_ATTEMPTS, OrderRepository
from services.messages import supplier_order_text
from services.notify import announce_order_status, notify_team
from utils.helpers import utcnow
from utils.ui import esc

logger = logging.getLogger(__name__)

SENT, RETRY, FAILED, SKIPPED = "sent", "retry", "failed", "skipped"


@dataclass
class DispatchOutcome:
    fulfillment_id: int
    result: str
    category: str = ""
    error: str | None = None


async def dispatch_fulfillment(bot, fulfillment_id: int) -> DispatchOutcome:
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        if not await repo.claim_fulfillment(fulfillment_id):
            return DispatchOutcome(fulfillment_id, SKIPPED)
        part = await repo.get_fulfillment_by_id(fulfillment_id)
        order = part.order
        order_pk, order_id = order.id, order.order_id
        chat_id, category, player_id = part.supplier_chat_id, part.category, order.player_id
        text = supplier_order_text(
            order_id, category, player_id, json.loads(part.items_snapshot),
            created_at=order.created_at, timeout_minutes=SUPPLIER_TIMEOUT_MINUTES,
        )

    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=supplier_done_error_kb(order_id, fulfillment_id, player_id),
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            attempts, final = await repo.record_dispatch_failure(fulfillment_id, error)
            if final:
                await repo.refresh_order_status_from_fulfillments(
                    order_pk, changed_by="supplier_dispatch",
                    note=f"Delivery to {category} failed after {attempts} attempts.",
                )
        logger.warning(
            "Delivery of order %s part %s to %s failed (attempt %s/%s): %s",
            order_id, fulfillment_id, chat_id, attempts, MAX_DISPATCH_ATTEMPTS, type(exc).__name__,
        )
        if not final:
            return DispatchOutcome(fulfillment_id, RETRY, category, error)
        await notify_team(
            bot,
            "⚠️ <b>SUPPLIER DELIVERY FAILED</b>\n\n"
            f"Order: <code>{esc(order_id)}</code>\n"
            f"Product group: {esc(category)}\n"
            f"Supplier chat: <code>{chat_id}</code>\n"
            f"Attempts: {attempts}\n"
            f"Error: <code>{esc(error)}</code>\n\n"
            "Check that the bot is still a member of that group, then use "
            "<b>Find order → Resend</b> or <b>Reassign</b>.",
        )
        await announce_order_status(bot, order_id)
        return DispatchOutcome(fulfillment_id, FAILED, category, error)

    async with AsyncSessionLocal() as session:
        await OrderRepository(session).mark_dispatched(fulfillment_id, message.message_id, utcnow())
    logger.info("Delivered order %s group %s to supplier chat %s", order_id, category, chat_id)
    return DispatchOutcome(fulfillment_id, SENT, category)


async def dispatch_order(bot, order_pk: int) -> list[DispatchOutcome]:
    async with AsyncSessionLocal() as session:
        part_ids = [part.id for part in await OrderRepository(session).get_fulfillments(order_pk)]
    return [await dispatch_fulfillment(bot, part_id) for part_id in part_ids]


async def dispatch_due(bot) -> int:
    """Retry deliveries whose next attempt is due. Returns the number attempted."""
    async with AsyncSessionLocal() as session:
        due = await OrderRepository(session).due_fulfillment_ids()
    attempted = 0
    for part_id in due:
        outcome = await dispatch_fulfillment(bot, part_id)
        if outcome.result != SKIPPED:
            attempted += 1
    return attempted
