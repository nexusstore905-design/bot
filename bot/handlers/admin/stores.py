"""API stores: keys, limits, webhooks, and which customers a store may order for."""
import re
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import admin_only
from bot.keyboards.admin_kb import api_stores_kb, cancel_conv_kb, confirm_kb, store_actions_kb
from bot.states.states import (
    ADMIN_ADD_STORE_NAME, ADMIN_STORE_CUSTOMERS, ADMIN_STORE_SET_LIMIT, ADMIN_STORE_WEBHOOK,
)
from database.database import AsyncSessionLocal
from database.repositories.api_store_repo import ApiStoreRepository
from services.audit import audit
from utils.ui import esc


def _key_notice(name: str, api_key: str, verb: str) -> str:
    return (
        f"🔑  <b>API key {verb} for {esc(name)}</b>\n\n"
        f"<code>{esc(api_key)}</code>\n\n"
        "⚠️  Save it now. Only a hash is stored, so it cannot be shown again."
    )


async def _store_view(store_id: int):
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id)
        if store is None:
            return None
        customers = await repo.count_customers(store.id)
    status = "🟢 Active" if store.is_active else "🔴 Disabled"
    limit_text = str(store.daily_limit) if store.daily_limit > 0 else "Unlimited"
    webhook = esc(store.webhook_url) if store.webhook_url else "not set"
    customer_text = f"{customers} linked customer(s) only" if customers else "any member"
    text = (
        f"🏪  <b>{esc(store.name)}</b>\n──────────────\n\n"
        f"📌  Status: <b>{status}</b>\n"
        f"🔑  Key: <code>{esc(store.api_key_prefix)}…</code>\n"
        f"📊  Daily limit: <b>{limit_text}</b> · today {store.orders_today}\n"
        f"🔔  Webhook: {webhook}\n"
        f"👥  May order for: <b>{customer_text}</b>"
    )
    return text, store_actions_kb(store.id, store.is_active)


@admin_only
async def cb_api_stores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🏪  <b>API STORES</b>\n──────────────\n\n"
        "External stores that place orders through your API.\n"
        "Each store has its own key, daily limit, webhook, and customer list.",
        reply_markup=api_stores_kb(),
        parse_mode="HTML",
    )


@admin_only
async def cb_add_store_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🏪  Enter a <b>name</b> for the new store:\n\n"
        "<i>(e.g. MyWebsite, PartnerShop)</i>",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_ADD_STORE_NAME


@admin_only
async def admin_add_store_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name or len(name) > 64:
        await update.message.reply_text("❌  Name must be 1-64 characters. Try again:")
        return ADMIN_ADD_STORE_NAME

    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        if await repo.get_by_name(name):
            await update.message.reply_text("❌  A store with that name already exists. Try a different name:")
            return ADMIN_ADD_STORE_NAME
        store, api_key = await repo.create(name=name, daily_limit=0)

    await audit(update.effective_user, "create_store", store.name)
    await update.message.reply_text(
        _key_notice(store.name, api_key, "created")
        + "\n\n📊  Daily limit: <b>Unlimited</b> · may order for <b>any member</b>.\n"
        "Open the store to set a limit, a webhook, or restrict its customers.",
        reply_markup=api_stores_kb(),
        parse_mode="HTML",
    )
    return ConversationHandler.END


@admin_only
async def cb_list_stores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        stores = await ApiStoreRepository(session).get_all()

    if not stores:
        await update.callback_query.message.edit_text(
            "📋  No API stores found.\n\nUse <b>➕ Add Store</b> to create one.",
            reply_markup=api_stores_kb(),
            parse_mode="HTML",
        )
        return

    rows = []
    for store in stores:
        status = "🟢" if store.is_active else "🔴"
        limit_text = f"{store.orders_today}/{store.daily_limit}" if store.daily_limit > 0 else f"{store.orders_today}/∞"
        rows.append([InlineKeyboardButton(
            f"{status}  {store.name}  [{limit_text}]",
            callback_data=f"store_view:{store.id}"
        )])
    rows.append([InlineKeyboardButton("◀  Back", callback_data="adm_api_stores")])
    await update.callback_query.message.edit_text(
        "📋  <b>ALL API STORES</b>\n\nTap a store to manage it:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


@admin_only
async def cb_store_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    view = await _store_view(int(update.callback_query.data.split(":")[1]))
    if view is None:
        await update.callback_query.message.edit_text("❌  Store not found.", reply_markup=api_stores_kb())
        return
    await update.callback_query.message.edit_text(view[0], reply_markup=view[1], parse_mode="HTML")


@admin_only
async def cb_store_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_id = int(update.callback_query.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id)
        if store is None:
            await update.callback_query.answer("Store not found!", show_alert=True)
            return
        await repo.toggle_active(store)
        enabled, name = store.is_active, store.name
    await audit(update.effective_user, "toggle_store", f"{name} → {'enabled' if enabled else 'disabled'}")
    await update.callback_query.answer(f"Store {'🟢 ENABLED' if enabled else '🔴 DISABLED'}!", show_alert=True)
    view = await _store_view(store_id)
    if view:
        await update.callback_query.message.edit_text(view[0], reply_markup=view[1], parse_mode="HTML")


@admin_only
async def cb_store_rotate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    await update.callback_query.message.edit_text(
        "♻️ <b>Rotate this store’s API key?</b>\n\n"
        "The current key stops working immediately. Update the store’s server with the new key right away.",
        parse_mode="HTML",
        reply_markup=confirm_kb(f"store_rotate_ok:{store_id}", f"store_view:{store_id}", "♻️  Yes, issue a new key"),
    )


@admin_only
async def cb_store_rotate_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id)
        if store is None:
            await update.callback_query.message.edit_text("❌  Store not found.", reply_markup=api_stores_kb())
            return
        api_key = await repo.rotate_key(store)
        name = store.name
    await audit(update.effective_user, "rotate_store_key", name)
    await update.callback_query.message.edit_text(
        _key_notice(name, api_key, "rotated"),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀  Store", callback_data=f"store_view:{store_id}")]]),
    )


@admin_only
async def cb_store_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    await update.callback_query.message.edit_text(
        "🗑 <b>Delete this API store?</b>\n\n"
        "Its key stops working immediately. Past orders keep their history.",
        parse_mode="HTML",
        reply_markup=confirm_kb(f"store_delete_ok:{store_id}", f"store_view:{store_id}", "🗑  Yes, delete store"),
    )


@admin_only
async def cb_store_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_id = int(update.callback_query.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id)
        if store is None:
            await update.callback_query.answer("Store not found!", show_alert=True)
            return
        name = store.name
        await repo.delete(store)
    await audit(update.effective_user, "delete_store", name)
    await update.callback_query.answer(f"🗑 Store '{name}' deleted.", show_alert=True)
    await update.callback_query.message.edit_text(
        f"🗑  Store <b>{esc(name)}</b> deleted.", reply_markup=api_stores_kb(), parse_mode="HTML",
    )


@admin_only
async def cb_store_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["edit_store_id"] = int(update.callback_query.data.split(":")[1])
    await update.callback_query.message.edit_text(
        "📊  Enter the <b>daily order limit</b> for this store:\n\n"
        "<i>Enter 0 for unlimited.</i>",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_STORE_SET_LIMIT


@admin_only
async def admin_store_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
        if limit < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌  Enter a valid number (0 or higher):")
        return ADMIN_STORE_SET_LIMIT

    store_id = context.user_data.pop("edit_store_id", None)
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id) if store_id else None
        if store is None:
            await update.message.reply_text("❌  Store not found.", reply_markup=api_stores_kb())
            return ConversationHandler.END
        await repo.set_daily_limit(store, limit)
        name = store.name
    await audit(update.effective_user, "store_limit", f"{name} → {limit}")
    view = await _store_view(store_id)
    await update.message.reply_text(
        f"✅  Daily limit for <b>{esc(name)}</b> set to <b>{limit if limit else 'Unlimited'}</b>.\n\n" + (view[0] if view else ""),
        reply_markup=view[1] if view else api_stores_kb(),
        parse_mode="HTML",
    )
    return ConversationHandler.END


@admin_only
async def cb_store_webhook_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    context.user_data["edit_store_id"] = store_id
    await update.callback_query.message.edit_text(
        "🔔 <b>Store webhook</b>\n\n"
        "Send an <b>https://</b> URL. The bot will POST a signed JSON event there every time one of this "
        "store’s orders changes status (header <code>X-Nexus-Signature</code>).\n\n"
        "Send <code>off</code> to remove the webhook.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_STORE_WEBHOOK


@admin_only
async def admin_store_webhook_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    url: str | None
    if value.casefold() in ("off", "none", "-", "remove"):
        url = None
    else:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or len(value) > 500:
            await update.message.reply_text("❌ Send a full https:// URL (max 500 characters), or <code>off</code>.", parse_mode="HTML")
            return ADMIN_STORE_WEBHOOK
        url = value
    store_id = context.user_data.pop("edit_store_id", None)
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id) if store_id else None
        if store is None:
            await update.message.reply_text("❌  Store not found.", reply_markup=api_stores_kb())
            return ConversationHandler.END
        new_secret = await repo.set_webhook(store, url)
        name = store.name
    await audit(update.effective_user, "store_webhook", f"{name} → {url or 'off'}")
    if url is None:
        text = f"🔕 Webhook removed for <b>{esc(name)}</b>."
    else:
        text = f"🔔 Webhook for <b>{esc(name)}</b> set to\n<code>{esc(url)}</code>"
        if new_secret:
            text += (
                f"\n\nSigning secret (shown once):\n<code>{esc(new_secret)}</code>\n"
                "Verify each request: HMAC-SHA256 of <code>&lt;t&gt;.&lt;body&gt;</code> with this secret must equal <code>v1</code>."
            )
        else:
            text += "\n\nThe existing signing secret is unchanged. Remove and re-add the webhook to get a new one."
    view = await _store_view(store_id)
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=view[1] if view else api_stores_kb(),
    )
    return ConversationHandler.END


@admin_only
async def cb_store_customers_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    store_id = int(update.callback_query.data.split(":")[1])
    context.user_data["edit_store_id"] = store_id
    async with AsyncSessionLocal() as session:
        ids = await ApiStoreRepository(session).get_customer_ids(store_id)
    current = ", ".join(f"<code>{tid}</code>" for tid in ids[:50]) if ids else "none — this store may order for <b>any member</b>"
    await update.callback_query.message.edit_text(
        "👥 <b>Customers this store may order for</b>\n\n"
        f"Current: {current}\n\n"
        "• Send Telegram IDs to allow them, e.g. <code>123456 789012</code>\n"
        "• Prefix with minus to remove, e.g. <code>-123456</code>\n"
        "• Send <code>clear</code> to allow any member again\n\n"
        "Revoked users and people who never signed in are always refused.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_STORE_CUSTOMERS


@admin_only
async def admin_store_customers_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_id = context.user_data.get("edit_store_id")
    text = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        repo = ApiStoreRepository(session)
        store = await repo.get_by_id(store_id) if store_id else None
        if store is None:
            await update.message.reply_text("❌  Store not found.", reply_markup=api_stores_kb())
            return ConversationHandler.END
        if text.casefold() == "clear":
            removed = await repo.unlink_customers(store.id)
            summary = f"Removed {removed} link(s). The store may order for any member."
        else:
            tokens = re.split(r"[\s,]+", text)
            if not all(re.fullmatch(r"-?\d{3,15}", token) for token in tokens if token):
                await update.message.reply_text("❌ Send numeric Telegram IDs (prefix with - to remove), or <code>clear</code>.", parse_mode="HTML")
                return ADMIN_STORE_CUSTOMERS
            to_add = [int(token) for token in tokens if token and not token.startswith("-")]
            to_remove = [int(token[1:]) for token in tokens if token.startswith("-")]
            added = await repo.link_customers(store.id, to_add) if to_add else 0
            removed = await repo.unlink_customers(store.id, to_remove) if to_remove else 0
            summary = f"Added {added}, removed {removed}."
        name = store.name
    context.user_data.pop("edit_store_id", None)
    await audit(update.effective_user, "store_customers", f"{name}: {summary}")
    view = await _store_view(store_id)
    await update.message.reply_text(
        f"✅ {summary}\n\n" + (view[0] if view else ""),
        parse_mode="HTML",
        reply_markup=view[1] if view else api_stores_kb(),
    )
    return ConversationHandler.END
