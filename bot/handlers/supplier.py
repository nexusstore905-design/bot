"""
Supplier handler — handles both text commands and inline buttons.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from database.models import OrderStatus
from config.settings import SUPPLIER_CHAT_ID

logger = logging.getLogger(__name__)

STATUS_ICONS = {
    "completed": "✅",
    "failed": "❌",
}


async def _process_supplier_action(context: ContextTypes.DEFAULT_TYPE, order_id: str, new_status: OrderStatus, changed_by: str) -> tuple[bool, str, int, str, str]:
    """Updates order status and returns (success, message, customer_id, product_name, player_id)."""
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)

        if order is None:
            return False, f"⚠️ Order <b>{order_id}</b> not found.", 0, "", ""

        if order.status not in (OrderStatus.pending, OrderStatus.processing):
            return False, f"⚠️ Order <b>{order_id}</b> is already <b>{order.status.value}</b>.", 0, "", ""

        updated = await repo.update_status(order, new_status, changed_by=changed_by)
        if not updated:
            current_status = order.status.value if order.status else "changed"
            return (
                False,
                f"⚠️ Order <b>{order_id}</b> was already changed to <b>{current_status}</b>.",
                0,
                "",
                "",
            )
        customer_id = order.user.telegram_id
        item = order.items[0] if order.items else None
        product_name = item.product_name if item else "—"
        player_id = order.player_id

    return True, "", customer_id, product_name, player_id


async def _notify_customer(context: ContextTypes.DEFAULT_TYPE, customer_id: int, order_id: str, new_status: OrderStatus, product_name: str, player_id: str):
    if new_status == OrderStatus.completed:
        customer_msg = (
            f"┌──────────────────────────┐\n"
            f"│   🎉  ORDER COMPLETED        │\n"
            f"└──────────────────────────┘\n\n"
            f"  🆔  Order:     <b>{order_id}</b>\n"
            f"  💎  Product:   <b>{product_name}</b>\n"
            f"  🎯  Player ID: <code>{player_id}</code>\n\n"
            f"Your UC has been delivered! Thank you 🙏"
        )
    else:
        customer_msg = (
            f"┌──────────────────────────┐\n"
            f"│   ❌  ORDER FAILED           │\n"
            f"└──────────────────────────┘\n\n"
            f"  🆔  Order:     <b>{order_id}</b>\n"
            f"  💎  Product:   <b>{product_name}</b>\n\n"
            f"There was an issue with your order.\n"
            f"Please contact support via main menu."
        )

    try:
        await context.bot.send_message(
            chat_id=customer_id,
            text=customer_msg,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Could not notify customer {customer_id}: {e}")


async def handle_supplier_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for old text-based DONE/ERROR commands in the group."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat.id
    if chat_id != SUPPLIER_CHAT_ID:
        return

    text = update.message.text.strip().upper()
    parts = text.split()

    if len(parts) != 2:
        return

    command, order_id = parts
    if command not in ("DONE", "ERROR"):
        return

    new_status = OrderStatus.completed if command == "DONE" else OrderStatus.failed
    changed_by = f"supplier:{update.effective_user.full_name or update.effective_user.id}"

    success, err_msg, customer_id, product_name, player_id = await _process_supplier_action(
        context, order_id, new_status, changed_by
    )

    if not success:
        await update.message.reply_text(err_msg, parse_mode="HTML")
        return

    icon = "✅" if new_status == OrderStatus.completed else "❌"
    await update.message.reply_text(
        f"{icon}  Order <b>{order_id}</b> marked as <b>{new_status.value.upper()}</b> by {update.effective_user.first_name}.",
        parse_mode="HTML",
    )
    await _notify_customer(context, customer_id, order_id, new_status, product_name, player_id)


async def cb_supplier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle modern inline buttons (DONE/ERROR) clicked by supplier."""
    await update.callback_query.answer()
    
    data = update.callback_query.data
    action, order_id = data.split(":")
    
    new_status = OrderStatus.completed if action == "sup_done" else OrderStatus.failed
    changed_by = f"supplier:{update.effective_user.full_name or update.effective_user.id}"

    success, err_msg, customer_id, product_name, player_id = await _process_supplier_action(
        context, order_id, new_status, changed_by
    )

    if not success:
        await update.callback_query.message.reply_text(err_msg, parse_mode="HTML")
        return

    icon = "✅" if new_status == OrderStatus.completed else "❌"
    
    # Edit the original message to remove buttons and show who completed it
    original_text = update.callback_query.message.text
    # We strip out the "Mark as DONE or ERROR" and replace it
    new_text = original_text.split("Mark as")[0]
    new_text += f"\n{icon} <b>Marked as {new_status.value.upper()}</b> by {update.effective_user.first_name}"
    
    await update.callback_query.message.edit_text(
        text=new_text,
        parse_mode="HTML",
        reply_markup=None  # Remove buttons
    )

    await _notify_customer(context, customer_id, order_id, new_status, product_name, player_id)


def get_supplier_handlers() -> list:
    handlers = [CallbackQueryHandler(cb_supplier_action, pattern=r"^sup_(done|error):")]
    if SUPPLIER_CHAT_ID:
        handlers.insert(
            0,
            MessageHandler(
                filters.Chat(SUPPLIER_CHAT_ID) & filters.TEXT & ~filters.COMMAND,
                handle_supplier_message,
            ),
        )
    return handlers
