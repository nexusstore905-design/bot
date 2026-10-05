import asyncio
import logging
import sys

from telegram import BotCommand, BotCommandScopeChat
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from bot.handlers.admin import cmd_admin, get_admin_callback_handlers, get_admin_conversation
from bot.handlers.auth import cb_logout, cmd_logout, get_auth_conversation
from bot.handlers.customer import (
    cb_balance,
    cb_my_orders_btn,
    cb_my_orders_page,
    cb_refresh_order,
    cb_view_order,
    cmd_my_orders,
    get_order_conversation,
)
from bot.handlers.home import (
    cb_about,
    cb_help,
    cb_language,
    cb_main_menu,
    cb_noop,
    cb_set_language,
    cmd_help,
    cmd_language,
)
from bot.handlers.supplier import get_supplier_handlers
from bot.handlers.support import get_support_conversation, get_team_support_reply_handler
from bot.i18n import LANGUAGES, t
from bot.workers import on_error, run_background_workers
from config.settings import BOT_TOKEN, LOG_LEVEL, SUPPLIER_CHAT_ID, SUPPLIER_TIMEOUT_MINUTES, TEAM_IDS
from database.database import AsyncSessionLocal, init_db
from database.repositories.product_repo import ProductRepository

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
# httpx logs every Telegram request URL, which contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CUSTOMER_COMMANDS = ("start", "myorders", "support", "language", "help", "cancel")


def _commands(lang: str) -> list[BotCommand]:
    return [BotCommand(name, t(lang, f"cmd_{name}")) for name in CUSTOMER_COMMANDS]


async def configure_bot_profile(app: Application) -> None:
    """Fill Telegram's Menu button and the bot's profile text, per language."""
    bot = app.bot
    try:
        for lang in LANGUAGES:
            code = None if lang == "en" else lang
            await bot.set_my_commands(_commands(lang), language_code=code)
            await bot.set_my_description(t(lang, "bot_description"), language_code=code)
            await bot.set_my_short_description(t(lang, "bot_short_description"), language_code=code)
        team_commands = _commands("en") + [BotCommand("admin", "Open the control panel")]
        for team_id in TEAM_IDS:
            await bot.set_my_commands(team_commands, scope=BotCommandScopeChat(team_id))
    except Exception as exc:
        # Cosmetic: never block start-up on profile updates.
        logger.warning("Could not update bot commands/description (%s)", type(exc).__name__)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).build()

    # Conversations first: while a user is mid-flow, their replies belong to it.
    app.add_handler(get_admin_conversation())
    app.add_handler(get_auth_conversation())
    app.add_handler(get_order_conversation())
    app.add_handler(get_support_conversation())

    # Team replies to support requests, then supplier group commands/buttons/proof photos.
    app.add_handler(get_team_support_reply_handler())
    for handler in get_supplier_handlers():
        app.add_handler(handler)

    for name, handler in [
        ("logout", cmd_logout), ("myorders", cmd_my_orders), ("admin", cmd_admin),
        ("help", cmd_help), ("language", cmd_language),
    ]:
        app.add_handler(CommandHandler(name, handler))

    customer_routes = [
        (cb_my_orders_btn, r"^my_orders$"),
        (cb_my_orders_page, r"^my_orders_page:\d+$"),
        (cb_view_order, r"^view_order:"),
        (cb_refresh_order, r"^refresh_order:"),
        (cb_balance, r"^balance$"),
        (cb_main_menu, r"^main_menu$"),
        (cb_logout, r"^logout$"),
        (cb_help, r"^help$"),
        (cb_about, r"^about$"),
        (cb_language, r"^language$"),
        (cb_set_language, r"^set_lang:\w+$"),
        (cb_noop, r"^noop$"),
    ]
    for handler, pattern in customer_routes:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))
    for handler in get_admin_callback_handlers():
        app.add_handler(handler)

    app.add_error_handler(on_error)
    return app


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        await ProductRepository(session).seed_defaults()
    logger.info("Database ready.")
    logger.info(
        "Supplier routing: %s; unanswered supplier work expires %s minutes after delivery.",
        "global fallback configured" if SUPPLIER_CHAT_ID else "package routes only",
        SUPPLIER_TIMEOUT_MINUTES,
    )

    app = build_application()
    logger.info("Starting Telegram Bot...")
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("✅ Telegram Bot is running and listening for messages.")
        await configure_bot_profile(app)
        worker = asyncio.create_task(run_background_workers(app.bot), name="background-worker")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            await app.updater.stop()
            await app.stop()
            logger.info("Telegram Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
