"""Resolve the active supplier chat consistently across bot and API orders."""

from config.settings import SUPPLIER_CHAT_ID


def resolve_supplier_chat(product_chat_id: int | None) -> tuple[int | None, str]:
    """Use a package destination when set, otherwise use the global fallback."""
    if product_chat_id:
        return product_chat_id, "product"
    if SUPPLIER_CHAT_ID:
        return SUPPLIER_CHAT_ID, "global"
    return None, "unconfigured"
