from datetime import datetime

from sqlalchemy import select, func, desc, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Order, OrderItem, OrderStatusHistory, OrderStatus, User
from utils.helpers import generate_order_id, utcnow


class OrderRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        product_id: int,
        product_name: str,
        quantity: int,
        player_id: str,
        api_store_id: int | None = None,
    ) -> Order:
        # Generate unique order ID
        for _ in range(10):
            oid = generate_order_id()
            existing = await self.session.execute(select(Order).where(Order.order_id == oid))
            if existing.scalar_one_or_none() is None:
                break

        order = Order(
            order_id=oid,
            user_id=user_id,
            api_store_id=api_store_id,
            player_id=player_id,
            status=OrderStatus.pending,
        )
        self.session.add(order)
        await self.session.flush()

        item = OrderItem(
            order_id=order.id,
            product_id=product_id,
            product_name=product_name,
            quantity=quantity,
        )
        self.session.add(item)

        history = OrderStatusHistory(
            order_id=order.id,
            old_status=None,
            new_status=OrderStatus.pending.value,
            changed_by="system",
            note="Order created",
        )
        self.session.add(history)

        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def create_cart(
        self,
        user_id: int,
        cart_items: list[dict],
        player_id: str,
    ) -> Order:
        # Generate unique order ID
        for _ in range(10):
            oid = generate_order_id()
            existing = await self.session.execute(select(Order).where(Order.order_id == oid))
            if existing.scalar_one_or_none() is None:
                break

        order = Order(
            order_id=oid,
            user_id=user_id,
            player_id=player_id,
            status=OrderStatus.pending,
        )
        self.session.add(order)
        await self.session.flush()

        for item in cart_items:
            oi = OrderItem(
                order_id=order.id,
                product_id=item["product_id"],
                product_name=item["product_name"],
                quantity=item["quantity"],
            )
            self.session.add(oi)

        history = OrderStatusHistory(
            order_id=order.id,
            old_status=None,
            new_status=OrderStatus.pending.value,
            changed_by="system",
            note="Cart order created",
        )
        self.session.add(history)

        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def get_by_order_id(self, order_id: str, api_store_id: int | None = None) -> Order | None:
        statement = select(Order).where(Order.order_id == order_id)
        if api_store_id is not None:
            statement = statement.where(Order.api_store_id == api_store_id)
        result = await self.session.execute(
            statement
            .options(selectinload(Order.items), selectinload(Order.history), selectinload(Order.user))
        )
        return result.scalar_one_or_none()

    async def get_by_user(self, user_id: int, limit: int = 10) -> list[Order]:
        result = await self.session.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .options(selectinload(Order.items))
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_all(self, limit: int = 50, offset: int = 0) -> list[Order]:
        result = await self.session.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user))
            .order_by(desc(Order.created_at))
            .limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_status(self, status: OrderStatus, limit: int = 50) -> list[Order]:
        result = await self.session.execute(
            select(Order)
            .where(Order.status == status)
            .options(selectinload(Order.items), selectinload(Order.user))
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(
        self, order: Order, new_status: OrderStatus, changed_by: str = "system", note: str | None = None
    ) -> bool:
        old_status = order.status.value if order.status else None
        updated_at = utcnow()
        result = await self.session.execute(
            update(Order)
            .where(Order.id == order.id, Order.status == order.status)
            .values(status=new_status, updated_at=updated_at)
        )
        if result.rowcount != 1:
            await self.session.refresh(order)
            return False

        history = OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=new_status.value,
            changed_by=changed_by,
            note=note,
        )
        self.session.add(history)
        await self.session.commit()
        return True

    async def cancel_stale_pending(self, older_than: datetime) -> list[tuple[str, int]]:
        """Atomically cancel pending orders older than a cutoff and return customer IDs."""
        result = await self.session.execute(
            select(Order)
            .where(Order.status == OrderStatus.pending, Order.created_at <= older_than)
            .options(selectinload(Order.user))
        )
        candidates = list(result.scalars().all())
        if not candidates:
            return []

        cancelled: list[tuple[str, int]] = []
        updated_at = utcnow()
        for order in candidates:
            update_result = await self.session.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    Order.status == OrderStatus.pending,
                    Order.created_at <= older_than,
                )
                .values(status=OrderStatus.cancelled, updated_at=updated_at)
            )
            if update_result.rowcount != 1:
                continue

            self.session.add(OrderStatusHistory(
                order_id=order.id,
                old_status=OrderStatus.pending.value,
                new_status=OrderStatus.cancelled.value,
                changed_by="auto_cancel",
                note="Automatically cancelled after 10 minutes without supplier action.",
            ))
            cancelled.append((order.order_id, order.user.telegram_id))

        if cancelled:
            await self.session.commit()
        return cancelled

    async def set_supplier_msg(self, order: Order, msg_id: int) -> None:
        order.supplier_msg_id = msg_id
        await self.session.commit()

    async def count_by_status(self) -> dict[str, int]:
        result = {}
        for status in OrderStatus:
            r = await self.session.execute(
                select(func.count()).select_from(Order).where(Order.status == status)
            )
            result[status.value] = r.scalar() or 0
        return result

    async def get_stale_pending(self, minutes: int = 10) -> list[Order]:
        """Return orders still pending after minutes minutes."""
        from datetime import timedelta
        cutoff = utcnow() - timedelta(minutes=minutes)
        result = await self.session.execute(
            select(Order)
            .where(Order.status == OrderStatus.pending, Order.created_at <= cutoff)
            .options(selectinload(Order.items), selectinload(Order.user))
        )
        return list(result.scalars().all())
