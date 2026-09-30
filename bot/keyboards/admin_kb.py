"""
Professional admin keyboards.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


import os

def admin_main_kb() -> InlineKeyboardMarkup:
    is_on = not os.path.exists("maintenance.flag")
    status_btn = InlineKeyboardButton("🟢 Bot: ON", callback_data="adm_toggle_power") if is_on else InlineKeyboardButton("🔴 Bot: OFF (Maintenance)", callback_data="adm_toggle_power")
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦  Orders",         callback_data="adm_orders"),
         InlineKeyboardButton("📊  Statistics",     callback_data="adm_stats")],
        [InlineKeyboardButton("🛍  Products",        callback_data="adm_products"),
         InlineKeyboardButton("👥  Users",           callback_data="adm_users")],
        [InlineKeyboardButton("🔐  Access Codes",   callback_data="adm_pin"),
         InlineKeyboardButton("🔑  API Settings",   callback_data="adm_api_info")],
        [InlineKeyboardButton("🏪  API Stores",     callback_data="adm_api_stores"),
         InlineKeyboardButton("🚦  User Limits",    callback_data="adm_user_limits")],
        [status_btn],
        [InlineKeyboardButton("🏠  Customer View",  callback_data="main_menu")],
    ])


def admin_orders_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕  Pending",     callback_data="adm_orders_pending"),
         InlineKeyboardButton("⚙️  Processing",  callback_data="adm_orders_processing")],
        [InlineKeyboardButton("✅  Completed",   callback_data="adm_orders_completed"),
         InlineKeyboardButton("❌  Failed",       callback_data="adm_orders_failed")],
        [InlineKeyboardButton("🔍  Search Order",callback_data="adm_search_order")],
        [InlineKeyboardButton("◀  Back",         callback_data="admin_menu")],
    ])


def admin_products_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Add Product",  callback_data="adm_add_product")],
        [InlineKeyboardButton("📡  Set Supplier Group", callback_data="adm_set_supplier")],
        [InlineKeyboardButton("✏️  Edit Name/UC", callback_data="adm_edit_name"),
         InlineKeyboardButton("🗑  Remove",        callback_data="adm_remove_product")],
        [InlineKeyboardButton("📋  View All",     callback_data="adm_list_products")],
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
