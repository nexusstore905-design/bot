"""Inline menus shown in the admin control panel."""
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_main_kb() -> InlineKeyboardMarkup:
    is_on = not os.path.exists("maintenance.flag")
    status_btn = (
        InlineKeyboardButton("🟢  Service is on", callback_data="adm_toggle_power")
        if is_on else
        InlineKeyboardButton("🔴  Maintenance mode", callback_data="adm_toggle_power")
    )
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦  Orders", callback_data="adm_orders"),
         InlineKeyboardButton("📊  Overview", callback_data="adm_stats")],
        [InlineKeyboardButton("🛍  Products", callback_data="adm_products"),
         InlineKeyboardButton("👥  Customers", callback_data="adm_users")],
        [InlineKeyboardButton("🔐  Access codes", callback_data="adm_pin"),
         InlineKeyboardButton("🔑  API settings", callback_data="adm_api_info")],
        [InlineKeyboardButton("🏪  API stores", callback_data="adm_api_stores"),
         InlineKeyboardButton("🚦  Order limits", callback_data="adm_user_limits")],
        [status_btn],
        [InlineKeyboardButton("🏠  Customer menu", callback_data="main_menu")],
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
        [InlineKeyboardButton("◀  Back",          callback_data="admin_menu")],
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


def supplier_done_error_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  DONE",   callback_data=f"sup_done:{order_id}"),
        InlineKeyboardButton("❌  ERROR",  callback_data=f"sup_error:{order_id}"),
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
