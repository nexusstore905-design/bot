"""
Customer order flow — no pricing shown, per-category supplier routing.
"""
import html
import json
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
from bot.middlewares.auth_middleware import require_auth, require_callback_auth
from bot.keyboards.customer_kb import (
    main_menu_kb, categories_kb, products_kb, quantity_kb, cart_kb,
    confirm_order_kb, order_status_kb, back_to_menu_kb, my_orders_kb,
)
from bot.states.states import (
    ORDER_SELECT_CATEGORY, ORDER_SELECT_PRODUCT, ORDER_SELECT_QUANTITY,
    ORDER_CART_ACTION, ORDER_ENTER_PLAYER_ID, ORDER_CONFIRM,
)
from bot.keyboards.admin_kb import supplier_done_error_kb
from utils.supplier_routing import resolve_supplier_chat
from utils.ui import panel

logger = logging.getLogger(__name__)

STATUS_DISPLAY = {
    "pending":    ("⏳", "PENDING",    "Order received, awaiting processing"),
    "processing": ("⚙️", "PROCESSING", "Your order is being processed"),
    "completed":  ("✅", "COMPLETED",  "Delivered successfully!"),
    "failed":     ("❌", "FAILED",     "Issue occurred — contact support"),
    "cancelled":  ("🚫", "CANCELLED",  "Order was cancelled"),
}


def _render_cart(cart: list) -> str:
    lines = []
    for i, item in enumerate(cart, 1):
        lines.append(
            f"{i}. <b>{html.escape(str(item['product_name']), quote=False)}</b> × {item['quantity']}"
        )
    return panel("Your cart", "\n".join(lines), icon="🛒")


def _order_step(context: ContextTypes.DEFAULT_TYPE, step: int, title: str, prompt: str) -> str:
    total = 4 if context.user_data.get("order_has_categories") else 3
    return panel(
        "New order",
        f"<i>Step {step} of {total}</i>\n\n<b>{html.escape(title, quote=False)}</b>\n\n{prompt}",
        icon="🛍",
    )


async def cb_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END

    if "cart" not in context.user_data:
        context.user_data["cart"] = []

    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    # Always show the game/product group first, even when it is the only one.
    # This keeps the intended flow: PUBG UC Top Up -> choose a UC package.
    context.user_data["order_has_categories"] = True

    if not categories:
        await update.callback_query.edit_message_text(
            "😕  <b>No Products Available</b>\n\nCheck back soon!",
            parse_mode="HTML", reply_markup=back_to_menu_kb()
        )
        return ConversationHandler.END

    await update.callback_query.edit_message_text(
        _order_step(context, 1, "Choose a product", "Select a product to see its available denominations."),
        reply_markup=categories_kb(categories),
        parse_mode="HTML",
    )
    return ORDER_SELECT_CATEGORY


async def cb_select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    category = update.callback_query.data.split(":", 1)[1]
    context.user_data["temp_cat"] = category

    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_by_category(category)

    await update.callback_query.edit_message_text(
        _order_step(
            context,
            2,
            category,
            "Choose a package to add it to your cart.",
        ),
        reply_markup=products_kb(products),
        parse_mode="HTML",
    )
    return ORDER_SELECT_PRODUCT


async def cb_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    product_id = int(update.callback_query.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(product_id)

    if not product or not product.is_active:
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
            3,
            product.name,
            "Choose how many you want.",
        ),
        reply_markup=quantity_kb(),
        parse_mode="HTML",
    )
    return ORDER_SELECT_QUANTITY


async def cb_select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    qty = int(update.callback_query.data.split(":", 1)[1])

    item = context.user_data.pop("temp_item")
    item["quantity"] = qty
    context.user_data["cart"].append(item)

    cart_text = _render_cart(context.user_data["cart"])

    await update.callback_query.edit_message_text(
        f"{cart_text}\n"
        "Choose another product or continue to checkout.",
        parse_mode="HTML",
        reply_markup=cart_kb(context.user_data["cart"]),
    )
    return ORDER_CART_ACTION


async def cb_back_to_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    category = context.user_data.get("temp_cat")
    if not category:
        async with AsyncSessionLocal() as session:
            categories = await ProductRepository(session).get_categories()
        if not categories:
            await update.callback_query.edit_message_text(
                "😕 <b>No products are available right now.</b>",
                parse_mode="HTML",
                reply_markup=back_to_menu_kb(),
            )
            return ConversationHandler.END
        await update.callback_query.edit_message_text(
            _order_step(context, 1, "Choose a product", "Select a product group."),
            reply_markup=categories_kb(categories),
            parse_mode="HTML",
        )
        return ORDER_SELECT_CATEGORY

    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_by_category(category)
        categories = await ProductRepository(session).get_categories()
    if not products:
        await update.callback_query.edit_message_text(
            "😕 <b>These packages are no longer available.</b>\n\nChoose another product group.",
            parse_mode="HTML",
            reply_markup=categories_kb(categories),
        )
        return ORDER_SELECT_CATEGORY

    await update.callback_query.edit_message_text(
        _order_step(context, 2, category, "Choose a package to add to your cart."),
        reply_markup=products_kb(products),
        parse_mode="HTML",
    )
    return ORDER_SELECT_PRODUCT


async def cb_cart_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END

    parts = update.callback_query.data.split(":")
    action, index = parts[0], int(parts[1])
    cart = context.user_data.get("cart", [])
    if not 0 <= index < len(cart):
        await update.callback_query.edit_message_text(
            "🛒 <b>Your cart changed.</b>\n\nPlease review it again.",
            parse_mode="HTML",
            reply_markup=cart_kb(cart),
        )
        return ORDER_CART_ACTION

    if action == "cart_remove":
        cart.pop(index)
        context.user_data["cart"] = cart
        if not cart:
            await update.callback_query.edit_message_text(
                "🛒 <b>Your cart is empty.</b>\n\nAdd a package to continue.",
                parse_mode="HTML",
                reply_markup=main_menu_kb(),
            )
            return ConversationHandler.END
    else:
        quantity = int(parts[2])
        if quantity < 1 or quantity > 99:
            await update.callback_query.message.reply_text("Choose a quantity from 1 to 99.")
            return ORDER_CART_ACTION
        cart[index]["quantity"] = quantity

    await update.callback_query.edit_message_text(
        f"{_render_cart(cart)}\n\nAdjust quantities, add another package, or continue to checkout.",
        parse_mode="HTML",
        reply_markup=cart_kb(cart),
    )
    return ORDER_CART_ACTION


async def cb_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Use + / − to change quantity, or 🗑 to remove it.")
    return ORDER_CART_ACTION


async def cb_cart_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
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
    if not await require_callback_auth(update, context):
        return ConversationHandler.END

    if not context.user_data.get("cart"):
        await update.callback_query.edit_message_text(
            "🛒 <b>Your cart is empty.</b>\n\nAdd a package before checkout.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    await update.callback_query.edit_message_text(
        _order_step(
            context,
            4,
            "Enter your PUBG Player ID",
            "Check the ID carefully before submitting.\n"
            "<i>It must contain 5–15 digits and start with 5.</i>",
        ),
        parse_mode="HTML",
    )
    return ORDER_ENTER_PLAYER_ID


async def msg_enter_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("auth_rate_limited", None)
    context.user_data.pop("auth_rejection_sent", None)
    if not await require_auth(update, context):
        throttled = context.user_data.pop("auth_rate_limited", False)
        rejection_sent = context.user_data.pop("auth_rejection_sent", False)
        if not rejection_sent:
            text = (
                "⏳ Please wait a moment before trying again."
                if throttled else "🔐 Please /start to authenticate."
            )
            await update.message.reply_text(text)
        if throttled:
            return ORDER_ENTER_PLAYER_ID
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
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
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

    await update.callback_query.message.edit_text(
        "⏳ <b>Checking your order…</b>\n──────────────\n\n"
        "Confirming package availability and supplier routing.",
        parse_mode="HTML",
    )

    normalized_cart: list[dict] = []
    fulfillment_specs: list[dict] = []
    missing_categories: list[str] = []
    unavailable_products = False
    limit_error: str | None = None
    order = None

    async with AsyncSessionLocal() as session:
        products = ProductRepository(session)
        cart_by_category: dict[str, list[dict]] = {}
        for item in cart:
            try:
                product = await products.get_by_id(int(item["product_id"]))
                quantity = int(item.get("quantity", 1))
            except (KeyError, TypeError, ValueError):
                product = None
                quantity = 0
            if not product or not product.is_active or not 1 <= quantity <= 99:
                unavailable_products = True
                break
            normalized = {
                "product_id": product.id,
                "product_name": product.name,
                "category": product.category,
                "quantity": quantity,
            }
            normalized_cart.append(normalized)
            cart_by_category.setdefault(product.category, []).append(normalized)

        if not unavailable_products:
            for category, items in cart_by_category.items():
                supplier_id = await products.get_supplier_for_category(category)
                target_chat, _ = resolve_supplier_chat(supplier_id)
                if not target_chat:
                    missing_categories.append(category)
                    continue
                fulfillment_specs.append({
                    "supplier_chat_id": target_chat,
                    "category": category,
                    "items": [
                        {"product_name": item["product_name"], "quantity": item["quantity"]}
                        for item in items
                    ],
                })

        if not unavailable_products and not missing_categories:
            from database.repositories.api_store_repo import UserOrderLimitRepository
            limit_repo = UserOrderLimitRepository(session)
            allowed, reason = await limit_repo.check_and_increment(user.id)
            if not allowed:
                limit_error = reason
            else:
                db_user = await UserRepository(session).get_by_telegram_id(user.id)
                repo = OrderRepository(session)
                order = await repo.create_cart(
                    user_id=db_user.id,
                    cart_items=normalized_cart,
                    player_id=player_id,
                    supplier_fulfillments=fulfillment_specs,
                )
                order = await repo.get_by_order_id(order.order_id)

    if unavailable_products:
        await update.callback_query.edit_message_text(
            "⚠️ <b>Your package selection changed.</b>\n\nOne or more packages are no longer available. Please start again.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    if missing_categories:
        category_list = ", ".join(html.escape(category, quote=False) for category in missing_categories)
        await _notify_admins(
            context,
            "⚠️ <b>ORDER BLOCKED: SUPPLIER NOT CONFIGURED</b>\n\n"
            f"Customer: <code>{user.id}</code>\nProduct group(s): {category_list}\n"
            "No order was created. Configure a supplier destination and ask the customer to try again.",
        )
        await update.callback_query.edit_message_text(
            "⚠️ <b>We can’t submit this order yet.</b>\n\n"
            "A supplier is not available for one of these product groups. The administrator has been notified. "
            "Your cart was not charged or submitted.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    if limit_error:
        await update.callback_query.edit_message_text(
            f"🚫 <b>Order limit reached</b>\n\n{html.escape(limit_error, quote=False)}",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    if order is None:
        await update.callback_query.edit_message_text(
            "⚠️ <b>We couldn’t create your order.</b>\n\nPlease try again in a moment.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    context.user_data.pop("cart", None)
    context.user_data.pop("player_id", None)
    order_id = order.order_id
    safe_order_id = html.escape(order_id, quote=False)
    lines = "\n".join(
        f"• {html.escape(item['product_name'], quote=False)} × {item['quantity']}"
        for item in normalized_cart
    )
    dispatching_text = (
        "⏳ <b>Order received</b>\n──────────────\n\n"
        f"🧾 <b>Order ID</b>  <code>{safe_order_id}</code>\n"
        f"{lines}\n\n"
        "Sending your order to the supplier groups now…"
    )
    await update.callback_query.edit_message_text(dispatching_text, parse_mode="HTML")

    delivery_failures = await _forward_to_suppliers(context, order)
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        final_order = await repo.get_by_order_id(order_id)
        status_value = final_order.status.value if final_order else "pending"
    status_icon, status_label, _ = STATUS_DISPLAY.get(status_value, ("❓", status_value.upper(), ""))

    if delivery_failures:
        result_text = (
            "⚠️ <b>Order saved, but delivery needs attention</b>\n──────────────\n\n"
            f"🧾 <b>Order ID</b>  <code>{safe_order_id}</code>\n"
            f"{lines}\n\n"
            "The administrator has been notified about the supplier delivery issue. "
            f"Please don’t submit the order again; use Refresh status to follow it.\n\n{status_icon} Status: <b>{status_label}</b>"
        )
    else:
        result_text = (
            "✅ <b>Order sent to suppliers</b>\n──────────────\n\n"
            f"🧾 <b>Order ID</b>  <code>{safe_order_id}</code>\n"
            f"{lines}\n\n"
            f"{status_icon} Status: <b>{status_label}</b>\n\n"
            "We’ll message you when all supplier groups finish processing it."
        )
    await update.callback_query.message.edit_text(
        result_text,
        reply_markup=order_status_kb(order_id),
        parse_mode="HTML",
    )

    return ConversationHandler.END


async def cb_edit_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    await update.callback_query.edit_message_text(
        _order_step(
            context,
            4,
            "Enter your PUBG Player ID",
            "Send the correct ID below. It must contain 5–15 digits and start with 5.",
        ),
        parse_mode="HTML",
    )
    return ORDER_ENTER_PLAYER_ID


async def cb_reset_order_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the current product selection and return to the product groups."""
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    context.user_data["cart"] = []
    context.user_data.pop("player_id", None)
    context.user_data.pop("temp_item", None)
    context.user_data.pop("temp_cat", None)
    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    context.user_data["order_has_categories"] = True
    if not categories:
        await update.callback_query.edit_message_text(
            "😕  <b>No Products Available</b>\n\nCheck back soon!",
            parse_mode="HTML", reply_markup=back_to_menu_kb()
        )
        return ConversationHandler.END
    await update.callback_query.edit_message_text(
        _order_step(context, 1, "Choose a product", "Select a product to see its available denominations."),
        reply_markup=categories_kb(categories),
        parse_mode="HTML",
    )
    return ORDER_SELECT_CATEGORY


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from config.settings import ADMIN_IDS
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            logger.exception("Could not notify admin %s about order delivery", admin_id)


async def _forward_to_suppliers(context: ContextTypes.DEFAULT_TYPE, order) -> list[str]:
    """Dispatch each persisted supplier part and record its own delivery result."""
    failures: list[str] = []
    supplier_message_saved = bool(order.supplier_msg_id)
    for fulfillment in order.fulfillments:
        if fulfillment.status.value != "queued":
            continue
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            if not await repo.mark_fulfillment_sending(fulfillment.id):
                continue
        items = json.loads(fulfillment.items_snapshot)
        safe_order_id = html.escape(order.order_id, quote=False)
        safe_category = html.escape(fulfillment.category, quote=False)
        supplier_text = (
            "🆕 <b>New order</b>\n──────────────\n"
            f"🧾 <b>Order ID</b>  <code>{safe_order_id}</code>\n"
            f"📂 <b>Product group</b>  {safe_category}\n"
            f"🎮 <b>Player ID</b>  <code>{html.escape(order.player_id, quote=False)}</code>\n\n"
            "<b>Items</b>\n"
        )
        supplier_text += "".join(
            f"• {html.escape(str(item['product_name']), quote=False)} × {int(item['quantity'])}\n"
            for item in items
        )
        supplier_text += "\nMark this group’s items as <b>DONE</b> or <b>ERROR</b>:"
        try:
            message = await context.bot.send_message(
                chat_id=fulfillment.supplier_chat_id,
                text=supplier_text,
                parse_mode="HTML",
                reply_markup=supplier_done_error_kb(order.order_id, fulfillment.id),
            )
            async with AsyncSessionLocal() as session:
                repo = OrderRepository(session)
                await repo.set_fulfillment_dispatched(fulfillment.id, message.message_id)
                if not supplier_message_saved:
                    saved_order = await repo.get_by_order_id(order.order_id)
                    if saved_order:
                        await repo.set_supplier_msg(saved_order, message.message_id)
                        supplier_message_saved = True
            logger.info(
                "Delivered order %s group %s to supplier chat %s",
                order.order_id, fulfillment.category, fulfillment.supplier_chat_id,
            )
        except Exception as exc:
            safe_error = html.escape(str(exc), quote=False)
            async with AsyncSessionLocal() as session:
                repo = OrderRepository(session)
                failure_recorded = await repo.set_fulfillment_failed(fulfillment.id, str(exc))
                await repo.refresh_order_status_from_fulfillments(
                    order.id,
                    changed_by="supplier_dispatch",
                    note=f"Delivery failed for {fulfillment.category}: {type(exc).__name__}",
                )
            if failure_recorded:
                failures.append(fulfillment.category)
                await _notify_admins(
                    context,
                    "⚠️ <b>SUPPLIER DELIVERY FAILED</b>\n\n"
                    f"Order: <code>{safe_order_id}</code>\n"
                    f"Product group: {safe_category}\n"
                    f"Supplier chat: <code>{fulfillment.supplier_chat_id}</code>\n"
                    f"Error: <code>{safe_error}</code>\n\nPlease contact the customer and arrange manual fulfillment.",
                )

    async with AsyncSessionLocal() as session:
        await OrderRepository(session).refresh_order_status_from_fulfillments(
            order.id,
            changed_by="supplier_dispatch",
            note="Supplier dispatch completed.",
        )
    return failures


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
    await _show_my_orders(update, context, page=0, from_callback=False)


async def _show_my_orders(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, from_callback: bool,
) -> None:
    user = update.effective_user
    page_size = 5
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_or_create(user.id, user.username, user.full_name)
        from config.settings import ADMIN_IDS
        if not await user_repo.is_session_valid(db_user) and user.id not in ADMIN_IDS:
            if from_callback:
                await update.callback_query.message.reply_text("🔐 Please /start to sign in first.")
            else:
                await update.message.reply_text("🔐 Please /start to sign in first.")
            return

        repo = OrderRepository(session)
        total = await repo.count_by_user(db_user.id)
        page_count = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, page_count - 1))
        orders = await repo.get_by_user(db_user.id, limit=page_size, offset=page * page_size)

    if not orders:
        text = "📭 <b>No orders yet</b>\n──────────────\n\nPlace your first order from the main menu."
        markup = back_to_menu_kb()
    else:
        lines = [f"📦 <b>Your orders</b> · Page {page + 1} of {page_count}", "──────────────", ""]
        for order in orders:
            icon, label, _ = STATUS_DISPLAY.get(order.status.value, ("❓", order.status.value, ""))
            lines.append(f"{icon} <b>{html.escape(order.order_id, quote=False)}</b> · {label}")
            product_summaries = [
                f"{html.escape(item.product_name[:32], quote=False)} × {item.quantity}"
                for item in order.items[:2]
            ]
            if len(order.items) > 2:
                product_summaries.append(f"+{len(order.items) - 2} more")
            products = ", ".join(product_summaries)
            if products:
                lines.append(products)
            lines.append("")
        text = "\n".join(lines).rstrip()
        markup = my_orders_kb(orders, page, total, page_size)

    if from_callback:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cb_my_orders_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    await _show_my_orders(update, context, page=0, from_callback=True)


async def cb_my_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    page = int(update.callback_query.data.split(":", 1)[1])
    await _show_my_orders(update, context, page=page, from_callback=True)


async def cb_view_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    _, order_id, page_value = update.callback_query.data.split(":", 2)
    page = max(0, int(page_value))
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        order = await OrderRepository(session).get_by_order_id(order_id)
        if not db_user or not order or order.user_id != db_user.id:
            order = None
    if order is None:
        await update.callback_query.message.edit_text(
            "⚠️ <b>That order could not be found.</b>",
            parse_mode="HTML",
            reply_markup=my_orders_kb([], page, 0),
        )
        return
    icon, label, desc = STATUS_DISPLAY.get(order.status.value, ("❓", order.status.value, ""))
    item_lines = "\n".join(
        f"• {html.escape(item.product_name, quote=False)} × {item.quantity}"
        for item in order.items
    ) or "• Order details unavailable"
    text = (
        "🔎 <b>Order details</b>\n──────────────\n\n"
        f"🧾 <b>Order ID</b>  <code>{html.escape(order.order_id, quote=False)}</code>\n"
        f"{icon} <b>{label}</b> · {desc}\n\n"
        f"<b>Items</b>\n{item_lines}\n\n"
        f"🎮 <b>Player ID</b>  <code>{html.escape(order.player_id, quote=False)}</code>"
    )
    await update.callback_query.edit_message_text(
        text, parse_mode="HTML", reply_markup=order_status_kb(order_id, page)
    )


async def cb_refresh_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    parts = update.callback_query.data.split(":")
    order_id = parts[1]
    page = max(0, int(parts[2])) if len(parts) > 2 else 0

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(update.effective_user.id)
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)
        if not user or not order or order.user_id != user.id:
            order = None

    if not order:
        await update.callback_query.edit_message_text(
            "⚠️ <b>That order could not be found.</b>",
            parse_mode="HTML",
            reply_markup=my_orders_kb([], page, 0),
        )
        return

    icon, label, desc = STATUS_DISPLAY.get(order.status.value, ("❓", order.status.value, ""))
    items = "\n".join(
        f"• {html.escape(item.product_name, quote=False)} × {item.quantity}"
        for item in order.items
    ) or "• Order details unavailable"
    await update.callback_query.edit_message_text(
        "🔎 <b>Order details</b>\n──────────────\n\n"
        f"🧾 <b>Order ID</b>  <code>{html.escape(order.order_id, quote=False)}</code>\n"
        f"{icon} <b>{label}</b>\n"
        f"📝 {desc}\n\n<b>Items</b>\n{items}\n\n"
        f"🎮 <b>Player ID</b>  <code>{html.escape(order.player_id, quote=False)}</code>",
        reply_markup=order_status_kb(order_id, page),
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
                CallbackQueryHandler(cb_back_to_products, pattern=r"^back_products$"),
            ],
            ORDER_CART_ACTION: [
                CallbackQueryHandler(cb_order_start, pattern=r"^order_start$"),
                CallbackQueryHandler(cb_cart_checkout, pattern=r"^cart_checkout$"),
                CallbackQueryHandler(cb_cart_clear, pattern=r"^cart_clear$"),
                CallbackQueryHandler(cb_cart_adjust, pattern=r"^cart_(qty|remove):"),
                CallbackQueryHandler(cb_cart_item, pattern=r"^cart_item:"),
            ],
            ORDER_ENTER_PLAYER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_enter_player_id),
            ],
            ORDER_CONFIRM: [
                CallbackQueryHandler(cb_confirm_order, pattern=r"^confirm_order$"),
                CallbackQueryHandler(cb_reset_order_products, pattern=r"^reset_order_products$"),
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
