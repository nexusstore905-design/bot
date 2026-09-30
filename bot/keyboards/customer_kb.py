"""Inline menus shown to customers."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨  New order", callback_data="order_start")],
        [InlineKeyboardButton("📦  My orders", callback_data="my_orders"),
         InlineKeyboardButton("💬  Help", callback_data="help")],
        [InlineKeyboardButton("🏪  About", callback_data="about"),
         InlineKeyboardButton("🔓  Sign out", callback_data="logout")],
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
        InlineKeyboardButton("🏠  Menu", callback_data="main_menu"),
        InlineKeyboardButton("✖  Cancel", callback_data="cancel_order"),
    ])
    return InlineKeyboardMarkup(rows)


def products_kb(products: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"💎  {p.name}",
            callback_data=f"prod:{p.id}"
        )]
        for p in products
    ]
    rows.append([
        InlineKeyboardButton("◀  Catalog", callback_data="order_start"),
        InlineKeyboardButton("✖  Cancel", callback_data="cancel_order"),
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
        [InlineKeyboardButton("◀  Products", callback_data="order_start"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel_order")],
    ])


def cart_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅  Continue to checkout", callback_data="cart_checkout")],
        [InlineKeyboardButton("➕  Add another item", callback_data="order_start")],
        [InlineKeyboardButton("🗑  Clear cart", callback_data="cart_clear"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel_order")],
    ])

def confirm_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀  Submit order", callback_data="confirm_order")],
        [InlineKeyboardButton("✏️  Change Player ID", callback_data="edit_player_id"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel_order")],
    ])


def order_status_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄  Refresh status", callback_data=f"refresh_order:{order_id}")],
        [InlineKeyboardButton("📋  My orders", callback_data="my_orders"),
         InlineKeyboardButton("🏠  Menu", callback_data="main_menu")],
    ])
