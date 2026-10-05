"""Admin entry points, settings toggles, and the business-data reset."""
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import admin_only, advanced_kb, logger, team_only
from bot.keyboards.admin_kb import admin_main_kb, reset_business_data_kb
from bot.middlewares.auth_middleware import is_admin
from config.settings import API_KEY
from database.database import AsyncSessionLocal
from database.repositories.business_data_repo import BusinessDataRepository
from services import app_settings
from services.audit import audit
from utils.ui import panel


def _menu_text(user_id: int) -> str:
    if is_admin(user_id):
        return panel("Admin panel", "Manage orders, products, customers, and access.", icon="👑")
    return panel("Team panel", "Handle orders and look up customers.", icon="🧑‍💼")


@team_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        _menu_text(user_id), reply_markup=admin_main_kb(is_admin(user_id)), parse_mode="HTML",
    )


@team_only
async def cb_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = update.effective_user.id
    await update.callback_query.message.edit_text(
        _menu_text(user_id), reply_markup=admin_main_kb(is_admin(user_id)), parse_mode="HTML",
    )


@admin_only
async def cb_admin_advanced(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        panel("Advanced settings", "API stores, order limits, and service availability.", icon="⚙️"),
        reply_markup=await advanced_kb(),
        parse_mode="HTML",
    )


@admin_only
async def cb_toggle_power(update: Update, context: ContextTypes.DEFAULT_TYPE):
    turning_off = not await app_settings.is_maintenance()
    await app_settings.set_setting(app_settings.MAINTENANCE, "on" if turning_off else None)
    await audit(update.effective_user, "maintenance", "on" if turning_off else "off")
    await update.callback_query.answer(
        "🔴 Maintenance mode ON — customers are paused." if turning_off else "🟢 Service is back ON.",
        show_alert=True,
    )
    await update.callback_query.message.edit_reply_markup(reply_markup=await advanced_kb())


@admin_only
async def cb_reset_business_data_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    async with AsyncSessionLocal() as session:
        counts = await BusinessDataRepository(session).get_reset_counts()
    await query.message.edit_text(
        "🧨 <b>Reset customer and sales data?</b>\n──────────────\n\n"
        f"Customers: <b>{counts['customers']}</b>\n"
        f"Orders and payment records: <b>{counts['orders']}</b>\n"
        f"Order lines and supplier history: <b>{counts['order_items'] + counts['order_status_history'] + counts['supplier_fulfillments']}</b>\n"
        f"Access codes and API store keys: <b>{counts['access_codes'] + counts['api_stores']}</b>\n"
        f"Customer order limits: <b>{counts['customer_limits']}</b>\n\n"
        "This permanently deletes all customer accounts, every order and its payment status, order/supplier history, access codes, API store keys, and customer-specific limits.\n\n"
        "Your product catalog, supplier routing, admin accounts, bot settings, and environment secrets will stay. You will need to create new access codes and API store keys afterward.",
        parse_mode="HTML",
        reply_markup=reset_business_data_kb(),
    )


@admin_only
async def cb_reset_business_data_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Resetting customer and sales data…")
    try:
        async with AsyncSessionLocal() as session:
            counts = await BusinessDataRepository(session).reset_business_data()
    except Exception:
        logger.exception("Admin business-data reset failed")
        await query.message.edit_text(
            "❌ <b>Reset failed.</b> The database transaction was rolled back. No reset was completed; check the bot logs and try again.",
            parse_mode="HTML",
            reply_markup=await advanced_kb(),
        )
        return
    await audit(update.effective_user, "reset_business_data", str(counts))
    await query.message.edit_text(
        "✅ <b>Business data reset completed.</b>\n\n"
        f"Customers deleted: <b>{counts['customers']}</b>\n"
        f"Orders and payment records deleted: <b>{counts['orders']}</b>\n"
        f"Order lines/history deleted: <b>{counts['order_items'] + counts['order_status_history'] + counts['supplier_fulfillments']}</b>\n"
        f"Access codes/API store keys deleted: <b>{counts['access_codes'] + counts['api_stores']}</b>\n"
        f"Customer limits deleted: <b>{counts['customer_limits']}</b>\n\n"
        "Products and bot configuration are still available. Create fresh access codes and API store keys before customers reconnect.",
        parse_mode="HTML",
        reply_markup=await advanced_kb(),
    )


@admin_only
async def cb_admin_api_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    key_status = (
        "Master key configured (hidden in this panel)"
        if API_KEY else "No master key; per-store keys only"
    )
    await update.callback_query.message.edit_text(
        "🔑 <b>API settings</b>\n──────────────\n"
        "Use the REST API to accept orders from connected platforms.\n\n"
        "🌐 <b>Service</b>  Flask API (PythonAnywhere Web tab)\n"
        f"🔐 <b>Keys</b>  {key_status}\n"
        "<i>Send the key in the X-API-Key header. Store keys are shown once; rotate them in API stores.</i>\n\n"
        "<b>Endpoints</b>\n"
        "<code>GET /v1/products/</code>  Active products\n"
        "<code>POST /v1/orders/</code>  Create an order (supports Idempotency-Key)\n"
        "<code>GET /v1/orders/{id}</code>  Order status and details\n"
        "<code>GET /health</code>  Database and bot status\n\n"
        "Stores can also receive signed status webhooks.",
        parse_mode="HTML",
        reply_markup=admin_main_kb()
    )


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✖  Cancelled.", reply_markup=admin_main_kb(is_admin(update.effective_user.id)))
    return ConversationHandler.END


async def cb_admin_cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "✖  Operation cancelled.", reply_markup=admin_main_kb(is_admin(update.effective_user.id)),
        )
    context.user_data.clear()
    return ConversationHandler.END
