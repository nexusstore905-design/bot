"""Read-only aggregates for the admin dashboard and audit views."""
from collections import defaultdict
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AdminAuditLog, ApiStore, AuthStatus, Order, OrderStatus,
    SupplierFulfillment, SupplierFulfillmentStatus, User,
)
from utils.helpers import as_utc


class StatsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def order_counts(self, start: datetime, end: datetime) -> dict[str, int]:
        result = await self.session.execute(
            select(Order.status, func.count())
            .where(Order.created_at >= start, Order.created_at < end)
            .group_by(Order.status)
        )
        counts = {status.value: 0 for status in OrderStatus}
        for status, count in result.all():
            counts[status.value] = int(count)
        return counts

    async def store_counts(self, start: datetime, end: datetime) -> list[tuple[str, int]]:
        result = await self.session.execute(
            select(Order.api_store_id, func.count())
            .where(Order.created_at >= start, Order.created_at < end)
            .group_by(Order.api_store_id)
        )
        names = {store.id: store.name for store in (await self.session.execute(select(ApiStore))).scalars()}
        rows = []
        for store_id, count in result.all():
            label = "Telegram bot" if store_id is None else names.get(store_id, f"Deleted store #{store_id}")
            rows.append((label, int(count)))
        return sorted(rows, key=lambda row: -row[1])

    async def supplier_stats(self, start: datetime, end: datetime) -> list[dict]:
        """Per supplier chat: parts delivered, outcomes, and average response time."""
        result = await self.session.execute(
            select(SupplierFulfillment)
            .where(SupplierFulfillment.created_at >= start, SupplierFulfillment.created_at < end)
        )
        stats: dict[int, dict] = defaultdict(lambda: {
            "parts": 0, "completed": 0, "failed": 0, "timed_out": 0, "open": 0, "response_seconds": [],
        })
        for part in result.scalars().all():
            row = stats[part.supplier_chat_id]
            row["parts"] += 1
            if part.status == SupplierFulfillmentStatus.completed:
                row["completed"] += 1
            elif part.status == SupplierFulfillmentStatus.failed:
                if part.changed_by == "auto_cancel":
                    row["timed_out"] += 1
                else:
                    row["failed"] += 1
            else:
                row["open"] += 1
            if part.dispatched_at and part.responded_at:
                row["response_seconds"].append(
                    (as_utc(part.responded_at) - as_utc(part.dispatched_at)).total_seconds()
                )
        rows = []
        for chat_id, row in stats.items():
            times = row.pop("response_seconds")
            row["chat_id"] = chat_id
            row["avg_response_seconds"] = sum(times) / len(times) if times else None
            rows.append(row)
        return sorted(rows, key=lambda row: -row["parts"])

    async def customer_counts(self) -> dict[str, int]:
        total = await self.session.scalar(select(func.count()).select_from(User))
        members = await self.session.scalar(
            select(func.count()).select_from(User).where(User.auth_status == AuthStatus.authenticated)
        )
        revoked = await self.session.scalar(
            select(func.count()).select_from(User).where(User.auth_status == AuthStatus.revoked)
        )
        return {"total": int(total or 0), "signed_in": int(members or 0), "revoked": int(revoked or 0)}

    async def recent_audit(self, limit: int = 20) -> list[AdminAuditLog]:
        result = await self.session.execute(
            select(AdminAuditLog).order_by(desc(AdminAuditLog.created_at)).limit(limit)
        )
        return list(result.scalars().all())
