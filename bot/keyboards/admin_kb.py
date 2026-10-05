"""Inline menus shown in the admin control panel."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦  Orders", callback_data="adm_orders"),
         InlineKeyboardButton("📊  Dashboard", callback_data="adm_stats")],
        [InlineKeyboardButton("🛍  Products", callback_data="adm_products"),
         InlineKeyboardButton("👥  Customers", callback_data="adm_users")],
        [InlineKeyboardButton("🔐  Access codes", callback_data="adm_pin"),
         InlineKeyboardButton("🔑  API settings", callback_data="adm_api_info")],
        [InlineKeyboardButton("📣  Broadcast", callback_data="adm_broadcast"),
         InlineKeyboardButton("⚙️  Advanced", callback_data="adm_advanced")],
        [InlineKeyboardButton("🏠  Customer menu", callback_data="main_menu")],
    ])


def admin_advanced_kb(is_on: bool = True, prices_on: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪  API stores", callback_data="adm_api_stores"),
         InlineKeyboardButton("🚦  Order limits", callback_data="adm_user_limits")],
        [InlineKeyboardButton(
            "🟢  Service is on" if is_on else "🔴  Maintenance mode",
            callback_data="adm_toggle_power",
        )],
        [InlineKeyboardButton(
            "💲  Prices shown to customers" if prices_on else "🙈  Prices hidden from customers",
            callback_data="adm_toggle_prices",
        )],
        [InlineKeyboardButton("🧾  Admin activity log", callback_data="adm_audit")],
        [InlineKeyboardButton("🧨  Reset customers, orders & keys", callback_data="adm_reset_business_data")],
        [InlineKeyboardButton("◀  Admin menu", callback_data="admin_menu")],
    ])


def admin_dashboard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄  Refresh", callback_data="adm_stats"),
         InlineKeyboardButton("🤝  Supplier stats", callback_data="adm_supplier_stats")],
        [InlineKeyboardButton("📤  Export all orders (CSV)", callback_data="adm_export_orders")],
        [InlineKeyboardButton("◀  Admin menu", callback_data="admin_menu")],
    ])


def admin_orders_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕  Pending",     callback_data="adm_orders_pending"),
         InlineKeyboardButton("⚙️  Processing",  callback_data="adm_orders_processing")],
        [InlineKeyboardButton("✅  Completed",   callback_data="adm_orders_completed"),
         InlineKeyboardButton("❌  Failed",       callback_data="adm_orders_failed")],
        [InlineKeyboardButton("🚫  Cancelled",   callback_data="adm_orders_cancelled")],
        [InlineKeyboardButton("🔍  Find order", callback_data="adm_search_order")],
        [InlineKeyboardButton("◀  Back",         callback_data="admin_menu")],
    ])


def admin_products_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Add group & packages", callback_data="adm_add_product")],
        [InlineKeyboardButton("📡  Supplier routing", callback_data="adm_set_supplier"),
         InlineKeyboardButton("💲  Prices", callback_data="adm_set_price")],
        [InlineKeyboardButton("✏️  Edit product", callback_data="adm_edit_name"),
         InlineKeyboardButton("🗑  Remove",        callback_data="adm_remove_product")],
        [InlineKeyboardButton("📝  Rename product group", callback_data="adm_rename_group")],
        [InlineKeyboardButton("📋  Product list", callback_data="adm_list_products")],
        [InlineKeyboardButton("🧰  Advanced product tools", callback_data="adm_product_advanced")],
        [InlineKeyboardButton("◀  Back",          callback_data="admin_menu")],
    ])


def admin_product_advanced_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹  Clean removed products", callback_data="adm_cleanup_removed")],
        [InlineKeyboardButton("🧨  Delete all & reset IDs", callback_data="adm_reset_products")],
        [InlineKeyboardButton("◀  Products", callback_data="adm_products")],
    ])


def cleanup_removed_products_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹  Permanently clean unused products", callback_data="adm_cleanup_removed_confirm")],
        [InlineKeyboardButton("✖  Cancel", callback_data="adm_product_advanced")],
    ])


def admin_pin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Create Code",  callback_data="adm_create_code")],
        [InlineKeyboardButton("📋  All Codes",    callback_data="adm_list_codes"),
         InlineKeyboardButton("🗑  Revoke Code",  callback_data="adm_revoke_code")],
        [InlineKeyboardButton("👥  Users",        callback_data="adm_view_users")],
        [InlineKeyboardButton("⛔  Revoke User",  callback_data="adm_revoke_user"),
         InlineKeyboardButton("🔄  Reset User",   callback_data="adm_reset_user")],
        [InlineKeyboardButton("◀  Back",          callback_data="admin_menu")],
    ])


def admin_customers_kb(customers: list, page: int, total: int, page_size: int = 10) -> InlineKeyboardMarkup:
    rows = []
    for user, order_count in customers:
        name = user.full_name or (f"@{user.username}" if user.username else str(user.telegram_id))
        name = name.replace("\n", " ")[:24]
        rows.append([InlineKeyboardButton(
            f"👤 {name} · {order_count} orders",
            callback_data=f"adm_customer:{user.telegram_id}",
        )])

    total_pages = max(1, (total + page_size - 1) // page_size)
    if total_pages > 1:
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton("◀ Previous", callback_data=f"adm_customer_page:{page - 1}"))
        if page + 1 < total_pages:
            navigation.append(InlineKeyboardButton("Next ▶", callback_data=f"adm_customer_page:{page + 1}"))
        rows.append(navigation)
    rows.append([InlineKeyboardButton("◀  Admin menu", callback_data="admin_menu")])
    return InlineKeyboardMarkup(rows)


def admin_customer_detail_kb(telegram_id: int, unsettled_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"💰  Unsettled orders ({unsettled_count})",
            callback_data=f"adm_customer_unsettled:{telegram_id}:0",
        )],
        [InlineKeyboardButton("✅  Clear paid orders", callback_data=f"adm_customer_settle:{telegram_id}")],
        [InlineKeyboardButton("🧾  Order history", callback_data=f"adm_customer_orders:{telegram_id}:0"),
         InlineKeyboardButton("📤  Export CSV", callback_data=f"adm_customer_export:{telegram_id}")],
        [InlineKeyboardButton("📅  Count by date", callback_data=f"adm_customer_dates:{telegram_id}")],
        [InlineKeyboardButton("◀  Customers", callback_data="adm_users")],
    ])


def admin_customer_unsettled_kb(
    telegram_id: int, page: int, has_next: bool,
) -> InlineKeyboardMarkup:
    rows = []
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "◀ Previous", callback_data=f"adm_customer_unsettled:{telegram_id}:{page - 1}"
        ))
    if has_next:
        navigation.append(InlineKeyboardButton(
            "Next ▶", callback_data=f"adm_customer_unsettled:{telegram_id}:{page + 1}"
        ))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(
        "✅  Clear paid orders", callback_data=f"adm_customer_settle:{telegram_id}"
    )])
    rows.append([InlineKeyboardButton(
        "🧾  Full order history", callback_data=f"adm_customer_orders:{telegram_id}:0"
    )])
    rows.append([InlineKeyboardButton(
        "◀  Customer details", callback_data=f"adm_customer:{telegram_id}"
    )])
    return InlineKeyboardMarkup(rows)


def confirm_customer_settlement_kb(telegram_id: int, cutoff_token: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅  Confirm: mark paid", callback_data=f"adm_customer_settle_confirm:{telegram_id}:{cutoff_token}"
        )],
        [InlineKeyboardButton("✖  Cancel", callback_data=f"adm_customer:{telegram_id}")],
    ])


def admin_customer_orders_kb(telegram_id: int, page: int, has_next: bool) -> InlineKeyboardMarkup:
    rows = []
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "◀ Previous", callback_data=f"adm_customer_orders:{telegram_id}:{page - 1}"
        ))
    if has_next:
        navigation.append(InlineKeyboardButton(
            "Next ▶", callback_data=f"adm_customer_orders:{telegram_id}:{page + 1}"
        ))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("◀  Customer details", callback_data=f"adm_customer:{telegram_id}")])
    return InlineKeyboardMarkup(rows)


def customer_date_result_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀  Customer details", callback_data=f"adm_customer:{telegram_id}")],
        [InlineKeyboardButton("👥  Customers", callback_data="adm_users")],
    ])


def reset_all_products_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧨  Confirm: delete all and reset IDs", callback_data="adm_reset_products_confirm")],
        [InlineKeyboardButton("✖  Cancel", callback_data="adm_product_advanced")],
    ])


def reset_business_data_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🧨  Yes, permanently reset business data",
            callback_data="adm_reset_business_data_confirm",
        )],
        [InlineKeyboardButton("✖  Cancel", callback_data="adm_advanced")],
    ])


def create_code_options_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡  Instant Code (No Label)", callback_data="adm_code_instant")],
        [InlineKeyboardButton("✖  Cancel", callback_data="adm_cancel_conv")],
    ])


def remove_products_kb(products: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"🗑  {p.name}",
            callback_data=f"rm_prod:{p.id}"
        )]
        for p in products
    ]
    rows.append([InlineKeyboardButton("◀  Back", callback_data="adm_products")])
    return InlineKeyboardMarkup(rows)


def admin_order_actions_kb(order_id: str, is_open: bool, can_resend: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_open:
        rows.append([
            InlineKeyboardButton("✅  Mark completed", callback_data=f"set_status:{order_id}:completed"),
            InlineKeyboardButton("❌  Mark failed", callback_data=f"set_status:{order_id}:failed"),
        ])
        rows.append([InlineKeyboardButton("🚫  Cancel order", callback_data=f"set_status:{order_id}:cancelled")])
    else:
        rows.append([InlineKeyboardButton("✅  Mark completed", callback_data=f"set_status:{order_id}:completed")])
    if can_resend:
        rows.append([
            InlineKeyboardButton("📨  Resend to supplier", callback_data=f"adm_resend:{order_id}"),
            InlineKeyboardButton("🔀  Reassign", callback_data=f"adm_reassign:{order_id}"),
        ])
    rows.append([InlineKeyboardButton("◀  Orders", callback_data="adm_orders")])
    return InlineKeyboardMarkup(rows)


def supplier_done_error_kb(order_id: str, fulfillment_id: int | None = None) -> InlineKeyboardMarkup:
    action_suffix = f"{order_id}:{fulfillment_id}" if fulfillment_id is not None else order_id
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  DONE",   callback_data=f"sup_done:{action_suffix}"),
        InlineKeyboardButton("❌  ERROR",  callback_data=f"sup_error:{action_suffix}"),
    ]])


def api_stores_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Add Store",     callback_data="adm_add_store")],
        [InlineKeyboardButton("📋  View Stores",   callback_data="adm_list_stores")],
        [InlineKeyboardButton("◀  Back",           callback_data="adm_advanced")],
    ])


def store_actions_kb(store_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Disable" if is_active else "🟢 Enable"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_text, callback_data=f"store_toggle:{store_id}"),
         InlineKeyboardButton("📊 Set Limit", callback_data=f"store_limit:{store_id}")],
        [InlineKeyboardButton("🔔 Webhook", callback_data=f"store_webhook:{store_id}"),
         InlineKeyboardButton("👥 Customers", callback_data=f"store_customers:{store_id}")],
        [InlineKeyboardButton("♻️ Rotate key", callback_data=f"store_rotate:{store_id}"),
         InlineKeyboardButton("🗑 Delete", callback_data=f"store_delete:{store_id}")],
        [InlineKeyboardButton("◀  Back", callback_data="adm_list_stores")],
    ])


def confirm_kb(confirm_data: str, cancel_data: str, confirm_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(confirm_text, callback_data=confirm_data)],
        [InlineKeyboardButton("✖  Cancel", callback_data=cancel_data)],
    ])


def user_limits_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚦  Set User Limit",   callback_data="adm_set_user_limit")],
        [InlineKeyboardButton("📋  View Limits",       callback_data="adm_list_user_limits")],
        [InlineKeyboardButton("◀  Back",               callback_data="adm_advanced")],
    ])


def cancel_conv_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖  Cancel", callback_data="adm_cancel_conv")]])
