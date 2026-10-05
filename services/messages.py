"""Message templates shared by the bot and the API."""
from datetime import timezone
from zoneinfo import ZoneInfo

from bot.i18n import t
from database.models import (
    OPEN_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    OrderStatus,
    SupplierFulfillmentStatus,
)
from utils.ui import esc, progress_bar, quote

PAKISTAN_TZ = ZoneInfo("Asia/Karachi")

STATUS_ICONS = {
    "pending": "⏳",
    "processing": "⚙️",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}

SUPPLIER_DIVIDER = "━━━━━━━━━━━━━━━━"
SUPPLIER_ACTION_MARKER = "⏳ Respond within"
TIMELINE_STEPS = 4


def status_label(status_value: str, lang: str | None) -> str:
    return f"{STATUS_ICONS.get(status_value, '❓')} {t(lang, 'st_' + status_value)}"


def pkt_clock(value) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PAKISTAN_TZ).strftime("%H:%M")


def order_timeline(order, lang: str | None) -> tuple[list[str], int]:
    """Timeline lines and how many of the four steps are done."""
    parts = list(getattr(order, "fulfillments", None) or [])
    dispatched = [part.dispatched_at for part in parts if part.dispatched_at]
    sent = bool(dispatched) or (not parts and order.supplier_msg_id is not None)
    retrying = any(
        part.status in (SupplierFulfillmentStatus.queued, SupplierFulfillmentStatus.sending)
        and part.dispatch_attempts > 0
        for part in parts
    )
    lines = [f"✅ {t(lang, 'tl_placed')} · {pkt_clock(order.created_at)}"]
    done = 1
    if sent:
        when = f" · {pkt_clock(min(dispatched))}" if dispatched else ""
        lines.append(f"✅ {t(lang, 'tl_sent')}{when}")
        done = 2
    elif retrying:
        lines.append(f"🔁 {t(lang, 'tl_retrying')}")
    elif order.status in OPEN_ORDER_STATUSES:
        lines.append(f"⏳ {t(lang, 'tl_sent')}")

    if order.status == OrderStatus.completed:
        lines.append(f"✅ {t(lang, 'tl_delivered')} · {pkt_clock(order.updated_at)}")
        done = TIMELINE_STEPS
    elif order.status == OrderStatus.failed:
        lines.append(f"❌ {t(lang, 'tl_failed')}")
    elif order.status == OrderStatus.cancelled:
        lines.append(f"🚫 {t(lang, 'tl_cancelled')}")
    else:
        if sent:
            lines.append(f"⏳ {t(lang, 'tl_working')}")
            done = 3
        lines.append(f"○ {t(lang, 'tl_delivered')}")
    return lines, done


def item_lines(items) -> str:
    lines = []
    for item in items:
        name = item["product_name"] if isinstance(item, dict) else item.product_name
        quantity = item["quantity"] if isinstance(item, dict) else item.quantity
        lines.append(f"• {esc(name)} × {quantity}")
    return "\n".join(lines) or "• —"


def order_card_text(order, lang: str | None, title: str | None = None, footer: str | None = None) -> str:
    """The customer's live order card: status bar, timeline, and details."""
    lines = []
    if title:
        lines += [title, f"🧾 <code>{esc(order.order_id)}</code>"]
    else:
        lines.append(f"🧾 <b>{t(lang, 'order')}</b> <code>{esc(order.order_id)}</code>")
    timeline, done = order_timeline(order, lang)
    if order.status not in (OrderStatus.failed, OrderStatus.cancelled):
        lines.append(f"{progress_bar(done, TIMELINE_STEPS)}  {status_label(order.status.value, lang)}")
    else:
        lines.append(status_label(order.status.value, lang))
    lines.append("")
    lines += timeline
    details = (
        f"<b>{t(lang, 'items')}</b>\n{item_lines(order.items)}\n"
        f"🎮 {t(lang, 'player_id')}: <code>{esc(order.player_id)}</code>"
    )
    lines.append(quote(details, expandable=len(order.items) > 4))
    if any(getattr(part, "proof_file_id", None) for part in getattr(order, "fulfillments", None) or []):
        lines.append(t(lang, "proof_received"))
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def is_terminal(order) -> bool:
    return order.status in TERMINAL_ORDER_STATUSES


def supplier_order_text(
    order_id: str,
    category: str,
    player_id: str,
    items: list[dict],
    created_at=None,
    timeout_minutes: int | None = None,
) -> str:
    """The task card a supplier group receives.

    The player ID is a code block (tap to copy in every Telegram app; the
    keyboard adds a one-tap copy button too). Everything from the deadline line
    down is the "action section", removed when the task is closed.
    """
    lines = [
        f"🆕 <b>NEW ORDER</b>  ·  {esc(category)}",
        SUPPLIER_DIVIDER,
        "🎮 <b>PLAYER ID</b>  <i>tap to copy</i>",
        f"<pre>{esc(player_id)}</pre>",
    ]
    lines += [f"📦 <b>{esc(item['product_name'])}</b>  ×  <b>{int(item['quantity'])}</b>" for item in items]
    when = f"  ·  🕒 {pkt_clock(created_at)} PKT" if created_at else ""
    lines += ["", f"🧾 <code>{esc(order_id)}</code>{when}"]
    if timeout_minutes:
        lines.append(f"{SUPPLIER_ACTION_MARKER} {timeout_minutes} min")
    lines += [
        SUPPLIER_DIVIDER,
        "<i>Tap ✅ Done after delivering · reply with a screenshot to send proof</i>",
    ]
    return "\n".join(lines)


def supplier_closed_text(original_html: str, footer: str) -> str:
    """Strip the action section from a supplier task card and add the outcome."""
    base = original_html
    for marker in (SUPPLIER_ACTION_MARKER, SUPPLIER_DIVIDER + "\n<i>", "Mark this group’s items as", "Mark as"):
        if marker in base:
            base = base.split(marker)[0]
            break
    return f"{base.rstrip()}\n{SUPPLIER_DIVIDER}\n{footer}"


def supplier_notice(icon: str, title: str, order_id: str, body: str) -> str:
    """Replacement text when a task card is closed without its original content."""
    return f"{icon} <b>{title}</b>\n{SUPPLIER_DIVIDER}\n🧾 <code>{esc(order_id)}</code>\n{body}"
