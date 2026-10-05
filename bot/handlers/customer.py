"""
Customer order flow — optional prices, per-package supplier routing.
"""
import logging
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CallbackQueryHandler, MessageHandler, CommandHandler, filters,
)

from database.database import AsyncSessionLocal
from database.repositories.api_store_repo import UserOrderLimitRepository
from database.repositories.customer_extras_repo import SavedPlayerIdRepository
from database.repositories.user_repo import UserRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.order_repo import OrderRepository
from bot.middlewares.auth_middleware import require_auth, require_callback_auth
from bot.keyboards.customer_kb import (
    main_menu_kb, categories_kb, products_kb, quantity_kb, cart_kb,
    confirm_order_kb, order_status_kb, back_to_menu_kb, my_orders_kb,
    saved_player_ids_kb,
)
from bot.states.states import (
    ORDER_SELECT_CATEGORY, ORDER_SELECT_PRODUCT, ORDER_SELECT_QUANTITY,
    ORDER_CART_ACTION, ORDER_ENTER_PLAYER_ID, ORDER_CONFIRM,
)
from services import app_settings
from services.dispatch import RETRY, SENT, dispatch_order
from services.messages import is_terminal, item_lines, order_card_text, order_total, status_parts
from services.notify import notify_admins
from utils.helpers import format_datetime
from utils.supplier_routing import resolve_supplier_chat
from utils.ui import DIVIDER, esc, money, panel

logger = logging.getLogger(__name__)

ORDER_SESSION_KEYS = ("cart", "temp_item", "player_id", "temp_cat", "order_has_categories")


def _clear_order_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ORDER_SESSION_KEYS:
        context.user_data.pop(key, None)


async def _remember_customer_order_message(order_id: str, message_id: int) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await OrderRepository(session).set_customer_msg(order_id, message_id)
    except Exception as exc:
        logger.warning(
            "Could not save customer order message for %s (%s)",
            order_id, type(exc).__name__,
        )


def _render_cart(cart: list, show_prices: bool) -> str:
    lines = []
    for i, item in enumerate(cart, 1):
        line = f"{i}. <b>{esc(item['product_name'])}</b> × {item['quantity']}"
        if show_prices and item.get("unit_price") is not None:
            line += f" — {money(item['unit_price'] * item['quantity'])}"
        lines.append(line)
    total = order_total(cart) if show_prices else None
    if total is not None:
        lines.append(f"\n💰 <b>Total</b>  {money(total)}")
    return panel("Your cart", "\n".join(lines), icon="🛒")


def _order_step(context: ContextTypes.DEFAULT_TYPE, step: int, title: str, prompt: str) -> str:
    total = 4 if context.user_data.get("order_has_categories") else 3
    return panel(
        "New order",
        f"<i>Step {step} of {total}</i>\n\n<b>{esc(title)}</b>\n\n{prompt}",
        icon="🛍",
    )


def _review_text(cart: list, player_id: str, show_prices: bool) -> str:
    return (
        f"🧾 <b>Review your order</b>\n{DIVIDER}\n"
        f"{_render_cart(cart, show_prices)}\n"
        f"🎮 <b>Player ID</b>  <code>{esc(player_id)}</code>\n\n"
        "Check these details, then submit your order."
    )


def _valid_player_id(value: str) -> bool:
    return 3 <= len(value) <= 20 and value.isprintable() and not any(ch.isspace() for ch in value)


async def _show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    # Always show the game/product group first, even when it is the only one.
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


async def cb_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    context.user_data.setdefault("cart", [])
    return await _show_categories(update, context)


async def cb_select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    category = update.callback_query.data.split(":", 1)[1]
    context.user_data["temp_cat"] = category

    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_by_category(category)

    await update.callback_query.edit_message_text(
        _order_step(context, 2, category, "Choose a package to add it to your cart."),
        reply_markup=products_kb(products, await app_settings.show_prices()),
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
        "unit_price": product.price,
    }
    await update.callback_query.edit_message_text(
        _order_step(context, 3, product.name, "Choose how many you want."),
        reply_markup=quantity_kb(),
        parse_mode="HTML",
    )
    return ORDER_SELECT_QUANTITY


async def cb_select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    qty = int(update.callback_query.data.split(":", 1)[1])

    item = context.user_data.pop("temp_item", None)
    if item is None:
        return await _show_categories(update, context)
    item["quantity"] = qty
    context.user_data.setdefault("cart", []).append(item)

    await update.callback_query.edit_message_text(
        f"{_render_cart(context.user_data['cart'], await app_settings.show_prices())}\n"
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
        return await _show_categories(update, context)

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
        reply_markup=products_kb(products, await app_settings.show_prices()),
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
        f"{_render_cart(cart, await app_settings.show_prices())}\n\n"
        "Adjust quantities, add another package, or continue to checkout.",
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
    _clear_order_session(context)
    await update.callback_query.edit_message_text(
        "🗑 <b>Your cart is empty.</b>\n\nYou can start a new order whenever you're ready.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def _player_id_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        saved = await SavedPlayerIdRepository(session).list(db_user.id) if db_user else []
    if saved:
        prompt += "\n\nOr tap one of your recent IDs:"
    await update.callback_query.edit_message_text(
        _order_step(context, 4, "Enter your Game Player ID", prompt),
        parse_mode="HTML",
        reply_markup=saved_player_ids_kb(saved),
    )
    return ORDER_ENTER_PLAYER_ID


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
    return await _player_id_prompt(update, context, "Send your Player ID. Check it carefully before submitting.")


async def cb_edit_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    return await _player_id_prompt(update, context, "Send the correct ID below.")


async def cb_use_saved_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    saved_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        saved = await SavedPlayerIdRepository(session).get(db_user.id, saved_id) if db_user else None
    if saved is None or not context.user_data.get("cart"):
        return await _player_id_prompt(update, context, "That saved ID is gone. Send your Player ID:")
    context.user_data["player_id"] = saved.player_id
    await update.callback_query.edit_message_text(
        _review_text(context.user_data["cart"], saved.player_id, await app_settings.show_prices()),
        reply_markup=confirm_order_kb(),
        parse_mode="HTML",
    )
    return ORDER_CONFIRM


async def cb_forget_player_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        if db_user:
            await SavedPlayerIdRepository(session).clear(db_user.id)
    return await _player_id_prompt(update, context, "Saved IDs cleared. Send your Player ID:")


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
    if not _valid_player_id(player_id):
        await update.message.reply_text(
            "❌ <b>That Player ID does not look right.</b>\n\n"
            "Enter a valid Game ID (3-20 characters, no spaces). Please try again:",
            parse_mode="HTML",
        )
        return ORDER_ENTER_PLAYER_ID
    if not context.user_data.get("cart"):
        await update.message.reply_text(
            "❌ <b>Your order session expired.</b>\n\nStart a new order from the menu.",
            parse_mode="HTML", reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    context.user_data["player_id"] = player_id
    await update.message.reply_text(
        _review_text(context.user_data["cart"], player_id, await app_settings.show_prices()),
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

    # Removing the keyboard first also stops double submissions.
    await update.callback_query.message.edit_text(
        f"⏳ <b>Checking your order…</b>\n{DIVIDER}\n\n"
        "Confirming package availability and supplier routing.",
        parse_mode="HTML",
    )

    normalized_cart: list[dict] = []
    fulfillment_specs: list[dict] = []
    missing_products: list[str] = []
    unavailable_products = False
    limit_error: str | None = None
    order = None

    async with AsyncSessionLocal() as session:
        products = ProductRepository(session)
        items_by_route: dict[tuple[str, int], list[dict]] = {}
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
                "unit_price": product.price,
            }
            normalized_cart.append(normalized)
            target_chat, _ = resolve_supplier_chat(product.supplier_chat_id)
            if not target_chat:
                missing_products.append(f"{product.category}: {product.name}")
            else:
                items_by_route.setdefault((product.category, target_chat), []).append(normalized)

        if not unavailable_products:
            for (category, target_chat), items in items_by_route.items():
                fulfillment_specs.append({
                    "supplier_chat_id": target_chat,
                    "category": category,
                    "items": [
                        {"product_name": item["product_name"], "quantity": item["quantity"]}
                        for item in items
                    ],
                })

        if not unavailable_products and not missing_products:
            allowed, reason = await UserOrderLimitRepository(session).check_and_increment(user.id)
            if not allowed:
                limit_error = reason
            else:
                db_user = await UserRepository(session).get_by_telegram_id(user.id)
                order = await OrderRepository(session).create_order(
                    user_id=db_user.id,
                    items=normalized_cart,
                    player_id=player_id,
                    fulfillments=fulfillment_specs,
                )
                await SavedPlayerIdRepository(session).remember(db_user.id, player_id)

    if unavailable_products:
        await update.callback_query.edit_message_text(
            "⚠️ <b>Your package selection changed.</b>\n\nOne or more packages are no longer available. Please start again.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    if missing_products:
        product_list = ", ".join(esc(name) for name in missing_products)
        await notify_admins(
            context.bot,
            "⚠️ <b>ORDER BLOCKED: SUPPLIER NOT CONFIGURED</b>\n\n"
            f"Customer: <code>{user.id}</code>\nPackage(s): {product_list}\n"
            "No order was created. Configure a supplier destination for each package and ask the customer to try again.",
        )
        await update.callback_query.edit_message_text(
            "⚠️ <b>We can’t submit this order yet.</b>\n\n"
            "A supplier is not available for one of these packages. The administrator has been notified. "
            "Your cart was not charged or submitted.",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    if limit_error:
        await update.callback_query.edit_message_text(
            f"🚫 <b>Order limit reached</b>\n\n{esc(limit_error)}",
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

    _clear_order_session(context)
    order_id = order.order_id
    await update.callback_query.edit_message_text(
        f"⏳ <b>Order received</b>\n{DIVIDER}\n\n"
        f"🧾 <b>Order ID</b>  <code>{esc(order_id)}</code>\n"
        f"{item_lines(normalized_cart, False)}\n\n"
        "Sending your order to the supplier groups now…",
        parse_mode="HTML",
    )
    await _remember_customer_order_message(order_id, update.callback_query.message.message_id)

    outcomes = await dispatch_order(context.bot, order.id)
    async with AsyncSessionLocal() as session:
        final_order = await OrderRepository(session).get_by_order_id(order_id)
    show_prices = await app_settings.show_prices()
    retrying = [outcome.category for outcome in outcomes if outcome.result == RETRY]
    if retrying:
        title = "⏳ <b>Order saved — delivery is being retried</b>"
        footer = (
            f"\n\nWe couldn’t reach the supplier for {esc(', '.join(retrying))} yet and will keep trying "
            "automatically. Please don’t submit the order again; you’ll get a message here."
        )
    elif all(outcome.result == SENT for outcome in outcomes):
        title = "✅ <b>Order sent to suppliers</b>"
        footer = "\n\nWe’ll message you when all supplier groups finish processing it."
    else:
        title = "⚠️ <b>Order saved, but delivery needs attention</b>"
        footer = "\n\nThe administrator has been notified. Please don’t submit the order again."
    await update.callback_query.message.edit_text(
        order_card_text(final_order, show_prices, title=title) + footer,
        reply_markup=order_status_kb(order_id, terminal=is_terminal(final_order)),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cb_reorder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Load a previous order's packages and player ID into a new checkout."""
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    order_id = update.callback_query.data.split(":", 1)[1]
    cart: list[dict] = []
    skipped: list[str] = []
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        order = await OrderRepository(session).get_by_order_id(order_id)
        if not db_user or not order or order.user_id != db_user.id:
            order = None
        else:
            products = ProductRepository(session)
            for item in order.items:
                product = await products.get_by_id(item.product_id) if item.product_id else None
                if product is None or not product.is_active:
                    skipped.append(item.product_name)
                    continue
                cart.append({
                    "product_id": product.id,
                    "product_name": product.name,
                    "category": product.category,
                    "quantity": item.quantity,
                    "unit_price": product.price,
                })
    if order is None:
        await update.callback_query.edit_message_text(
            "⚠️ <b>That order could not be found.</b>", parse_mode="HTML", reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END
    if not cart:
        await update.callback_query.edit_message_text(
            "😕 <b>Those packages are no longer available.</b>\n\nStart a new order from the menu.",
            parse_mode="HTML", reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    _clear_order_session(context)
    context.user_data["cart"] = cart
    context.user_data["player_id"] = order.player_id
    context.user_data["order_has_categories"] = True
    text = _review_text(cart, order.player_id, await app_settings.show_prices())
    if skipped:
        text += f"\n\n<i>No longer available, left out: {esc(', '.join(skipped))}</i>"
    await update.callback_query.edit_message_text(text, reply_markup=confirm_order_kb(), parse_mode="HTML")
    return ORDER_CONFIRM


async def cb_reset_order_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the current product selection and return to the product groups."""
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    _clear_order_session(context)
    context.user_data["cart"] = []
    return await _show_categories(update, context)


async def cb_return_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _clear_order_session(context)
    await update.callback_query.edit_message_text(
        "🏠 <b>Main menu</b>\n\nYour unfinished cart was cleared.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer("Order cancelled")
    _clear_order_session(context)
    text = "✖ <b>Order cancelled</b>\n\nYour cart has been cleared. You can start again anytime."
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=main_menu_kb()
        )
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ─── Order history ────────────────────────────────────────────────────

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
        text = f"📭 <b>No orders yet</b>\n{DIVIDER}\n\nPlace your first order from the main menu."
        markup = back_to_menu_kb()
    else:
        lines = [f"📦 <b>Your orders</b> · Page {page + 1} of {page_count}", DIVIDER, ""]
        for order in orders:
            icon, label, _ = status_parts(order.status.value)
            lines.append(f"{icon} <b>{esc(order.order_id)}</b> · {label}")
            product_summaries = [
                f"{esc(item.product_name[:32])} × {item.quantity}"
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


async def _show_own_order(update: Update, order_id: str, page: int) -> None:
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        order = await OrderRepository(session).get_by_order_id(order_id)
        if not db_user or not order or order.user_id != db_user.id:
            order = None
    if order is None:
        await update.callback_query.edit_message_text(
            "⚠️ <b>That order could not be found.</b>",
            parse_mode="HTML",
            reply_markup=my_orders_kb([], page, 0),
        )
        return
    await update.callback_query.edit_message_text(
        order_card_text(order, await app_settings.show_prices()),
        parse_mode="HTML",
        reply_markup=order_status_kb(order_id, page, terminal=is_terminal(order)),
    )
    await _remember_customer_order_message(order_id, update.callback_query.message.message_id)


async def cb_view_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    _, order_id, page_value = update.callback_query.data.split(":", 2)
    await _show_own_order(update, order_id, max(0, int(page_value)))


async def cb_refresh_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    parts = update.callback_query.data.split(":")
    page = max(0, int(parts[2])) if len(parts) > 2 else 0
    await _show_own_order(update, parts[1], page)


async def cb_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        balance = await OrderRepository(session).unsettled_balance_by_user(db_user.id) if db_user else None
    if balance is None:
        await update.callback_query.edit_message_text("🔐 Please /start to sign in first.")
        return
    lines = [f"Completed orders awaiting payment: <b>{balance['orders']}</b>"]
    if await app_settings.show_prices() and balance["orders"]:
        lines.append(f"Amount due: <b>{money(balance['total'])}</b>")
        if balance["unpriced_lines"]:
            lines.append(f"<i>{balance['unpriced_lines']} package line(s) have no price and are not included.</i>")
    last = balance["last_settled_at"]
    lines.append(f"Last payment cleared: {format_datetime(last) if last else 'never'}")
    await update.callback_query.edit_message_text(
        panel("Your balance", "\n".join(lines), icon="💰"),
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(),
    )


def get_order_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_order_start, pattern=r"^order_start$"),
            CallbackQueryHandler(cb_reorder, pattern=r"^reorder:"),
        ],
        states={
            ORDER_SELECT_CATEGORY: [
                CallbackQueryHandler(cb_select_category, pattern=r"^cat:"),
            ],
            ORDER_SELECT_PRODUCT: [
                CallbackQueryHandler(cb_select_product, pattern=r"^prod:"),
                CallbackQueryHandler(cb_order_start, pattern=r"^order_start$"),
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
                CallbackQueryHandler(cb_use_saved_player_id, pattern=r"^use_pid:\d+$"),
                CallbackQueryHandler(cb_forget_player_ids, pattern=r"^forget_pids$"),
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
