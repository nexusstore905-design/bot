"""
Customer order flow — no pricing shown, per-category supplier routing.
"""
import logging
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CallbackQueryHandler, MessageHandler, CommandHandler, filters,
)

from database.database import AsyncSessionLocal
from database.repositories.user_repo import UserRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.order_repo import OrderRepository
from database.models import OrderStatus
from bot.middlewares.auth_middleware import require_auth
from bot.keyboards.customer_kb import (
    main_menu_kb, categories_kb, products_kb, quantity_kb, cart_kb,
    confirm_order_kb, order_status_kb, back_to_menu_kb,
)
from bot.states.states import (
    ORDER_SELECT_CATEGORY, ORDER_SELECT_PRODUCT, ORDER_SELECT_QUANTITY,
    ORDER_CART_ACTION, ORDER_ENTER_PLAYER_ID, ORDER_CONFIRM,
)
from config.settings import SUPPLIER_CHAT_ID, STORE_NAME
from bot.keyboards.admin_kb import supplier_done_error_kb

logger = logging.getLogger(__name__)

STATUS_DISPLAY = {
    "pending":    ("⏳", "PENDING",    "Order received, awaiting processing"),
    "processing": ("⚙️", "PROCESSING", "Your order is being processed"),
    "completed":  ("✅", "COMPLETED",  "Delivered successfully!"),
    "failed":     ("❌", "FAILED",     "Issue occurred — contact support"),
    "cancelled":  ("🚫", "CANCELLED",  "Order was cancelled"),
}


def _render_cart(cart: list) -> str:
    text = "<b>🛒 Your Shopping Cart</b>\n\n"
    for i, item in enumerate(cart, 1):
        text += f"  {i}. <b>{item['product_name']}</b> x{item['quantity']}\n"
    return text


async def cb_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()

    if "cart" not in context.user_data:
        context.user_data["cart"] = []

    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()

    if not categories:
        await update.callback_query.edit_message_text(
            "😕  <b>No Products Available</b>\n\nCheck back soon!",
            parse_mode="HTML", reply_markup=back_to_menu_kb()
        )
        return ConversationHandler.END

    if len(categories) == 1:
        context.user_data["temp_cat"] = categories[0]
        async with AsyncSessionLocal() as session:
            products = await ProductRepository(session).get_by_category(categories[0])

        await update.callback_query.edit_message_text(
            f"┌──────────────────────────┐\n"
            f"│  🛒  ADD TO CART            │\n"
            f"└──────────────────────────┘\n\n"
            f"<b>Select your package:</b>\n",
            reply_markup=products_kb(products),
            parse_mode="HTML",
        )
        return ORDER_SELECT_PRODUCT

    await update.callback_query.edit_message_text(
        f"┌──────────────────────────┐\n"
        f"│  🛒  ADD TO CART            │\n"
        f"└──────────────────────────┘\n\n"
        f"<b>Select a category:</b>\n",
        reply_markup=categories_kb(categories),
        parse_mode="HTML",
    )
    return ORDER_SELECT_CATEGORY


async def cb_select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()
    category = update.callback_query.data.split(":", 1)[1]
    context.user_data["temp_cat"] = category

    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_by_category(category)

    await update.callback_query.edit_message_text(
        f"┌──────────────────────────┐\n"
        f"│  🛒  ADD TO CART            │\n"
        f"└──────────────────────────┘\n\n"
        f"📂  <b>{category}</b>\n\n"
        f"Select your package:",
        reply_markup=products_kb(products),
        parse_mode="HTML",
    )
    return ORDER_SELECT_PRODUCT


async def cb_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()
    product_id = int(update.callback_query.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(product_id)

    if not product:
        await update.callback_query.edit_message_text("❌  Product not found.")
        return ConversationHandler.END

    context.user_data["temp_item"] = {
        "product_id": product.id,
        "product_name": product.name,
        "category": product.category,
    }

    await update.callback_query.edit_message_text(
        f"┌──────────────────────────┐\n"
        f"│  🛒  ADD TO CART            │\n"
        f"└──────────────────────────┘\n\n"
        f"✅  <b>{product.name}</b>\n\n"
        f"How many do you want?",
        reply_markup=quantity_kb(),
        parse_mode="HTML",
    )
    return ORDER_SELECT_QUANTITY


async def cb_select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()
    qty = int(update.callback_query.data.split(":", 1)[1])

    item = context.user_data.pop("temp_item")
    item["quantity"] = qty
    context.user_data["cart"].append(item)

    cart_text = _render_cart(context.user_data["cart"])

    await update.callback_query.edit_message_text(
        f"┌──────────────────────────┐\n"
        f"│  🛒  CART UPDATED           │\n"
        f"└──────────────────────────┘\n\n"
        f"{cart_text}\n\n"
        f"What would you like to do next?",
        parse_mode="HTML",
        reply_markup=cart_kb(),
    )
    return ORDER_CART_ACTION


async def cb_cart_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Cart cleared!")
    context.user_data["cart"] = []
    await update.callback_query.edit_message_text(
        "🗑  Cart has been cleared.",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def cb_cart_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    if not context.user_data.get("cart"):
        await update.callback_query.answer("Your cart is empty!", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()

    await update.callback_query.edit_message_text(
        f"┌──────────────────────────┐\n"
        f"│  🛒  CHECKOUT               │\n"
        f"└──────────────────────────┘\n\n"
        f"📝  <b>Enter your PUBG Player ID:</b>\n\n"
        f"<i>(Note: IDs must start with 5, e.g. 5123456789)</i>",
        parse_mode="HTML",
    )
    return ORDER_ENTER_PLAYER_ID


async def msg_enter_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.message.reply_text("🔐  Please /start to authenticate.")
        return ConversationHandler.END

    player_id = update.message.text.strip()
    if not player_id.isdigit() or not (5 <= len(player_id) <= 15) or not player_id.startswith("5"):
        await update.message.reply_text(
            "❌  <b>Invalid Player ID</b>\n\n"
            "PUBG Player IDs must <b>start with 5</b> and contain only numbers.\n"
            "Please try again:",
            parse_mode="HTML",
        )
        return ORDER_ENTER_PLAYER_ID

    context.user_data["player_id"] = player_id
    cart_text = _render_cart(context.user_data["cart"])

    await update.message.reply_text(
        f"┌──────────────────────────┐\n"
        f"│  🛒  ORDER REVIEW           │\n"
        f"└──────────────────────────┘\n\n"
        f"{cart_text}\n\n"
        f"  🎯  <b>Player ID:</b> <code>{player_id}</code>\n\n"
        f"Please confirm your order:",
        reply_markup=confirm_order_kb(),
        parse_mode="HTML",
    )
    return ORDER_CONFIRM


async def cb_confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer("Submitting order...")
    user = update.effective_user
    cart = context.user_data.get("cart", [])
    player_id = context.user_data.get("player_id")

    if not cart or not player_id:
        await update.callback_query.edit_message_text(
            "❌  Session expired or cart is empty. Please start again.",
            reply_markup=back_to_menu_kb()
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        # Check user order limit
        from database.repositories.api_store_repo import UserOrderLimitRepository
        limit_repo = UserOrderLimitRepository(session)
        allowed, reason = await limit_repo.check_and_increment(user.id)
        if not allowed:
            await update.callback_query.edit_message_text(
                f"🚫  <b>Order Blocked</b>\n\n{reason}",
                parse_mode="HTML",
                reply_markup=back_to_menu_kb()
            )
            return ConversationHandler.END

        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)
        db_user = await user_repo.get_by_telegram_id(user.id)

        order = await order_repo.create_cart(
            user_id=db_user.id,
            cart_items=cart,
            player_id=player_id,
        )

    context.user_data.pop("cart", None)
    context.user_data.pop("player_id", None)

    msg_text = (
        f"┌──────────────────────────┐\n"
        f"│   ✅  ORDER PLACED!         │\n"
        f"└──────────────────────────┘\n\n"
        f"Your order has been sent to our team.\n\n"
        f"  🆔  Order ID:  <b>{order.order_id}</b>\n"
    )
    for item in cart:
        msg_text += f"      - {item['product_name']} x{item['quantity']}\n"
    msg_text += f"\n  🎯  Player ID: <code>{player_id}</code>\n"
    msg_text += f"  ⏳  Status:    <b>PENDING</b>\n\n"
    msg_text += f"You will be notified once processed. Thank you! 🙏"

    await update.callback_query.edit_message_text(
        msg_text,
        reply_markup=order_status_kb(order.order_id),
        parse_mode="HTML",
    )

    # Forward each item group to its own supplier based on category
    await _forward_to_suppliers(context, order.order_id, cart, player_id)

    return ConversationHandler.END


async def _forward_to_suppliers(context, order_id: str, cart_items: list, player_id: str):
    """
    Route order items to the correct supplier group per category.
    If a category has its own supplier_chat_id, use that.
    Otherwise fall back to the global SUPPLIER_CHAT_ID from .env.
    Groups items by category and sends one message per supplier group.
    """
    # Group cart items by category
    from collections import defaultdict
    by_category: dict[str, list] = defaultdict(list)
    for item in cart_items:
        cat = item.get("category", "")
        by_category[cat].append(item)

    sent_to = set()  # track which chat IDs we already messaged (avoid duplicates)

    for category, items in by_category.items():
        # Look up supplier for this category
        async with AsyncSessionLocal() as session:
            supplier_id = await ProductRepository(session).get_supplier_for_category(category)

        target_chat = supplier_id or SUPPLIER_CHAT_ID
        if not target_chat:
            logger.warning(f"No supplier configured for category '{category}' and no global supplier.")
            continue

        # Build supplier message
        supplier_text = (
            f"┌──────────────────────────┐\n"
            f"│    🆕  NEW ORDER             │\n"
            f"└──────────────────────────┘\n\n"
            f"  🆔  Order:     <b>{order_id}</b>\n"
            f"  🎯  PUBG UID:  <code>{player_id}</code>\n\n"
            f"  📦  <b>Items ({category}):</b>\n"
        )
        for item in items:
            supplier_text += f"      - {item['product_name']} (x{item['quantity']})\n"
        supplier_text += f"\nMark as <b>DONE</b> or <b>ERROR</b>:"

        try:
            msg = await context.bot.send_message(
                chat_id=target_chat,
                text=supplier_text,
                parse_mode="HTML",
                reply_markup=supplier_done_error_kb(order_id),
            )
            # Only save supplier msg id once (first group)
            if target_chat not in sent_to:
                async with AsyncSessionLocal() as session:
                    repo = OrderRepository(session)
                    fresh = await repo.get_by_order_id(order_id)
                    if fresh:
                        await repo.set_supplier_msg(fresh, msg.message_id)
            sent_to.add(target_chat)
        except Exception as e:
            logger.error(f"Failed to send order to supplier {target_chat} for category '{category}': {e}")


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Cancelled")
    context.user_data.pop("cart", None)
    context.user_data.pop("temp_item", None)
    await update.callback_query.message.reply_text(
        "✖  <b>Order Cancelled & Cart Cleared</b>\n\nCome back anytime. 😊",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def cmd_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_or_create(user.id, user.username, user.full_name)
        from config.settings import ADMIN_IDS
        if not await user_repo.is_session_valid(db_user) and user.id not in ADMIN_IDS:
            msg = update.message or update.callback_query.message
            await msg.reply_text("🔐  Please /start to authenticate first.")
            return

        order_repo = OrderRepository(session)
        orders = await order_repo.get_by_user(db_user.id)

    msg = update.message or update.callback_query.message

    if not orders:
        await msg.reply_text(
            "📭  <b>No orders yet.</b>\n\nPlace your first order from the main menu!",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
        return

    text = "📦  <b>Your Recent Orders</b>\n\n"
    for order in orders:
        icon, label, _ = STATUS_DISPLAY.get(order.status.value, ("❓", order.status.value, ""))
        text += f"{icon}  <b>{order.order_id}</b> — {label}\n"
        for item in order.items:
            text += f"     • {item.product_name} x{item.quantity}\n"
        text += f"     🎯 <code>{order.player_id}</code>\n\n"

    await msg.reply_text(text, parse_mode="HTML", reply_markup=main_menu_kb())


async def cb_my_orders_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await cmd_my_orders(update, context)


async def cb_refresh_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Refreshing...")
    order_id = update.callback_query.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)

    if not order:
        await update.callback_query.answer("Order not found.", show_alert=True)
        return

    icon, label, desc = STATUS_DISPLAY.get(order.status.value, ("❓", order.status.value, ""))
    await update.callback_query.edit_message_text(
        f"🔄  <b>Order Status</b>\n\n"
        f"  🆔  <b>{order.order_id}</b>\n"
        f"  {icon}  <b>{label}</b>\n"
        f"  📝  {desc}",
        reply_markup=order_status_kb(order_id),
        parse_mode="HTML",
    )


def get_order_conversation() -> ConversationHandler:
    from telegram.ext import ConversationHandler
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_order_start, pattern=r"^order_start$")],
        states={
            ORDER_SELECT_CATEGORY: [
                CallbackQueryHandler(cb_select_category, pattern=r"^cat:"),
            ],
            ORDER_SELECT_PRODUCT: [
                CallbackQueryHandler(cb_select_product, pattern=r"^prod:"),
            ],
            ORDER_SELECT_QUANTITY: [
                CallbackQueryHandler(cb_select_quantity, pattern=r"^qty:"),
            ],
            ORDER_CART_ACTION: [
                CallbackQueryHandler(cb_order_start, pattern=r"^order_start$"),
                CallbackQueryHandler(cb_cart_checkout, pattern=r"^cart_checkout$"),
                CallbackQueryHandler(cb_cart_clear, pattern=r"^cart_clear$"),
            ],
            ORDER_ENTER_PLAYER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_enter_player_id),
            ],
            ORDER_CONFIRM: [
                CallbackQueryHandler(cb_confirm_order, pattern=r"^confirm_order$"),
                CallbackQueryHandler(cb_cancel, pattern=r"^cancel_order$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cb_cancel, pattern=r"^cancel_order$"),
            CommandHandler("cancel", cb_cancel),
        ],
        allow_reentry=True,
        per_message=False,
    )
