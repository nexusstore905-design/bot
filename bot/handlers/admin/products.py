"""Product catalog: groups, packages, supplier routing, and cleanup."""
import re

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import admin_only, logger
from bot.keyboards.admin_kb import (
    admin_product_advanced_kb,
    admin_products_kb,
    cancel_conv_kb,
    cleanup_removed_products_kb,
    remove_products_kb,
    reset_all_products_kb,
)
from bot.states.states import (
    ADMIN_ADD_CAT,
    ADMIN_ADD_NAME,
    ADMIN_EDIT_NAME_SELECT,
    ADMIN_EDIT_NAME_VALUE,
    ADMIN_RENAME_GROUP_SELECT,
    ADMIN_RENAME_GROUP_VALUE,
    ADMIN_SET_SUPPLIER,
)
from database.database import AsyncSessionLocal
from database.repositories.product_repo import ProductRepository
from services.audit import audit
from services.messages import SUPPLIER_DIVIDER
from utils.supplier_routing import resolve_supplier_chat
from utils.ui import esc, panel


@admin_only
async def cb_admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        panel("Products", "Manage packages and supplier routing.", icon="🛍"),
        reply_markup=admin_products_kb(),
        parse_mode="HTML",
    )


@admin_only
async def cb_admin_product_advanced(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        panel("Advanced product tools", "These actions affect removed products or the full catalog.", icon="🧰"),
        reply_markup=admin_product_advanced_kb(),
        parse_mode="HTML",
    )


@admin_only
async def cb_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        repo = ProductRepository(session)
        products = await repo.get_all_active()
        deletable_count, preserved_count = await repo.get_inactive_cleanup_counts()

    if not products:
        await update.callback_query.message.edit_text(
            "No active products.\n"
            f"Removed products hidden: <b>{deletable_count + preserved_count}</b>.",
            parse_mode="HTML", reply_markup=admin_products_kb()
        )
        return

    lines = [
        "📦 <b>Active product groups and packages</b>\n──────────────",
        f"Removed products hidden: <b>{deletable_count + preserved_count}</b>\n",
    ]
    category = None
    for product in products:
        if product.category != category:
            category = product.category
            lines.append(f"\n📂 <b>{esc(category)}</b>")
        target_chat, route_source = resolve_supplier_chat(product.supplier_chat_id)
        supplier = (
            f"<code>{target_chat}</code> · {route_source}"
            if target_chat else "<i>not configured</i>"
        )
        lines.append(
            f"✅ <code>#{product.id}</code>  {esc(product.name)} → {supplier}"
        )
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_products_kb()
    )


@admin_only
async def cb_cleanup_removed_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        deletable_count, preserved_count = await ProductRepository(session).get_inactive_cleanup_counts()

    if not deletable_count and not preserved_count:
        await update.callback_query.message.edit_text(
            "✅ There are no removed products to clean.", reply_markup=admin_product_advanced_kb()
        )
        return

    if not deletable_count:
        await update.callback_query.message.edit_text(
            "🧹 Nothing can be permanently deleted.\n\n"
            f"Removed products kept for old order history: <b>{preserved_count}</b>.",
            parse_mode="HTML", reply_markup=admin_product_advanced_kb()
        )
        return

    await update.callback_query.message.edit_text(
        "🧹 <b>Clean removed products?</b>\n\n"
        f"Unused removed products to delete: <b>{deletable_count}</b>\n"
        f"Products kept for old order history: <b>{preserved_count}</b>\n\n"
        "Deleted products cannot be restored.",
        parse_mode="HTML", reply_markup=cleanup_removed_products_kb(),
    )


@admin_only
async def cb_cleanup_removed_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        deleted_count, preserved_count = await ProductRepository(session).cleanup_inactive_products()
    await audit(update.effective_user, "cleanup_products", f"deleted {deleted_count}")
    await update.callback_query.message.edit_text(
        "🧹 <b>Product cleanup finished</b>\n\n"
        f"Permanently deleted: <b>{deleted_count}</b>\n"
        f"Kept for old order history: <b>{preserved_count}</b>",
        parse_mode="HTML", reply_markup=admin_product_advanced_kb(),
    )


@admin_only
async def cb_reset_all_products_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        product_count, linked_items, affected_orders = await ProductRepository(session).get_full_reset_counts()

    await update.callback_query.message.edit_text(
        "🧨 <b>Delete every product and reset IDs?</b>\n\n"
        f"Product records to delete: <b>{product_count}</b>\n"
        f"Past orders that use these products: <b>{affected_orders}</b>\n"
        f"Historical product lines to detach: <b>{linked_items}</b>\n\n"
        "Past orders and their saved product names will stay visible. Their old product IDs will be cleared. "
        "All products and supplier group settings will be deleted, and the next product IDs will start at #1. "
        "If another website uses product IDs, update those IDs after the reset. "
        "This cannot be undone.",
        parse_mode="HTML", reply_markup=reset_all_products_kb(),
    )


@admin_only
async def cb_reset_all_products_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        deleted_count, detached_items = await ProductRepository(session).reset_all_products()
    await audit(update.effective_user, "reset_products", f"deleted {deleted_count}")
    await update.callback_query.message.edit_text(
        "✅ <b>Product reset finished</b>\n\n"
        f"Products deleted: <b>{deleted_count}</b>\n"
        f"Old order product IDs cleared: <b>{detached_items}</b>\n\n"
        "Old order history and product names are kept. Your next new product will use ID #1.",
        parse_mode="HTML", reply_markup=admin_product_advanced_kb(),
    )


@admin_only
async def cb_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        groups = await ProductRepository(session).get_categories()
    group_hint = "\n\nExisting groups: " + ", ".join(groups) if groups else ""
    await update.callback_query.message.edit_text(
        "📂 <b>Enter the product group name</b>\n\n"
        "Customers will tap this name first, then choose one of its packages.\n"
        "For example, enter <code>PUBG UC Top Up</code>. "
        "To add more packages to a group, enter that group name again."
        f"{esc(group_hint)}",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_ADD_CAT


@admin_only
async def admin_add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "You can include several new packages in one message.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_ADD_NAME


@admin_only
async def admin_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = context.user_data.get("add_cat", "")
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
        products, skipped = await ProductRepository(session).add_many(category, names)

    if not products:
        await update.message.reply_text(
            "ℹ️ Those package names are already in this group. Send different names or /cancel."
        )
        return ADMIN_ADD_NAME

    package_lines = [f"• <code>#{product.id}</code> {esc(product.name)}" for product in products[:20]]
    if len(products) > 20:
        package_lines.append(f"• …and {len(products) - 20} more")
    supplier_note = (
        "\n📡 The existing package supplier route was applied to these packages."
        if products[0].supplier_chat_id else
        "\n📡 No single package route was inherited; the global fallback may be used."
    )
    skipped_note = f"\nSkipped existing names: <b>{len(skipped)}</b>." if skipped else ""
    await audit(update.effective_user, "add_products", f"{category}: {', '.join(p.name for p in products)}")
    await update.message.reply_text(
        f"✅ <b>Packages added to {esc(products[0].category)}</b>\n"
        f"──────────────\n"
        f"{chr(10).join(package_lines)}\n"
        f"\nAdded: <b>{len(products)}</b>{skipped_note}{supplier_note}",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    context.user_data.pop("add_cat", None)
    return ConversationHandler.END


@admin_only
async def cb_rename_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        groups = await ProductRepository(session).get_categories()
    if not groups:
        await update.callback_query.message.edit_text("No product groups to rename.", reply_markup=admin_products_kb())
        return ConversationHandler.END
    names = "\n".join(f"• {esc(group)}" for group in groups)
    await update.callback_query.message.edit_text(
        "📝 <b>Rename a product group</b>\n\n"
        f"Current groups:\n{names}\n\n"
        "Send the current group name exactly as shown:",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_RENAME_GROUP_SELECT


@admin_only
async def admin_rename_group_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requested = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        groups = await ProductRepository(session).get_categories()
    matched = next((group for group in groups if group.casefold() == requested.casefold()), None)
    if not matched:
        await update.message.reply_text("I could not find that group. Copy its exact name from the list and try again.")
        return ADMIN_RENAME_GROUP_SELECT
    context.user_data["rename_group_old"] = matched
    await update.message.reply_text(
        f"Current group: <b>{esc(matched)}</b>\n\n"
        "Send the new name. For example: <code>PUBG UC Top Up</code>",
        parse_mode="HTML",
    )
    return ADMIN_RENAME_GROUP_VALUE


@admin_only
async def admin_rename_group_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await audit(update.effective_user, "rename_group", f"{old_name} → {result}")
    await update.message.reply_text(
        f"✅ Product group renamed to <b>{esc(result)}</b>.\n"
        f"Packages kept: <b>{count}</b>. Supplier settings were kept.",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    return ConversationHandler.END


# ─── Supplier routing per package ─────────────────────────────────────

@admin_only
async def cb_set_supplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_all_active()
    if not products:
        await update.callback_query.message.edit_text("❌  No packages found. Add products first.", reply_markup=admin_products_kb())
        return ConversationHandler.END

    lines = ["📡  <b>Set Supplier Group per Package</b>\n\n"
             "Each package can go to a different supplier, even within the same product group. Unassigned packages use the global <code>SUPPLIER_CHAT_ID</code> fallback.\n\n"
             "Use the <code>#ID</code> shown beside the package name. Send <code>#ID | -1001234567890</code>.\n"
             "To use the global default for one package, send <code>#PACKAGE_ID | 0</code>. You can also send a package ID by itself, then forward any message from its supplier group so I can read the exact group ID.\n\n"
             "Current package routes:\n"]
    for product in products:
        target_chat, route_source = resolve_supplier_chat(product.supplier_chat_id)
        route_text = (
            f"<code>{target_chat}</code> · {route_source}"
            if target_chat else "<i>not configured</i>"
        )
        lines.append(
            f"  📦 <code>#{product.id}</code> <b>{esc(product.name)}</b> "
            f"({esc(product.category)}) → {route_text}"
        )
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=cancel_conv_kb(),
    )
    return ADMIN_SET_SUPPLIER


@admin_only
async def admin_set_supplier_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "|" not in text:
        try:
            product_id = int(text.removeprefix("#"))
        except ValueError:
            product_id = 0
        async with AsyncSessionLocal() as session:
            product = await ProductRepository(session).get_by_id(product_id) if product_id > 0 else None
        if not product or not product.is_active:
            await update.message.reply_text(
                "❌ Send an active package ID by itself, or use <code>#PACKAGE_ID | -1001234567890</code>.\n\nTry again or /cancel.",
                parse_mode="HTML",
            )
            return ADMIN_SET_SUPPLIER
        context.user_data["supplier_forward_product_id"] = product.id
        await update.message.reply_text(
            f"Now forward any message from the supplier group for <b>#{product.id} {esc(product.name)}</b>.\n\n"
            "I will use Telegram’s original group ID and send a test message there.\n"
            "If you meant to enter an ID, send <code>#PACKAGE_ID | -1001234567890</code> instead.",
            parse_mode="HTML",
        )
        return ADMIN_SET_SUPPLIER

    product_input, raw_chat_id = (part.strip() for part in text.split("|", 1))
    try:
        product_id = int(product_input.removeprefix("#"))
    except ValueError:
        product_id = 0
    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(product_id) if product_id > 0 else None
    if not product or not product.is_active:
        await update.message.reply_text(
            f"❌ Active package <b>{esc(product_input)}</b> not found. Use its numeric ID from the package list.",
            parse_mode="HTML",
        )
        return ADMIN_SET_SUPPLIER

    if raw_chat_id == "0" or raw_chat_id.casefold() == "default":
        async with AsyncSessionLocal() as session:
            await ProductRepository(session).set_product_supplier(product.id, None)
        await audit(update.effective_user, "set_supplier", f"#{product.id} → default")
        await update.message.reply_text(
            f"✅ <b>#{product.id} {esc(product.name)}</b> now uses the global default supplier.",
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

    return await _verify_and_save_supplier(update, context, product.id, supplier_chat_id)


@admin_only
async def admin_set_supplier_from_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Use the original group ID from an admin-forwarded group message."""
    product_id = context.user_data.get("supplier_forward_product_id")
    if not product_id:
        await update.message.reply_text("First send a package ID in Supplier routing, then forward a message from its supplier group.")
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

    context.user_data.pop("supplier_forward_product_id", None)
    return await _verify_and_save_supplier(update, context, int(product_id), int(source_id))


async def _verify_and_save_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, supplier_chat_id: int):
    """Confirm Telegram access before changing the saved supplier destination."""
    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(product_id)
    if not product or not product.is_active:
        await update.message.reply_text(
            f"❌ Active package <code>#{product_id}</code> not found. Open Supplier routing and try again.",
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
                "🤝 <b>SUPPLIER GROUP CONNECTED</b>\n"
                f"{SUPPLIER_DIVIDER}\n"
                "New orders for this package will arrive here:\n"
                f"📦 <b>{esc(product.name)}</b>  ·  {esc(product.category)}\n\n"
                "On each order card: copy the Player ID, deliver, then tap ✅ <b>Done</b> "
                "(or ❌ <b>Error</b> if you can't). Reply with a screenshot to send proof."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("Supplier test failed for chat %s using bot @%s (%s): %s", target_chat, bot_info.username, type(e).__name__, e)
        detail = esc(e)
        if "chat not found" in str(e).casefold():
            suggestion = (
                "Telegram cannot find that group for this bot. This usually means the ID is not the ID of the group this bot can see, "
                "or the running bot token belongs to a different bot. Use the forward-message method so I can read the exact ID, "
                "and confirm this same bot (@" + esc(bot_info.username or "unknown") + ") is in the group."
            )
        else:
            suggestion = "Telegram found the destination but rejected the test. Check this bot’s permission to send messages in that group."
        await update.message.reply_text(
            f"❌ <b>Group not connected</b>\n\n"
            f"Package: <b>#{product.id} {esc(product.name)}</b>\n"
            f"ID tried: <code>{target_chat}</code>\n"
            f"Bot: <b>@{esc(bot_info.username or 'unknown')}</b>\n"
            f"Telegram error: <code>{detail}</code>\n\n{suggestion}\n\n"
            "I did not replace the previously saved supplier destination.",
            parse_mode="HTML", reply_markup=admin_products_kb(),
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        saved_product = await ProductRepository(session).set_product_supplier(product_id, supplier_chat_id)
    if not saved_product:
        await update.message.reply_text(
            f"❌ Package <code>#{product_id}</code> is no longer active. The route was not changed.",
            parse_mode="HTML", reply_markup=admin_products_kb(),
        )
        return ConversationHandler.END
    await audit(update.effective_user, "set_supplier", f"#{product_id} → {supplier_chat_id}")
    await update.message.reply_text(
        f"✅ <b>Supplier group connected</b>\n\n"
        f"Package: <b>#{saved_product.id} {esc(saved_product.name)}</b>\n"
        f"Product group: <b>{esc(saved_product.category)}</b>\n"
        f"Supplier group: <b>{esc(chat.title or target_chat)}</b>\n"
        f"Chat ID: <code>{target_chat}</code>\n"
        f"Bot: <b>@{esc(bot_info.username or 'unknown')}</b>\n"
        "Packages updated: <b>1</b>\n\n"
        "The bot sent a test message to this group.",
        parse_mode="HTML", reply_markup=admin_products_kb(),
    )
    return ConversationHandler.END


# ─── Edit name / remove ───────────────────────────────────────────────

@admin_only
async def cb_edit_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_all_active()
    lines = ["Enter the <b>product ID</b> to rename:\n"]
    for product in products:
        lines.append(f"  <code>#{product.id}</code>  {esc(product.name)} ({esc(product.category)})")
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=cancel_conv_kb(),
    )
    return ADMIN_EDIT_NAME_SELECT


@admin_only
async def admin_edit_name_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        product_id = int(update.message.text.strip().replace("#", ""))
    except ValueError:
        await update.message.reply_text("❌  Enter a valid product ID:")
        return ADMIN_EDIT_NAME_SELECT
    async with AsyncSessionLocal() as session:
        product = await ProductRepository(session).get_by_id(product_id)
    if not product or not product.is_active:
        await update.message.reply_text("Product not found. Enter an ID from the list:")
        return ADMIN_EDIT_NAME_SELECT
    context.user_data["edit_pid"] = product_id
    await update.message.reply_text(
        f"Editing <b>{esc(product.name)}</b>\n\nEnter the <b>new name</b> (e.g. <code>1800 UC</code>):",
        parse_mode="HTML",
    )
    return ADMIN_EDIT_NAME_VALUE


@admin_only
async def admin_edit_name_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    if len(new_name) < 2 or len(new_name) > 128:
        await update.message.reply_text("❌  Use 2 to 128 characters. Try again:")
        return ADMIN_EDIT_NAME_VALUE
    product_id = context.user_data.pop("edit_pid", None)
    async with AsyncSessionLocal() as session:
        updated = product_id is not None and await ProductRepository(session).update_name(product_id, new_name)
    if not updated:
        await update.message.reply_text("❌  That product no longer exists.", reply_markup=admin_products_kb())
        return ConversationHandler.END
    await audit(update.effective_user, "rename_product", f"#{product_id} → {new_name}")
    await update.message.reply_text(
        f"✅  Product updated to <b>{esc(new_name)}</b>", parse_mode="HTML", reply_markup=admin_products_kb()
    )
    return ConversationHandler.END


@admin_only
async def cb_remove_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_all_active()
    if not products:
        await update.callback_query.message.edit_text("No products to remove.", reply_markup=admin_products_kb())
        return
    await update.callback_query.message.edit_text(
        "🗑  Tap to remove:", reply_markup=remove_products_kb(products)
    )


@admin_only
async def cb_remove_product_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        await ProductRepository(session).deactivate(product_id)
    await audit(update.effective_user, "remove_product", f"#{product_id}")
    await update.callback_query.answer("Removed!", show_alert=True)
    async with AsyncSessionLocal() as session:
        products = await ProductRepository(session).get_all_active()
    if products:
        await update.callback_query.message.edit_reply_markup(reply_markup=remove_products_kb(products))
    else:
        await update.callback_query.message.edit_text("All products removed.", reply_markup=admin_products_kb())
