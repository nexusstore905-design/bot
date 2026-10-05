import logging

from database.database import AsyncSessionLocal
from database.models import AdminAuditLog

logger = logging.getLogger(__name__)


async def audit(user, action: str, detail: str = "") -> None:
    """Record an admin action. Never raises: auditing must not break the action itself."""
    try:
        async with AsyncSessionLocal() as session:
            session.add(AdminAuditLog(
                admin_id=user.id,
                admin_name=(user.full_name or user.username or str(user.id))[:128],
                action=action[:64],
                detail=detail[:1000] if detail else None,
            ))
            await session.commit()
    except Exception:
        logger.exception("Could not write admin audit entry %s", action)
