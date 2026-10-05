"""Inline menus shown to customers, in their language."""
from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup

from bot.i18n import LANGUAGES, t

CATALOG_PAGE_SIZE = 10


def _grid(buttons: list[Button], columns: int = 2) -> list[list[Button]]:
    return [buttons[i:i + columns] for i in range(0, len(buttons), columns)]


def _pager(prefix: str, page: int, total: int, page_size: int) -> list[Button]:
    pages = max(1, (total + page_size - 1) // page_size)
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(Button("◀", callback_data=f"{prefix}:{page - 1}"))
    row.append(Button(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        row.append(Button("▶", callback_data=f"{prefix}:{page + 1}"))
    return row


def main_menu_kb(lang: str | None = None, reorder_order_id: str | None = None) -> InlineKeyboardMarkup:
    rows = [[Button(t(lang, "btn_new_order"), callback_data="order_start")]]
    second = [Button(t(lang, "btn_my_orders"), callback_data="my_orders")]
    if reorder_order_id:
        second.append(Button(t(lang, "btn_reorder"), callback_data=f"reorder:{reorder_order_id}"))
    rows.append(second)
    rows.append([
        Button(t(lang, "btn_balance"), callback_data="balance"),
        Button(t(lang, "btn_support"), callback_data="support"),
    ])
    rows.append([
        Button(t(lang, "btn_help"), callback_data="help"),
        Button(t(lang, "btn_language"), callback_data="language"),
    ])
    return InlineKeyboardMarkup(rows)


def back_to_menu_kb(lang: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Button(t(lang, "btn_menu"), callback_data="main_menu")]])


def language_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        Button(label, callback_data=f"set_lang:{code}") for code, label in LANGUAGES.items()
    ]])


def _cart_button(lang, cart: list | None) -> list[Button]:
    if not cart:
        return []
    count = sum(int(item.get("quantity", 1)) for item in cart)
    return [Button(t(lang, "btn_cart", count=count), callback_data="cart_view")]


def categories_kb(categories: list[str], lang: str | None, cart: list | None = None, page: int = 0) -> InlineKeyboardMarkup:
    start = page * CATALOG_PAGE_SIZE
    visible = categories[start:start + CATALOG_PAGE_SIZE]
    rows = _grid([Button(f"🎮 {category}", callback_data=f"cat:{category}") for category in visible], 1)
    pager = _pager("cat_page", page, len(categories), CATALOG_PAGE_SIZE)
    if pager:
        rows.append(pager)
    if cart:
        rows.append(_cart_button(lang, cart))
    rows.append([
        Button(t(lang, "btn_menu"), callback_data="main_menu"),
        Button(t(lang, "btn_cancel"), callback_data="cancel_order"),
    ])
    return InlineKeyboardMarkup(rows)


def products_kb(
    products: list, lang: str | None, cart: list | None = None, page: int = 0, back: str = "order_start",
) -> InlineKeyboardMarkup:
    page_size = CATALOG_PAGE_SIZE * 2
    start = page * page_size
    visible = products[start:start + page_size]
    rows = _grid([Button(f"💎 {product.name}", callback_data=f"prod:{product.id}") for product in visible])
    pager = _pager("prod_page", page, len(products), page_size)
    if pager:
        rows.append(pager)
    if cart:
        rows.append(_cart_button(lang, cart))
    rows.append([
        Button(t(lang, "btn_products"), callback_data=back),
        Button(t(lang, "btn_cancel"), callback_data="cancel_order"),
    ])
    return InlineKeyboardMarkup(rows)


def quantity_kb(lang: str | None) -> InlineKeyboardMarkup:
    numbers = [1, 2, 3, 4, 5, 10]
    rows = _grid([Button(str(n), callback_data=f"qty:{n}") for n in numbers], 3)
    rows.append([
        Button(t(lang, "btn_packages"), callback_data="back_products"),
        Button(t(lang, "btn_cancel"), callback_data="cancel_order"),
    ])
    return InlineKeyboardMarkup(rows)


def cart_kb(cart: list[dict], lang: str | None) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(cart):
        name = str(item.get("product_name", "Package"))
        if len(name) > 18:
            name = name[:17] + "…"
        quantity = int(item.get("quantity", 1))
        rows.append([Button(f"📦 {name}", callback_data=f"cart_item:{index}")])
        rows.append([
            Button("−", callback_data=f"cart_qty:{index}:{max(1, quantity - 1)}"),
            Button(t(lang, "btn_qty", qty=quantity), callback_data=f"cart_item:{index}"),
            Button("+", callback_data=f"cart_qty:{index}:{min(99, quantity + 1)}"),
            Button("🗑", callback_data=f"cart_remove:{index}"),
        ])
    rows.append([Button(t(lang, "btn_checkout"), callback_data="cart_checkout")])
    rows.append([
        Button(t(lang, "btn_add_more"), callback_data="order_start"),
        Button(t(lang, "btn_clear_cart"), callback_data="cart_clear"),
    ])
    return InlineKeyboardMarkup(rows)


def saved_player_ids_kb(saved: list, lang: str | None) -> InlineKeyboardMarkup:
    rows = _grid([Button(f"🎮 {item.player_id}", callback_data=f"use_pid:{item.id}") for item in saved])
    if saved:
        rows.append([Button(t(lang, "btn_forget_pids"), callback_data="forget_pids")])
    rows.append([Button(t(lang, "btn_cancel"), callback_data="cancel_order")])
    return InlineKeyboardMarkup(rows)


def confirm_order_kb(lang: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Button(t(lang, "btn_submit"), callback_data="confirm_order")],
        [
            Button(t(lang, "btn_change_pid"), callback_data="edit_player_id"),
            Button(t(lang, "btn_start_over"), callback_data="reset_order_products"),
        ],
        [Button(t(lang, "btn_cancel"), callback_data="cancel_order")],
    ])


def order_status_kb(order_id: str, lang: str | None = None, terminal: bool = False, page: int = 0) -> InlineKeyboardMarkup:
    first = [Button(t(lang, "btn_refresh"), callback_data=f"refresh_order:{order_id}:{page}")]
    if terminal:
        first.append(Button(t(lang, "btn_reorder"), callback_data=f"reorder:{order_id}"))
    return InlineKeyboardMarkup([
        first,
        [
            Button(t(lang, "btn_get_help"), callback_data=f"support:{order_id}"),
            Button(t(lang, "btn_my_orders"), callback_data=f"my_orders_page:{page}"),
        ],
        [Button(t(lang, "btn_menu"), callback_data="main_menu")],
    ])


def my_orders_kb(orders: list, page: int, total: int, lang: str | None, page_size: int = 5) -> InlineKeyboardMarkup:
    from services.messages import STATUS_ICONS
    rows = [
        [Button(
            f"{STATUS_ICONS.get(order.status.value, '•')} {order.order_id}",
            callback_data=f"view_order:{order.order_id}:{page}",
        )]
        for order in orders
    ]
    pager = _pager("my_orders_page", page, total, page_size)
    if pager:
        rows.append(pager)
    rows.append([Button(t(lang, "btn_menu"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def support_cancel_kb(lang: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[Button(t(lang, "btn_cancel"), callback_data="support_cancel")]])
