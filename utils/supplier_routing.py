"""Resolve the active supplier chat consistently across bot and API orders."""

from config.settings import SUPPLIER_CHAT_ID


def resolve_supplier_chat(category_chat_id: int | None) -> tuple[int | None, str]:
    """Use a category destination when set, otherwise use the global fallback."""
    if category_chat_id:
        return category_chat_id, "category"
    if SUPPLIER_CHAT_ID:
        return SUPPLIER_CHAT_ID, "global"
    return None, "unconfigured"
