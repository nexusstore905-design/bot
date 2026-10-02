from datetime import datetime, timezone
import json

from sqlalchemy import select, func, desc, update, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    Order, OrderItem, OrderStatusHistory, OrderStatus, SupplierFulfillment,
    SupplierFulfillmentStatus,
)
from utils.helpers import generate_order_id, utcnow


def _as_utc_aware(value: datetime) -> datetime:
    """SQLite may return UTC timestamps without tzinfo despite timezone=True."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
        supplier_fulfillment: dict | None = None,
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

        if supplier_fulfillment:
            self.session.add(self._make_fulfillment(order.id, supplier_fulfillment))

        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def create_cart(
        self,
        user_id: int,
        cart_items: list[dict],
        player_id: str,
        supplier_fulfillments: list[dict] | None = None,
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

        for fulfillment in supplier_fulfillments or []:
            self.session.add(self._make_fulfillment(order.id, fulfillment))

        await self.session.commit()
        await self.session.refresh(order)
        return order

    @staticmethod
    def _make_fulfillment(order_id: int, spec: dict) -> SupplierFulfillment:
        return SupplierFulfillment(
            order_id=order_id,
            supplier_chat_id=int(spec["supplier_chat_id"]),
            category=str(spec["category"]),
            items_snapshot=json.dumps(spec["items"], ensure_ascii=False),
            status=SupplierFulfillmentStatus.queued,
        )

    async def get_fulfillment_by_id(self, fulfillment_id: int) -> SupplierFulfillment | None:
        result = await self.session.execute(
            select(SupplierFulfillment)
            .where(SupplierFulfillment.id == fulfillment_id)
            .options(
                selectinload(SupplierFulfillment.order).selectinload(Order.user),
                selectinload(SupplierFulfillment.order).selectinload(Order.items),
                selectinload(SupplierFulfillment.order).selectinload(Order.fulfillments),
            )
        )
        return result.scalar_one_or_none()

    async def get_fulfillments(self, order_id: int) -> list[SupplierFulfillment]:
        result = await self.session.execute(
            select(SupplierFulfillment)
            .where(SupplierFulfillment.order_id == order_id)
            .order_by(SupplierFulfillment.id)
        )
        return list(result.scalars().all())

    async def mark_fulfillment_sending(self, fulfillment_id: int) -> bool:
        result = await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment_id,
                SupplierFulfillment.status == SupplierFulfillmentStatus.queued,
            )
            .values(status=SupplierFulfillmentStatus.sending, updated_at=utcnow())
        )
        await self.session.commit()
        return result.rowcount == 1

    async def set_fulfillment_dispatched(self, fulfillment_id: int, message_id: int) -> bool:
        await self.session.execute(
            update(SupplierFulfillment)
            .where(SupplierFulfillment.id == fulfillment_id)
            .values(supplier_msg_id=message_id, updated_at=utcnow())
        )
        result = await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment_id,
                SupplierFulfillment.status == SupplierFulfillmentStatus.sending,
            )
            .values(status=SupplierFulfillmentStatus.pending, updated_at=utcnow())
        )
        await self.session.commit()
        return result.rowcount == 1

    async def set_fulfillment_failed(self, fulfillment_id: int, reason: str) -> bool:
        result = await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment_id,
                SupplierFulfillment.status.in_((
                    SupplierFulfillmentStatus.queued,
                    SupplierFulfillmentStatus.sending,
                )),
            )
            .values(
                status=SupplierFulfillmentStatus.failed,
                failure_note=reason,
                updated_at=utcnow(),
            )
        )
        await self.session.commit()
        return result.rowcount == 1

    async def update_fulfillment_status(
        self,
        fulfillment: SupplierFulfillment,
        new_status: SupplierFulfillmentStatus,
        changed_by: str,
    ) -> bool:
        result = await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment.id,
                SupplierFulfillment.status.in_((
                    SupplierFulfillmentStatus.sending,
                    SupplierFulfillmentStatus.pending,
                )),
            )
            .values(
                status=new_status,
                changed_by=changed_by,
                failure_note="Supplier marked this group as ERROR." if new_status == SupplierFulfillmentStatus.failed else None,
                updated_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            await self.session.refresh(fulfillment)
            return False

        fulfillment.status = new_status
        await self.session.commit()
        return True

    async def refresh_order_status_from_fulfillments(
        self, order_id: int, changed_by: str, note: str | None = None,
    ) -> OrderStatus | None:
        for _ in range(3):
            order = await self.session.get(Order, order_id)
            if order is None:
                return None
            await self.session.refresh(order)
            result = await self.session.execute(
                select(SupplierFulfillment.status).where(SupplierFulfillment.order_id == order_id)
            )
            statuses = list(result.scalars().all())
            if not statuses:
                return order.status

            terminal = {SupplierFulfillmentStatus.completed, SupplierFulfillmentStatus.failed}
            if all(status in terminal for status in statuses):
                new_status = (
                    OrderStatus.completed
                    if all(status == SupplierFulfillmentStatus.completed for status in statuses)
                    else OrderStatus.failed
                )
            elif any(status in terminal for status in statuses):
                new_status = OrderStatus.processing
            else:
                new_status = OrderStatus.pending

            if order.status == new_status:
                return order.status
            if await self.update_status(order, new_status, changed_by=changed_by, note=note):
                await self.session.refresh(order)
                return order.status
        return order.status

    async def get_by_order_id(self, order_id: str, api_store_id: int | None = None) -> Order | None:
        statement = select(Order).where(Order.order_id == order_id)
        if api_store_id is not None:
            statement = statement.where(Order.api_store_id == api_store_id)
        result = await self.session.execute(
            statement
            .options(
                selectinload(Order.items), selectinload(Order.history), selectinload(Order.user),
                selectinload(Order.fulfillments),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user(
        self,
        user_id: int,
        limit: int = 10,
        offset: int = 0,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[Order]:
        statement = (
            select(Order)
            .where(Order.user_id == user_id)
            .options(selectinload(Order.items))
            .order_by(desc(Order.created_at))
        )
        if start_at is not None:
            statement = statement.where(Order.created_at >= start_at)
        if end_at is not None:
            statement = statement.where(Order.created_at < end_at)
        result = await self.session.execute(
            statement.limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Order).where(Order.user_id == user_id)
        )
        return result.scalar_one() or 0

    async def count_unsettled_by_user(
        self, user_id: int, through_order_id: int | None = None,
    ) -> int:
        statement = select(func.count()).select_from(Order).where(
            Order.user_id == user_id,
            Order.settled_at.is_(None),
            Order.status == OrderStatus.completed,
        )
        if through_order_id is not None:
            statement = statement.where(Order.id <= through_order_id)
        result = await self.session.execute(statement)
        return result.scalar_one() or 0

    async def get_unsettled_high_watermark_by_user(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.max(Order.id)).where(
                Order.user_id == user_id,
                Order.settled_at.is_(None),
                Order.status == OrderStatus.completed,
            )
        )
        return result.scalar_one() or 0

    async def get_unsettled_by_user(
        self, user_id: int, limit: int = 10, offset: int = 0,
        through_order_id: int | None = None,
    ) -> list[Order]:
        statement = (
            select(Order)
            .where(
                Order.user_id == user_id,
                Order.settled_at.is_(None),
                Order.status == OrderStatus.completed,
            )
            .options(selectinload(Order.items))
            .order_by(desc(Order.created_at))
        )
        if through_order_id is not None:
            statement = statement.where(Order.id <= through_order_id)
        result = await self.session.execute(statement.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def settle_unsettled_by_user(
        self, user_id: int, settled_by: str, settled_at: datetime,
        through_order_id: int,
    ) -> int:
        """Mark existing orders paid without deleting history or clearing newer orders."""
        result = await self.session.execute(
            update(Order)
            .where(
                Order.user_id == user_id,
                Order.settled_at.is_(None),
                Order.status == OrderStatus.completed,
                Order.id <= through_order_id,
            )
            .values(settled_at=settled_at, settled_by=settled_by)
        )
        await self.session.commit()
        return max(result.rowcount or 0, 0)

    async def summarize_user_orders(
        self,
        user_id: int,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, dict[str, int]]:
        """Count the customer's orders and package quantities by status."""
        summary = {
            status.value: {"orders": 0, "packages": 0}
            for status in OrderStatus
        }
        statement = (
            select(
                Order.status,
                func.count(distinct(Order.id)),
                func.coalesce(func.sum(OrderItem.quantity), 0),
            )
            .outerjoin(OrderItem, OrderItem.order_id == Order.id)
            .where(Order.user_id == user_id)
            .group_by(Order.status)
        )
        if start_at is not None:
            statement = statement.where(Order.created_at >= start_at)
        if end_at is not None:
            statement = statement.where(Order.created_at < end_at)
        result = await self.session.execute(statement)
        for status, order_count, package_count in result.all():
            summary[status.value] = {
                "orders": int(order_count or 0),
                "packages": int(package_count or 0),
            }
        return summary

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

    async def cancel_stale_pending(self, older_than: datetime) -> list[tuple[str, int, OrderStatus]]:
        """Expire unclaimed supplier work and return order, customer, and final status."""
        result = await self.session.execute(
            select(Order)
            .where(
                Order.status.in_((OrderStatus.pending, OrderStatus.processing)),
                Order.created_at <= older_than,
            )
            .options(selectinload(Order.user), selectinload(Order.fulfillments))
        )
        candidates = list(result.scalars().all())
        if not candidates:
            return []

        expired_orders: list[tuple[str, int, OrderStatus]] = []
        updated_at = utcnow()
        for order in candidates:
            fulfillments = list(order.fulfillments)
            if fulfillments:
                open_statuses = (
                    SupplierFulfillmentStatus.queued,
                    SupplierFulfillmentStatus.sending,
                    SupplierFulfillmentStatus.pending,
                )
                open_fulfillments = [f for f in fulfillments if f.status in open_statuses]
                if not open_fulfillments:
                    continue

                # Start the supplier's response timer from the last state change
                # made when its Telegram task was sent, not from order creation.
                # A queued task still uses order age as a fallback in case it
                # never made it to Telegram.
                timed_out = any(
                    (
                        fulfillment.status in (
                            SupplierFulfillmentStatus.sending,
                            SupplierFulfillmentStatus.pending,
                        )
                        and _as_utc_aware(fulfillment.updated_at) <= _as_utc_aware(older_than)
                    )
                    or (
                        fulfillment.status == SupplierFulfillmentStatus.queued
                        and _as_utc_aware(order.created_at) <= _as_utc_aware(older_than)
                    )
                    for fulfillment in open_fulfillments
                )
                if not timed_out:
                    continue

                expired_fulfillment_ids: list[int] = []
                for fulfillment in open_fulfillments:
                    timeout_result = await self.session.execute(
                        update(SupplierFulfillment)
                        .where(
                            SupplierFulfillment.id == fulfillment.id,
                            SupplierFulfillment.status.in_(open_statuses),
                        )
                        .values(
                            status=SupplierFulfillmentStatus.failed,
                            failure_note="Supplier did not respond within 10 minutes.",
                            updated_at=updated_at,
                        )
                    )
                    if timeout_result.rowcount == 1:
                        expired_fulfillment_ids.append(fulfillment.id)
                if not expired_fulfillment_ids:
                    continue
                # One supplier timeout cancels the full customer order. Any
                # sibling supplier tasks still waiting are also closed below.
                final_status = OrderStatus.cancelled
                for _ in range(3):
                    await self.session.refresh(order)
                    old_status = order.status
                    if old_status not in (OrderStatus.pending, OrderStatus.processing):
                        break
                    order_update = await self.session.execute(
                        update(Order)
                        .where(Order.id == order.id, Order.status == old_status)
                        .values(status=final_status, updated_at=updated_at)
                    )
                    if order_update.rowcount == 1:
                        self.session.add(OrderStatusHistory(
                            order_id=order.id,
                            old_status=old_status.value,
                            new_status=final_status.value,
                            changed_by="auto_cancel",
                            note="Supplier work expired after 10 minutes without a response.",
                        ))
                        expired_orders.append((order.order_id, order.user.telegram_id, final_status))
                        break
                # Persist child expiry even if a concurrent supplier action changed
                # the parent order while this worker was running.
                await self.session.commit()
                continue

            if order.status != OrderStatus.pending:
                continue
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
            expired_orders.append((order.order_id, order.user.telegram_id, OrderStatus.cancelled))

        if expired_orders:
            await self.session.commit()
        return expired_orders

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
