"""Message templates shared by the bot and the API."""
from database.models import TERMINAL_ORDER_STATUSES
from utils.ui import DIVIDER, esc, money

STATUS_DISPLAY = {
    "pending":    ("⏳", "PENDING",    "Order received, awaiting processing"),
    "processing": ("⚙️", "PROCESSING", "Your order is being processed"),
    "completed":  ("✅", "COMPLETED",  "Delivered successfully!"),
    "failed":     ("❌", "FAILED",     "Issue occurred — contact support"),
    "cancelled":  ("🚫", "CANCELLED",  "Order was cancelled"),
}

SUPPLIER_ACTION_PROMPT = "Mark this group’s items as"


def status_parts(status_value: str) -> tuple[str, str, str]:
    return STATUS_DISPLAY.get(status_value, ("❓", status_value.upper(), ""))


def order_total(items) -> float | None:
    """Total of priced lines, or None when any line has no price."""
    total = 0.0
    for item in items:
        price = item.get("unit_price") if isinstance(item, dict) else item.unit_price
        quantity = item.get("quantity") if isinstance(item, dict) else item.quantity
        if price is None:
            return None
        total += price * quantity
    return round(total, 2)


def item_lines(items, show_prices: bool) -> str:
    lines = []
    for item in items:
        name = item["product_name"] if isinstance(item, dict) else item.product_name
        quantity = item["quantity"] if isinstance(item, dict) else item.quantity
        price = item.get("unit_price") if isinstance(item, dict) else item.unit_price
        line = f"• {esc(name)} × {quantity}"
        if show_prices and price is not None:
            line += f" — {money(price * quantity)}"
        lines.append(line)
    return "\n".join(lines) or "• Order details unavailable"


def order_card_text(order, show_prices: bool, title: str = "🔎 <b>Order details</b>") -> str:
    icon, label, desc = status_parts(order.status.value)
    text = (
        f"{title}\n{DIVIDER}\n\n"
        f"🧾 <b>Order ID</b>  <code>{esc(order.order_id)}</code>\n"
        f"{icon} <b>{label}</b> · {esc(desc)}\n\n"
        f"<b>Items</b>\n{item_lines(order.items, show_prices)}\n"
    )
    total = order_total(order.items) if show_prices else None
    if total is not None:
        text += f"💰 <b>Total</b>  {money(total)}\n"
    text += f"\n🎮 <b>Player ID</b>  <code>{esc(order.player_id)}</code>"
    if any(getattr(part, "proof_file_id", None) for part in getattr(order, "fulfillments", []) or []):
        text += "\n📸 Delivery proof received"
    return text


def is_terminal(order) -> bool:
    return order.status in TERMINAL_ORDER_STATUSES


def supplier_order_text(order_id: str, category: str, player_id: str, items: list[dict]) -> str:
    text = (
        f"🆕 <b>New order</b>\n{DIVIDER}\n"
        f"🧾 <b>Order ID</b>  <code>{esc(order_id)}</code>\n"
        f"📂 <b>Product group</b>  {esc(category)}\n"
        f"🎮 <b>Player ID</b>  <code>{esc(player_id)}</code>\n\n"
        "<b>Items</b>\n"
    )
    text += "".join(
        f"• {esc(item['product_name'])} × {int(item['quantity'])}\n"
        for item in items
    )
    text += (
        f"\n{SUPPLIER_ACTION_PROMPT} <b>DONE</b> or <b>ERROR</b>:\n"
        "<i>Reply to this message with a screenshot to send delivery proof.</i>"
    )
    return text


def supplier_closed_text(original_html: str, footer: str) -> str:
    """Strip the action prompt from a supplier message and append a final line."""
    base = original_html.split(SUPPLIER_ACTION_PROMPT)[0].split("Mark as")[0].rstrip()
    return f"{base}\n\n{footer}"
