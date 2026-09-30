from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import event, inspect, text
import os
import sqlite3
from database.models import Base
from config.settings import DATABASE_URL

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
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    """Create tables and apply safe additive upgrades for existing databases."""
    has_orders_to_upgrade = False
    async with engine.connect() as conn:
        has_orders_to_upgrade, columns = await conn.run_sync(
            lambda sync_conn: (
                inspect(sync_conn).has_table("orders"),
                {column["name"] for column in inspect(sync_conn).get_columns("orders")}
                if inspect(sync_conn).has_table("orders") else set(),
            )
        )
    if has_orders_to_upgrade and "api_store_id" not in columns and engine.dialect.name == "sqlite":
        database_path = engine.url.database
        if database_path and database_path != ":memory:" and os.path.isfile(database_path):
            backup_path = database_path + ".pre-api-store-scope.bak"
            if not os.path.exists(backup_path):
                with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as backup:
                    source.backup(backup)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("orders")
            }
        )
        if "api_store_id" not in columns:
            await conn.execute(text(
                "ALTER TABLE orders ADD COLUMN api_store_id INTEGER"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_orders_api_store_id ON orders (api_store_id)"
            ))


async def get_session() -> AsyncSession:
    """Dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
