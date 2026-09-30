"""
Admin handler — full admin panel.
All functions are gated by is_admin() Telegram ID check.
"""
import logging
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
    admin_pin_kb, remove_products_kb, change_status_kb,
    api_stores_kb, store_actions_kb, user_limits_kb,
)
from bot.keyboards.customer_kb import main_menu_kb
from bot.states.states import (
    ADMIN_SET_PIN, ADMIN_ADD_CAT, ADMIN_ADD_NAME, ADMIN_SET_SUPPLIER,
    ADMIN_EDIT_NAME_SELECT, ADMIN_EDIT_NAME_VALUE,
    ADMIN_CHANGE_STATUS_ID, ADMIN_SEARCH_ORDER,
    ADMIN_REVOKE_USER, ADMIN_RESET_USER,
    ADMIN_CREATE_CODE_LABEL, ADMIN_REVOKE_CODE,
    ADMIN_ADD_STORE_NAME, ADMIN_STORE_SET_LIMIT,
    ADMIN_SET_USER_LIMIT_ID, ADMIN_SET_USER_LIMIT_VALUE,
)


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
        await repo.update_status(
            order, new_status,
            changed_by=f"admin:{update.effective_user.full_name}",
        )

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
        products = await repo.get_all()

    if not products:
        await update.callback_query.message.reply_text("No products.", reply_markup=admin_products_kb())
        return

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━\n  📦  <b>ALL PRODUCTS</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n"]
    cat = None
    for p in products:
        if p.category != cat:
            cat = p.category
            sup = f"<code>{p.supplier_chat_id}</code>" if p.supplier_chat_id else "<i>default</i>"
            lines.append(f"\n📂  <b>{cat}</b>  📡 {sup}")
        active = "✅" if p.is_active else "❌"
        lines.append(f"  {active}  #{p.id}  {p.name}")
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_products_kb()
    )


@_admin_guard
async def cb_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "📂  Enter <b>category</b> name:\n<i>e.g. PUBG UC, Free Fire, Mobile Legends</i>\n\n/cancel to abort",
        parse_mode="HTML",
    )
    return ADMIN_ADD_CAT


async def admin_add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    context.user_data["add_cat"] = update.message.text.strip()
    await update.message.reply_text("📝  Enter <b>product name</b>:", parse_mode="HTML")
    return ADMIN_ADD_NAME


async def admin_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    cat = context.user_data.get("add_cat", "")
    name = update.message.text.strip()

    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        product = await repo.add(cat, name)

    await update.message.reply_text(
        f"✅  <b>Product Added</b>\n\n"
        f"  📂  Category: <b>{cat}</b>\n"
        f"  📦  Name:     <b>{product.name}</b>\n\n"
        f"<i>Tip: Set the supplier group for this category in Products → Set Supplier Group</i>",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    context.user_data.clear()
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

    lines = ["📡  <b>Set Supplier Group per Category</b>\n\n"
             "Reply with:\n<code>CATEGORY | CHAT_ID</code>\n\n"
             "Examples:\n"
             "  <code>PUBG UC | -1001234567890</code>\n"
             "  <code>Free Fire | -1009876543210</code>\n\n"
             "To <b>remove</b> a custom supplier (use global default):\n"
             "  <code>PUBG UC | 0</code>\n\n"
             "Current categories:\n"]
    for cat in categories:
        async with AsyncSessionLocal() as session:
            sup_id = await ProductRepository(session).get_supplier_for_category(cat)
        sup_text = f"<code>{sup_id}</code>" if sup_id else "<i>default</i>"
        lines.append(f"  📂  <b>{cat}</b> → {sup_text}")
    lines.append("\n/cancel to abort")
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML")
    return ADMIN_SET_SUPPLIER


async def admin_set_supplier_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    text = update.message.text.strip()
    if "|" not in text:
        await update.message.reply_text(
            "❌  Wrong format. Use:\n<code>CATEGORY | CHAT_ID</code>\n\nTry again or /cancel",
            parse_mode="HTML"
        )
        return ADMIN_SET_SUPPLIER

    parts = text.split("|", 1)
    category = parts[0].strip()
    try:
        chat_id = int(parts[1].strip())
    except ValueError:
        await update.message.reply_text("❌  Chat ID must be a number. Try again or /cancel")
        return ADMIN_SET_SUPPLIER

    supplier_chat_id = None if chat_id == 0 else chat_id

    async with AsyncSessionLocal() as session:
        count = await ProductRepository(session).set_category_supplier(category, supplier_chat_id)

    if count == 0:
        await update.message.reply_text(
            f"❌  Category <b>{category}</b> not found. Check the spelling.",
            parse_mode="HTML"
        )
        return ADMIN_SET_SUPPLIER

    if supplier_chat_id:
        await update.message.reply_text(
            f"✅  Category <b>{category}</b> → supplier set to <code>{supplier_chat_id}</code>\n"
            f"   ({count} product(s) updated)",
            parse_mode="HTML", reply_markup=admin_products_kb()
        )
    else:
        await update.message.reply_text(
            f"✅  Category <b>{category}</b> → supplier reset to <b>default</b>",
            parse_mode="HTML", reply_markup=admin_products_kb()
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
    from config.settings import API_KEY, API_HOST, API_PORT
    await update.callback_query.answer()
    
    await update.callback_query.message.reply_text(
        f"┌──────────────────────────┐\n"
        f"│   🔑  API SETTINGS           │\n"
        f"└──────────────────────────┘\n\n"
        f"Use the REST API to manage orders from\n"
        f"external platforms or web panels.\n\n"
        f"🌐  <b>Host:</b> <code>{API_HOST}</code>\n"
        f"🔌  <b>Port:</b> <code>{API_PORT}</code>\n"
        f"🔐  <b>API Key:</b>\n"
        f"<code>{API_KEY}</code>\n\n"
        f"<i>Pass this key in the <code>X-API-Key</code> header.</i>\n\n"
        f"<b>Endpoints:</b>\n"
        f"<code>POST /orders/</code> - Create order\n"
        f"<code>GET /orders/{{id}}</code> - Check status",
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
        "Enter a <b>label</b> for this code (e.g. <i>John's code</i>).\n"
        "This helps you remember who it's for.\n\n"
        "Or send <b>skip</b> to create without a label.\n\n"
        "/cancel to abort",
        parse_mode="HTML",
    )
    return ADMIN_CREATE_CODE_LABEL


async def admin_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    label_input = update.message.text.strip()
    label = None if label_input.lower() == "skip" else label_input

    async with AsyncSessionLocal() as session:
        repo = AccessCodeRepository(session)
        code = await repo.create(label=label)

    await update.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  ✅  <b>Access Code Created</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔑  Code: <code>{code.code}</code>\n"
        f"🏷  Label: {code.label or '—'}\n\n"
        f"<b>Give this code to the user.</b>\n"
        f"They enter it on /start to register.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML",
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
        f"✅  Store <b>{store.name}</b> created!\n\n"
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
        f"  🏪  <b>{store.name}</b>\n"
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
                f"  🏪  <b>{store.name}</b>\n"
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
    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_search_order_start, pattern=r"^adm_search_order$")],
        states={ADMIN_SEARCH_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_order)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    add_product_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_product_start, pattern=r"^adm_add_product$")],
        states={
            ADMIN_ADD_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_cat)],
            ADMIN_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_name)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    set_supplier_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_set_supplier_start, pattern=r"^adm_set_supplier$")],
        states={
            ADMIN_SET_SUPPLIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_supplier_value)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    edit_name_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_edit_name_start, pattern=r"^adm_edit_name$")],
        states={
            ADMIN_EDIT_NAME_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_name_select)],
            ADMIN_EDIT_NAME_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_name_value)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    create_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_create_code_start, pattern=r"^adm_create_code$")],
        states={ADMIN_CREATE_CODE_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_code)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    revoke_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_revoke_code_start, pattern=r"^adm_revoke_code$")],
        states={ADMIN_REVOKE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_revoke_code)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    revoke_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_revoke_user_start, pattern=r"^adm_revoke_user$")],
        states={ADMIN_REVOKE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_revoke_user)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    reset_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_reset_user_start, pattern=r"^adm_reset_user$")],
        states={ADMIN_RESET_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reset_user)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    # API Store management
    add_store_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_store_start, pattern=r"^adm_add_store$")],
        states={ADMIN_ADD_STORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_store_name)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    store_limit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_store_limit_start, pattern=r"^store_limit:")],
        states={ADMIN_STORE_SET_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_store_set_limit)]},
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    # User order limits
    user_limit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_set_user_limit_start, pattern=r"^adm_set_user_limit$")],
        states={
            ADMIN_SET_USER_LIMIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_user_limit_id)],
            ADMIN_SET_USER_LIMIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_user_limit_value)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )

    toggle_handler = CallbackQueryHandler(cb_toggle_power, pattern=r"^adm_toggle_power$")
    return [
        search_conv, add_product_conv, set_supplier_conv, edit_name_conv, 
        create_code_conv, revoke_code_conv, revoke_conv, reset_conv,
        add_store_conv, store_limit_conv, user_limit_conv,
        toggle_handler,
    ]
