"""
Professional customer keyboards with clean, attractive layout.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config.settings import CURRENCY, STORE_NAME


# ─── Decorative separators used in messages ───────────────────────────
SEP = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"
SEP_THIN = "─────────────────────────"


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒  Place an Order", callback_data="order_start")],
        [InlineKeyboardButton("📦  My Orders",     callback_data="my_orders"),
         InlineKeyboardButton("💬  Support",        callback_data="help")],
        [InlineKeyboardButton("ℹ️  About Store",   callback_data="about"),
         InlineKeyboardButton("🔓  Logout",         callback_data="logout")],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠  Main Menu", callback_data="main_menu")
    ]])


def categories_kb(categories: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🎮  {c}", callback_data=f"cat:{c}")]
        for c in categories
    ]
    rows.append([
        InlineKeyboardButton("🏠  Main Menu", callback_data="main_menu"),
        InlineKeyboardButton("✖  Cancel",    callback_data="cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def products_kb(products: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"💎  {p.name}  ·  {CURRENCY} {p.price:.2f}",
            callback_data=f"prod:{p.id}"
        )]
        for p in products
    ]
    rows.append([
        InlineKeyboardButton("◀  Back",       callback_data="order_start"),
        InlineKeyboardButton("✖  Cancel",    callback_data="cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def quantity_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣", callback_data="qty:1"),
         InlineKeyboardButton("2️⃣", callback_data="qty:2"),
         InlineKeyboardButton("3️⃣", callback_data="qty:3")],
        [InlineKeyboardButton("4️⃣", callback_data="qty:4"),
         InlineKeyboardButton("5️⃣", callback_data="qty:5"),
         InlineKeyboardButton("🔟", callback_data="qty:10")],
        [InlineKeyboardButton("◀  Back", callback_data="order_start"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel")],
    ])

def cart_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒  Checkout Now", callback_data="cart_checkout")],
        [InlineKeyboardButton("➕  Add Another Package", callback_data="order_start")],
        [InlineKeyboardButton("🗑  Clear Cart", callback_data="cart_clear"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel")],
    ])

def confirm_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅  Confirm & Submit", callback_data="confirm_order")],
        [InlineKeyboardButton("✏️  Edit Player ID",  callback_data="order_start"),
         InlineKeyboardButton("✖  Cancel",          callback_data="cancel")],
    ])


def order_status_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄  Refresh Status",  callback_data=f"refresh_order:{order_id}")],
        [InlineKeyboardButton("📋  All My Orders",   callback_data="my_orders"),
         InlineKeyboardButton("🏠  Main Menu",       callback_data="main_menu")],
    ])
