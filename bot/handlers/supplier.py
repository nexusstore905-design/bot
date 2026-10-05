"""Supplier actions scoped to the supplier group assigned to each order part."""
import json
import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bot.i18n import t
from database.database import AsyncSessionLocal
from database.models import (
    OPEN_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    OrderItem,
    OrderStatus,
    Product,
    SupplierFulfillmentStatus,
)
from database.repositories.order_repo import OrderRepository
from services.messages import pkt_clock, supplier_closed_text
from services.notify import announce_order_status, notify_team
from utils.helpers import utcnow
from utils.supplier_routing import resolve_supplier_chat
from utils.ui import esc

logger = logging.getLogger(__name__)


def outcome_footer(status: SupplierFulfillmentStatus, supplier_user, order_status: OrderStatus) -> str:
    """The closing line on a supplier task card: who did what, when, and where the order stands."""
    who = esc(supplier_user.full_name or supplier_user.id)
    when = pkt_clock(utcnow())
    if status == SupplierFulfillmentStatus.completed:
        line = f"✅ <b>DONE</b>  ·  {who}  ·  {when} PKT"
    else:
        line = f"❌ <b>ERROR REPORTED</b>  ·  {who}  ·  {when} PKT"
    if order_status == OrderStatus.completed:
        return f"{line}\n🎉 Whole order completed — customer notified."
    if order_status == OrderStatus.failed:
        return f"{line}\n🛟 Order needs support — the team has been alerted."
    return f"{line}\n⏳ Other supplier groups are still working on this order."


async def _expected_legacy_chats(session, order_id: int) -> set[int]:
    """Resolve old orders that predate per-supplier fulfillment records."""
    result = await session.execute(
        select(Product.supplier_chat_id)
        .select_from(OrderItem)
        .join(Product, Product.id == OrderItem.product_id)
        .where(OrderItem.order_id == order_id)
    )
    chats = set()
    for supplier_id in result.scalars().all():
        target, _ = resolve_supplier_chat(supplier_id)
        if target:
            chats.add(target)
    if not chats:
        target, _ = resolve_supplier_chat(None)
        if target:
            chats.add(target)
    return chats


async def _process_supplier_action(
    order_id: str,
    fulfillment_id: int | None,
    new_status: SupplierFulfillmentStatus,
    changed_by: str,
    chat_id: int,
) -> dict:
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)
        if order is None:
            return {"ok": False, "message": f"⚠️ Order <code>{esc(order_id)}</code> not found."}

        fulfillment = None
        if fulfillment_id is not None:
            fulfillment = await repo.get_fulfillment_by_id(fulfillment_id)
            if not fulfillment or fulfillment.order_id != order.id:
                return {"ok": False, "message": "⚠️ This supplier task is not linked to that order."}
        elif order.fulfillments:
            matches = [
                item for item in order.fulfillments
                if item.supplier_chat_id == chat_id and item.status in (
                    SupplierFulfillmentStatus.pending, SupplierFulfillmentStatus.sending,
                )
            ]
            if len(matches) != 1:
                return {
                    "ok": False,
                    "message": "⚠️ Use the buttons on the matching order message for this order.",
                }
            fulfillment = matches[0]

        if fulfillment is not None:
            if fulfillment.supplier_chat_id != chat_id:
                return {"ok": False, "message": "⛔ This order action is for a different supplier group."}
            if order.status not in OPEN_ORDER_STATUSES:
                return {
                    "ok": False,
                    "message": f"⚠️ Order <code>{esc(order_id)}</code> is already <b>{order.status.value}</b>.",
                }
            if fulfillment.status not in (
                SupplierFulfillmentStatus.pending, SupplierFulfillmentStatus.sending,
            ):
                return {"ok": False, "message": "⚠️ This supplier task was already handled or is not ready."}
            updated = await repo.update_fulfillment_status(fulfillment, new_status, changed_by)
            if not updated:
                return {"ok": False, "message": "⚠️ This supplier task was already handled."}
            final_status = await repo.refresh_order_status_from_fulfillments(
                order.id,
                changed_by=changed_by,
                note=f"Supplier group {fulfillment.category} marked {new_status.value}.",
            )
            item_names = [item["product_name"] for item in json.loads(fulfillment.items_snapshot)]
        else:
            expected_chats = await _expected_legacy_chats(session, order.id)
            if chat_id not in expected_chats or len(expected_chats) != 1:
                return {
                    "ok": False,
                    "message": "⛔ This supplier message is outdated or belongs to another group. Ask an admin for help.",
                }
            if order.status not in OPEN_ORDER_STATUSES:
                return {
                    "ok": False,
                    "message": f"⚠️ Order <code>{esc(order_id)}</code> is already <b>{order.status.value}</b>.",
                }
            overall = OrderStatus.completed if new_status == SupplierFulfillmentStatus.completed else OrderStatus.failed
            updated = await repo.update_status(order, overall, changed_by=changed_by)
            if not updated:
                return {"ok": False, "message": "⚠️ This order was already updated."}
            final_status = overall
            item_names = [item.product_name for item in order.items]

        return {
            "ok": True,
            "order_id": order.order_id,
            "item_names": item_names,
            "fulfillment_status": new_status,
            "order_status": final_status,
            "category": fulfillment.category if fulfillment else None,
            "supplier_chat_id": fulfillment.supplier_chat_id if fulfillment else chat_id,
        }


async def _after_supplier_action(context: ContextTypes.DEFAULT_TYPE, result: dict) -> None:
    if result["fulfillment_status"] == SupplierFulfillmentStatus.failed:
        await notify_team(
            context.bot,
            "⚠️ <b>SUPPLIER REPORTED AN ORDER ISSUE</b>\n\n"
            f"Order: <code>{esc(result['order_id'])}</code>\n"
            f"Product group: {esc(result.get('category') or 'Unknown')}\n"
            f"Supplier chat: <code>{result['supplier_chat_id']}</code>\n"
            f"Current order status: <b>{result['order_status'].value.upper()}</b>",
        )
    notice = None
    if (
        result["order_status"] not in TERMINAL_ORDER_STATUSES
        and result["fulfillment_status"] == SupplierFulfillmentStatus.failed
    ):
        notice = "n_group_issue"
    await announce_order_status(context.bot, result["order_id"], notice)


async def handle_supplier_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support `DONE NX12345678` / `ERROR NX12345678` text commands in supplier groups."""
    if not update.message or not update.message.text:
        return
    parts = update.message.text.strip().upper().split()
    if len(parts) != 2 or parts[0] not in ("DONE", "ERROR"):
        return
    command, order_id = parts
    chat_id = update.message.chat.id
    status = SupplierFulfillmentStatus.completed if command == "DONE" else SupplierFulfillmentStatus.failed
    changed_by = f"supplier:{update.effective_user.full_name or update.effective_user.id}"
    result = await _process_supplier_action(order_id, None, status, changed_by, chat_id)
    if not result["ok"]:
        await update.message.reply_text(result["message"], parse_mode="HTML")
        return
    await update.message.reply_text(
        f"🧾 <code>{esc(order_id)}</code>\n" + outcome_footer(status, update.effective_user, result["order_status"]),
        parse_mode="HTML",
    )
    await _after_supplier_action(context, result)


async def cb_supplier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a supplier button only from the chat assigned to that fulfillment."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) not in (2, 3) or not parts[1]:
        await query.message.reply_text("⚠️ This supplier action is invalid.")
        return
    action = parts[0]
    order_id = parts[1]
    try:
        fulfillment_id = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        await query.message.reply_text("⚠️ This supplier action is invalid.")
        return
    status = SupplierFulfillmentStatus.completed if action == "sup_done" else SupplierFulfillmentStatus.failed
    changed_by = f"supplier:{update.effective_user.full_name or update.effective_user.id}"
    result = await _process_supplier_action(
        order_id, fulfillment_id, status, changed_by, query.message.chat.id,
    )
    if not result["ok"]:
        await query.message.reply_text(result["message"], parse_mode="HTML")
        return

    footer = outcome_footer(status, update.effective_user, result["order_status"])
    # text_html keeps the original formatting escaped, so names like "a<b" cannot break the edit.
    try:
        await query.message.edit_text(
            supplier_closed_text(query.message.text_html or "", footer),
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception as exc:
        logger.warning("Could not edit supplier message for %s (%s)", order_id, type(exc).__name__)
        await query.message.reply_text(footer, parse_mode="HTML")
    await _after_supplier_action(context, result)


async def handle_supplier_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A photo replying to an order message is forwarded to the customer as delivery proof."""
    message = update.message
    if not message or not message.photo or not message.reply_to_message:
        return
    async with AsyncSessionLocal() as session:
        part = await OrderRepository(session).set_fulfillment_proof(
            message.chat.id, message.reply_to_message.message_id, message.photo[-1].file_id,
        )
    if part is None:
        return
    order = part.order
    try:
        await context.bot.send_photo(
            chat_id=order.user.telegram_id,
            photo=part.proof_file_id,
            caption=f"{t(order.user.language, 'n_proof', order_id=order.order_id)}\n{esc(part.category)}",
            parse_mode="HTML",
        )
        await message.reply_text("📸 Proof sent to the customer.")
    except Exception as exc:
        logger.warning("Could not forward proof for %s (%s)", order.order_id, type(exc).__name__)
        await message.reply_text("⚠️ Proof saved, but the customer could not be reached.")


def get_supplier_handlers() -> list:
    return [
        # Text commands only in groups, so private chats never reveal order details.
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_supplier_message,
        ),
        MessageHandler(
            filters.ChatType.GROUPS & filters.PHOTO & filters.REPLY,
            handle_supplier_proof,
        ),
        CallbackQueryHandler(cb_supplier_action, pattern=r"^sup_(done|err|error):"),
    ]
