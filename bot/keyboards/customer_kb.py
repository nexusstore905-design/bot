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
        InlineKeyboardButton("◀  Product groups", callback_data="order_start"),
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
        [InlineKeyboardButton("◀  Product packages", callback_data="back_products"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel_order")],
    ])


def cart_kb(cart: list[dict] | None = None) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(cart or []):
        name = str(item.get("product_name", "Package"))
        if len(name) > 18:
            name = name[:17] + "…"
        quantity = int(item.get("quantity", 1))
        rows.append([
            InlineKeyboardButton(f"📦 {name} ×{quantity}", callback_data=f"cart_item:{index}"),
        ])
        rows.append([
            InlineKeyboardButton("−", callback_data=f"cart_qty:{index}:{max(1, quantity - 1)}"),
            InlineKeyboardButton(f"Qty {quantity}", callback_data=f"cart_item:{index}"),
            InlineKeyboardButton("+", callback_data=f"cart_qty:{index}:{min(99, quantity + 1)}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"cart_remove:{index}"),
        ])
    rows.extend([
        [InlineKeyboardButton("✅  Continue to checkout", callback_data="cart_checkout")],
        [InlineKeyboardButton("➕  Add another item", callback_data="order_start")],
        [InlineKeyboardButton("🗑  Clear cart", callback_data="cart_clear"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel_order")],
    ])
    return InlineKeyboardMarkup(rows)

def confirm_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀  Submit order", callback_data="confirm_order")],
        [InlineKeyboardButton("↩️  Start over", callback_data="reset_order_products")],
        [InlineKeyboardButton("✏️  Change Player ID", callback_data="edit_player_id"),
         InlineKeyboardButton("✖  Cancel", callback_data="cancel_order")],
    ])


def order_status_kb(order_id: str, page: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄  Refresh status", callback_data=f"refresh_order:{order_id}:{page}")],
        [InlineKeyboardButton("📋  My orders", callback_data=f"my_orders_page:{page}"),
         InlineKeyboardButton("🏠  Menu", callback_data="main_menu")],
    ])


def my_orders_kb(orders: list, page: int, total: int, page_size: int = 5) -> InlineKeyboardMarkup:
    rows = []
    for order in orders:
        rows.append([InlineKeyboardButton(
            f"{order.order_id} · {order.status.value.title()}",
            callback_data=f"view_order:{order.order_id}:{page}",
        )])

    page_count = max(1, (total + page_size - 1) // page_size)
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("◀ Previous", callback_data=f"my_orders_page:{page - 1}"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton("Next ▶", callback_data=f"my_orders_page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("🏠  Main menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)
