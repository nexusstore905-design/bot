"""Admin-only bulk cleanup for customer and sales data."""
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import ADMIN_IDS
from database.models import (
    AccessCode,
    ApiStore,
    Order,
    OrderItem,
    OrderStatusHistory,
    SupplierFulfillment,
    User,
    UserOrderLimit,
)


class BusinessDataRepository:
    """Read counts and reset business records while retaining bot configuration."""

    _models = (
        ("customers", User),
        ("orders", Order),
        ("order_items", OrderItem),
        ("order_status_history", OrderStatusHistory),
        ("supplier_fulfillments", SupplierFulfillment),
        ("access_codes", AccessCode),
        ("api_stores", ApiStore),
        ("customer_limits", UserOrderLimit),
    )

    # Child tables must be cleared before their parents when SQLite foreign keys
    # are enabled. Payment clearance is stored on Order, so deleting orders clears it.
    _delete_order = (
        SupplierFulfillment,
        OrderStatusHistory,
        OrderItem,
        Order,
        UserOrderLimit,
        AccessCode,
        ApiStore,
        User,
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_reset_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for label, model in self._models:
            statement = select(func.count()).select_from(model)
            if model is User and ADMIN_IDS:
                statement = statement.where(User.telegram_id.not_in(ADMIN_IDS))
            result = await self.session.execute(statement)
            counts[label] = result.scalar_one() or 0
        return counts

    async def reset_business_data(self) -> dict[str, int]:
        """Delete all customer, order, payment, access-code, and API-store data."""
        counts = await self.get_reset_counts()
        try:
            for model in self._delete_order:
                statement = delete(model)
                if model is User and ADMIN_IDS:
                    statement = statement.where(User.telegram_id.not_in(ADMIN_IDS))
                await self.session.execute(statement)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return counts
