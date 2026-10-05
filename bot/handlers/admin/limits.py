"""Per-customer daily order limits. No limit = unlimited, 0 = blocked."""
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import admin_only
from bot.keyboards.admin_kb import cancel_conv_kb, user_limits_kb
from bot.states.states import ADMIN_SET_USER_LIMIT_ID, ADMIN_SET_USER_LIMIT_VALUE
from database.database import AsyncSessionLocal
from database.repositories.api_store_repo import UserOrderLimitRepository
from services.audit import audit


@admin_only
async def cb_user_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🚦  <b>USER ORDER LIMITS</b>\n──────────────\n\n"
        "Restrict how many orders a specific user can place per day (UTC).\n\n"
        "• No limit set = unlimited\n"
        "• Limit <b>0</b> = blocked from ordering\n"
        "• Limit <b>N</b> = up to N orders per day",
        reply_markup=user_limits_kb(),
        parse_mode="HTML",
    )


@admin_only
async def cb_set_user_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🚦  Enter the <b>Telegram ID</b> of the user you want to limit:\n\n"
        "<i>(You can find their ID in the Customers list)</i>",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_SET_USER_LIMIT_ID


@admin_only
async def admin_set_user_limit_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        telegram_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌  Enter a valid Telegram ID (numbers only):")
        return ADMIN_SET_USER_LIMIT_ID

    context.user_data["limit_telegram_id"] = telegram_id
    await update.message.reply_text(
        f"📊  User ID: <b>{telegram_id}</b>\n\n"
        "Enter the <b>daily order limit</b>:\n\n"
        "• <b>0</b> = Block all orders\n"
        "• <b>5</b> = Max 5 orders/day\n"
        "• <b>-1</b> = Remove limit (unlimited)",
        parse_mode="HTML",
    )
    return ADMIN_SET_USER_LIMIT_VALUE


@admin_only
async def admin_set_user_limit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
    except ValueError:
        limit = -2
    if limit < -1 or limit > 10_000:
        await update.message.reply_text("❌  Enter -1 (remove), 0 (block), or a positive number:")
        return ADMIN_SET_USER_LIMIT_VALUE

    telegram_id = context.user_data.pop("limit_telegram_id", None)
    if telegram_id is None:
        await update.message.reply_text("Selection expired. Start again.", reply_markup=user_limits_kb())
        return ConversationHandler.END
    async with AsyncSessionLocal() as session:
        repo = UserOrderLimitRepository(session)
        if limit == -1:
            await repo.remove_limit(telegram_id)
            text = f"✅  Limit removed for user <b>{telegram_id}</b>.\nThey can now place unlimited orders."
        else:
            await repo.set_limit(telegram_id, limit)
            limit_text = "BLOCKED" if limit == 0 else f"{limit} orders/day"
            text = f"✅  User <b>{telegram_id}</b> limit set to: <b>{limit_text}</b>"
    await audit(update.effective_user, "user_limit", f"{telegram_id} → {limit}")
    await update.message.reply_text(text, reply_markup=user_limits_kb(), parse_mode="HTML")
    return ConversationHandler.END


@admin_only
async def cb_list_user_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        limits = await UserOrderLimitRepository(session).get_all()

    if not limits:
        await update.callback_query.message.edit_text(
            "📋  No user limits configured.\n\n"
            "All users have unlimited orders.\n"
            "Use <b>🚦 Set User Limit</b> to add one.",
            reply_markup=user_limits_kb(),
            parse_mode="HTML",
        )
        return

    lines = []
    for limit in limits[:60]:
        limit_text = "🚫 BLOCKED" if limit.daily_limit == 0 else f"{limit.orders_today}/{limit.daily_limit} today"
        lines.append(f"  👤  <code>{limit.telegram_id}</code>  →  <b>{limit_text}</b>")

    await update.callback_query.message.edit_text(
        "📋  <b>USER LIMITS</b>\n──────────────\n\n"
        + "\n".join(lines) + "\n\n"
        "<i>Set a limit to -1 to remove it.</i>",
        reply_markup=user_limits_kb(),
        parse_mode="HTML",
    )
