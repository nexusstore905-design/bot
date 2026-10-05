"""Deliver queued order-status webhooks to API stores.

Events are written in the same transaction as the status change (outbox
pattern), so none are lost if a process restarts. The bot worker delivers
them with exponential backoff. Each request is signed:

    X-Nexus-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">
"""
import logging
import time
from datetime import timedelta

import httpx
from sqlalchemy import select

from database.database import AsyncSessionLocal
from database.models import ApiStore, WebhookEvent
from utils.helpers import utcnow
from utils.security import sign_webhook

logger = logging.getLogger(__name__)

MAX_WEBHOOK_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 30
TIMEOUT_SECONDS = 10


async def deliver_due(client: httpx.AsyncClient, limit: int = 20) -> int:
    """Attempt due webhook events. Returns the number delivered."""
    now = utcnow()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebhookEvent)
            .where(
                WebhookEvent.delivered_at.is_(None),
                WebhookEvent.attempts < MAX_WEBHOOK_ATTEMPTS,
                WebhookEvent.next_attempt_at <= now,
            )
            .order_by(WebhookEvent.id)
            .limit(limit)
        )
        events = list(result.scalars().all())
        delivered = 0
        for event in events:
            store = await session.get(ApiStore, event.store_id)
            if store is None or not store.webhook_url or not store.webhook_secret:
                event.delivered_at = now
                event.last_error = "Skipped: store or webhook removed."
                continue
            body = event.payload.encode("utf-8")
            timestamp = int(time.time())
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "NexusStore-Webhooks/1.0",
                "X-Nexus-Event": event.event,
                "X-Nexus-Event-Id": str(event.id),
                "X-Nexus-Signature": f"t={timestamp},v1={sign_webhook(store.webhook_secret, timestamp, body)}",
            }
            event.attempts += 1
            try:
                response = await client.post(store.webhook_url, content=body, headers=headers, timeout=TIMEOUT_SECONDS)
                if 200 <= response.status_code < 300:
                    event.delivered_at = utcnow()
                    event.last_error = None
                    delivered += 1
                    continue
                error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                error = type(exc).__name__
            event.last_error = error
            event.next_attempt_at = utcnow() + timedelta(
                seconds=BASE_BACKOFF_SECONDS * (2 ** (event.attempts - 1))
            )
            if event.attempts >= MAX_WEBHOOK_ATTEMPTS:
                logger.warning(
                    "Giving up on webhook %s for order %s to store %s (%s)",
                    event.id, event.order_id, event.store_id, error,
                )
        await session.commit()
    return delivered
