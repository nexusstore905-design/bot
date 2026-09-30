"""
Repository for API Store management and User Order Limits.
"""
import secrets
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ApiStore, UserOrderLimit, Order


def _utcnow():
    return datetime.now(timezone.utc)


def _is_same_day(dt: datetime | None) -> bool:
    """Check if the given datetime is today (UTC)."""
    if dt is None:
        return False
    now = _utcnow()
    return dt.date() == now.date()


class ApiStoreRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, daily_limit: int = 0) -> ApiStore:
        """Create a new API store with a generated key. daily_limit=0 means unlimited."""
        api_key = f"nxs_{secrets.token_hex(24)}"
        store = ApiStore(
            name=name,
            api_key=api_key,
            is_active=True,
            daily_limit=daily_limit,
            orders_today=0,
            last_reset=_utcnow(),
        )
        self.session.add(store)
        await self.session.commit()
        await self.session.refresh(store)
        return store

    async def get_by_api_key(self, api_key: str) -> ApiStore | None:
        result = await self.session.execute(
            select(ApiStore).where(ApiStore.api_key == api_key)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ApiStore | None:
        result = await self.session.execute(
            select(ApiStore).where(ApiStore.name == name)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> list[ApiStore]:
        result = await self.session.execute(
            select(ApiStore).order_by(ApiStore.created_at.desc())
        )
        return list(result.scalars().all())

    async def toggle_active(self, store: ApiStore) -> None:
        store.is_active = not store.is_active
        await self.session.commit()

    async def set_daily_limit(self, store: ApiStore, limit: int) -> None:
        store.daily_limit = limit
        await self.session.commit()

    async def delete(self, store: ApiStore) -> None:
        await self.session.delete(store)
        await self.session.commit()

    async def check_and_increment(self, store: ApiStore) -> tuple[bool, str]:
        """
        Check if the store can place an order. If yes, increment counter.
        Returns (allowed: bool, reason: str).
        """
        if not store.is_active:
            return False, "This API store is disabled by admin."

        # Reset daily counter if it's a new day
        if not _is_same_day(store.last_reset):
            store.orders_today = 0
            store.last_reset = _utcnow()

        # Check limit (0 = unlimited)
        if store.daily_limit > 0 and store.orders_today >= store.daily_limit:
            return False, f"Daily order limit reached ({store.daily_limit}/{store.daily_limit})."

        store.orders_today += 1
        await self.session.commit()
        return True, "OK"


class UserOrderLimitRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, telegram_id: int, default_limit: int = 0) -> UserOrderLimit:
        """Get existing limit or create one. default_limit=0 means unlimited."""
        result = await self.session.execute(
            select(UserOrderLimit).where(UserOrderLimit.telegram_id == telegram_id)
        )
        limit = result.scalar_one_or_none()
        if limit is None:
            limit = UserOrderLimit(
                telegram_id=telegram_id,
                daily_limit=default_limit,
                orders_today=0,
                last_reset=_utcnow(),
            )
            self.session.add(limit)
            await self.session.commit()
            await self.session.refresh(limit)
        return limit

    async def set_limit(self, telegram_id: int, daily_limit: int) -> UserOrderLimit:
        """Set or update the daily order limit for a user."""
        limit = await self.get_or_create(telegram_id, daily_limit)
        limit.daily_limit = daily_limit
        await self.session.commit()
        return limit

    async def get_all_limited(self) -> list[UserOrderLimit]:
        """Get all users that have a limit set (non-zero)."""
        result = await self.session.execute(
            select(UserOrderLimit)
            .where(UserOrderLimit.daily_limit > 0)
            .order_by(UserOrderLimit.created_at.desc())
        )
        return list(result.scalars().all())

    async def remove_limit(self, telegram_id: int) -> None:
        result = await self.session.execute(
            select(UserOrderLimit).where(UserOrderLimit.telegram_id == telegram_id)
        )
        limit = result.scalar_one_or_none()
        if limit:
            await self.session.delete(limit)
            await self.session.commit()

    async def check_and_increment(self, telegram_id: int) -> tuple[bool, str]:
        """
        Check if user can place an order. If yes, increment their counter.
        Returns (allowed: bool, reason: str).
        Users without a limit entry = unlimited.
        """
        result = await self.session.execute(
            select(UserOrderLimit).where(UserOrderLimit.telegram_id == telegram_id)
        )
        limit = result.scalar_one_or_none()

        # No limit record = unlimited
        if limit is None:
            return True, "OK"

        # daily_limit == 0 means blocked completely
        if limit.daily_limit == 0:
            return False, "Your ordering is currently disabled by admin."

        # Reset daily counter if it's a new day
        if not _is_same_day(limit.last_reset):
            limit.orders_today = 0
            limit.last_reset = _utcnow()

        if limit.orders_today >= limit.daily_limit:
            return False, f"You have reached your daily order limit ({limit.daily_limit} orders/day)."

        limit.orders_today += 1
        await self.session.commit()
        return True, "OK"
