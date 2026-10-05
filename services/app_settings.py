"""Runtime settings stored in the database, shared by the bot and the API.

Values are cached briefly so the auth middleware does not query on every update.
"""
import time

from sqlalchemy import select

from database.database import AsyncSessionLocal
from database.models import AppSetting
from utils.helpers import as_utc, utcnow

CACHE_SECONDS = 5.0
_cache: dict[str, tuple[float, str | None]] = {}

MAINTENANCE = "maintenance"
SHOW_PRICES = "show_prices"
BOT_HEARTBEAT = "bot_heartbeat"


async def get_setting(key: str, default: str | None = None, *, fresh: bool = False) -> str | None:
    cached = _cache.get(key)
    if not fresh and cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1] if cached[1] is not None else default
    async with AsyncSessionLocal() as session:
        value = await session.scalar(select(AppSetting.value).where(AppSetting.key == key))
    _cache[key] = (time.monotonic(), value)
    return value if value is not None else default


async def set_setting(key: str, value: str | None) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(AppSetting, key)
        if value is None:
            if row is not None:
                await session.delete(row)
        elif row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
        await session.commit()
    _cache[key] = (time.monotonic(), value)


async def is_maintenance() -> bool:
    return await get_setting(MAINTENANCE) == "on"


async def show_prices() -> bool:
    return await get_setting(SHOW_PRICES) == "on"


async def beat() -> None:
    await set_setting(BOT_HEARTBEAT, utcnow().isoformat())


async def last_heartbeat():
    from datetime import datetime
    value = await get_setting(BOT_HEARTBEAT, fresh=True)
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None
