"""Customer home screen, help, about, and language choice."""
from telegram import Update
from telegram.ext import ContextTypes

from bot.i18n import LANGUAGES, t
from bot.keyboards.customer_kb import back_to_menu_kb, language_kb, main_menu_kb
from bot.middlewares.auth_middleware import is_team, require_callback_auth, user_language
from config.settings import STORE_NAME
from database.database import AsyncSessionLocal
from database.models import TERMINAL_ORDER_STATUSES
from database.repositories.order_repo import OrderRepository
from database.repositories.user_repo import UserRepository
from services.messages import status_label
from utils.ui import esc, header, quote


async def render_home(telegram_user, lang: str, returning: bool = True, notice: str | None = None):
    """Home text and keyboard with the customer's live numbers."""
    async with AsyncSessionLocal() as session:
        db_user = await UserRepository(session).get_by_telegram_id(telegram_user.id)
        summary = await OrderRepository(session).customer_summary(db_user.id) if db_user else None

    greeting = t(lang, "home_welcome_back" if returning else "home_welcome", name=telegram_user.first_name or "")
    lines = [header("✨", STORE_NAME.strip(), esc(greeting))]
    if notice:
        lines += ["", notice]
    reorder_id = None
    if summary and summary["latest"] is not None:
        latest = summary["latest"]
        lines.append(quote(t(lang, "home_stats", active=summary["active"], due=summary["due"])))
        lines.append(t(
            lang, "home_last", order_id=latest.order_id,
            raw_status=status_label(latest.status.value, lang),
        ))
        if latest.status in TERMINAL_ORDER_STATUSES:
            reorder_id = latest.order_id
    else:
        lines += ["", t(lang, "home_empty")]
    return "\n".join(lines), main_menu_kb(lang, reorder_id)


async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE, returning: bool = True, notice: str | None = None):
    lang = user_language(context, update.effective_user)
    text, keyboard = await render_home(update.effective_user, lang, returning, notice)
    await update.effective_chat.send_message(text, parse_mode="HTML", reply_markup=keyboard)


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    lang = user_language(context, update.effective_user)
    text, keyboard = await render_home(update.effective_user, lang)
    await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    lang = user_language(context, update.effective_user)
    await update.callback_query.message.edit_text(
        t(lang, "help"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = user_language(context, update.effective_user)
    await update.message.reply_text(t(lang, "help"), parse_mode="HTML", reply_markup=back_to_menu_kb(lang))


async def cb_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_callback_auth(update, context):
        return
    lang = user_language(context, update.effective_user)
    await update.callback_query.message.edit_text(
        t(lang, "about", store=STORE_NAME), parse_mode="HTML", reply_markup=back_to_menu_kb(lang),
    )


async def cb_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(
        f"{t('en', 'lang_pick')}\n{t('ur', 'lang_pick')}", parse_mode="HTML", reply_markup=language_kb(),
    )


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{t('en', 'lang_pick')}\n{t('ur', 'lang_pick')}", parse_mode="HTML", reply_markup=language_kb(),
    )


async def cb_set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = update.callback_query.data.split(":", 1)[1]
    if lang not in LANGUAGES:
        await update.callback_query.answer()
        return
    await update.callback_query.answer(t(lang, "lang_set"))
    context.user_data["lang"] = lang
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        db_user = await repo.get_or_create(user.id, user.username, user.full_name)
        await repo.set_language(db_user, lang)
        signed_in = is_team(user.id) or await repo.is_session_valid(db_user)
    if signed_in:
        text, keyboard = await render_home(user, lang)
        await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        from bot.handlers.auth import signin_text
        await update.callback_query.message.edit_text(signin_text(lang), parse_mode="HTML")


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
