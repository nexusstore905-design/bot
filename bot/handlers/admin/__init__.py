"""Admin panel. Every handler is gated by the ADMIN_IDS Telegram ID check."""
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from bot.handlers.admin import (
    access, broadcast, customers, dashboard, limits, menu, orders, products, stores,
)
from bot.states.states import (
    ADMIN_ADD_CAT, ADMIN_ADD_NAME, ADMIN_ADD_STORE_NAME, ADMIN_BROADCAST_CONFIRM,
    ADMIN_BROADCAST_MESSAGE, ADMIN_CREATE_CODE_LABEL, ADMIN_CUSTOMER_DATE_RANGE,
    ADMIN_EDIT_NAME_SELECT, ADMIN_EDIT_NAME_VALUE, ADMIN_REASSIGN_ORDER, ADMIN_RENAME_GROUP_SELECT,
    ADMIN_RENAME_GROUP_VALUE, ADMIN_RESET_USER, ADMIN_REVOKE_CODE, ADMIN_REVOKE_USER,
    ADMIN_SEARCH_ORDER, ADMIN_SET_PRICE, ADMIN_SET_SUPPLIER, ADMIN_SET_USER_LIMIT_ID,
    ADMIN_SET_USER_LIMIT_VALUE, ADMIN_STORE_CUSTOMERS, ADMIN_STORE_SET_LIMIT, ADMIN_STORE_WEBHOOK,
)

cmd_admin = menu.cmd_admin

TEXT = filters.TEXT & ~filters.COMMAND


def get_admin_conversation() -> ConversationHandler:
    # Keep all admin workflows in one conversation. Separate ConversationHandlers
    # can remain active together, and the first one registered then steals replies
    # from later workflows.
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(orders.cb_search_order_start, pattern=r"^adm_search_order$"),
            CallbackQueryHandler(orders.cb_reassign_start, pattern=r"^adm_reassign:"),
            CallbackQueryHandler(products.cb_add_product_start, pattern=r"^adm_add_product$"),
            CallbackQueryHandler(products.cb_set_supplier_start, pattern=r"^adm_set_supplier$"),
            CallbackQueryHandler(products.cb_set_price_start, pattern=r"^adm_set_price$"),
            CallbackQueryHandler(products.cb_edit_name_start, pattern=r"^adm_edit_name$"),
            CallbackQueryHandler(products.cb_rename_group_start, pattern=r"^adm_rename_group$"),
            CallbackQueryHandler(access.cb_create_code_start, pattern=r"^adm_create_code$"),
            CallbackQueryHandler(access.cb_revoke_code_start, pattern=r"^adm_revoke_code$"),
            CallbackQueryHandler(access.cb_revoke_user_start, pattern=r"^adm_revoke_user$"),
            CallbackQueryHandler(access.cb_reset_user_start, pattern=r"^adm_reset_user$"),
            CallbackQueryHandler(stores.cb_add_store_start, pattern=r"^adm_add_store$"),
            CallbackQueryHandler(stores.cb_store_limit_start, pattern=r"^store_limit:\d+$"),
            CallbackQueryHandler(stores.cb_store_webhook_start, pattern=r"^store_webhook:\d+$"),
            CallbackQueryHandler(stores.cb_store_customers_start, pattern=r"^store_customers:\d+$"),
            CallbackQueryHandler(limits.cb_set_user_limit_start, pattern=r"^adm_set_user_limit$"),
            CallbackQueryHandler(customers.cb_customer_dates_start, pattern=r"^adm_customer_dates:\d+$"),
            CallbackQueryHandler(broadcast.cb_broadcast_start, pattern=r"^adm_broadcast$"),
        ],
        states={
            ADMIN_SEARCH_ORDER: [MessageHandler(TEXT, orders.admin_search_order)],
            ADMIN_REASSIGN_ORDER: [
                MessageHandler(filters.FORWARDED, orders.admin_reassign_value),
                MessageHandler(TEXT, orders.admin_reassign_value),
            ],
            ADMIN_CUSTOMER_DATE_RANGE: [MessageHandler(TEXT, customers.admin_customer_date_range)],
            ADMIN_ADD_CAT: [MessageHandler(TEXT, products.admin_add_cat)],
            ADMIN_ADD_NAME: [MessageHandler(TEXT, products.admin_add_name)],
            ADMIN_SET_PRICE: [MessageHandler(TEXT, products.admin_set_price_value)],
            ADMIN_SET_SUPPLIER: [
                MessageHandler(filters.FORWARDED, products.admin_set_supplier_from_forward),
                MessageHandler(TEXT, products.admin_set_supplier_value),
            ],
            ADMIN_EDIT_NAME_SELECT: [MessageHandler(TEXT, products.admin_edit_name_select)],
            ADMIN_EDIT_NAME_VALUE: [MessageHandler(TEXT, products.admin_edit_name_value)],
            ADMIN_RENAME_GROUP_SELECT: [MessageHandler(TEXT, products.admin_rename_group_select)],
            ADMIN_RENAME_GROUP_VALUE: [MessageHandler(TEXT, products.admin_rename_group_value)],
            ADMIN_CREATE_CODE_LABEL: [
                CallbackQueryHandler(access.cb_create_code_instant, pattern=r"^adm_code_instant$"),
                MessageHandler(TEXT, access.admin_create_code),
            ],
            ADMIN_REVOKE_CODE: [MessageHandler(TEXT, access.admin_revoke_code)],
            ADMIN_REVOKE_USER: [MessageHandler(TEXT, access.admin_revoke_user)],
            ADMIN_RESET_USER: [MessageHandler(TEXT, access.admin_reset_user)],
            ADMIN_ADD_STORE_NAME: [MessageHandler(TEXT, stores.admin_add_store_name)],
            ADMIN_STORE_SET_LIMIT: [MessageHandler(TEXT, stores.admin_store_set_limit)],
            ADMIN_STORE_WEBHOOK: [MessageHandler(TEXT, stores.admin_store_webhook_value)],
            ADMIN_STORE_CUSTOMERS: [MessageHandler(TEXT, stores.admin_store_customers_value)],
            ADMIN_SET_USER_LIMIT_ID: [MessageHandler(TEXT, limits.admin_set_user_limit_id)],
            ADMIN_SET_USER_LIMIT_VALUE: [MessageHandler(TEXT, limits.admin_set_user_limit_value)],
            ADMIN_BROADCAST_MESSAGE: [MessageHandler(~filters.COMMAND, broadcast.admin_broadcast_message)],
            ADMIN_BROADCAST_CONFIRM: [
                CallbackQueryHandler(broadcast.cb_broadcast_confirm, pattern=r"^adm_broadcast_confirm$"),
            ],
        },
        fallbacks=[
            CommandHandler(["cancel", "admin", "start"], menu.admin_cancel),
            CallbackQueryHandler(menu.cb_admin_cancel_conv, pattern=r"^adm_cancel_conv$"),
        ],
        allow_reentry=True,
        per_message=False,
    )


def get_admin_callback_handlers() -> list:
    routes = [
        (menu.cb_admin_menu, r"^admin_menu$"),
        (menu.cb_admin_advanced, r"^adm_advanced$"),
        (menu.cb_toggle_power, r"^adm_toggle_power$"),
        (menu.cb_toggle_prices, r"^adm_toggle_prices$"),
        (menu.cb_reset_business_data_start, r"^adm_reset_business_data$"),
        (menu.cb_reset_business_data_confirm, r"^adm_reset_business_data_confirm$"),
        (menu.cb_admin_api_info, r"^adm_api_info$"),
        (menu.cb_admin_cancel_conv, r"^adm_cancel_conv$"),
        (dashboard.cb_admin_stats, r"^adm_stats$"),
        (dashboard.cb_supplier_stats, r"^adm_supplier_stats$"),
        (dashboard.cb_export_orders, r"^adm_export_orders$"),
        (dashboard.cb_audit_log, r"^adm_audit$"),
        (orders.cb_admin_orders, r"^adm_orders$"),
        (orders.cb_orders_by_status, r"^adm_orders_(pending|processing|completed|failed|cancelled)$"),
        (orders.cb_set_status, r"^set_status:"),
        (orders.cb_resend, r"^adm_resend:"),
        (customers.cb_admin_users, r"^adm_(users|view_users)$"),
        (customers.cb_customer_page, r"^adm_customer_page:\d+$"),
        (customers.cb_customer_details, r"^adm_customer:\d+$"),
        (customers.cb_customer_orders, r"^adm_customer_orders:\d+:\d+$"),
        (customers.cb_customer_unsettled, r"^adm_customer_unsettled:\d+:\d+$"),
        (customers.cb_customer_settle_start, r"^adm_customer_settle:\d+$"),
        (customers.cb_customer_settle_confirm, r"^adm_customer_settle_confirm:\d+:\d+$"),
        (customers.cb_customer_export, r"^adm_customer_export:\d+$"),
        (products.cb_admin_products, r"^adm_products$"),
        (products.cb_admin_product_advanced, r"^adm_product_advanced$"),
        (products.cb_list_products, r"^adm_list_products$"),
        (products.cb_cleanup_removed_start, r"^adm_cleanup_removed$"),
        (products.cb_cleanup_removed_confirm, r"^adm_cleanup_removed_confirm$"),
        (products.cb_reset_all_products_start, r"^adm_reset_products$"),
        (products.cb_reset_all_products_confirm, r"^adm_reset_products_confirm$"),
        (products.cb_remove_product, r"^adm_remove_product$"),
        (products.cb_remove_product_confirm, r"^rm_prod:\d+$"),
        (access.cb_admin_pin, r"^adm_pin$"),
        (access.cb_list_codes, r"^adm_list_codes$"),
        (stores.cb_api_stores, r"^adm_api_stores$"),
        (stores.cb_list_stores, r"^adm_list_stores$"),
        (stores.cb_store_view, r"^store_view:\d+$"),
        (stores.cb_store_toggle, r"^store_toggle:\d+$"),
        (stores.cb_store_rotate, r"^store_rotate:\d+$"),
        (stores.cb_store_rotate_confirm, r"^store_rotate_ok:\d+$"),
        (stores.cb_store_delete, r"^store_delete:\d+$"),
        (stores.cb_store_delete_confirm, r"^store_delete_ok:\d+$"),
        (limits.cb_user_limits, r"^adm_user_limits$"),
        (limits.cb_list_user_limits, r"^adm_list_user_limits$"),
    ]
    return [CallbackQueryHandler(handler, pattern=pattern) for handler, pattern in routes]
