"""Saved player IDs and support-message routing."""
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SavedPlayerId, SupportMessage
from utils.helpers import utcnow

MAX_SAVED_PLAYER_IDS = 5


class SavedPlayerIdRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, user_id: int) -> list[SavedPlayerId]:
        result = await self.session.execute(
            select(SavedPlayerId)
            .where(SavedPlayerId.user_id == user_id)
            .order_by(desc(SavedPlayerId.last_used_at))
            .limit(MAX_SAVED_PLAYER_IDS)
        )
        return list(result.scalars().all())

    async def get(self, user_id: int, saved_id: int) -> SavedPlayerId | None:
        result = await self.session.execute(
            select(SavedPlayerId).where(SavedPlayerId.id == saved_id, SavedPlayerId.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def remember(self, user_id: int, player_id: str) -> None:
        result = await self.session.execute(
            select(SavedPlayerId).where(
                SavedPlayerId.user_id == user_id, SavedPlayerId.player_id == player_id,
            )
        )
        saved = result.scalar_one_or_none()
        if saved is None:
            self.session.add(SavedPlayerId(user_id=user_id, player_id=player_id, last_used_at=utcnow()))
        else:
            saved.last_used_at = utcnow()
        await self.session.flush()
        # Keep only the most recent few.
        result = await self.session.execute(
            select(SavedPlayerId.id)
            .where(SavedPlayerId.user_id == user_id)
            .order_by(desc(SavedPlayerId.last_used_at))
            .offset(MAX_SAVED_PLAYER_IDS)
        )
        stale = list(result.scalars().all())
        if stale:
            await self.session.execute(delete(SavedPlayerId).where(SavedPlayerId.id.in_(stale)))
        await self.session.commit()

    async def clear(self, user_id: int) -> int:
        result = await self.session.execute(delete(SavedPlayerId).where(SavedPlayerId.user_id == user_id))
        await self.session.commit()
        return result.rowcount or 0


class SupportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self, admin_chat_id: int, admin_message_id: int, customer_telegram_id: int, order_id: str | None,
    ) -> None:
        self.session.add(SupportMessage(
            admin_chat_id=admin_chat_id,
            admin_message_id=admin_message_id,
            customer_telegram_id=customer_telegram_id,
            order_id=order_id,
        ))
        await self.session.commit()

    async def find(self, admin_chat_id: int, admin_message_id: int) -> SupportMessage | None:
        result = await self.session.execute(
            select(SupportMessage).where(
                SupportMessage.admin_chat_id == admin_chat_id,
                SupportMessage.admin_message_id == admin_message_id,
            )
        )
        return result.scalars().first()
