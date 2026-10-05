"""Background jobs that run inside the always-on bot process."""
import asyncio
import html
import logging
import time
import traceback
from datetime import datetime, timedelta

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import BACKUP_DIR, BACKUP_KEEP_DAYS, SUPPLIER_TIMEOUT_MINUTES
from services import app_settings
from services.dispatch import dispatch_due
from services.expiry import expire_stale_orders
from services.notify import notify_admins
from services.webhooks import deliver_due
from utils.backup import backup_sqlite
from utils.helpers import as_utc, utcnow

logger = logging.getLogger(__name__)

TICK_SECONDS = 5
EXPIRY_EVERY_TICKS = 3        # 15 seconds
HEARTBEAT_EVERY_TICKS = 6     # 30 seconds
MAINTENANCE_EVERY_TICKS = 720  # hourly: backups if due, old rate-limit rows
BACKUP_INTERVAL = timedelta(hours=24)
LAST_BACKUP_KEY = "last_backup"


async def daily_maintenance(bot) -> None:
    """Back up the database once a day and prune stale API rate-limit windows."""
    from sqlalchemy import delete

    from database.database import AsyncSessionLocal, engine
    from database.models import ApiRateCounter

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(ApiRateCounter).where(ApiRateCounter.window_start < utcnow() - timedelta(hours=1))
        )
        await session.commit()

    database_path = engine.url.database
    if engine.dialect.name != "sqlite" or not database_path or database_path == ":memory:":
        return
    last = await app_settings.get_setting(LAST_BACKUP_KEY, fresh=True)
    if last and utcnow() - as_utc(datetime.fromisoformat(last)) < BACKUP_INTERVAL:
        return
    try:
        path = await asyncio.to_thread(backup_sqlite, database_path, BACKUP_DIR, BACKUP_KEEP_DAYS)
    except Exception as exc:
        logger.exception("Database backup failed")
        await notify_admins(bot, f"⚠️ <b>Daily database backup failed</b>\n<code>{html.escape(str(exc))[:200]}</code>")
        return
    await app_settings.set_setting(LAST_BACKUP_KEY, utcnow().isoformat())
    logger.info("Database backed up to %s", path)


async def _run_step(name: str, step) -> None:
    try:
        await step()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Background step %s failed", name)


async def run_background_workers(bot) -> None:
    logger.info(
        "Background worker started: supplier timeout %s min, delivery retries and webhooks every %ss",
        SUPPLIER_TIMEOUT_MINUTES, TICK_SECONDS,
    )
    tick = 0
    async with httpx.AsyncClient(follow_redirects=False) as client:
        while True:
            await _run_step("dispatch_retries", lambda: dispatch_due(bot))
            await _run_step("webhooks", lambda: deliver_due(client))
            if tick % EXPIRY_EVERY_TICKS == 0:
                await _run_step("supplier_timeouts", lambda: expire_stale_orders(bot))
            if tick % HEARTBEAT_EVERY_TICKS == 0:
                await _run_step("heartbeat", app_settings.beat)
            if tick % MAINTENANCE_EVERY_TICKS == 0:
                await _run_step("daily_maintenance", lambda: daily_maintenance(bot))
            tick += 1
            await asyncio.sleep(TICK_SECONDS)


# ─── Error alerts ─────────────────────────────────────────────────────

ALERT_COOLDOWN_SECONDS = 300
_last_alert: dict[str, float] = {}


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every handler error and alert admins, at most once per error type per 5 minutes."""
    error = context.error
    logger.error("Unhandled error while processing an update", exc_info=error)
    key = type(error).__name__
    now = time.monotonic()
    if now - _last_alert.get(key, 0) < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert[key] = now

    where = "unknown update"
    if isinstance(update, Update):
        if update.callback_query:
            where = f"button <code>{html.escape(update.callback_query.data or '')[:60]}</code>"
        elif update.effective_message and update.effective_message.text:
            where = "message " + html.escape(update.effective_message.text.split()[0][:30])
        if update.effective_user:
            where += f" from <code>{update.effective_user.id}</code>"
    last_frame = traceback.extract_tb(error.__traceback__)[-1] if error and error.__traceback__ else None
    location = f"{last_frame.filename.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}:{last_frame.lineno}" if last_frame else "?"
    await notify_admins(
        context.bot,
        "🐞 <b>BOT ERROR</b>\n\n"
        f"Type: <code>{html.escape(key)}</code>\n"
        f"Where: {where}\n"
        f"Code: <code>{html.escape(location)}</code>\n"
        f"Detail: <code>{html.escape(str(error))[:300]}</code>\n\n"
        "<i>Similar errors are muted for 5 minutes. Full trace is in the bot log.</i>",
    )
