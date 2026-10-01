"""Inline menus shown in the admin control panel."""
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦  Orders", callback_data="adm_orders"),
         InlineKeyboardButton("📊  Overview", callback_data="adm_stats")],
        [InlineKeyboardButton("🛍  Products", callback_data="adm_products"),
         InlineKeyboardButton("👥  Customers", callback_data="adm_users")],
        [InlineKeyboardButton("🔐  Access codes", callback_data="adm_pin"),
         InlineKeyboardButton("🔑  API settings", callback_data="adm_api_info")],
        [InlineKeyboardButton("⚙️  Advanced settings", callback_data="adm_advanced")],
        [InlineKeyboardButton("🏠  Customer menu", callback_data="main_menu")],
    ])


def admin_advanced_kb(is_on: bool | None = None) -> InlineKeyboardMarkup:
    if is_on is None:
        is_on = not os.path.exists("maintenance.flag")
    status_btn = InlineKeyboardButton(
        "🟢  Service is on" if is_on else "🔴  Maintenance mode",
        callback_data="adm_toggle_power",
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪  API stores", callback_data="adm_api_stores"),
         InlineKeyboardButton("🚦  Order limits", callback_data="adm_user_limits")],
        [status_btn],
        [InlineKeyboardButton("◀  Admin menu", callback_data="admin_menu")],
    ])


def admin_orders_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕  Pending",     callback_data="adm_orders_pending"),
         InlineKeyboardButton("⚙️  Processing",  callback_data="adm_orders_processing")],
        [InlineKeyboardButton("✅  Completed",   callback_data="adm_orders_completed"),
         InlineKeyboardButton("❌  Failed",       callback_data="adm_orders_failed")],
        [InlineKeyboardButton("🔍  Find order", callback_data="adm_search_order")],
        [InlineKeyboardButton("◀  Back",         callback_data="admin_menu")],
    ])


def admin_products_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Add group & packages", callback_data="adm_add_product")],
        [InlineKeyboardButton("📡  Supplier groups", callback_data="adm_set_supplier")],
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


def admin_customer_detail_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾  Order history", callback_data=f"adm_customer_orders:{telegram_id}:0")],
        [InlineKeyboardButton("📅  Count by date", callback_data=f"adm_customer_dates:{telegram_id}")],
        [InlineKeyboardButton("◀  Customers", callback_data="adm_users")],
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


def change_status_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳  Pending",      callback_data=f"set_status:{order_id}:pending"),
         InlineKeyboardButton("⚙️  Processing",  callback_data=f"set_status:{order_id}:processing")],
        [InlineKeyboardButton("✅  Completed",    callback_data=f"set_status:{order_id}:completed"),
         InlineKeyboardButton("❌  Failed",        callback_data=f"set_status:{order_id}:failed")],
        [InlineKeyboardButton("🚫  Cancelled",    callback_data=f"set_status:{order_id}:cancelled")],
        [InlineKeyboardButton("◀  Back",          callback_data="adm_orders")],
    ])


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
        [InlineKeyboardButton("◀  Back",           callback_data="admin_menu")],
    ])


def store_actions_kb(store_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Disable" if is_active else "🟢 Enable"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_text, callback_data=f"store_toggle:{store_id}"),
         InlineKeyboardButton("📊 Set Limit", callback_data=f"store_limit:{store_id}")],
        [InlineKeyboardButton("🗑 Delete Store", callback_data=f"store_delete:{store_id}")],
        [InlineKeyboardButton("◀  Back", callback_data="adm_list_stores")],
    ])


def user_limits_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚦  Set User Limit",   callback_data="adm_set_user_limit")],
        [InlineKeyboardButton("📋  View Limits",       callback_data="adm_list_user_limits")],
        [InlineKeyboardButton("◀  Back",               callback_data="admin_menu")],
    ])
