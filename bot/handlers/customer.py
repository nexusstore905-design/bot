"""
Customer order flow — no pricing shown, per-category supplier routing.
"""
import html
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
from bot.middlewares.auth_middleware import require_auth
from bot.keyboards.customer_kb import (
    main_menu_kb, categories_kb, products_kb, quantity_kb, cart_kb,
    confirm_order_kb, order_status_kb, back_to_menu_kb,
)
from bot.states.states import (
    ORDER_SELECT_CATEGORY, ORDER_SELECT_PRODUCT, ORDER_SELECT_QUANTITY,
    ORDER_CART_ACTION, ORDER_ENTER_PLAYER_ID, ORDER_CONFIRM,
)
from bot.keyboards.admin_kb import supplier_done_error_kb
from utils.supplier_routing import resolve_supplier_chat

logger = logging.getLogger(__name__)

STATUS_DISPLAY = {
    "pending":    ("⏳", "PENDING",    "Order received, awaiting processing"),
    "processing": ("⚙️", "PROCESSING", "Your order is being processed"),
    "completed":  ("✅", "COMPLETED",  "Delivered successfully!"),
    "failed":     ("❌", "FAILED",     "Issue occurred — contact support"),
    "cancelled":  ("🚫", "CANCELLED",  "Order was cancelled"),
}


def _render_cart(cart: list) -> str:
    text = "🛒 <b>Your cart</b>\n──────────────\n"
    for i, item in enumerate(cart, 1):
        text += (
            f"{i}. <b>{html.escape(str(item['product_name']), quote=False)}</b>"
            f"  × {item['quantity']}\n"
        )
    return text


def _order_step(context: ContextTypes.DEFAULT_TYPE, step: int, title: str, prompt: str) -> str:
    total = 4 if context.user_data.get("order_has_categories") else 3
    return (
        f"🛍 <b>New order</b>  <i>{step}/{total}</i>\n"
        f"<b>{title}</b>\n\n{prompt}"
    )


async def cb_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()

    if "cart" not in context.user_data:
        context.user_data["cart"] = []

    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    context.user_data["order_has_categories"] = len(categories) > 1

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
            _order_step(context, 1, "Choose a package", "Select a package to add it to your cart."),
            reply_markup=products_kb(products),
            parse_mode="HTML",
        )
        return ORDER_SELECT_PRODUCT

    await update.callback_query.edit_message_text(
        _order_step(context, 1, "Choose a category", "Select a category to see its available products."),
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
        _order_step(
            context,
            2,
            html.escape(category, quote=False),
            "Choose a package to add it to your cart.",
        ),
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
        await update.callback_query.edit_message_text(
            "❌ <b>This product is no longer available.</b>\n\nPlease start a new order.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    context.user_data["temp_item"] = {
        "product_id": product.id,
        "product_name": product.name,
        "category": product.category,
    }

    await update.callback_query.edit_message_text(
        _order_step(
            context,
            3 if context.user_data.get("order_has_categories") else 2,
            html.escape(product.name, quote=False),
            "Choose how many you want.",
        ),
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
        f"{cart_text}\n"
        "Choose another product or continue to checkout.",
        parse_mode="HTML",
        reply_markup=cart_kb(),
    )
    return ORDER_CART_ACTION


async def cb_cart_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Cart cleared!")
    context.user_data["cart"] = []
    context.user_data.pop("player_id", None)
    context.user_data.pop("temp_item", None)
    await update.callback_query.edit_message_text(
        "🗑 <b>Your cart is empty.</b>\n\nYou can start a new order whenever you're ready.",
        parse_mode="HTML",
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
        _order_step(
            context,
            4 if context.user_data.get("order_has_categories") else 3,
            "Enter your PUBG Player ID",
            "Check the ID carefully before submitting.\n"
            "<i>It must contain 5–15 digits and start with 5.</i>",
        ),
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
            "❌ <b>That Player ID does not look right.</b>\n\n"
            "Enter 5–15 digits starting with 5. Please try again:",
            parse_mode="HTML",
        )
        return ORDER_ENTER_PLAYER_ID

    context.user_data["player_id"] = player_id
    cart_text = _render_cart(context.user_data["cart"])

    await update.message.reply_text(
        "🧾 <b>Review your order</b>\n──────────────\n"
        f"{cart_text}\n"
        f"🎮 <b>Player ID</b>  <code>{html.escape(player_id, quote=False)}</code>\n\n"
        "Check these details, then submit your order.",
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
            "❌ <b>Your order session expired.</b>\n\nStart a new order from the menu.",
            parse_mode="HTML",
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
                f"🚫 <b>Order limit reached</b>\n\n{html.escape(reason, quote=False)}",
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
        "✅ <b>Order submitted</b>\n"
        "──────────────\n"
        "Your order is now waiting for processing.\n\n"
        f"🧾 <b>Order ID</b>  <code>{html.escape(order.order_id, quote=False)}</code>\n"
    )
    for item in cart:
        msg_text += f"      - {html.escape(str(item['product_name']), quote=False)} x{item['quantity']}\n"
    msg_text += f"\n🎮 <b>Player ID</b>  <code>{html.escape(player_id, quote=False)}</code>\n"
    msg_text += "⏳ <b>Status</b>  Pending\n\n"
    msg_text += "You can check progress with the buttons below."

    await update.callback_query.edit_message_text(
        msg_text,
        reply_markup=order_status_kb(order.order_id),
        parse_mode="HTML",
    )

    # Forward each item group to its own supplier based on category
    await _forward_to_suppliers(context, order.order_id, cart, player_id)

    return ConversationHandler.END


async def cb_edit_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth(update, context):
        await update.callback_query.answer("🔐 Please authenticate first.", show_alert=True)
        return ConversationHandler.END

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        _order_step(
            context,
            4 if context.user_data.get("order_has_categories") else 3,
            "Enter your PUBG Player ID",
            "Send the correct ID below. It must contain 5–15 digits and start with 5.",
        ),
        parse_mode="HTML",
    )
    return ORDER_ENTER_PLAYER_ID


async def _forward_to_suppliers(context, order_id: str, cart_items: list, player_id: str):
    """
    Route order items to the configured supplier group.
    A category supplier takes priority; SUPPLIER_CHAT_ID is the fallback.
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

        target_chat, route_source = resolve_supplier_chat(supplier_id)
        if not target_chat:
            logger.warning(f"No supplier configured for category '{category}' and no global supplier.")
            continue

        import html
        safe_order_id = html.escape(str(order_id))
        safe_uid = html.escape(str(player_id))
        safe_cat = html.escape(str(category))

        # Build a compact order card for the supplier group.
        supplier_text = (
            "🆕 <b>New order</b>\n──────────────\n"
            f"🧾 <b>Order ID</b>  <code>{safe_order_id}</code>\n"
            f"📂 <b>Category</b>  {safe_cat}\n"
            f"🎮 <b>Player ID</b>  <code>{safe_uid}</code>\n\n"
            "<b>Items</b>\n"
        )
        for item in items:
            safe_pname = html.escape(str(item['product_name']))
            supplier_text += f"      - {safe_pname} (x{item['quantity']})\n"
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
            logger.info(
                "Delivered order %s for category %s to supplier chat %s via %s routing",
                order_id, category, target_chat, route_source,
            )
        except Exception as e:
            logger.error(
                "Failed to send order to supplier %s for category %s (%s)",
                target_chat, category, type(e).__name__,
            )
            # Alert the admin immediately so they know why an order didn't go through!
            from config.settings import ADMIN_IDS
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"⚠️ <b>SUPPLIER DELIVERY ALERT</b>\n\n"
                            f"Order: <b>{safe_order_id}</b> (Category: {safe_cat})\n"
                            f"Failed to deliver to Group ID: <code>{target_chat}</code>\n\n"
                            "<b>Telegram Error:</b> delivery failed; check the bot logs.\n\n"
                            f"👉 <i>Check that your bot is added as an <b>Administrator</b> in that group!</i>"
                        ),
                        parse_mode="HTML"
                    )
                except Exception:
                    pass


async def cb_return_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data.pop("cart", None)
    context.user_data.pop("temp_item", None)
    context.user_data.pop("player_id", None)
    context.user_data.pop("temp_cat", None)
    context.user_data.pop("order_has_categories", None)
    await update.callback_query.edit_message_text(
        "🏠 <b>Main menu</b>\n\nYour unfinished cart was cleared.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer("Order cancelled")
    context.user_data.pop("cart", None)
    context.user_data.pop("temp_item", None)
    context.user_data.pop("player_id", None)
    context.user_data.pop("temp_cat", None)
    context.user_data.pop("order_has_categories", None)
    message = update.callback_query.message if update.callback_query else update.message
    text = "✖ <b>Order cancelled</b>\n\nYour cart has been cleared. You can start again anytime."
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=main_menu_kb()
        )
    else:
        await message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_kb())
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

    text = "📦 <b>Your orders</b>\n──────────────\n"
    for order in orders:
        icon, label, _ = STATUS_DISPLAY.get(order.status.value, ("❓", order.status.value, ""))
        text += f"{icon} <b>{html.escape(order.order_id, quote=False)}</b> · {label}\n"
        for item in order.items:
            text += f"📦 {html.escape(item.product_name, quote=False)} × {item.quantity}\n"
        text += f"🎮 <code>{html.escape(order.player_id, quote=False)}</code>\n\n"

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
        "🔎 <b>Order status</b>\n──────────────\n"
        f"🧾 <b>Order ID</b>  <code>{html.escape(order.order_id, quote=False)}</code>\n"
        f"{icon} <b>{label}</b>\n"
        f"📝 {desc}",
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
                CallbackQueryHandler(cb_edit_player_id, pattern=r"^edit_player_id$"),
                CallbackQueryHandler(cb_cancel, pattern=r"^cancel_order$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cb_return_to_menu, pattern=r"^main_menu$"),
            CallbackQueryHandler(cb_cancel, pattern=r"^cancel_order$"),
            CommandHandler("cancel", cb_cancel),
        ],
        allow_reentry=True,
        per_message=False,
    )
