"""Small shared helpers for consistent Telegram message layouts."""
import html


DIVIDER = "──────────────"


def panel(title: str, body: str = "", icon: str = "") -> str:
    heading = f"{icon} <b>{html.escape(title, quote=False)}</b>".strip()
    if not body:
        return f"{heading}\n{DIVIDER}"
    return f"{heading}\n{DIVIDER}\n\n{body.strip()}"
