import asyncio
import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config.settings import DATABASE_URL, SQLITE_JOURNAL_MODE
from database.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool,
)

if "sqlite" in DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create tables and apply pending schema migrations."""
    database_path = engine.url.database
    if engine.dialect.name == "sqlite" and database_path and database_path != ":memory:":
        from database.migrations import migrate_sqlite_file
        version = await asyncio.to_thread(migrate_sqlite_file, database_path)
        logger.info("Database schema at version %s", version)
        return

    # In-memory SQLite and other databases: create the current schema only.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if engine.dialect.name != "sqlite":
        logger.warning("Schema migrations are SQLite-only; created missing tables only.")
