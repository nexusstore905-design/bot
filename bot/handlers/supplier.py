"""Supplier actions scoped to the supplier group assigned to each order part."""
import html
import json
import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

from database.database import AsyncSessionLocal
from database.models import (
    OrderStatus, SupplierFulfillmentStatus, OrderItem, Product,
)
from database.repositories.order_repo import OrderRepository
from config.settings import SUPPLIER_CHAT_ID
from utils.supplier_routing import resolve_supplier_chat

logger = logging.getLogger(__name__)


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
            return {"ok": False, "message": f"⚠️ Order <code>{html.escape(order_id)}</code> not found."}

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
                    "message": "⚠️ This order has multiple supplier tasks. Use the buttons on the matching order message.",
                }
            fulfillment = matches[0]

        if fulfillment is not None:
            if order.status not in (OrderStatus.pending, OrderStatus.processing):
                return {
                    "ok": False,
                    "message": f"⚠️ Order <code>{html.escape(order_id)}</code> is already <b>{order.status.value}</b>.",
                }
            if fulfillment.supplier_chat_id != chat_id:
                return {"ok": False, "message": "⛔ This order action is for a different supplier group."}
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
            if order.status not in (OrderStatus.pending, OrderStatus.processing):
                return {
                    "ok": False,
                    "message": f"⚠️ Order <code>{html.escape(order_id)}</code> is already <b>{order.status.value}</b>.",
                }
            from database.models import OrderStatus as OverallOrderStatus
            overall = OverallOrderStatus.completed if new_status == SupplierFulfillmentStatus.completed else OverallOrderStatus.failed
            updated = await repo.update_status(order, overall, changed_by=changed_by)
            if not updated:
                return {"ok": False, "message": "⚠️ This order was already updated."}
            final_status = overall
            item_names = [item.product_name for item in order.items]

        return {
            "ok": True,
            "order_id": order.order_id,
            "customer_id": order.user.telegram_id,
            "player_id": order.player_id,
            "item_names": item_names,
            "fulfillment_status": new_status,
            "order_status": final_status,
            "category": fulfillment.category if fulfillment else None,
            "supplier_chat_id": fulfillment.supplier_chat_id if fulfillment else chat_id,
        }


async def _notify_customer(context: ContextTypes.DEFAULT_TYPE, result: dict) -> None:
    status = result["order_status"]
    if status not in (OrderStatus.completed, OrderStatus.failed):
        if result["fulfillment_status"] == SupplierFulfillmentStatus.failed:
            text = (
                "⚠️ <b>One supplier group reported an issue</b>\n\n"
                f"Order <code>{html.escape(result['order_id'])}</code> is still being coordinated. "
                "The remaining supplier work will continue, and support has been notified."
            )
        else:
            return
    elif status == OrderStatus.completed:
        items = ", ".join(html.escape(name, quote=False) for name in result["item_names"])
        text = (
            "🎉 <b>Order completed</b>\n──────────────\n\n"
            f"🧾 <b>Order ID</b>  <code>{html.escape(result['order_id'])}</code>\n"
            f"📦 {items}\n"
            f"🎮 <b>Player ID</b>  <code>{html.escape(result['player_id'])}</code>\n\n"
            "All supplier groups have completed your order."
        )
    else:
        text = (
            "❌ <b>Order needs support</b>\n──────────────\n\n"
            f"🧾 <b>Order ID</b>  <code>{html.escape(result['order_id'])}</code>\n\n"
            "A supplier could not complete the order. Please contact support."
        )
    try:
        await context.bot.send_message(chat_id=result["customer_id"], text=text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Could not notify customer about order %s (%s)", result["order_id"], type(exc).__name__)


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, result: dict) -> None:
    if result["fulfillment_status"] != SupplierFulfillmentStatus.failed:
        return
    from config.settings import ADMIN_IDS
    category = html.escape(result.get("category") or "Unknown", quote=False)
    text = (
        "⚠️ <b>SUPPLIER REPORTED AN ORDER ISSUE</b>\n\n"
        f"Order: <code>{html.escape(result['order_id'])}</code>\n"
        f"Product group: {category}\n"
        f"Supplier chat: <code>{result['supplier_chat_id']}</code>\n"
        f"Current order status: <b>{result['order_status'].value.upper()}</b>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not notify admin %s about order %s (%s)", admin_id, result["order_id"], type(exc).__name__)


async def handle_supplier_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support the legacy DONE/ERROR text command in the global supplier chat."""
    if not update.message or not update.message.text or update.message.chat.id != SUPPLIER_CHAT_ID:
        return
    parts = update.message.text.strip().upper().split()
    if len(parts) != 2 or parts[0] not in ("DONE", "ERROR"):
        return
    command, order_id = parts
    status = SupplierFulfillmentStatus.completed if command == "DONE" else SupplierFulfillmentStatus.failed
    changed_by = f"supplier:{update.effective_user.full_name or update.effective_user.id}"
    result = await _process_supplier_action(order_id, None, status, changed_by, update.message.chat.id)
    if not result["ok"]:
        await update.message.reply_text(result["message"], parse_mode="HTML")
        return
    await update.message.reply_text(
        f"✅ Supplier task for <code>{html.escape(order_id)}</code> marked <b>{status.value.upper()}</b>.",
        parse_mode="HTML",
    )
    await _notify_admins(context, result)
    await _notify_customer(context, result)


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

    icon = "✅" if status == SupplierFulfillmentStatus.completed else "❌"
    original_text = query.message.text or ""
    new_text = original_text.split("Mark this group’s items as")[0].split("Mark as")[0].rstrip()
    new_text += f"\n\n{icon} <b>This group marked {status.value.upper()}</b>"
    if result["order_status"] in (OrderStatus.completed, OrderStatus.failed):
        new_text += f"\nOverall order: <b>{result['order_status'].value.upper()}</b>"
    else:
        new_text += "\nOther supplier groups are still processing."
    await query.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    await _notify_admins(context, result)
    await _notify_customer(context, result)


def get_supplier_handlers() -> list:
    handlers = [CallbackQueryHandler(cb_supplier_action, pattern=r"^sup_(done|err|error):")]
    if SUPPLIER_CHAT_ID:
        handlers.insert(
            0,
            MessageHandler(
                filters.Chat(SUPPLIER_CHAT_ID) & filters.TEXT & ~filters.COMMAND,
                handle_supplier_message,
            ),
        )
    return handlers
