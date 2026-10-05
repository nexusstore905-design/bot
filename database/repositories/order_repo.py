import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, desc, distinct, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    OPEN_ORDER_STATUSES,
    ApiStore,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
    SupplierFulfillment,
    SupplierFulfillmentStatus,
    WebhookEvent,
)
from utils.helpers import as_utc, generate_order_id, utcnow
from utils.supplier_routing import resolve_supplier_chat

# Supplier delivery: first attempt immediately, then retry after these delays.
DISPATCH_RETRY_DELAYS = (20, 60)
MAX_DISPATCH_ATTEMPTS = len(DISPATCH_RETRY_DELAYS) + 1
# A queued part nobody claimed (e.g. the API worker died mid-request).
ORPHAN_QUEUED_SECONDS = 30
# A part stuck in "sending" (process died between claim and send).
STALE_SENDING_SECONDS = 180

OPEN_FULFILLMENT_STATUSES = (
    SupplierFulfillmentStatus.queued,
    SupplierFulfillmentStatus.sending,
    SupplierFulfillmentStatus.pending,
)
TERMINAL_FULFILLMENT_STATUSES = (
    SupplierFulfillmentStatus.completed,
    SupplierFulfillmentStatus.failed,
)


@dataclass
class ExpiredOrder:
    order_id: str
    order_pk: int
    customer_telegram_id: int
    status: OrderStatus
    # (supplier_chat_id, supplier_msg_id) for each part that timed out after delivery.
    timed_out_messages: list[tuple[int, int | None]] = field(default_factory=list)
    timed_out_categories: list[str] = field(default_factory=list)
    completed_categories: list[str] = field(default_factory=list)


class OrderRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    # ─── Creation ──────────────────────────────────────────────────────

    async def _new_order_id(self) -> str:
        for _ in range(20):
            oid = generate_order_id()
            exists = await self.session.scalar(select(Order.id).where(Order.order_id == oid))
            if exists is None:
                return oid
        raise RuntimeError("Could not allocate a unique order ID")

    async def create_order(
        self,
        user_id: int,
        items: list[dict],
        player_id: str,
        api_store_id: int | None = None,
        idempotency_key: str | None = None,
        fulfillments: list[dict] | None = None,
    ) -> Order:
        """Create an order with its items and supplier parts in one transaction.

        Each item is {"product_id", "product_name", "quantity"}.
        Each fulfillment is {"supplier_chat_id", "category", "items": [...]}.
        """
        order = Order(
            order_id=await self._new_order_id(),
            user_id=user_id,
            api_store_id=api_store_id,
            idempotency_key=idempotency_key,
            player_id=player_id,
            status=OrderStatus.pending,
        )
        self.session.add(order)
        await self.session.flush()

        for item in items:
            self.session.add(OrderItem(
                order_id=order.id,
                product_id=item["product_id"],
                product_name=item["product_name"],
                quantity=item["quantity"],
            ))
        self.session.add(OrderStatusHistory(
            order_id=order.id,
            old_status=None,
            new_status=OrderStatus.pending.value,
            changed_by="system",
            note="Order created",
        ))
        for spec in fulfillments or []:
            self.session.add(self._make_fulfillment(order.id, spec))

        await self.session.commit()
        return await self.get_by_order_id(order.order_id)

    @staticmethod
    def _make_fulfillment(order_pk: int, spec: dict) -> SupplierFulfillment:
        return SupplierFulfillment(
            order_id=order_pk,
            supplier_chat_id=int(spec["supplier_chat_id"]),
            category=str(spec["category"]),
            items_snapshot=json.dumps(spec["items"], ensure_ascii=False),
            status=SupplierFulfillmentStatus.queued,
        )

    async def get_by_idempotency_key(self, api_store_id: int | None, key: str) -> Order | None:
        store_filter = (
            Order.api_store_id.is_(None) if api_store_id is None
            else Order.api_store_id == api_store_id
        )
        result = await self.session.execute(
            select(Order)
            .where(store_filter, Order.idempotency_key == key)
            .options(selectinload(Order.items), selectinload(Order.fulfillments))
        )
        return result.scalars().first()

    # ─── Status changes ────────────────────────────────────────────────

    async def _apply_status(
        self, order: Order, new_status: OrderStatus, changed_by: str, note: str | None,
    ) -> bool:
        """Change status only if nobody changed it first. Does not commit."""
        old_status = order.status
        result = await self.session.execute(
            update(Order)
            .where(Order.id == order.id, Order.status == old_status)
            .values(status=new_status, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return False
        order.status = new_status
        self.session.add(OrderStatusHistory(
            order_id=order.id,
            old_status=old_status.value if old_status else None,
            new_status=new_status.value,
            changed_by=changed_by,
            note=note,
        ))
        await self._enqueue_webhook(order, old_status, new_status)
        return True

    async def _enqueue_webhook(
        self, order: Order, old_status: OrderStatus | None, new_status: OrderStatus,
    ) -> None:
        if not order.api_store_id:
            return
        store = await self.session.get(ApiStore, order.api_store_id)
        if store is None or not store.webhook_url:
            return
        now = utcnow()
        payload = {
            "event": "order.status_changed",
            "order_id": order.order_id,
            "status": new_status.value,
            "previous_status": old_status.value if old_status else None,
            "occurred_at": now.isoformat(),
        }
        self.session.add(WebhookEvent(
            store_id=store.id,
            order_id=order.order_id,
            event="order.status_changed",
            payload=json.dumps(payload),
            next_attempt_at=now,
        ))

    async def update_status(
        self, order: Order, new_status: OrderStatus, changed_by: str = "system", note: str | None = None
    ) -> bool:
        if not await self._apply_status(order, new_status, changed_by, note):
            await self.session.rollback()
            await self.session.refresh(order)
            return False
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

            if all(status in TERMINAL_FULFILLMENT_STATUSES for status in statuses):
                new_status = (
                    OrderStatus.completed
                    if all(status == SupplierFulfillmentStatus.completed for status in statuses)
                    else OrderStatus.failed
                )
            elif any(status in TERMINAL_FULFILLMENT_STATUSES for status in statuses):
                new_status = OrderStatus.processing
            else:
                new_status = OrderStatus.pending

            if order.status == new_status:
                return order.status
            if await self.update_status(order, new_status, changed_by=changed_by, note=note):
                return order.status
        return order.status

    async def admin_close_order(
        self, order: Order, new_status: OrderStatus, changed_by: str,
    ) -> tuple[bool, list[tuple[int, int | None]]]:
        """Set a terminal status and close any supplier parts still open.

        Returns (applied, supplier messages that should lose their buttons).
        """
        part_status = (
            SupplierFulfillmentStatus.completed
            if new_status == OrderStatus.completed else SupplierFulfillmentStatus.failed
        )
        closed: list[tuple[int, int | None]] = []
        for part in order.fulfillments:
            if part.status not in OPEN_FULFILLMENT_STATUSES:
                continue
            result = await self.session.execute(
                update(SupplierFulfillment)
                .where(SupplierFulfillment.id == part.id, SupplierFulfillment.status == part.status)
                .values(
                    status=part_status,
                    changed_by=changed_by,
                    failure_note=None if part_status == SupplierFulfillmentStatus.completed
                    else f"Closed by {changed_by}.",
                    updated_at=utcnow(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1 and part.supplier_msg_id:
                closed.append((part.supplier_chat_id, part.supplier_msg_id))
        if not await self._apply_status(order, new_status, changed_by, "Set by admin"):
            await self.session.rollback()
            await self.session.refresh(order)
            return False, []
        await self.session.commit()
        return True, closed

    # ─── Supplier delivery ─────────────────────────────────────────────

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

    async def claim_fulfillment(self, fulfillment_id: int, now: datetime | None = None) -> bool:
        """Atomically move a due queued part to sending so only one process delivers it."""
        now = now or utcnow()
        result = await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment_id,
                SupplierFulfillment.status == SupplierFulfillmentStatus.queued,
                or_(
                    SupplierFulfillment.next_attempt_at.is_(None),
                    SupplierFulfillment.next_attempt_at <= now,
                ),
            )
            .values(status=SupplierFulfillmentStatus.sending, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return result.rowcount == 1

    async def mark_dispatched(self, fulfillment_id: int, message_id: int, now: datetime | None = None) -> bool:
        now = now or utcnow()
        result = await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment_id,
                SupplierFulfillment.status == SupplierFulfillmentStatus.sending,
            )
            .values(
                status=SupplierFulfillmentStatus.pending,
                supplier_msg_id=message_id,
                dispatched_at=now,
                next_attempt_at=None,
                failure_note=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        order_pk = await self.session.scalar(
            select(SupplierFulfillment.order_id).where(SupplierFulfillment.id == fulfillment_id)
        )
        if order_pk is not None:
            await self.session.execute(
                update(Order)
                .where(Order.id == order_pk, Order.supplier_msg_id.is_(None))
                .values(supplier_msg_id=message_id)
                .execution_options(synchronize_session=False)
            )
        await self.session.commit()
        return result.rowcount == 1

    async def record_dispatch_failure(
        self, fulfillment_id: int, error: str, now: datetime | None = None,
    ) -> tuple[int, bool]:
        """Schedule a retry, or fail the part after the last attempt.

        Returns (attempts so far, True if this was the final attempt).
        """
        now = now or utcnow()
        part = await self.session.get(SupplierFulfillment, fulfillment_id, populate_existing=True)
        if part is None or part.status != SupplierFulfillmentStatus.sending:
            return (part.dispatch_attempts if part else 0), False
        attempts = part.dispatch_attempts + 1
        final = attempts >= MAX_DISPATCH_ATTEMPTS
        values = {"dispatch_attempts": attempts, "updated_at": now}
        if final:
            values.update(
                status=SupplierFulfillmentStatus.failed,
                failure_note=f"Delivery failed after {attempts} attempts: {error}"[:500],
                next_attempt_at=None,
            )
        else:
            values.update(
                status=SupplierFulfillmentStatus.queued,
                failure_note=f"Delivery attempt {attempts} failed: {error}"[:500],
                next_attempt_at=now + timedelta(seconds=DISPATCH_RETRY_DELAYS[attempts - 1]),
            )
        await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.id == fulfillment_id,
                SupplierFulfillment.status == SupplierFulfillmentStatus.sending,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return attempts, final

    async def due_fulfillment_ids(self, now: datetime | None = None, limit: int = 50) -> list[int]:
        """Parts that need a (re)delivery attempt now."""
        now = now or utcnow()
        await self.session.execute(
            update(SupplierFulfillment)
            .where(
                SupplierFulfillment.status == SupplierFulfillmentStatus.sending,
                SupplierFulfillment.updated_at < now - timedelta(seconds=STALE_SENDING_SECONDS),
            )
            .values(status=SupplierFulfillmentStatus.queued, next_attempt_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        result = await self.session.execute(
            select(SupplierFulfillment.id)
            .join(Order, Order.id == SupplierFulfillment.order_id)
            .where(
                Order.status.in_(OPEN_ORDER_STATUSES),
                SupplierFulfillment.status == SupplierFulfillmentStatus.queued,
                or_(
                    SupplierFulfillment.next_attempt_at <= now,
                    and_(
                        SupplierFulfillment.next_attempt_at.is_(None),
                        SupplierFulfillment.updated_at <= now - timedelta(seconds=ORPHAN_QUEUED_SECONDS),
                    ),
                ),
            )
            .order_by(SupplierFulfillment.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_fulfillment_status(
        self,
        fulfillment: SupplierFulfillment,
        new_status: SupplierFulfillmentStatus,
        changed_by: str,
    ) -> bool:
        now = utcnow()
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
                responded_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.refresh(fulfillment)
            return False

        fulfillment.status = new_status
        await self.session.commit()
        return True

    async def set_fulfillment_proof(
        self, supplier_chat_id: int, supplier_msg_id: int, file_id: str,
    ) -> SupplierFulfillment | None:
        result = await self.session.execute(
            select(SupplierFulfillment)
            .where(
                SupplierFulfillment.supplier_chat_id == supplier_chat_id,
                SupplierFulfillment.supplier_msg_id == supplier_msg_id,
            )
            .options(selectinload(SupplierFulfillment.order).selectinload(Order.user))
        )
        part = result.scalars().first()
        if part is None:
            return None
        part.proof_file_id = file_id
        await self.session.commit()
        return part

    async def requeue_for_resend(
        self, order: Order, changed_by: str, new_chat_id: int | None = None,
    ) -> tuple[list[tuple[int, int]], int, str | None]:
        """Queue every unfinished supplier part for a fresh delivery.

        Returns (old supplier messages to close, parts queued, error message).
        """
        old_messages: list[tuple[int, int]] = []
        queued = 0
        if not order.fulfillments:
            groups: dict[tuple[str, int], list[dict]] = {}
            for item in order.items:
                product = await self.session.get(Product, item.product_id) if item.product_id else None
                target = new_chat_id or resolve_supplier_chat(
                    product.supplier_chat_id if product else None
                )[0]
                if not target:
                    return [], 0, f"No supplier route for {item.product_name}."
                category = product.category if product else "Order"
                groups.setdefault((category, target), []).append(
                    {"product_name": item.product_name, "quantity": item.quantity}
                )
            for (category, target), items in groups.items():
                self.session.add(self._make_fulfillment(
                    order.id, {"supplier_chat_id": target, "category": category, "items": items}
                ))
                queued += 1
        else:
            for part in order.fulfillments:
                if part.status == SupplierFulfillmentStatus.completed:
                    continue
                if part.supplier_msg_id and part.status in (
                    SupplierFulfillmentStatus.pending, SupplierFulfillmentStatus.sending,
                ):
                    old_messages.append((part.supplier_chat_id, part.supplier_msg_id))
                if new_chat_id:
                    part.supplier_chat_id = new_chat_id
                part.status = SupplierFulfillmentStatus.queued
                part.dispatch_attempts = 0
                part.next_attempt_at = None
                part.dispatched_at = None
                part.responded_at = None
                part.supplier_msg_id = None
                part.failure_note = None
                part.changed_by = changed_by
                queued += 1
        if not queued:
            return [], 0, "Every supplier part of this order is already completed."
        if order.status != OrderStatus.pending:
            await self._apply_status(order, OrderStatus.pending, changed_by, "Re-sent to supplier")
        await self.session.commit()
        return old_messages, queued, None

    async def expire_stale(self, now: datetime, timeout: timedelta) -> list[ExpiredOrder]:
        """Time out supplier parts that were not answered within `timeout` of delivery.

        Completed parts are never touched. An order becomes cancelled only if
        no part was completed; if some parts were delivered it becomes failed
        so an admin can settle it manually.
        """
        cutoff = now - timeout
        result = await self.session.execute(
            select(Order)
            .where(Order.status.in_(OPEN_ORDER_STATUSES))
            .options(selectinload(Order.user), selectinload(Order.fulfillments))
            .execution_options(populate_existing=True)
        )
        expired: list[ExpiredOrder] = []
        for order in result.scalars().all():
            info = ExpiredOrder(
                order_id=order.order_id,
                order_pk=order.id,
                customer_telegram_id=order.user.telegram_id,
                status=order.status,
            )
            if order.fulfillments:
                for part in order.fulfillments:
                    if part.status == SupplierFulfillmentStatus.pending:
                        started = as_utc(part.dispatched_at or part.updated_at)
                    elif part.status in (SupplierFulfillmentStatus.queued, SupplierFulfillmentStatus.sending):
                        started = as_utc(part.updated_at)
                    else:
                        continue
                    if started is None or started > cutoff:
                        continue
                    previous = part.status
                    update_result = await self.session.execute(
                        update(SupplierFulfillment)
                        .where(SupplierFulfillment.id == part.id, SupplierFulfillment.status == previous)
                        .values(
                            status=SupplierFulfillmentStatus.failed,
                            failure_note=(
                                f"Supplier did not respond within {int(timeout.total_seconds() // 60)} minutes."
                                if previous == SupplierFulfillmentStatus.pending
                                else "Could not be delivered to the supplier in time."
                            ),
                            changed_by="auto_cancel",
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if update_result.rowcount != 1:
                        continue
                    part.status = SupplierFulfillmentStatus.failed
                    info.timed_out_categories.append(part.category)
                    if previous == SupplierFulfillmentStatus.pending:
                        info.timed_out_messages.append((part.supplier_chat_id, part.supplier_msg_id))
                if not info.timed_out_categories:
                    continue
                statuses = [part.status for part in order.fulfillments]
                info.completed_categories = [
                    part.category for part in order.fulfillments
                    if part.status == SupplierFulfillmentStatus.completed
                ]
                if all(status in TERMINAL_FULFILLMENT_STATUSES for status in statuses):
                    new_status = OrderStatus.failed if info.completed_categories else OrderStatus.cancelled
                else:
                    new_status = OrderStatus.processing
            else:
                if as_utc(order.created_at) > cutoff:
                    continue
                new_status = OrderStatus.cancelled

            if new_status != order.status:
                note = (
                    "Automatically cancelled: no supplier response in time."
                    if new_status == OrderStatus.cancelled
                    else "Some supplier parts timed out; completed parts were kept."
                )
                await self._apply_status(order, new_status, "auto_cancel", note)
            info.status = order.status
            expired.append(info)

        if expired:
            await self.session.commit()
        return expired

    # ─── Queries ───────────────────────────────────────────────────────

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
            .execution_options(populate_existing=True)
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

    async def customer_summary(self, user_id: int) -> dict:
        """Home-screen numbers: open orders, completed orders awaiting payment, latest order."""
        active = await self.session.scalar(
            select(func.count()).select_from(Order)
            .where(Order.user_id == user_id, Order.status.in_(OPEN_ORDER_STATUSES))
        )
        due = await self.count_unsettled_by_user(user_id)
        last_settled = await self.session.scalar(
            select(func.max(Order.settled_at)).where(Order.user_id == user_id)
        )
        latest = await self.session.execute(
            select(Order).where(Order.user_id == user_id).order_by(desc(Order.created_at)).limit(1)
        )
        return {
            "active": int(active or 0),
            "due": int(due or 0),
            "last_settled_at": as_utc(last_settled),
            "latest": latest.scalars().first(),
        }

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
        """Mark completed, unsettled orders paid without deleting history or newer orders."""
        result = await self.session.execute(
            update(Order)
            .where(
                Order.user_id == user_id,
                Order.settled_at.is_(None),
                Order.status == OrderStatus.completed,
                Order.id <= through_order_id,
            )
            .values(settled_at=settled_at, settled_by=settled_by)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        # Some database drivers report a negative/unknown rowcount. Count the
        # persisted rows in the same session so the admin receives a reliable result.
        if result.rowcount is not None and result.rowcount >= 0:
            return result.rowcount
        verify = await self.session.execute(
            select(func.count()).select_from(Order).where(
                Order.user_id == user_id,
                Order.settled_at == settled_at,
                Order.settled_by == settled_by,
                Order.id <= through_order_id,
            )
        )
        return verify.scalar_one() or 0

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

    async def get_all(self, limit: int | None = 50, offset: int = 0) -> list[Order]:
        statement = (
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user))
            .order_by(desc(Order.created_at))
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self.session.execute(statement)
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

    async def set_customer_msg(self, order_id: str, msg_id: int) -> bool:
        result = await self.session.execute(
            update(Order)
            .where(Order.order_id == order_id)
            .values(customer_msg_id=msg_id)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return result.rowcount == 1

    async def count_by_status(self) -> dict[str, int]:
        result = await self.session.execute(
            select(Order.status, func.count()).group_by(Order.status)
        )
        counts = {status.value: 0 for status in OrderStatus}
        for status, count in result.all():
            counts[status.value] = int(count)
        return counts
