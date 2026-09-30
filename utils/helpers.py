import random
import string
from datetime import datetime, timezone


def generate_order_id() -> str:
    """Generate a unique order ID like NX123456."""
    digits = "".join(random.choices(string.digits, k=6))
    return f"NX{digits}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")
