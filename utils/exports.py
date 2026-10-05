"""CSV exports for accounting."""
import csv
import io
from datetime import timezone
from zoneinfo import ZoneInfo

PAKISTAN_TZ = ZoneInfo("Asia/Karachi")

ORDER_COLUMNS = [
    "order_id", "created_at_pkt", "status", "customer_telegram_id", "customer_name",
    "player_id", "items", "source", "settled_at_pkt", "settled_by",
]


def _pkt(value) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PAKISTAN_TZ).strftime("%Y-%m-%d %H:%M")


def orders_csv(orders, store_names: dict[int, str] | None = None) -> bytes:
    """Orders (with items and user loaded) as UTF-8 CSV that Excel opens correctly."""
    store_names = store_names or {}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(ORDER_COLUMNS)
    for order in orders:
        items = "; ".join(f"{item.product_name} x{item.quantity}" for item in order.items)
        source = (
            "telegram" if order.api_store_id is None
            else store_names.get(order.api_store_id, f"store#{order.api_store_id}")
        )
        writer.writerow([
            order.order_id,
            _pkt(order.created_at),
            order.status.value,
            order.user.telegram_id if order.user else "",
            (order.user.full_name or order.user.username or "") if order.user else "",
            order.player_id,
            items,
            source,
            _pkt(order.settled_at),
            order.settled_by or "",
        ])
    return buffer.getvalue().encode("utf-8-sig")
