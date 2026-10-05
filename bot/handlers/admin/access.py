"""Access codes and per-user sign-in control."""
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import admin_only, logger
from bot.keyboards.admin_kb import admin_pin_kb, cancel_conv_kb, create_code_options_kb
from bot.states.states import (
    ADMIN_CREATE_CODE_LABEL, ADMIN_RESET_USER, ADMIN_REVOKE_CODE, ADMIN_REVOKE_USER,
)
from database.database import AsyncSessionLocal
from database.repositories.user_repo import AccessCodeRepository, UserRepository
from services.audit import audit
from utils.ui import esc


def _code_created_text(code) -> str:
    return (
        "✅  <b>Access code created</b>\n──────────────\n\n"
        f"🔑  Code: <code>{esc(code.code)}</code>\n"
        f"🏷  Label: {esc(code.label) if code.label else '—'}\n\n"
        "<b>Give this code to the user.</b>\n"
        "They enter it after /start to register. Each code works for one person."
    )


@admin_only
async def cb_admin_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🔐  <b>Access Code Management</b>\n\n"
        "Create unique codes for trusted users.\n"
        "Each code registers one user.",
        reply_markup=admin_pin_kb(),
        parse_mode="HTML",
    )


@admin_only
async def cb_create_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "➕  <b>Create Access Code</b>\n\n"
        "Send a <b>label</b> for this code (e.g. <i>John's Code</i>),\n"
        "or tap <b>⚡ Instant Code</b> to generate one immediately without a label.",
        parse_mode="HTML",
        reply_markup=create_code_options_kb(),
    )
    return ADMIN_CREATE_CODE_LABEL


@admin_only
async def cb_create_code_instant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    try:
        async with AsyncSessionLocal() as session:
            code = await AccessCodeRepository(session).create(label=None)
        await audit(update.effective_user, "create_code", code.code)
        await update.callback_query.message.edit_text(
            _code_created_text(code), parse_mode="HTML", reply_markup=admin_pin_kb(),
        )
    except Exception:
        logger.exception("Error creating instant access code")
        await update.callback_query.message.edit_text(
            "❌  Could not generate a code. Please try again.", reply_markup=admin_pin_kb(),
        )
    return ConversationHandler.END


@admin_only
async def admin_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_input = update.message.text.strip()
    label = None if raw_input.lower() in ("skip", "-", "none") else raw_input[:64]
    try:
        async with AsyncSessionLocal() as session:
            code = await AccessCodeRepository(session).create(label=label)
        await audit(update.effective_user, "create_code", f"{code.code} ({label or 'no label'})")
        await update.message.reply_text(
            _code_created_text(code), parse_mode="HTML", reply_markup=admin_pin_kb(),
        )
    except Exception:
        logger.exception("Error in admin_create_code")
        await update.message.reply_text("❌  Could not create the code. Please try again.", reply_markup=admin_pin_kb())
    return ConversationHandler.END


@admin_only
async def cb_list_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        codes = await AccessCodeRepository(session).get_all()

    if not codes:
        await update.callback_query.message.edit_text(
            "No access codes yet.\nTap ➕ Create to make one.",
            reply_markup=admin_pin_kb(),
        )
        return

    lines = ["📋  <b>ACCESS CODES</b>\n──────────────"]
    for code in codes[:40]:
        if code.used_by:
            status = "🟢 Used"
        elif code.is_active:
            status = "✅ Active"
        else:
            status = "❌ Revoked"
        lines.append(
            f"\n🔑  <code>{esc(code.code)}</code>  —  {status}\n"
            f"   🏷  {esc(code.label or '—')}\n"
            f"   👤  Used by: {code.used_by or 'Not yet'}"
        )
    if len(codes) > 40:
        lines.append(f"\n…and {len(codes) - 40} older codes")
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_pin_kb()
    )


@admin_only
async def cb_revoke_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        codes = await AccessCodeRepository(session).get_active_unused()

    if not codes:
        await update.callback_query.message.edit_text(
            "No active unused codes to revoke.", reply_markup=admin_pin_kb()
        )
        return ConversationHandler.END

    lines = ["Enter the <b>code</b> to revoke:\n"]
    for code in codes:
        lines.append(f"  <code>{esc(code.code)}</code>  —  {esc(code.label or '—')}")
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=cancel_conv_kb(),
    )
    return ADMIN_REVOKE_CODE


@admin_only
async def admin_revoke_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    async with AsyncSessionLocal() as session:
        success = await AccessCodeRepository(session).revoke_code(code)
    if success:
        await audit(update.effective_user, "revoke_code", code)
    msg = f"✅  Code <code>{esc(code)}</code> revoked." if success else f"❌  Code <code>{esc(code)}</code> not found."
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=admin_pin_kb())
    return ConversationHandler.END


@admin_only
async def cb_revoke_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "⛔  Enter the <b>Telegram User ID</b> to revoke.\n"
        "Revoked users cannot use the bot, and API stores cannot order for them.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_REVOKE_USER


@admin_only
async def admin_revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        telegram_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid ID. Send numbers only, or /cancel.")
        return ADMIN_REVOKE_USER
    async with AsyncSessionLocal() as session:
        success = await UserRepository(session).revoke(telegram_id)
    if success:
        await audit(update.effective_user, "revoke_user", str(telegram_id))
    msg = f"✅  User <code>{telegram_id}</code> revoked." if success else f"User <code>{telegram_id}</code> not found."
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=admin_pin_kb())
    return ConversationHandler.END


@admin_only
async def cb_reset_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        "🔄  Enter the <b>Telegram User ID</b> to reset.\n"
        "This signs them out and clears lockouts; a revoked user gets access back.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_RESET_USER


@admin_only
async def admin_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        telegram_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid ID. Send numbers only, or /cancel.")
        return ADMIN_RESET_USER
    async with AsyncSessionLocal() as session:
        success = await UserRepository(session).reset_auth(telegram_id)
    if success:
        await audit(update.effective_user, "reset_user", str(telegram_id))
    msg = f"✅  User <code>{telegram_id}</code> auth reset." if success else f"User <code>{telegram_id}</code> not found."
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=admin_pin_kb())
    return ConversationHandler.END
