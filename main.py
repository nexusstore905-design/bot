import asyncio
import logging
import sys

from telegram.ext import (
    AIORateLimiter, Application, CallbackQueryHandler, CommandHandler,
)

from config.settings import BOT_TOKEN, LOG_LEVEL, SUPPLIER_CHAT_ID, SUPPLIER_TIMEOUT_MINUTES
from database.database import AsyncSessionLocal, init_db
from database.repositories.product_repo import ProductRepository

from bot.handlers.admin import cmd_admin, get_admin_callback_handlers, get_admin_conversation
from bot.handlers.auth import (
    get_auth_conversation, cmd_logout,
    cb_main_menu, cb_logout, cb_help, cb_about
)
from bot.handlers.customer import (
    get_order_conversation, cmd_my_orders,
    cb_my_orders_btn, cb_my_orders_page, cb_view_order, cb_refresh_order, cb_balance,
)
from bot.handlers.supplier import get_supplier_handlers
from bot.handlers.support import get_admin_support_reply_handler, get_support_conversation
from bot.workers import on_error, run_background_workers

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
# httpx logs every Telegram request URL, which contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).build()

    # Conversations first: while a user is mid-flow, their replies belong to it.
    app.add_handler(get_admin_conversation())
    app.add_handler(get_auth_conversation())
    app.add_handler(get_order_conversation())
    app.add_handler(get_support_conversation())

    # Admin replies to support requests, then supplier group commands/buttons/proof photos.
    app.add_handler(get_admin_support_reply_handler())
    for handler in get_supplier_handlers():
        app.add_handler(handler)

    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("myorders", cmd_my_orders))
    app.add_handler(CommandHandler("admin", cmd_admin))

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
