"""Order lists, lookup, manual status changes, resend, and supplier reassignment."""
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import (
    STATUS_ICONS,
    admin_label,
    local_datetime,
    parse_chat_id_or_forward,
    team_only,
)
from bot.keyboards.admin_kb import admin_order_actions_kb, admin_orders_kb, cancel_conv_kb
from bot.states.states import ADMIN_REASSIGN_ORDER, ADMIN_SEARCH_ORDER
from database.database import AsyncSessionLocal
from database.models import (
    OPEN_ORDER_STATUSES,
    ApiStore,
    OrderStatus,
    SupplierFulfillmentStatus,
)
from database.repositories.order_repo import OrderRepository
from services.audit import audit
from services.dispatch import FAILED, RETRY, SENT, dispatch_order
from services.notify import announce_order_status, close_supplier_message
from utils.ui import esc

PART_ICONS = {
    SupplierFulfillmentStatus.queued: "🕓",
    SupplierFulfillmentStatus.sending: "📤",
    SupplierFulfillmentStatus.pending: "📬",
    SupplierFulfillmentStatus.completed: "✅",
    SupplierFulfillmentStatus.failed: "❌",
}


@team_only
async def cb_admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "📦  <b>Orders</b>", reply_markup=admin_orders_kb(), parse_mode="HTML"
    )


@team_only
async def cb_orders_by_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    status = OrderStatus(update.callback_query.data.removeprefix("adm_orders_"))
    async with AsyncSessionLocal() as session:
        orders = await OrderRepository(session).get_by_status(status, limit=20)

    if not orders:
        await update.callback_query.message.edit_text(
            f"No {status.value} orders.", reply_markup=admin_orders_kb()
        )
        return

    lines = [f"{STATUS_ICONS.get(status.value, '')}  <b>{status.value.upper()} ORDERS</b> (latest {len(orders)})\n"]
    for order in orders:
        items = ", ".join(f"{item.product_name} ×{item.quantity}" for item in order.items[:2]) or "—"
        customer = order.user.full_name or order.user.username or str(order.user.telegram_id)
        lines.append(
            f"🆔 <code>{esc(order.order_id)}</code> · {local_datetime(order.created_at)}\n"
            f"  👤 {esc(customer)}\n"
            f"  🎮 {esc(items)}\n"
            f"  🎯 <code>{esc(order.player_id)}</code>"
        )
    lines.append("\nUse 🔍 Find order with an ID to manage it.")
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_orders_kb()
    )


async def _order_view(order_id: str) -> tuple[str, object] | None:
    async with AsyncSessionLocal() as session:
        order = await OrderRepository(session).get_by_order_id(order_id)
        if order is None:
            return None
        store = await session.get(ApiStore, order.api_store_id) if order.api_store_id else None

    source = "Telegram bot" if order.api_store_id is None else (
        f"API store <b>{esc(store.name)}</b>" if store else f"Deleted API store #{order.api_store_id}"
    )
    items = "\n".join(f"• {esc(item.product_name)} × {item.quantity}" for item in order.items) or "• —"
    lines = [
        f"📦 <b>ORDER {esc(order.order_id)}</b>",
        "──────────────",
        f"Status: {STATUS_ICONS.get(order.status.value, '')} <b>{order.status.value.upper()}</b>",
        f"Customer: {esc(order.user.full_name or order.user.username or '—')} · <code>{order.user.telegram_id}</code>",
        f"Source: {source}",
        f"Player ID: <code>{esc(order.player_id)}</code>",
        f"Created: {local_datetime(order.created_at)}",
    ]
    if order.settled_at:
        lines.append(f"Payment cleared: {local_datetime(order.settled_at)}")
    lines += ["", "<b>Items</b>", items]
    if order.fulfillments:
        lines += ["", "<b>Supplier parts</b>"]
        for part in order.fulfillments:
            extra = []
            if part.dispatch_attempts:
                extra.append(f"attempts {part.dispatch_attempts}")
            if part.proof_file_id:
                extra.append("📸 proof")
            if part.failure_note:
                extra.append(esc(part.failure_note[:80]))
            lines.append(
                f"{PART_ICONS.get(part.status, '•')} {esc(part.category)} → <code>{part.supplier_chat_id}</code>"
                f" · {part.status.value}" + (f" · {' · '.join(extra)}" if extra else "")
            )
    history = order.history[-8:]
    if history:
        lines += ["", "<b>History</b>"]
        for entry in history:
            note = f" — {esc(entry.note[:60])}" if entry.note else ""
            lines.append(
                f"{local_datetime(entry.created_at)}: {esc(entry.old_status or '—')} → "
                f"{esc(entry.new_status)} ({esc(entry.changed_by or '?')}){note}"
            )
    is_open = order.status in OPEN_ORDER_STATUSES
    can_resend = order.status != OrderStatus.completed and (
        not order.fulfillments
        or any(part.status != SupplierFulfillmentStatus.completed for part in order.fulfillments)
    )
    return "\n".join(lines), admin_order_actions_kb(order.order_id, is_open, can_resend)


@team_only
async def cb_search_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🔍  Enter the <b>Order ID</b> (e.g. NX12345678):",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_SEARCH_ORDER


@team_only
async def admin_search_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = update.message.text.strip().upper()
    view = await _order_view(order_id)
    if view is None:
        await update.message.reply_text(
            f"❌  Order <b>{esc(order_id)}</b> not found. Send another ID or /cancel.", parse_mode="HTML"
        )
        return ADMIN_SEARCH_ORDER
    text, keyboard = view
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    return ConversationHandler.END


@team_only
async def cb_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, order_id, new_status_str = query.data.split(":", 2)
    try:
        new_status = OrderStatus(new_status_str)
    except ValueError:
        await query.answer("Invalid status.", show_alert=True)
        return
    if new_status in OPEN_ORDER_STATUSES:
        await query.answer("Use Resend to reopen an order.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)
        if not order:
            await query.answer("Order not found.", show_alert=True)
            return
        if order.status == new_status:
            await query.answer(f"Already {new_status.value}.", show_alert=True)
            return
        applied, closed_messages = await repo.admin_close_order(
            order, new_status, admin_label(update.effective_user),
        )
    if not applied:
        await query.answer("The order changed meanwhile. Refreshing.", show_alert=True)
    else:
        await query.answer(f"Order marked {new_status.value}.")
        for chat_id, message_id in closed_messages:
            await close_supplier_message(
                context.bot, chat_id, message_id,
                f"🛑 <b>Order <code>{esc(order_id)}</code> was closed by an admin</b> "
                f"({new_status.value}). No further action is needed.",
            )
        await audit(update.effective_user, "set_order_status", f"{order_id} → {new_status.value}")
        await announce_order_status(context.bot, order_id)

    view = await _order_view(order_id)
    if view:
        await query.message.edit_text(view[0], reply_markup=view[1], parse_mode="HTML")


async def _resend(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str, new_chat_id: int | None):
    changed_by = admin_label(update.effective_user)
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)
        if order is None:
            return f"❌ Order <code>{esc(order_id)}</code> not found."
        old_messages, queued, error = await repo.requeue_for_resend(order, changed_by, new_chat_id)
        order_pk = order.id
    if error:
        return f"❌ {esc(error)}"
    for chat_id, message_id in old_messages:
        await close_supplier_message(
            context.bot, chat_id, message_id,
            f"♻️ <b>Order <code>{esc(order_id)}</code> was re-sent</b>. Use the new message instead.",
        )
    outcomes = await dispatch_order(context.bot, order_pk)
    sent = sum(1 for outcome in outcomes if outcome.result == SENT)
    retry = sum(1 for outcome in outcomes if outcome.result == RETRY)
    failed = sum(1 for outcome in outcomes if outcome.result == FAILED)
    await audit(
        update.effective_user, "reassign_order" if new_chat_id else "resend_order",
        f"{order_id} parts={queued}" + (f" chat={new_chat_id}" if new_chat_id else ""),
    )
    await announce_order_status(context.bot, order_id)
    return (
        f"📨 <b>Order <code>{esc(order_id)}</code> re-sent</b>\n\n"
        f"Parts queued: {queued} · delivered: {sent} · retrying: {retry} · failed: {failed}\n"
        "The supplier timeout restarts from delivery."
    )


@team_only
async def cb_resend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Re-sending…")
    order_id = query.data.split(":", 1)[1]
    result = await _resend(update, context, order_id, None)
    view = await _order_view(order_id)
    if view:
        await query.message.edit_text(f"{result}\n\n{view[0]}", reply_markup=view[1], parse_mode="HTML")
    else:
        await query.message.edit_text(result, parse_mode="HTML")


@team_only
async def cb_reassign_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    order_id = update.callback_query.data.split(":", 1)[1]
    context.user_data["reassign_order_id"] = order_id
    await update.callback_query.message.edit_text(
        f"🔀 <b>Reassign order <code>{esc(order_id)}</code></b>\n\n"
        "Forward any message from the new supplier group, or send its negative group ID.\n"
        "Every unfinished part of this order moves to that group and is delivered again.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_REASSIGN_ORDER


@team_only
async def admin_reassign_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data.get("reassign_order_id")
    if not order_id:
        await update.message.reply_text("Reassignment expired. Open the order again.")
        return ConversationHandler.END
    chat_id, error = parse_chat_id_or_forward(update.message)
    if error:
        await update.message.reply_text(f"❌ {error}", parse_mode="HTML")
        return ADMIN_REASSIGN_ORDER
    try:
        chat = await context.bot.get_chat(chat_id)
    except Exception as exc:
        await update.message.reply_text(
            f"❌ The bot cannot see group <code>{chat_id}</code> ({esc(type(exc).__name__)}). "
            "Add the bot to that group and try again.",
            parse_mode="HTML",
        )
        return ADMIN_REASSIGN_ORDER
    context.user_data.pop("reassign_order_id", None)
    result = await _resend(update, context, order_id, chat_id)
    view = await _order_view(order_id)
    await update.message.reply_text(
        f"{result}\nNew group: <b>{esc(chat.title or chat_id)}</b>"
        + (f"\n\n{view[0]}" if view else ""),
        parse_mode="HTML",
        reply_markup=view[1] if view else None,
    )
    return ConversationHandler.END
