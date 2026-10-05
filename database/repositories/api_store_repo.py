"""
Repository for API Store management and User Order Limits.
"""
from sqlalchemy import delete, select, func, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ApiStore, ApiStoreCustomer, UserOrderLimit
from utils.helpers import utcnow
from utils.security import (
    api_key_prefix, generate_api_key, generate_webhook_secret, hash_api_key,
)


def _day_start():
    return utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


class ApiStoreRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, daily_limit: int = 0) -> tuple[ApiStore, str]:
        """Create a store and return it with its plaintext key, which is never stored."""
        api_key = generate_api_key()
        store = ApiStore(
            name=name,
            api_key_hash=hash_api_key(api_key),
            api_key_prefix=api_key_prefix(api_key),
            is_active=True,
            daily_limit=daily_limit,
            orders_today=0,
            last_reset=utcnow(),
        )
        self.session.add(store)
        await self.session.commit()
        await self.session.refresh(store)
        return store, api_key

    async def rotate_key(self, store: ApiStore) -> str:
        api_key = generate_api_key()
        store.api_key_hash = hash_api_key(api_key)
        store.api_key_prefix = api_key_prefix(api_key)
        await self.session.commit()
        return api_key

    async def get_by_api_key(self, api_key: str) -> ApiStore | None:
        result = await self.session.execute(
            select(ApiStore).where(ApiStore.api_key_hash == hash_api_key(api_key))
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ApiStore | None:
        result = await self.session.execute(
            select(ApiStore).where(func.lower(ApiStore.name) == name.casefold())
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, store_id: int) -> ApiStore | None:
        return await self.session.get(ApiStore, store_id)

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

    async def set_webhook(self, store: ApiStore, url: str | None) -> str | None:
        """Set or clear the webhook URL. Returns a newly generated secret when one is created."""
        store.webhook_url = url
        new_secret = None
        if url and not store.webhook_secret:
            new_secret = generate_webhook_secret()
            store.webhook_secret = new_secret
        if not url:
            store.webhook_secret = None
        await self.session.commit()
        return new_secret

    async def delete(self, store: ApiStore) -> None:
        await self.session.execute(delete(ApiStoreCustomer).where(ApiStoreCustomer.store_id == store.id))
        await self.session.delete(store)
        await self.session.commit()

    # ─── Customer links ───────────────────────────────────────────────

    async def get_customer_ids(self, store_id: int) -> list[int]:
        result = await self.session.execute(
            select(ApiStoreCustomer.telegram_id)
            .where(ApiStoreCustomer.store_id == store_id)
            .order_by(ApiStoreCustomer.telegram_id)
        )
        return list(result.scalars().all())

    async def count_customers(self, store_id: int) -> int:
        return int(await self.session.scalar(
            select(func.count()).select_from(ApiStoreCustomer)
            .where(ApiStoreCustomer.store_id == store_id)
        ) or 0)

    async def may_order_for(self, store_id: int, telegram_id: int) -> bool:
        """A store with no linked customers may order for any member."""
        if not await self.count_customers(store_id):
            return True
        return await self.session.scalar(
            select(ApiStoreCustomer.id).where(
                ApiStoreCustomer.store_id == store_id,
                ApiStoreCustomer.telegram_id == telegram_id,
            )
        ) is not None

    async def link_customers(self, store_id: int, telegram_ids: list[int]) -> int:
        existing = set(await self.get_customer_ids(store_id))
        added = 0
        for telegram_id in dict.fromkeys(telegram_ids):
            if telegram_id not in existing:
                self.session.add(ApiStoreCustomer(store_id=store_id, telegram_id=telegram_id))
                added += 1
        await self.session.commit()
        return added

    async def unlink_customers(self, store_id: int, telegram_ids: list[int] | None = None) -> int:
        statement = delete(ApiStoreCustomer).where(ApiStoreCustomer.store_id == store_id)
        if telegram_ids is not None:
            statement = statement.where(ApiStoreCustomer.telegram_id.in_(telegram_ids))
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount or 0

    async def check_and_increment(self, store: ApiStore) -> tuple[bool, str]:
        """
        Check if the store can place an order. If yes, increment counter.
        Returns (allowed: bool, reason: str).
        """
        if not store.is_active:
            return False, "This API store is disabled by admin."

        day_start = _day_start()
        await self.session.execute(
            update(ApiStore)
            .where(
                ApiStore.id == store.id,
                or_(ApiStore.last_reset.is_(None), ApiStore.last_reset < day_start),
            )
            .values(orders_today=0, last_reset=day_start)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(
            update(ApiStore)
            .where(
                ApiStore.id == store.id,
                ApiStore.is_active.is_(True),
                or_(ApiStore.daily_limit == 0, ApiStore.orders_today < ApiStore.daily_limit),
            )
            .values(orders_today=ApiStore.orders_today + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            # Leave the update in the caller's transaction. The order and its
            # quota reservation are committed together by OrderRepository.
            return True, "OK"

        await self.session.refresh(store)
        if not store.is_active:
            return False, "This API store is disabled by admin."
        return False, f"Daily order limit reached ({store.daily_limit}/{store.daily_limit})."


class UserOrderLimitRepository:
    """No row = unlimited. daily_limit 0 = blocked. daily_limit N > 0 = N orders per UTC day."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, telegram_id: int) -> UserOrderLimit | None:
        result = await self.session.execute(
            select(UserOrderLimit).where(UserOrderLimit.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def set_limit(self, telegram_id: int, daily_limit: int) -> UserOrderLimit:
        """Set or update the daily order limit for a user (0 blocks ordering)."""
        if daily_limit < 0:
            raise ValueError("daily_limit must be 0 or higher")
        limit = await self.get(telegram_id)
        if limit is None:
            limit = UserOrderLimit(
                telegram_id=telegram_id,
                daily_limit=daily_limit,
                orders_today=0,
                last_reset=utcnow(),
            )
            self.session.add(limit)
        else:
            limit.daily_limit = daily_limit
        await self.session.commit()
        return limit

    async def get_all(self) -> list[UserOrderLimit]:
        """All configured limits, including blocked users (limit 0)."""
        result = await self.session.execute(
            select(UserOrderLimit).order_by(UserOrderLimit.daily_limit, UserOrderLimit.created_at.desc())
        )
        return list(result.scalars().all())

    async def remove_limit(self, telegram_id: int) -> bool:
        result = await self.session.execute(
            delete(UserOrderLimit).where(UserOrderLimit.telegram_id == telegram_id)
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def check_and_increment(self, telegram_id: int) -> tuple[bool, str]:
        """
        Check if user can place an order. If yes, increment their counter.
        Returns (allowed: bool, reason: str).
        """
        limit = await self.get(telegram_id)
        if limit is None:
            return True, "OK"
        if limit.daily_limit <= 0:
            return False, "Your ordering is currently disabled by admin."

        day_start = _day_start()
        await self.session.execute(
            update(UserOrderLimit)
            .where(
                UserOrderLimit.id == limit.id,
                or_(UserOrderLimit.last_reset.is_(None), UserOrderLimit.last_reset < day_start),
            )
            .values(orders_today=0, last_reset=day_start)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(
            update(UserOrderLimit)
            .where(
                UserOrderLimit.id == limit.id,
                UserOrderLimit.daily_limit > UserOrderLimit.orders_today,
            )
            .values(orders_today=UserOrderLimit.orders_today + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            # The caller commits this reservation with the order record.
            return True, "OK"
        return False, f"You have reached your daily order limit ({limit.daily_limit} orders/day)."
