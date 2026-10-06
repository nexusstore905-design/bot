"""
Customer order flow: browse or search, cart, player ID, review, live tracking.
"""
import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.i18n import t
from bot.keyboards.customer_kb import (
    back_to_menu_kb,
    cart_kb,
    categories_kb,
    confirm_order_kb,
    main_menu_kb,
    my_orders_kb,
    order_status_kb,
    products_kb,
    quantity_kb,
    saved_player_ids_kb,
)
from bot.middlewares.auth_middleware import is_team, require_auth, require_callback_auth, user_language
from bot.states.states import (
    ORDER_CART_ACTION,
    ORDER_CONFIRM,
    ORDER_ENTER_PLAYER_ID,
    ORDER_SELECT_CATEGORY,
    ORDER_SELECT_PRODUCT,
    ORDER_SELECT_QUANTITY,
)
from database.database import AsyncSessionLocal
from database.repositories.api_store_repo import UserOrderLimitRepository
from database.repositories.customer_extras_repo import SavedPlayerIdRepository
from database.repositories.order_repo import OrderRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.user_repo import UserRepository
from services.dispatch import RETRY, SENT, dispatch_order
from services.messages import is_terminal, item_lines, order_card_text, status_label
from services.notify import notify_team
from utils.helpers import format_datetime
from utils.supplier_routing import resolve_supplier_chat
from utils.ui import esc, header, quote, step_dots

logger = logging.getLogger(__name__)

ORDER_SESSION_KEYS = ("cart", "temp_item", "player_id", "temp_cat", "search_query")
TOTAL_STEPS = 4
ORDERS_PAGE_SIZE = 5


def _lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    return user_language(context, update.effective_user)


def _clear_order_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ORDER_SESSION_KEYS:
        context.user_data.pop(key, None)


def _step(lang: str, step: int, title: str, prompt: str) -> str:
    subtitle = f"{step_dots(step, TOTAL_STEPS)}  {esc(t(lang, 'step', step=step, total=TOTAL_STEPS))}"
    return f"{header('🛍', t(lang, 'new_order'), subtitle)}\n\n<b>{esc(title)}</b>\n{prompt}"


def _cart_text(cart: list, lang: str) -> str:
    return f"{t(lang, 'cart_title')}\n{quote(item_lines(cart))}\n{t(lang, 'cart_hint')}"


def _review_text(cart: list, player_id: str, lang: str) -> str:
    details = f"{item_lines(cart)}\n🎮 {t(lang, 'player_id')}: <code>{esc(player_id)}</code>"
    return f"{t(lang, 'review_title')}\n{quote(details)}\n{t(lang, 'review_hint')}"


def _valid_player_id(value: str) -> bool:
    return 3 <= len(value) <= 20 and value.isprintable() and not any(ch.isspace() for ch in value)


async def _remember_customer_order_message(order_id: str, message_id: int) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await OrderRepository(session).set_customer_msg(order_id, message_id)
    except Exception as exc:
        logger.warning("Could not save customer order message for %s (%s)", order_id, type(exc).__name__)


# ─── Browse ───────────────────────────────────────────────────────────

async def _show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    lang = _lang(update, context)
    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    if not categories:
        await update.callback_query.edit_message_text(
            t(lang, "no_products"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    await update.callback_query.edit_message_text(
        _step(lang, 1, t(lang, "choose_product"), t(lang, "choose_product_hint")),
        reply_markup=categories_kb(categories, lang, context.user_data.get("cart"), page),
        parse_mode="HTML",
    )
    return ORDER_SELECT_CATEGORY


async def _current_products(context: ContextTypes.DEFAULT_TYPE) -> tuple[list, str]:
    """Products for the current category or search, and the title to show."""
    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        if context.user_data.get("search_query"):
            query = context.user_data["search_query"]
            return await repo.search_active(query), query
        category = context.user_data.get("temp_cat") or ""
        return await repo.get_by_category(category), category


async def _show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0, notice: str = ""):
    lang = _lang(update, context)
    products, title = await _current_products(context)
    if not products:
        return await _show_categories(update, context)
    if context.user_data.get("search_query"):
        title = t(lang, "search_results", query=title)
    prompt = (f"{notice}\n" if notice else "") + t(lang, "choose_package")
    await update.callback_query.edit_message_text(
        _step(lang, 2, title, prompt),
        reply_markup=products_kb(products, lang, context.user_data.get("cart"), page),
        parse_mode="HTML",
    )
    return ORDER_SELECT_PRODUCT


async def cb_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    context.user_data.setdefault("cart", [])
    context.user_data.pop("search_query", None)
    return await _show_categories(update, context)


async def cb_category_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    return await _show_categories(update, context, int(update.callback_query.data.split(":", 1)[1]))


async def cb_select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    prefix, value = update.callback_query.data.split(":", 1)
    if prefix == "cat_i":
        # Long category names are sent by position; resolve against the current list.
        async with AsyncSessionLocal() as session:
            categories = await ProductRepository(session).get_categories()
        index = int(value)
        if not 0 <= index < len(categories):
            return await _show_categories(update, context)
        value = categories[index]
    context.user_data["temp_cat"] = value
    context.user_data.pop("search_query", None)
    return await _show_products(update, context)


async def cb_product_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    return await _show_products(update, context, int(update.callback_query.data.split(":", 1)[1]))


async def msg_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Typing while choosing a product searches package names and groups."""
    if not await require_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    query = update.message.text.strip()[:40]
    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        results = await repo.search_active(query)
        categories = await repo.get_categories()
    if not results:
        await update.message.reply_text(
            t(lang, "search_none", query=query),
            reply_markup=categories_kb(categories, lang, context.user_data.get("cart")),
            parse_mode="HTML",
        )
        return ORDER_SELECT_CATEGORY
    context.user_data["search_query"] = query
    context.user_data.pop("temp_cat", None)
    await update.message.reply_text(
        _step(lang, 2, t(lang, "search_results", query=query), t(lang, "choose_package")),
        reply_markup=products_kb(results, lang, context.user_data.get("cart")),
        parse_mode="HTML",
    )
    return ORDER_SELECT_PRODUCT


async def cb_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    product_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(product_id)
    if not product or not product.is_active:
        await update.callback_query.edit_message_text(
            t(lang, "product_gone"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    context.user_data["temp_item"] = {
        "product_id": product.id,
        "product_name": product.name,
        "category": product.category,
    }
    await update.callback_query.edit_message_text(
        _step(lang, 3, product.name, t(lang, "choose_qty")),
        reply_markup=quantity_kb(lang),
        parse_mode="HTML",
    )
    return ORDER_SELECT_QUANTITY


async def cb_select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _lang(update, context)
    if not await require_callback_auth(update, context, toast=t(lang, "added_toast")):
        return ConversationHandler.END
    item = context.user_data.pop("temp_item", None)
    if item is None:
        return await _show_categories(update, context)
    item["quantity"] = int(update.callback_query.data.split(":", 1)[1])
    cart = context.user_data.setdefault("cart", [])
    existing = next((line for line in cart if line["product_id"] == item["product_id"]), None)
    if existing:
        existing["quantity"] = min(99, existing["quantity"] + item["quantity"])
    else:
        cart.append(item)
    # Stay in the package list so more can be added; the cart button is pinned.
    return await _show_products(update, context, notice=f"✓ {esc(item['product_name'])} × {item['quantity']}")


async def cb_back_to_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    context.user_data.pop("temp_item", None)
    return await _show_products(update, context)


# ─── Cart ─────────────────────────────────────────────────────────────

async def cb_cart_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    cart = context.user_data.get("cart") or []
    if not cart:
        return await _show_categories(update, context)
    await update.callback_query.edit_message_text(
        _cart_text(cart, lang), parse_mode="HTML", reply_markup=cart_kb(cart, lang),
    )
    return ORDER_CART_ACTION


async def cb_cart_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    parts = update.callback_query.data.split(":")
    action, index = parts[0], int(parts[1])
    cart = context.user_data.get("cart", [])
    if not 0 <= index < len(cart):
        await update.callback_query.edit_message_text(
            t(lang, "cart_changed"), parse_mode="HTML", reply_markup=cart_kb(cart, lang),
        )
        return ORDER_CART_ACTION

    if action == "cart_remove":
        cart.pop(index)
        if not cart:
            await update.callback_query.edit_message_text(
                t(lang, "cart_empty"), parse_mode="HTML", reply_markup=main_menu_kb(lang),
            )
            return ConversationHandler.END
    else:
        quantity = int(parts[2])
        if not 1 <= quantity <= 99:
            await update.callback_query.message.reply_text(t(lang, "qty_range"))
            return ORDER_CART_ACTION
        cart[index]["quantity"] = quantity

    await update.callback_query.edit_message_text(
        _cart_text(cart, lang), parse_mode="HTML", reply_markup=cart_kb(cart, lang),
    )
    return ORDER_CART_ACTION


async def cb_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(t(_lang(update, context), "qty_hint"))
    return ORDER_CART_ACTION


async def cb_cart_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    _clear_order_session(context)
    await update.callback_query.edit_message_text(
        t(lang, "cart_empty"), parse_mode="HTML", reply_markup=main_menu_kb(lang),
    )
    return ConversationHandler.END


# ─── Player ID and review ─────────────────────────────────────────────

async def _player_id_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, notice: str = ""):
    lang = _lang(update, context)
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        saved = await SavedPlayerIdRepository(session).list(db_user.id) if db_user else []
    prompt = (f"{notice}\n" if notice else "") + t(lang, "pid_prompt")
    if saved:
        prompt += f"\n\n{t(lang, 'pid_saved_hint')}"
    await update.callback_query.edit_message_text(
        _step(lang, 4, t(lang, "pid_title"), prompt),
        parse_mode="HTML",
        reply_markup=saved_player_ids_kb(saved, lang),
    )
    return ORDER_ENTER_PLAYER_ID


async def cb_cart_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    if not context.user_data.get("cart"):
        lang = _lang(update, context)
        await update.callback_query.edit_message_text(
            t(lang, "cart_empty"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    return await _player_id_prompt(update, context)


async def cb_edit_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    return await _player_id_prompt(update, context)


async def cb_use_saved_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    saved_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        saved = await SavedPlayerIdRepository(session).get(db_user.id, saved_id) if db_user else None
    if saved is None or not context.user_data.get("cart"):
        return await _player_id_prompt(update, context)
    context.user_data["player_id"] = saved.player_id
    await update.callback_query.edit_message_text(
        _review_text(context.user_data["cart"], saved.player_id, lang),
        reply_markup=confirm_order_kb(lang),
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
    return await _player_id_prompt(update, context, notice=t(_lang(update, context), "pids_cleared"))


async def msg_enter_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("auth_rate_limited", None)
    context.user_data.pop("auth_rejection_sent", None)
    lang = _lang(update, context)
    if not await require_auth(update, context):
        throttled = context.user_data.pop("auth_rate_limited", False)
        if not context.user_data.pop("auth_rejection_sent", False):
            await update.message.reply_text(t(lang, "slow_down" if throttled else "need_signin"))
        return ORDER_ENTER_PLAYER_ID if throttled else ConversationHandler.END

    player_id = update.message.text.strip()
    if not _valid_player_id(player_id):
        await update.message.reply_text(t(lang, "pid_invalid"), parse_mode="HTML")
        return ORDER_ENTER_PLAYER_ID
    if not context.user_data.get("cart"):
        await update.message.reply_text(
            t(lang, "session_expired"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END

    context.user_data["player_id"] = player_id
    await update.message.reply_text(
        _review_text(context.user_data["cart"], player_id, lang),
        reply_markup=confirm_order_kb(lang),
        parse_mode="HTML",
    )
    return ORDER_CONFIRM


# ─── Submit ───────────────────────────────────────────────────────────

async def cb_confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
    user = update.effective_user
    cart = context.user_data.get("cart", [])
    player_id = context.user_data.get("player_id")
    if not cart or not player_id:
        await update.callback_query.edit_message_text(
            t(lang, "session_expired"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END

    # Removing the keyboard first also stops double submissions.
    await update.callback_query.message.edit_text(t(lang, "checking"), parse_mode="HTML")

    normalized_cart: list[dict] = []
    fulfillment_specs: list[dict] = []
    missing_products: list[str] = []
    unavailable = False
    limit_text: str | None = None
    order = None

    async with AsyncSessionLocal() as session:
        products = ProductRepository(session)
        routes: dict[tuple[str, int], list[dict]] = {}
        for item in cart:
            try:
                product = await products.get_by_id(int(item["product_id"]))
                quantity = int(item.get("quantity", 1))
            except (KeyError, TypeError, ValueError):
                product, quantity = None, 0
            if not product or not product.is_active or not 1 <= quantity <= 99:
                unavailable = True
                break
            line = {
                "product_id": product.id,
                "product_name": product.name,
                "category": product.category,
                "quantity": quantity,
            }
            normalized_cart.append(line)
            target_chat, _ = resolve_supplier_chat(product.supplier_chat_id)
            if target_chat:
                routes.setdefault((product.category, target_chat), []).append(line)
            else:
                missing_products.append(f"{product.category}: {product.name}")

        if not unavailable:
            fulfillment_specs = [
                {
                    "supplier_chat_id": target_chat,
                    "category": category,
                    "items": [{"product_name": line["product_name"], "quantity": line["quantity"]} for line in lines],
                }
                for (category, target_chat), lines in routes.items()
            ]

        if not unavailable and not missing_products:
            limits = UserOrderLimitRepository(session)
            allowed, _ = await limits.check_and_increment(user.id)
            if not allowed:
                limit = await limits.get(user.id)
                limit_text = (
                    t(lang, "limit_blocked") if not limit or limit.daily_limit <= 0
                    else t(lang, "limit_daily", limit=limit.daily_limit)
                )
            else:
                db_user = await UserRepository(session).get_by_telegram_id(user.id)
                order = await OrderRepository(session).create_order(
                    user_id=db_user.id,
                    items=normalized_cart,
                    player_id=player_id,
                    fulfillments=fulfillment_specs,
                )
                await SavedPlayerIdRepository(session).remember(db_user.id, player_id)

    if unavailable:
        await update.callback_query.edit_message_text(
            t(lang, "selection_changed"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    if missing_products:
        await notify_team(
            context.bot,
            "⚠️ <b>ORDER BLOCKED: SUPPLIER NOT CONFIGURED</b>\n\n"
            f"Customer: <code>{user.id}</code>\nPackage(s): {esc(', '.join(missing_products))}\n"
            "No order was created. Configure a supplier destination for each package.",
        )
        await update.callback_query.edit_message_text(
            t(lang, "no_supplier"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    if limit_text:
        await update.callback_query.edit_message_text(
            f"{t(lang, 'limit_title')}\n\n{esc(limit_text)}", parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    if order is None:
        await update.callback_query.edit_message_text(
            t(lang, "create_failed"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END

    _clear_order_session(context)
    order_id = order.order_id
    await update.callback_query.edit_message_text(
        order_card_text(order, lang, title=f"<b>{t(lang, 'order_received')}</b>"), parse_mode="HTML",
    )
    await _remember_customer_order_message(order_id, update.callback_query.message.message_id)

    outcomes = await dispatch_order(context.bot, order.id)
    async with AsyncSessionLocal() as session:
        final_order = await OrderRepository(session).get_by_order_id(order_id)
    if any(outcome.result == RETRY for outcome in outcomes):
        title, hint = "retry_title", "retry_hint"
    elif all(outcome.result == SENT for outcome in outcomes):
        title, hint = "sent_title", "sent_hint"
    else:
        title, hint = "attention_title", "attention_hint"
    await update.callback_query.message.edit_text(
        order_card_text(final_order, lang, title=f"<b>{t(lang, title)}</b>", footer=f"<i>{t(lang, hint)}</i>"),
        reply_markup=order_status_kb(order_id, lang, terminal=is_terminal(final_order)),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cb_reorder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Load a previous order's packages and player ID straight into review."""
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    lang = _lang(update, context)
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
                })
    if order is None:
        await update.callback_query.edit_message_text(
            t(lang, "order_not_found"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END
    if not cart:
        await update.callback_query.edit_message_text(
            t(lang, "reorder_none"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
        )
        return ConversationHandler.END

    _clear_order_session(context)
    context.user_data["cart"] = cart
    context.user_data["player_id"] = order.player_id
    text = _review_text(cart, order.player_id, lang)
    if skipped:
        text += f"\n\n<i>{t(lang, 'reorder_missing', names=', '.join(skipped))}</i>"
    await update.callback_query.edit_message_text(text, reply_markup=confirm_order_kb(lang), parse_mode="HTML")
    return ORDER_CONFIRM


async def cb_reset_order_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return ConversationHandler.END
    _clear_order_session(context)
    context.user_data["cart"] = []
    return await _show_categories(update, context)


async def cb_return_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.handlers.home import render_home
    await update.callback_query.answer()
    lang = _lang(update, context)
    had_cart = bool(context.user_data.get("cart"))
    _clear_order_session(context)
    text, keyboard = await render_home(
        update.effective_user, lang, notice=t(lang, "menu_cart_cleared") if had_cart else None,
    )
    await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    return ConversationHandler.END


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _lang(update, context)
    _clear_order_session(context)
    text = t(lang, "order_cancelled_flow")
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu_kb(lang))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_kb(lang))
    return ConversationHandler.END


# ─── Order history, live card, balance ────────────────────────────────

async def _show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, from_callback: bool):
    user = update.effective_user
    lang = _lang(update, context)
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_or_create(user.id, user.username, user.full_name)
        if not is_team(user.id) and not await user_repo.is_session_valid(db_user):
            target = update.callback_query.message if from_callback else update.message
            await target.reply_text(t(lang, "need_signin"))
            return
        repo = OrderRepository(session)
        total = await repo.count_by_user(db_user.id)
        pages = max(1, (total + ORDERS_PAGE_SIZE - 1) // ORDERS_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        orders = await repo.get_by_user(db_user.id, limit=ORDERS_PAGE_SIZE, offset=page * ORDERS_PAGE_SIZE)

    if not orders:
        text, markup = t(lang, "orders_empty"), back_to_menu_kb(lang)
    else:
        lines = [t(lang, "orders_title"), f"<i>{t(lang, 'orders_page', page=page + 1, pages=pages)}</i>", ""]
        for order in orders:
            items = ", ".join(f"{esc(item.product_name[:28])} × {item.quantity}" for item in order.items[:2])
            if len(order.items) > 2:
                items += " " + t(lang, "more_items", count=len(order.items) - 2)
            lines.append(f"<code>{esc(order.order_id)}</code> · {status_label(order.status.value, lang)}")
            lines.append(f"<i>{items}</i>\n")
        text = "\n".join(lines).rstrip()
        markup = my_orders_kb(orders, page, total, lang, ORDERS_PAGE_SIZE)

    if from_callback:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_my_orders(update, context, page=0, from_callback=False)


async def cb_my_orders_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    await _show_my_orders(update, context, page=0, from_callback=True)


async def cb_my_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    await _show_my_orders(update, context, int(update.callback_query.data.split(":", 1)[1]), True)


async def _show_own_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str, page: int) -> None:
    lang = _lang(update, context)
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        order = await OrderRepository(session).get_by_order_id(order_id)
        if not db_user or not order or order.user_id != db_user.id:
            order = None
    if order is None:
        await update.callback_query.edit_message_text(
            t(lang, "order_not_found"), parse_mode="HTML", reply_markup=my_orders_kb([], page, 0, lang),
        )
        return
    await update.callback_query.edit_message_text(
        order_card_text(order, lang),
        parse_mode="HTML",
        reply_markup=order_status_kb(order_id, lang, terminal=is_terminal(order), page=page),
    )
    await _remember_customer_order_message(order_id, update.callback_query.message.message_id)


async def cb_view_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    _, order_id, page_value = update.callback_query.data.split(":", 2)
    await _show_own_order(update, context, order_id, max(0, int(page_value)))


async def cb_refresh_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    parts = update.callback_query.data.split(":")
    page = max(0, int(parts[2])) if len(parts) > 2 else 0
    await _show_own_order(update, context, parts[1], page)


async def cb_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    lang = _lang(update, context)
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(update.effective_user.id)
        summary = await OrderRepository(session).customer_summary(db_user.id) if db_user else None
    if summary is None:
        await update.callback_query.edit_message_text(t(lang, "need_signin"))
        return
    last = summary["last_settled_at"]
    body = (
        f"{t(lang, 'balance_due', count=summary['due'])}\n"
        f"{t(lang, 'balance_last', date=format_datetime(last) if last else t(lang, 'never'))}"
    )
    await update.callback_query.edit_message_text(
        f"{t(lang, 'balance_title')}\n{quote(body)}\n<i>{t(lang, 'balance_hint')}</i>",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(lang),
    )


def get_order_conversation() -> ConversationHandler:
    browse = [
        CallbackQueryHandler(cb_order_start, pattern=r"^order_start$"),
        CallbackQueryHandler(cb_cart_view, pattern=r"^cart_view$"),
    ]
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_order_start, pattern=r"^order_start$"),
            CallbackQueryHandler(cb_reorder, pattern=r"^reorder:"),
        ],
        states={
            ORDER_SELECT_CATEGORY: [
                CallbackQueryHandler(cb_select_category, pattern=r"^(cat:|cat_i:\d+$)"),
                CallbackQueryHandler(cb_category_page, pattern=r"^cat_page:\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_search),
                *browse,
            ],
            ORDER_SELECT_PRODUCT: [
                CallbackQueryHandler(cb_select_product, pattern=r"^prod:\d+$"),
                CallbackQueryHandler(cb_product_page, pattern=r"^prod_page:\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_search),
                *browse,
            ],
            ORDER_SELECT_QUANTITY: [
                CallbackQueryHandler(cb_select_quantity, pattern=r"^qty:\d+$"),
                CallbackQueryHandler(cb_back_to_products, pattern=r"^back_products$"),
            ],
            ORDER_CART_ACTION: [
                CallbackQueryHandler(cb_cart_checkout, pattern=r"^cart_checkout$"),
                CallbackQueryHandler(cb_cart_clear, pattern=r"^cart_clear$"),
                CallbackQueryHandler(cb_cart_adjust, pattern=r"^cart_(qty|remove):"),
                CallbackQueryHandler(cb_cart_item, pattern=r"^cart_item:"),
                *browse,
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
