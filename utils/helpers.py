import secrets
from datetime import datetime, timezone


def generate_order_id() -> str:
    """Generate an unpredictable order ID like NX12345678."""
    return f"NX{secrets.randbelow(10**8):08d}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Treat naive datetimes as UTC so comparisons never mix naive and aware values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")
