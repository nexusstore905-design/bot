from sqlalchemy import select, func, desc
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
        unit_price: float,
        quantity: int,
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
        await self.session.flush()  # Get order.id

        item = OrderItem(
            order_id=order.id,
            product_id=product_id,
            product_name=product_name,
            quantity=quantity,
            unit_price=unit_price,
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
        await self.session.flush()  # Get order.id

        for item in cart_items:
            oi = OrderItem(
                order_id=order.id,
                product_id=item["product_id"],
                product_name=item["product_name"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
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

    async def get_by_order_id(self, order_id: str) -> Order | None:
        result = await self.session.execute(
            select(Order)
            .where(Order.order_id == order_id)
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
    ) -> None:
        old_status = order.status.value if order.status else None
        order.status = new_status
        order.updated_at = utcnow()

        history = OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=new_status.value,
            changed_by=changed_by,
            note=note,
        )
        self.session.add(history)
        await self.session.commit()

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
