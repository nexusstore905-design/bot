"""Shared building blocks so every screen follows the same visual system:

    <icon> <b>Title</b>
    <i>subtitle</i>

    body…
    ┃ key facts in a blockquote
"""
import html

DIVIDER = "──────────────"


def esc(value) -> str:
    """Escape any value for Telegram HTML text."""
    return html.escape(str(value), quote=False)


def header(icon: str, title: str, subtitle: str | None = None) -> str:
    text = f"{icon} <b>{esc(title)}</b>".strip()
    if subtitle:
        text += f"\n<i>{subtitle}</i>"
    return text


def quote(body: str, expandable: bool = False) -> str:
    """A highlighted block; expandable blocks start collapsed in Telegram."""
    return f"<blockquote{' expandable' if expandable else ''}>{body}</blockquote>"


def step_dots(step: int, total: int) -> str:
    return "●" * step + "○" * max(0, total - step)


def progress_bar(done: int, total: int) -> str:
    return "▰" * done + "▱" * max(0, total - done)


def panel(title: str, body: str = "", icon: str = "") -> str:
    """Admin-panel layout: bold title, divider, body."""
    heading = f"{icon} <b>{esc(title)}</b>".strip()
    if not body:
        return f"{heading}\n{DIVIDER}"
    return f"{heading}\n{DIVIDER}\n\n{body.strip()}"
