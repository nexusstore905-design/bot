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
    """Create tables and apply safe upgrades for existing databases."""
    has_orders_to_upgrade = False
    needs_nullable_product_migration = False
    async with engine.connect() as conn:
        def inspect_existing_schema(sync_conn):
            inspector = inspect(sync_conn)
            has_orders = inspector.has_table("orders")
            order_columns = (
                {column["name"] for column in inspector.get_columns("orders")}
                if has_orders else set()
            )
            has_order_items = inspector.has_table("order_items")
            product_column = next(
                (column for column in inspector.get_columns("order_items")
                 if column["name"] == "product_id"),
                None,
            ) if has_order_items else None
            return has_orders, order_columns, bool(product_column and not product_column["nullable"])

        has_orders_to_upgrade, columns, needs_nullable_product_migration = await conn.run_sync(
            inspect_existing_schema
        )

    database_path = engine.url.database
    if has_orders_to_upgrade and "api_store_id" not in columns and engine.dialect.name == "sqlite":
        if database_path and database_path != ":memory:" and os.path.isfile(database_path):
            backup_path = database_path + ".pre-api-store-scope.bak"
            if not os.path.exists(backup_path):
                with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as backup:
                    source.backup(backup)

    if needs_nullable_product_migration and engine.dialect.name == "sqlite":
        if not database_path or database_path == ":memory:":
            raise RuntimeError("Cannot migrate order history for this SQLite database URL.")
        backup_path = database_path + ".pre-product-reset.bak"
        if os.path.isfile(database_path) and not os.path.exists(backup_path):
            with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as backup:
                source.backup(backup)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        settled_timestamp_type = (
            "TIMESTAMP WITH TIME ZONE"
            if conn.dialect.name == "postgresql" else "TIMESTAMP"
        )
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
            columns.add("api_store_id")
        if "settled_at" not in columns:
            await conn.execute(text(
                f"ALTER TABLE orders ADD COLUMN settled_at {settled_timestamp_type}"
            ))
        if "settled_by" not in columns:
            await conn.execute(text(
                "ALTER TABLE orders ADD COLUMN settled_by VARCHAR(128)"
            ))
        if "customer_msg_id" not in columns:
            await conn.execute(text(
                "ALTER TABLE orders ADD COLUMN customer_msg_id BIGINT"
            ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_orders_settled_at ON orders (settled_at)"
        ))

    if needs_nullable_product_migration:
        if engine.dialect.name != "sqlite":
            raise RuntimeError("Order-item product reset migration currently supports SQLite only.")
        _migrate_order_items_product_id(database_path)


def _migrate_order_items_product_id(database_path: str) -> None:
    """Allow products to reset while retaining product-name snapshots in old orders."""
    with sqlite3.connect(database_path, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE order_items__product_reset (
                    id INTEGER NOT NULL PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER,
                    product_name VARCHAR(128) NOT NULL,
                    quantity INTEGER NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders (id),
                    FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE SET NULL
                )
            """)
            conn.execute("""
                INSERT INTO order_items__product_reset
                    (id, order_id, product_id, product_name, quantity)
                SELECT id, order_id, product_id, product_name, quantity FROM order_items
            """)
            conn.execute("DROP TABLE order_items")
            conn.execute("ALTER TABLE order_items__product_reset RENAME TO order_items")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"Foreign-key check failed during order-item migration: {violations[:3]}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")


async def get_session() -> AsyncSession:
    """Dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
