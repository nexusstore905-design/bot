"""Small shared helpers for consistent Telegram message layouts."""
import html

from config.settings import CURRENCY


DIVIDER = "──────────────"


def esc(value) -> str:
    """Escape any value for Telegram HTML text."""
    return html.escape(str(value), quote=False)


def panel(title: str, body: str = "", icon: str = "") -> str:
    heading = f"{icon} <b>{esc(title)}</b>".strip()
    if not body:
        return f"{heading}\n{DIVIDER}"
    return f"{heading}\n{DIVIDER}\n\n{body.strip()}"


def money(amount: float | None) -> str:
    if amount is None:
        return "—"
    return f"{amount:,.2f} {esc(CURRENCY)}"
