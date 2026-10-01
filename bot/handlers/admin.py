"""
Admin handler — full admin panel.
All functions are gated by is_admin() Telegram ID check.
"""
import html
import logging
import re
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

from database.database import AsyncSessionLocal
from database.repositories.user_repo import UserRepository, AccessCodeRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.order_repo import OrderRepository
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository
from database.models import OrderStatus
from bot.middlewares.auth_middleware import is_admin
from bot.keyboards.admin_kb import (
    admin_main_kb, admin_orders_kb, admin_products_kb,
    cleanup_removed_products_kb,
    admin_pin_kb, remove_products_kb, change_status_kb,
    api_stores_kb, store_actions_kb, user_limits_kb,
    create_code_options_kb,
)
from bot.keyboards.customer_kb import main_menu_kb
from bot.states.states import (
    ADMIN_SET_PIN, ADMIN_ADD_CAT, ADMIN_ADD_NAME, ADMIN_SET_SUPPLIER,
    ADMIN_EDIT_NAME_SELECT, ADMIN_EDIT_NAME_VALUE,
    ADMIN_RENAME_GROUP_SELECT, ADMIN_RENAME_GROUP_VALUE,
    ADMIN_CHANGE_STATUS_ID, ADMIN_SEARCH_ORDER,
    ADMIN_REVOKE_USER, ADMIN_RESET_USER,
    ADMIN_CREATE_CODE_LABEL, ADMIN_REVOKE_CODE,
    ADMIN_ADD_STORE_NAME, ADMIN_STORE_SET_LIMIT,
    ADMIN_SET_USER_LIMIT_ID, ADMIN_SET_USER_LIMIT_VALUE,
)
from utils.supplier_routing import resolve_supplier_chat


logger = logging.getLogger(__name__)
STATUS_ICONS = {"pending": "⏳", "processing": "⚙️", "completed": "✅", "failed": "❌", "cancelled": "🚫"}


def _admin_guard(func):
    """Decorator to reject non-admins on any admin function."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if update.callback_query:
                await update.callback_query.answer("⛔ Admins only.", show_alert=True)
            elif msg:
                await msg.reply_text("⛔ Admin only command.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


# ─── Admin Entry ─────────────────────────────────────────────────────

@_admin_guard
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  👑  <b>ADMIN PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=admin_main_kb(),
        parse_mode="HTML",
    )


@_admin_guard
async def cb_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "👑  <b>ADMIN PANEL</b>",
        reply_markup=admin_main_kb(),
        parse_mode="HTML",
    )

@_admin_guard
async def cb_toggle_power(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import os
    flag = "maintenance.flag"
    if os.path.exists(flag):
        os.remove(flag)
        await update.callback_query.answer("🟢 BOT TURNED ON!", show_alert=True)
    else:
        open(flag, "w").close()
        await update.callback_query.answer("🔴 BOT TURNED OFF (Maintenance)!", show_alert=True)
        
    await update.callback_query.message.edit_reply_markup(reply_markup=admin_main_kb())


# ─── Stats ───────────────────────────────────────────────────────────

@_admin_guard
async def cb_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        counts = await repo.count_by_status()
        user_repo = UserRepository(session)
        users = await user_repo.get_all()

    total = sum(counts.values())
    await update.callback_query.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  📊  <b>STATISTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥  Total Users: <b>{len(users)}</b>\n\n"
        f"📦  Orders:\n"
        f"  ⏳  Pending:    <b>{counts.get('pending', 0)}</b>\n"
        f"  ⚙️  Processing: <b>{counts.get('processing', 0)}</b>\n"
        f"  ✅  Completed:  <b>{counts.get('completed', 0)}</b>\n"
        f"  ❌  Failed:     <b>{counts.get('failed', 0)}</b>\n"
        f"  🚫  Cancelled:  <b>{counts.get('cancelled', 0)}</b>\n"
        f"  ─────────────────────\n"
        f"  📈  Total:      <b>{total}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=admin_main_kb(),
        parse_mode="HTML",
    )


# ─── Orders ──────────────────────────────────────────────────────────

@_admin_guard
async def cb_admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "📦  <b>Orders</b>", reply_markup=admin_orders_kb(), parse_mode="HTML"
    )


@_admin_guard
async def cb_orders_by_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    status_map = {
        "adm_orders_pending": OrderStatus.pending,
        "adm_orders_processing": OrderStatus.processing,
        "adm_orders_completed": OrderStatus.completed,
        "adm_orders_failed": OrderStatus.failed,
    }
    cb_data = update.callback_query.data
    status = status_map.get(cb_data, OrderStatus.pending)

    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        orders = await repo.get_by_status(status)

    if not orders:
        await update.callback_query.message.reply_text(
            f"No {status.value} orders.", reply_markup=admin_orders_kb()
        )
        return

    lines = [f"━━━━━━━━━━━━━━━━━━━━━━━\n  {STATUS_ICONS.get(status.value, '')}  <b>{status.value.upper()} ORDERS</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for o in orders[:20]:
        item = o.items[0] if o.items else None
        lines.append(
            f"\n🆔  <b>{o.order_id}</b>\n"
            f"  👤  {o.user.full_name or o.user.username or str(o.user.telegram_id)}\n"
            f"  🎮  {item.product_name if item else '—'}\n"
            f"  🎯  <code>{o.player_id}</code>\n"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")

    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_orders_kb()
    )


@_admin_guard
async def cb_search_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🔍  Enter the <b>Order ID</b> (e.g. NX123456):\n\n/cancel to abort",
        parse_mode="HTML",
    )
    return ADMIN_SEARCH_ORDER


async def admin_search_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    order_id = update.message.text.strip().upper()
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)

    if not order:
        await update.message.reply_text(f"❌  Order <b>{order_id}</b> not found.", parse_mode="HTML")
        return ConversationHandler.END

    item = order.items[0] if order.items else None
    history_lines = [
        f"  {h.old_status or '—'} → {h.new_status}  ({h.changed_by or '?'})"
        for h in order.history
    ]

    await update.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  📦  <b>ORDER {order.order_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Status: <b>{order.status.value.upper()}</b>\n"
        f"Customer: {order.user.full_name or order.user.username}\n"
        f"Telegram: <code>{order.user.telegram_id}</code>\n"
        f"Product: {item.product_name if item else '—'}\n"
        f"Player ID: <code>{order.player_id}</code>\n"
        f"Created: {order.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"<b>Status History:</b>\n" + "\n".join(history_lines),
        reply_markup=change_status_kb(order.order_id),
        parse_mode="HTML",
    )
    return ConversationHandler.END


@_admin_guard
async def cb_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    parts = update.callback_query.data.split(":")
    order_id, new_status_str = parts[1], parts[2]

    try:
        new_status = OrderStatus(new_status_str)
    except ValueError:
        await update.callback_query.message.reply_text("Invalid status.")
        return

    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)
        if not order:
            await update.callback_query.message.reply_text("Order not found.")
            return
        customer_id = order.user.telegram_id
        updated = await repo.update_status(
            order, new_status,
            changed_by=f"admin:{update.effective_user.full_name}",
        )
        if not updated:
            current_status = order.status.value if order.status else "changed"
            await update.callback_query.message.reply_text(
                f"⚠️  Order <b>{html.escape(order_id)}</b> was already changed to <b>{html.escape(current_status)}</b>.",
                parse_mode="HTML",
            )
            return

    await update.callback_query.message.reply_text(
        f"✅  Order <b>{order_id}</b> → <b>{new_status_str.upper()}</b>",
        parse_mode="HTML",
    )

    # Notify customer
    try:
        icon = STATUS_ICONS.get(new_status_str, "📦")
        await context.bot.send_message(
            chat_id=customer_id,
            text=(
                f"{icon}  Your order <b>{order_id}</b> status changed:\n"
                f"→ <b>{new_status_str.upper()}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ─── Users ───────────────────────────────────────────────────────────

@_admin_guard
async def cb_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        users = await repo.get_all()

    if not users:
        await update.callback_query.message.reply_text("No users yet.", reply_markup=admin_main_kb())
        return

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━\n  👥  <b>USERS</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for u in users[:30]:
        status_icon = {"authenticated": "🟢", "unauthenticated": "⚪", "locked": "🔒", "revoked": "⛔"}.get(u.auth_status.value, "❓")
        lines.append(
            f"{status_icon}  <b>{u.full_name or u.username or 'Unknown'}</b>\n"
            f"   ID: <code>{u.telegram_id}</code>  •  {u.auth_status.value}\n"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_pin_kb()
    )


# ─── Products ────────────────────────────────────────────────────────

@_admin_guard
async def cb_admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🛍  <b>Products</b>", reply_markup=admin_products_kb(), parse_mode="HTML"
    )


@_admin_guard
async def cb_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        products = await repo.get_all_active()
        deletable_count, preserved_count = await repo.get_inactive_cleanup_counts()

    if not products:
        await update.callback_query.message.reply_text(
            "No active products.\n"
            f"Removed products hidden: <b>{deletable_count + preserved_count}</b>.",
            parse_mode="HTML", reply_markup=admin_products_kb()
        )
        return

    lines = [
        "📦 <b>Active product groups and packages</b>\n──────────────",
        f"Removed products hidden: <b>{deletable_count + preserved_count}</b>\n",
    ]
    cat = None
    for p in products:
        if p.category != cat:
            cat = p.category
            sup = f"<code>{p.supplier_chat_id}</code>" if p.supplier_chat_id else "<i>default</i>"
            lines.append(f"\n📂 <b>{html.escape(cat, quote=False)}</b>  · Supplier: {sup}")
        active = "✅" if p.is_active else "❌"
        lines.append(f"{active} <code>#{p.id}</code>  {html.escape(p.name, quote=False)}")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_products_kb()
    )


@_admin_guard
async def cb_cleanup_removed_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        deletable_count, preserved_count = await ProductRepository(session).get_inactive_cleanup_counts()

    if not deletable_count and not preserved_count:
        await update.callback_query.message.reply_text(
            "✅ There are no removed products to clean.", reply_markup=admin_products_kb()
        )
        return

    if not deletable_count:
        await update.callback_query.message.reply_text(
            "🧹 Nothing can be permanently deleted.\n\n"
            f"Removed products kept for old order history: <b>{preserved_count}</b>.",
            parse_mode="HTML", reply_markup=admin_products_kb()
        )
        return

    await update.callback_query.message.reply_text(
        "🧹 <b>Clean removed products?</b>\n\n"
        f"Unused removed products to delete: <b>{deletable_count}</b>\n"
        f"Products kept for old order history: <b>{preserved_count}</b>\n\n"
        "Deleted products cannot be restored. Supplier settings will be copied to active packages in the same group before cleanup.",
        parse_mode="HTML", reply_markup=cleanup_removed_products_kb(),
    )


@_admin_guard
async def cb_cleanup_removed_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        deleted_count, preserved_count = await ProductRepository(session).cleanup_inactive_products()
    await update.callback_query.message.edit_text(
        "🧹 <b>Product cleanup finished</b>\n\n"
        f"Permanently deleted: <b>{deleted_count}</b>\n"
        f"Kept for old order history: <b>{preserved_count}</b>",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )


@_admin_guard
async def cb_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        groups = await ProductRepository(session).get_categories()
    group_hint = "\n\nExisting groups: " + ", ".join(groups) if groups else ""
    await update.callback_query.message.reply_text(
        "📂 <b>Enter the product group name</b>\n\n"
        "Customers will tap this name first, then choose one of its packages.\n"
        "For example, enter <code>PUBG UC Top Up</code>. "
        "To add more packages to a group, enter that group name again."
        f"{html.escape(group_hint, quote=False)}\n\n/cancel to stop",
        parse_mode="HTML",
    )
    return ADMIN_ADD_CAT


async def admin_add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    category = update.message.text.strip()
    if not category or len(category) > 64:
        await update.message.reply_text("Enter a group name from 1 to 64 characters:")
        return ADMIN_ADD_CAT
    context.user_data["add_cat"] = category
    await update.message.reply_text(
        "💎 <b>Enter the packages or denominations</b>\n\n"
        "Send one package per line or separate them with commas.\n\n"
        "Example for PUBG UC Top Up:\n"
        "<code>60 UC\n325 UC\n660 UC\n1800 UC\n3850 UC\n8100 UC</code>\n\n"
        "You can include several new packages in one message.\n/cancel to stop",
        parse_mode="HTML",
    )
    return ADMIN_ADD_NAME


async def admin_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    cat = context.user_data.get("add_cat", "")
    names = [
        part.strip()
        for line in update.message.text.splitlines()
        for part in re.split(r"[,;]", line)
        if part.strip()
    ]
    if not names:
        await update.message.reply_text("Send at least one package name, or /cancel to stop.")
        return ADMIN_ADD_NAME
    if len(names) > 50:
        await update.message.reply_text("Add up to 50 packages in one message. Please send a shorter list.")
        return ADMIN_ADD_NAME
    if any(len(name) > 128 for name in names):
        await update.message.reply_text("Each package name must be 128 characters or fewer. Please try again.")
        return ADMIN_ADD_NAME

    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        products, skipped = await repo.add_many(cat, names)

    if not products:
        await update.message.reply_text(
            "ℹ️ Those package names are already in this group. Send different names or /cancel."
        )
        return ADMIN_ADD_NAME

    package_lines = [
        f"• {html.escape(product.name, quote=False)}"
        for product in products[:20]
    ]
    if len(products) > 20:
        package_lines.append(f"• …and {len(products) - 20} more")
    supplier_note = (
        "\n📡 The group’s saved supplier destination was applied to these packages."
        if products[0].supplier_chat_id else
        "\n📡 No group supplier is saved; the global fallback may be used."
    )
    skipped_note = f"\nSkipped existing names: <b>{len(skipped)}</b>." if skipped else ""

    await update.message.reply_text(
        f"✅ <b>Packages added to {html.escape(products[0].category, quote=False)}</b>\n"
        f"──────────────\n"
        f"{chr(10).join(package_lines)}\n"
        f"\nAdded: <b>{len(products)}</b>{skipped_note}{supplier_note}",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    context.user_data.pop("add_cat", None)
    return ConversationHandler.END


@_admin_guard
async def cb_rename_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        groups = await ProductRepository(session).get_categories()
    if not groups:
        await update.callback_query.message.reply_text("No product groups to rename.")
        return ConversationHandler.END
    names = "\n".join(f"• {html.escape(group, quote=False)}" for group in groups)
    await update.callback_query.message.reply_text(
        "📝 <b>Rename a product group</b>\n\n"
        f"Current groups:\n{names}\n\n"
        "Send the current group name exactly as shown:",
        parse_mode="HTML",
    )
    return ADMIN_RENAME_GROUP_SELECT


async def admin_rename_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    requested = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        groups = await ProductRepository(session).get_categories()
    matched = next((group for group in groups if group.casefold() == requested.casefold()), None)
    if not matched:
        await update.message.reply_text("I could not find that group. Copy its exact name from the list and try again.")
        return ADMIN_RENAME_GROUP_SELECT
    context.user_data["rename_group_old"] = matched
    await update.message.reply_text(
        f"Current group: <b>{html.escape(matched, quote=False)}</b>\n\n"
        "Send the new name. For example: <code>PUBG UC Top Up</code>",
        parse_mode="HTML",
    )
    return ADMIN_RENAME_GROUP_VALUE


async def admin_rename_group_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    new_name = update.message.text.strip()
    if not new_name or len(new_name) > 64:
        await update.message.reply_text("Enter a new group name from 1 to 64 characters:")
        return ADMIN_RENAME_GROUP_VALUE
    old_name = context.user_data.pop("rename_group_old", "")
    async with AsyncSessionLocal() as session:
        count, result = await ProductRepository(session).rename_category(old_name, new_name)
    if result == "already_exists":
        await update.message.reply_text(
            "That group name already exists. Choose another name, or add packages to that existing group instead."
        )
        context.user_data["rename_group_old"] = old_name
        return ADMIN_RENAME_GROUP_VALUE
    if not count:
        await update.message.reply_text("I could not rename that group. Open Products and try again.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"✅ Product group renamed to <b>{html.escape(result, quote=False)}</b>.\n"
        f"Packages kept: <b>{count}</b>. Supplier settings were kept.",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    return ConversationHandler.END


# ─── Set Supplier Group per Category ─────────────────────────────────

@_admin_guard
async def cb_set_supplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    if not categories:
        await update.callback_query.message.reply_text("❌  No categories found. Add products first.")
        return ConversationHandler.END

    lines = ["📡  <b>Set Supplier Group per Product</b>\n\n"
             "<b>Routing:</b> A saved supplier group receives orders for its product group. Other groups use the global <code>SUPPLIER_CHAT_ID</code> fallback.\n\n"
             "Reply with:\n<code>PRODUCT GROUP | CHAT_ID</code>\n\n"
             "Examples:\n"
             "  <code>PUBG UC Top Up | -1001234567890</code>\n"
             "  <code>Free Fire | -1009876543210</code>\n\n"
             "You can also send the product group name by itself, then forward any message from its Telegram supplier group. I will read the real group ID from the forwarded message.\n\n"
             "To <b>remove</b> a custom supplier (use global default):\n"
             "  <code>PUBG UC Top Up | 0</code>\n\n"
             "Current product groups:\n"]
    for cat in categories:
        async with AsyncSessionLocal() as session:
            sup_id = await ProductRepository(session).get_supplier_for_category(cat)
        sup_text = f"<code>{sup_id}</code>" if sup_id else "<i>default</i>"
        lines.append(f"  📂  <b>{html.escape(cat, quote=False)}</b> → {sup_text}")
    lines.append("\n/cancel to abort")
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML")
    return ADMIN_SET_SUPPLIER


async def admin_set_supplier_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    text = update.message.text.strip()
    if "|" not in text:
        async with AsyncSessionLocal() as session:
            categories = await ProductRepository(session).get_categories()
        matched_category = next((c for c in categories if c.casefold() == text.casefold()), None)
        if matched_category:
            context.user_data["supplier_forward_category"] = matched_category
            await update.message.reply_text(
                f"Now forward any message from the supplier group for <b>{html.escape(matched_category)}</b>.\n\n"
                "I will use Telegram’s original group ID and send a test message there.\n"
                "If you meant to enter an ID, send <code>PRODUCT GROUP | -1001234567890</code> instead.",
                parse_mode="HTML",
            )
            return ADMIN_SET_SUPPLIER
        await update.message.reply_text(
            "❌  Send <code>PRODUCT GROUP | -1001234567890</code>, or send the exact product group name by itself and then forward a message from its supplier group.\n\nTry again or /cancel",
            parse_mode="HTML"
        )
        return ADMIN_SET_SUPPLIER

    category_input, raw_chat_id = (part.strip() for part in text.split("|", 1))
    if raw_chat_id == "0" or raw_chat_id.casefold() == "default":
        async with AsyncSessionLocal() as session:
            count, matched_cat = await ProductRepository(session).set_category_supplier(category_input, None)
        if not count:
            await update.message.reply_text(
                f"❌ Product group <b>{html.escape(category_input)}</b> not found. Copy its name from the product list.",
                parse_mode="HTML",
            )
            return ADMIN_SET_SUPPLIER
        await update.message.reply_text(
            f"✅ <b>{html.escape(matched_cat)}</b> now uses the global default supplier.",
            parse_mode="HTML", reply_markup=admin_products_kb()
        )
        return ConversationHandler.END

    # Keep the supplied ID exactly as typed. Rewriting it can point to a different chat.
    try:
        supplier_chat_id = int(raw_chat_id.replace(" ", ""))
    except ValueError:
        await update.message.reply_text(
            "❌ Enter the full negative Telegram chat ID (for example <code>-1001234567890</code>), or use the forward-message method.",
            parse_mode="HTML",
        )
        return ADMIN_SET_SUPPLIER
    if supplier_chat_id >= 0:
        await update.message.reply_text(
            "❌ The chat ID must be negative and copied exactly from Telegram (usually it starts with <code>-100</code>). Or forward a message from the group to avoid typing the ID.",
            parse_mode="HTML",
        )
        return ADMIN_SET_SUPPLIER

    return await _verify_and_save_supplier(update, context, category_input, supplier_chat_id)


async def admin_set_supplier_from_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Use the original group ID from an admin-forwarded group message."""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    category = context.user_data.get("supplier_forward_category")
    if not category:
        await update.message.reply_text("First send the product group name in Supplier groups, then forward a message from its supplier group.")
        return ADMIN_SET_SUPPLIER

    message = update.message
    origin = getattr(message, "forward_origin", None)
    source_chat = getattr(origin, "chat", None)
    if source_chat is None:
        source_chat = getattr(message, "forward_from_chat", None)
    source_type = getattr(source_chat, "type", None)
    source_id = getattr(source_chat, "id", None)
    if source_id is None or source_type not in ("group", "supergroup"):
        await update.message.reply_text(
            "❌ I could not read a group ID from that forward. Forward a message directly from the supplier group (not from a person or channel).",
        )
        return ADMIN_SET_SUPPLIER

    context.user_data.pop("supplier_forward_category", None)
    return await _verify_and_save_supplier(update, context, category, int(source_id))


async def _verify_and_save_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE, category_input: str, supplier_chat_id: int):
    """Confirm Telegram access before changing the saved supplier destination."""
    async with AsyncSessionLocal() as session:
        categories = await ProductRepository(session).get_categories()
    matched_cat = next((cat for cat in categories if cat.casefold() == category_input.casefold()), None)
    if not matched_cat:
        await update.message.reply_text(
            f"❌ Product group <b>{html.escape(category_input)}</b> not found. Copy its name from the product list.",
            parse_mode="HTML",
        )
        return ADMIN_SET_SUPPLIER

    target_chat, route_source = resolve_supplier_chat(supplier_chat_id)
    bot_info = await context.bot.get_me()
    try:
        chat = await context.bot.get_chat(target_chat)
        await context.bot.send_message(
            chat_id=target_chat,
            text=(
                f"🤖  <b>SUPPLIER GROUP CONNECTED</b>\n\n"
                f"✅ This is the active destination for orders in:\n"
                f"📂 Product group: <b>{html.escape(matched_cat)}</b>\n"
                f"🧭 Routing: <b>{route_source}</b>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("Supplier test failed for chat %s using bot @%s (%s): %s", target_chat, bot_info.username, type(e).__name__, e)
        detail = html.escape(str(e), quote=False)
        if "chat not found" in str(e).casefold():
            suggestion = (
                "Telegram cannot find that group for this bot. This usually means the ID is not the ID of the group this bot can see, "
                "or the running bot token belongs to a different bot. Use the forward-message method so I can read the exact ID, "
                "and confirm this same bot (@" + html.escape(bot_info.username or "unknown", quote=False) + ") is in the group."
            )
        else:
            suggestion = "Telegram found the destination but rejected the test. Check this bot’s permission to send messages in that group."
        await update.message.reply_text(
            f"❌ <b>Group not connected</b>\n\n"
            f"Product group: <b>{html.escape(matched_cat)}</b>\n"
            f"ID tried: <code>{target_chat}</code>\n"
            f"Bot: <b>@{html.escape(bot_info.username or 'unknown')}</b>\n"
            f"Telegram error: <code>{detail}</code>\n\n{suggestion}\n\n"
            "I did not replace the previously saved supplier destination.",
            parse_mode="HTML", reply_markup=admin_products_kb(),
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        count, matched_cat = await ProductRepository(session).set_category_supplier(matched_cat, supplier_chat_id)
    await update.message.reply_text(
        f"✅ <b>Supplier group connected</b>\n\n"
        f"Product group: <b>{html.escape(matched_cat)}</b>\n"
        f"Supplier group: <b>{html.escape(chat.title or str(target_chat))}</b>\n"
        f"Chat ID: <code>{target_chat}</code>\n"
        f"Bot: <b>@{html.escape(bot_info.username or 'unknown')}</b>\n"
        f"Packages updated: <b>{count}</b>\n\n"
        "The bot sent a test message to this group.",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    return ConversationHandler.END


# ─── Edit UC Quantity/Name ──────────────────────────────────────────

@_admin_guard
async def cb_edit_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        products = await repo.get_all_active()
    lines = ["Enter <b>product ID</b> to edit name/UC:\n"]
    for p in products:
        lines.append(f"  <b>#{p.id}</b>  {p.name}")
    lines.append("\n/cancel to abort")
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML")
    return ADMIN_EDIT_NAME_SELECT

async def admin_edit_name_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try:
        pid = int(update.message.text.strip().replace("#", ""))
    except ValueError:
        await update.message.reply_text("❌  Enter a valid product ID:")
        return ADMIN_EDIT_NAME_SELECT
    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(pid)
    if not product:
        await update.message.reply_text("Product not found.")
        return ADMIN_EDIT_NAME_SELECT
    context.user_data["edit_pid"] = pid
    await update.message.reply_text(
        f"Editing <b>{product.name}</b>\n\nEnter <b>new name/quantity</b> (e.g., '1800 UC'):",
        parse_mode="HTML",
    )
    return ADMIN_EDIT_NAME_VALUE

async def admin_edit_name_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    new_name = update.message.text.strip()
    if len(new_name) < 2:
        await update.message.reply_text("❌  Name is too short. Try again:")
        return ADMIN_EDIT_NAME_VALUE
        
    pid = context.user_data.pop("edit_pid")
    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(pid)
        product.name = new_name
        await session.commit()
        
    await update.message.reply_text(f"✅  Product updated to <b>{new_name}</b>", parse_mode="HTML", reply_markup=admin_products_kb())
    context.user_data.clear()
    return ConversationHandler.END


@_admin_guard
async def cb_remove_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_all_active()
    if not products:
        await update.callback_query.message.reply_text("No products to remove.")
        return
    await update.callback_query.message.reply_text(
        "🗑  Tap to remove:", reply_markup=remove_products_kb(products)
    )


@_admin_guard
async def cb_remove_product_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    pid = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        await ProductRepository(session).deactivate(pid)
    await update.callback_query.answer("Removed!", show_alert=True)
    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_all_active()
    if products:
        await update.callback_query.message.edit_reply_markup(reply_markup=remove_products_kb(products))
    else:
        await update.callback_query.message.edit_text("All products removed.")


# ─── API Settings ──────────────────────────────────────────────────────

@_admin_guard
async def cb_admin_api_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config.settings import API_KEY
    await update.callback_query.answer()
    key_status = (
        "Configured (hidden in this panel)"
        if API_KEY else "Not configured; use per-store API keys"
    )
    
    await update.callback_query.message.reply_text(
        "🔑 <b>API settings</b>\n──────────────\n"
        "Use the REST API to accept orders from connected platforms.\n\n"
        "🌐 <b>Service</b>  Flask API\n"
        "📍 <b>Host</b>  PythonAnywhere Web tab\n"
        "🔐 <b>API key</b>\n"
        f"{key_status}\n\n"
        "<i>Send the key in the X-API-Key header.</i>\n\n"
        "<b>Endpoints</b>\n"
        "<code>POST /orders/</code>  Create an order\n"
        "<code>GET /orders/{id}</code>  Check order status",
        parse_mode="HTML",
        reply_markup=admin_main_kb()
    )


# ─── Access Code Management ──────────────────────────────────────────

@_admin_guard
async def cb_admin_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🔐  <b>Access Code Management</b>\n\n"
        "Create unique codes for trusted users.\n"
        "Each code registers one user.",
        reply_markup=admin_pin_kb(),
        parse_mode="HTML",
    )


@_admin_guard
async def cb_create_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "➕  <b>Create Access Code</b>\n\n"
        "Send a <b>label</b> for this code (e.g. <i>John's Code</i>),\n"
        "or tap <b>⚡ Instant Code</b> to generate one immediately without a label.",
        parse_mode="HTML",
        reply_markup=create_code_options_kb(),
    )
    return ADMIN_CREATE_CODE_LABEL


@_admin_guard
async def cb_create_code_instant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    try:
        async with AsyncSessionLocal() as session:
            repo = AccessCodeRepository(session)
            code = await repo.create(label=None)

        await update.callback_query.message.reply_text(
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  ✅  <b>Access Code Created</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑  Code: <code>{code.code}</code>\n"
            f"🏷  Label: —\n\n"
            f"<b>Give this code to the user.</b>\n"
            f"They enter it on /start to register.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML",
            reply_markup=admin_pin_kb(),
        )
    except Exception as e:
        logger.error(f"Error creating instant access code: {e}", exc_info=True)
        await update.callback_query.message.reply_text(
            f"❌  Error generating code: {html.escape(str(e))}\n\nPlease try again.",
            reply_markup=admin_pin_kb(),
        )
    return ConversationHandler.END


async def admin_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    raw_input = update.message.text.strip()
    label = None if raw_input.lower() in ("skip", "-", "none") else raw_input[:64]

    try:
        async with AsyncSessionLocal() as session:
            repo = AccessCodeRepository(session)
            code = await repo.create(label=label)

        safe_label = html.escape(code.label) if code.label else "—"
        await update.message.reply_text(
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  ✅  <b>Access Code Created</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑  Code: <code>{code.code}</code>\n"
            f"🏷  Label: {safe_label}\n\n"
            f"<b>Give this code to the user.</b>\n"
            f"They enter it on /start to register.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML",
            reply_markup=admin_pin_kb(),
        )
    except Exception as e:
        logger.error(f"Error in admin_create_code: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌  Error creating code: {html.escape(str(e))}\n\nPlease try again.",
            reply_markup=admin_pin_kb(),
        )
    return ConversationHandler.END


@_admin_guard
async def cb_list_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = AccessCodeRepository(session)
        codes = await repo.get_all()

    if not codes:
        await update.callback_query.message.reply_text(
            "No access codes yet.\nTap ➕ Create to make one.",
            reply_markup=admin_pin_kb(),
        )
        return

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━\n  📋  <b>ACCESS CODES</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for c in codes:
        status = "✅ Active" if c.is_active and c.used_by is None else ("🟢 Used" if c.used_by else "❌ Revoked")
        lines.append(
            f"\n🔑  <code>{c.code}</code>  —  {status}\n"
            f"   🏷  {c.label or '—'}\n"
            f"   👤  Used by: {c.used_by or 'Not yet'}\n"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_pin_kb()
    )


@_admin_guard
async def cb_revoke_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        codes = await AccessCodeRepository(session).get_active_unused()

    if not codes:
        await update.callback_query.message.reply_text(
            "No active unused codes to revoke.", reply_markup=admin_pin_kb()
        )
        return

    lines = ["Enter the <b>code</b> to revoke:\n"]
    for c in codes:
        lines.append(f"  <code>{c.code}</code>  —  {c.label or '—'}")
    lines.append("\n/cancel to abort")
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML")
    return ADMIN_REVOKE_CODE


async def admin_revoke_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    code = update.message.text.strip().upper()
    async with AsyncSessionLocal() as session:
        success = await AccessCodeRepository(session).revoke_code(code)
    msg = f"✅  Code <code>{code}</code> revoked." if success else f"❌  Code <code>{code}</code> not found."
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=admin_pin_kb())
    return ConversationHandler.END


@_admin_guard
async def cb_revoke_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "⛔  Enter <b>Telegram User ID</b> to revoke:\n\n/cancel to abort", parse_mode="HTML"
    )
    return ADMIN_REVOKE_USER


async def admin_revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try:
        tid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return ConversationHandler.END
    async with AsyncSessionLocal() as session:
        success = await UserRepository(session).revoke(tid)
    msg = f"✅  User <code>{tid}</code> revoked." if success else f"User <code>{tid}</code> not found."
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=admin_pin_kb())
    return ConversationHandler.END


@_admin_guard
async def cb_reset_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🔄  Enter <b>Telegram User ID</b> to reset auth:\n\n/cancel to abort", parse_mode="HTML"
    )
    return ADMIN_RESET_USER


async def admin_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try:
        tid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return ConversationHandler.END
    async with AsyncSessionLocal() as session:
        success = await UserRepository(session).reset_auth(tid)
    msg = f"✅  User <code>{tid}</code> auth reset." if success else f"User <code>{tid}</code> not found."
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=admin_pin_kb())
    return ConversationHandler.END


# ─── Cancel ──────────────────────────────────────────────────────────

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✖  Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


async def cb_admin_cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("✖  Operation cancelled.", reply_markup=admin_main_kb())
    context.user_data.clear()
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════
#  API STORE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

@_admin_guard
async def cb_api_stores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🏪  <b>API STORES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Manage external stores that can\n"
        "place orders via your API.",
        reply_markup=api_stores_kb(),
        parse_mode="HTML",
    )

@_admin_guard
async def cb_add_store_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🏪  Enter a <b>name</b> for the new store:\n\n"
        "<i>(e.g. MyWebsite, PartnerShop)</i>",
        parse_mode="HTML",
    )
    return ADMIN_ADD_STORE_NAME

@_admin_guard
async def admin_add_store_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name or len(name) > 64:
        await update.message.reply_text("❌  Name must be 1-64 characters. Try again:")
        return ADMIN_ADD_STORE_NAME

    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        existing = await repo.get_by_name(name)
        if existing:
            await update.message.reply_text("❌  A store with that name already exists. Try a different name:")
            return ADMIN_ADD_STORE_NAME
        store = await repo.create(name=name, daily_limit=0)

    await update.message.reply_text(
        f"✅  Store <b>{html.escape(store.name)}</b> created!\n\n"
        f"🔑  API Key:\n<code>{store.api_key}</code>\n\n"
        f"📊  Daily Limit: <b>Unlimited</b>\n\n"
        f"⚠️  Save this key now! It won't be shown again in full.",
        reply_markup=api_stores_kb(),
        parse_mode="HTML",
    )
    return ConversationHandler.END

@_admin_guard
async def cb_list_stores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        stores = await repo.get_all()

    if not stores:
        await update.callback_query.message.reply_text(
            "📋  No API stores found.\n\nUse <b>➕ Add Store</b> to create one.",
            reply_markup=api_stores_kb(),
            parse_mode="HTML",
        )
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for s in stores:
        status = "🟢" if s.is_active else "🔴"
        limit_text = f"{s.orders_today}/{s.daily_limit}" if s.daily_limit > 0 else f"{s.orders_today}/∞"
        rows.append([InlineKeyboardButton(
            f"{status}  {s.name}  [{limit_text}]",
            callback_data=f"store_view:{s.id}"
        )])
    rows.append([InlineKeyboardButton("◀  Back", callback_data="adm_api_stores")])

    await update.callback_query.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📋  <b>ALL API STORES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Tap a store to manage it:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )

@_admin_guard
async def cb_store_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        from sqlalchemy import select
        from database.models import ApiStore
        result = await session.execute(select(ApiStore).where(ApiStore.id == store_id))
        store = result.scalar_one_or_none()

    if not store:
        await update.callback_query.message.reply_text("❌  Store not found.")
        return

    status = "🟢 Active" if store.is_active else "🔴 Disabled"
    limit_text = str(store.daily_limit) if store.daily_limit > 0 else "Unlimited"
    key_preview = store.api_key[:12] + "..." + store.api_key[-6:]

    await update.callback_query.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  🏪  <b>{html.escape(store.name)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  📌  Status:      <b>{status}</b>\n"
        f"  🔑  Key:         <code>{key_preview}</code>\n"
        f"  📊  Daily Limit: <b>{limit_text}</b>\n"
        f"  📦  Today:       <b>{store.orders_today}</b> orders\n",
        reply_markup=store_actions_kb(store.id, store.is_active),
        parse_mode="HTML",
    )

@_admin_guard
async def cb_store_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_id = int(update.callback_query.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        from sqlalchemy import select
        from database.models import ApiStore
        result = await session.execute(select(ApiStore).where(ApiStore.id == store_id))
        store = result.scalar_one_or_none()
        if store:
            await repo.toggle_active(store)
            new_status = "🟢 ENABLED" if store.is_active else "🔴 DISABLED"
            await update.callback_query.answer(f"Store {new_status}!", show_alert=True)
            # Refresh the view
            limit_text = str(store.daily_limit) if store.daily_limit > 0 else "Unlimited"
            key_preview = store.api_key[:12] + "..." + store.api_key[-6:]
            status = "🟢 Active" if store.is_active else "🔴 Disabled"
            await update.callback_query.message.edit_text(
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🏪  <b>{html.escape(store.name)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"  📌  Status:      <b>{status}</b>\n"
                f"  🔑  Key:         <code>{key_preview}</code>\n"
                f"  📊  Daily Limit: <b>{limit_text}</b>\n"
                f"  📦  Today:       <b>{store.orders_today}</b> orders\n",
                reply_markup=store_actions_kb(store.id, store.is_active),
                parse_mode="HTML",
            )
        else:
            await update.callback_query.answer("Store not found!", show_alert=True)

@_admin_guard
async def cb_store_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    context.user_data["edit_store_id"] = store_id
    await update.callback_query.message.reply_text(
        "📊  Enter the <b>daily order limit</b> for this store:\n\n"
        "<i>Enter 0 for unlimited.</i>",
        parse_mode="HTML",
    )
    return ADMIN_STORE_SET_LIMIT

@_admin_guard
async def admin_store_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
        if limit < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌  Enter a valid number (0 or higher):")
        return ADMIN_STORE_SET_LIMIT

    store_id = context.user_data.get("edit_store_id")
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        from sqlalchemy import select
        from database.models import ApiStore
        result = await session.execute(select(ApiStore).where(ApiStore.id == store_id))
        store = result.scalar_one_or_none()
        if store:
            await repo.set_daily_limit(store, limit)
            limit_text = str(limit) if limit > 0 else "Unlimited"
            await update.message.reply_text(
                f"✅  Daily limit for <b>{store.name}</b> set to: <b>{limit_text}</b>",
                reply_markup=api_stores_kb(),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌  Store not found.", reply_markup=api_stores_kb())

    context.user_data.pop("edit_store_id", None)
    return ConversationHandler.END

@_admin_guard
async def cb_store_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_id = int(update.callback_query.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        from sqlalchemy import select
        from database.models import ApiStore
        result = await session.execute(select(ApiStore).where(ApiStore.id == store_id))
        store = result.scalar_one_or_none()
        if store:
            name = store.name
            await repo.delete(store)
            await update.callback_query.answer(f"🗑 Store '{name}' deleted!", show_alert=True)
        else:
            await update.callback_query.answer("Store not found!", show_alert=True)

    await update.callback_query.message.edit_text(
        "🗑  Store deleted.\n\nReturning to API Stores...",
        reply_markup=api_stores_kb(),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════════
#  USER ORDER LIMITS
# ═══════════════════════════════════════════════════════════════════════

@_admin_guard
async def cb_user_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🚦  <b>USER ORDER LIMITS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Restrict how many orders a\n"
        "specific user can place per day.\n\n"
        "• Set limit to <b>0</b> = block all orders\n"
        "• Remove limit = unlimited orders",
        reply_markup=user_limits_kb(),
        parse_mode="HTML",
    )

@_admin_guard
async def cb_set_user_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🚦  Enter the <b>Telegram ID</b> of the user you want to limit:\n\n"
        "<i>(You can find their ID in the Users list)</i>",
        parse_mode="HTML",
    )
    return ADMIN_SET_USER_LIMIT_ID

@_admin_guard
async def admin_set_user_limit_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        telegram_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌  Enter a valid Telegram ID (numbers only):")
        return ADMIN_SET_USER_LIMIT_ID

    context.user_data["limit_telegram_id"] = telegram_id
    await update.message.reply_text(
        f"📊  User ID: <b>{telegram_id}</b>\n\n"
        f"Enter the <b>daily order limit</b>:\n\n"
        f"• <b>0</b> = Block all orders\n"
        f"• <b>5</b> = Max 5 orders/day\n"
        f"• <b>-1</b> = Remove limit (unlimited)",
        parse_mode="HTML",
    )
    return ADMIN_SET_USER_LIMIT_VALUE

@_admin_guard
async def admin_set_user_limit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌  Enter a valid number:")
        return ADMIN_SET_USER_LIMIT_VALUE

    telegram_id = context.user_data.get("limit_telegram_id")
    async with AsyncSessionLocal() as session:
        repo = UserOrderLimitRepository(session)
        if limit == -1:
            await repo.remove_limit(telegram_id)
            await update.message.reply_text(
                f"✅  Limit removed for user <b>{telegram_id}</b>.\n"
                f"They can now place unlimited orders.",
                reply_markup=user_limits_kb(),
                parse_mode="HTML",
            )
        else:
            await repo.set_limit(telegram_id, limit)
            limit_text = "BLOCKED" if limit == 0 else f"{limit} orders/day"
            await update.message.reply_text(
                f"✅  User <b>{telegram_id}</b> limit set to: <b>{limit_text}</b>",
                reply_markup=user_limits_kb(),
                parse_mode="HTML",
            )

    context.user_data.pop("limit_telegram_id", None)
    return ConversationHandler.END

@_admin_guard
async def cb_list_user_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = UserOrderLimitRepository(session)
        limits = await repo.get_all_limited()

    if not limits:
        await update.callback_query.message.reply_text(
            "📋  No user limits configured.\n\n"
            "All users have unlimited orders.\n"
            "Use <b>🚦 Set User Limit</b> to add one.",
            reply_markup=user_limits_kb(),
            parse_mode="HTML",
        )
        return

    lines = []
    for l in limits:
        limit_text = "🚫 BLOCKED" if l.daily_limit == 0 else f"{l.orders_today}/{l.daily_limit}"
        lines.append(f"  👤  <code>{l.telegram_id}</code>  →  <b>{limit_text}</b>")

    await update.callback_query.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📋  <b>USER LIMITS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines) + "\n\n"
        "<i>Set limit to -1 to remove it.</i>",
        reply_markup=user_limits_kb(),
        parse_mode="HTML",
    )


# ─── Build conversations ──────────────────────────────────────────────

def get_admin_conversations() -> list[ConversationHandler]:
    common_fallbacks = [
        CommandHandler(["cancel", "admin", "start"], admin_cancel),
        CallbackQueryHandler(cb_admin_cancel_conv, pattern=r"^adm_cancel_conv$"),
    ]

    # Keep all admin workflows in one conversation. Separate ConversationHandlers
    # can remain active together, and the first one registered then steals replies
    # from later workflows (for example, edit-product consumes Add Store input).
    admin_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_search_order_start, pattern=r"^adm_search_order$"),
            CallbackQueryHandler(cb_add_product_start, pattern=r"^adm_add_product$"),
            CallbackQueryHandler(cb_set_supplier_start, pattern=r"^adm_set_supplier$"),
            CallbackQueryHandler(cb_edit_name_start, pattern=r"^adm_edit_name$"),
            CallbackQueryHandler(cb_rename_group_start, pattern=r"^adm_rename_group$"),
            CallbackQueryHandler(cb_create_code_start, pattern=r"^adm_create_code$"),
            CallbackQueryHandler(cb_revoke_code_start, pattern=r"^adm_revoke_code$"),
            CallbackQueryHandler(cb_revoke_user_start, pattern=r"^adm_revoke_user$"),
            CallbackQueryHandler(cb_reset_user_start, pattern=r"^adm_reset_user$"),
            CallbackQueryHandler(cb_add_store_start, pattern=r"^adm_add_store$"),
            CallbackQueryHandler(cb_store_limit_start, pattern=r"^store_limit:"),
            CallbackQueryHandler(cb_set_user_limit_start, pattern=r"^adm_set_user_limit$"),
        ],
        states={
            ADMIN_SEARCH_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_order)],
            ADMIN_ADD_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_cat)],
            ADMIN_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_name)],
            ADMIN_SET_SUPPLIER: [
                MessageHandler(filters.FORWARDED, admin_set_supplier_from_forward),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_supplier_value),
            ],
            ADMIN_EDIT_NAME_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_name_select)],
            ADMIN_EDIT_NAME_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_name_value)],
            ADMIN_RENAME_GROUP_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rename_group_select)],
            ADMIN_RENAME_GROUP_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rename_group_value)],
            ADMIN_CREATE_CODE_LABEL: [
                CallbackQueryHandler(cb_create_code_instant, pattern=r"^adm_code_instant$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_code),
            ],
            ADMIN_REVOKE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_revoke_code)],
            ADMIN_REVOKE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_revoke_user)],
            ADMIN_RESET_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reset_user)],
            ADMIN_ADD_STORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_store_name)],
            ADMIN_STORE_SET_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_store_set_limit)],
            ADMIN_SET_USER_LIMIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_user_limit_id)],
            ADMIN_SET_USER_LIMIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_user_limit_value)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
        per_message=False,
    )

    toggle_handler = CallbackQueryHandler(cb_toggle_power, pattern=r"^adm_toggle_power$")
    return [admin_conversation, toggle_handler]
