import asyncio
import logging
import sys

from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

from config.settings import BOT_TOKEN, LOG_LEVEL, SUPPLIER_CHAT_ID
from database.database import init_db
from database.database import AsyncSessionLocal
from database.repositories.product_repo import ProductRepository

from bot.handlers.auth import (
    get_auth_conversation, cmd_logout,
    cb_main_menu, cb_logout, cb_help, cb_about
)
from bot.handlers.customer import (
    get_order_conversation, cmd_my_orders,
    cb_my_orders_btn, cb_refresh_order,
)
from bot.handlers.admin import (
    cmd_admin, cb_admin_menu, cb_admin_stats,
    cb_admin_orders, cb_orders_by_status,
    cb_set_status, cb_admin_users, cb_admin_products,
    cb_list_products, cb_remove_product, cb_remove_product_confirm,
    cb_admin_pin, cb_list_codes, cb_admin_api_info,
    cb_api_stores, cb_list_stores, cb_store_view, cb_store_toggle, cb_store_delete,
    cb_user_limits, cb_list_user_limits,
    get_admin_conversations,
)
from bot.handlers.supplier import get_supplier_handlers
from bot.auto_cancel import run_auto_cancel_worker

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main():
    # 1. Init database and seed products
    await init_db()
    async with AsyncSessionLocal() as session:
        await ProductRepository(session).seed_defaults()
    logger.info("Database ready.")
    logger.info(
        "Supplier routing: %s destination configured%s",
        "global" if SUPPLIER_CHAT_ID else "no global",
        "; category destinations override it" if SUPPLIER_CHAT_ID else "; category destinations are used when set",
    )
    logger.info("Pending orders will be auto-cancelled after 10 minutes; checking every 60 seconds.")

    # 2. Build bot app with Rate Limiting (Outbound DDoS protection)
    from telegram.ext import AIORateLimiter
    app = Application.builder().token(BOT_TOKEN).rate_limiter(AIORateLimiter()).build()
    
    # 3. Admin conversations (highest priority)
    for conv in get_admin_conversations():
        app.add_handler(conv)

    # 4. Auth conversation (/start → access code)
    app.add_handler(get_auth_conversation())

    # 5. Order conversation
    app.add_handler(get_order_conversation())

    # 6. Supplier message handler (both text and inline buttons)
    for handler in get_supplier_handlers():
        app.add_handler(handler)

    # 7. Commands
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("myorders", cmd_my_orders))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # 8. Callbacks — customer
    app.add_handler(CallbackQueryHandler(cb_my_orders_btn, pattern=r"^my_orders$"))
    app.add_handler(CallbackQueryHandler(cb_refresh_order, pattern=r"^refresh_order:"))
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_logout, pattern=r"^logout$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern=r"^help$"))
    app.add_handler(CallbackQueryHandler(cb_about, pattern=r"^about$"))

    # 9. Callbacks — admin
    app.add_handler(CallbackQueryHandler(cb_admin_menu, pattern=r"^admin_menu$"))
    app.add_handler(CallbackQueryHandler(cb_admin_stats, pattern=r"^adm_stats$"))
    app.add_handler(CallbackQueryHandler(cb_admin_orders, pattern=r"^adm_orders$"))
    app.add_handler(CallbackQueryHandler(cb_orders_by_status, pattern=r"^adm_orders_(pending|processing|completed|failed)$"))
    app.add_handler(CallbackQueryHandler(cb_set_status, pattern=r"^set_status:"))
    app.add_handler(CallbackQueryHandler(cb_admin_users, pattern=r"^adm_users$"))
    app.add_handler(CallbackQueryHandler(cb_admin_users, pattern=r"^adm_view_users$"))
    app.add_handler(CallbackQueryHandler(cb_admin_products, pattern=r"^adm_products$"))
    app.add_handler(CallbackQueryHandler(cb_list_products, pattern=r"^adm_list_products$"))
    app.add_handler(CallbackQueryHandler(cb_remove_product, pattern=r"^adm_remove_product$"))
    app.add_handler(CallbackQueryHandler(cb_remove_product_confirm, pattern=r"^rm_prod:"))
    app.add_handler(CallbackQueryHandler(cb_admin_pin, pattern=r"^adm_pin$"))
    app.add_handler(CallbackQueryHandler(cb_list_codes, pattern=r"^adm_list_codes$"))
    app.add_handler(CallbackQueryHandler(cb_admin_api_info, pattern=r"^adm_api_info$"))

    # 10. Callbacks — API Stores & User Limits
    app.add_handler(CallbackQueryHandler(cb_api_stores, pattern=r"^adm_api_stores$"))
    app.add_handler(CallbackQueryHandler(cb_list_stores, pattern=r"^adm_list_stores$"))
    app.add_handler(CallbackQueryHandler(cb_store_view, pattern=r"^store_view:"))
    app.add_handler(CallbackQueryHandler(cb_store_toggle, pattern=r"^store_toggle:"))
    app.add_handler(CallbackQueryHandler(cb_store_delete, pattern=r"^store_delete:"))
    app.add_handler(CallbackQueryHandler(cb_user_limits, pattern=r"^adm_user_limits$"))
    app.add_handler(CallbackQueryHandler(cb_list_user_limits, pattern=r"^adm_list_user_limits$"))

    # 10. Start Telegram Bot polling
    logger.info("Starting Telegram Bot...")
    
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("✅ Telegram Bot is running and listening for messages.")
        auto_cancel_task = asyncio.create_task(
            run_auto_cancel_worker(app.bot),
            name="pending-order-auto-cancel",
        )
        
        # Keep running continuously in background task
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            auto_cancel_task.cancel()
            try:
                await auto_cancel_task
            except asyncio.CancelledError:
                pass
            await app.updater.stop()
            await app.stop()
            logger.info("Telegram Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
