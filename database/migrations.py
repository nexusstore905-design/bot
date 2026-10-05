"""Versioned SQLite schema migrations.

The schema version lives in app_settings["schema_version"]. Each migration runs
in its own IMMEDIATE transaction and is written to tolerate partially upgraded
databases, so a database from any earlier release upgrades in one start-up.
Both the bot task and the WSGI app call this, guarded by a file lock.
"""
import logging
import os
import sqlite3

from sqlalchemy import create_engine

from database.models import Base
from utils.filelock import file_lock
from utils.security import api_key_prefix, hash_api_key

logger = logging.getLogger(__name__)

VERSION_KEY = "schema_version"


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, bool]:
    """Return {column_name: is_not_null} for a table."""
    return {row[1]: bool(row[3]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _m001_legacy_columns(conn: sqlite3.Connection) -> None:
    """Upgrades that earlier releases applied ad hoc in init_db()."""
    _add_column(conn, "orders", "api_store_id", "INTEGER")
    _add_column(conn, "orders", "settled_at", "TIMESTAMP")
    _add_column(conn, "orders", "settled_by", "VARCHAR(128)")
    _add_column(conn, "orders", "customer_msg_id", "BIGINT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_orders_api_store_id ON orders (api_store_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_orders_settled_at ON orders (settled_at)")
    _add_column(conn, "products", "supplier_chat_id", "BIGINT")

    # Allow products to be reset while order history keeps product-name snapshots.
    if _columns(conn, "order_items").get("product_id"):
        conn.execute("""
            CREATE TABLE order_items__new (
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
            INSERT INTO order_items__new (id, order_id, product_id, product_name, quantity)
            SELECT id, order_id, product_id, product_name, quantity FROM order_items
        """)
        conn.execute("DROP TABLE order_items")
        conn.execute("ALTER TABLE order_items__new RENAME TO order_items")


def _m002_product_price_optional(conn: sqlite3.Connection) -> None:
    """An old release left products.price as FLOAT NOT NULL, which broke every insert."""
    columns = _columns(conn, "products")
    if "price" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN price FLOAT")
        return
    if not columns["price"]:
        return
    conn.execute("""
        CREATE TABLE products__new (
            id INTEGER NOT NULL PRIMARY KEY,
            category VARCHAR(64) NOT NULL,
            name VARCHAR(128) NOT NULL,
            price FLOAT,
            is_active BOOLEAN NOT NULL,
            supplier_chat_id BIGINT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO products__new
            (id, category, name, price, is_active, supplier_chat_id, created_at, updated_at)
        SELECT id, category, name, price, is_active, supplier_chat_id, created_at, updated_at
        FROM products
    """)
    conn.execute("DROP TABLE products")
    conn.execute("ALTER TABLE products__new RENAME TO products")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_products_category ON products (category)")


def _m003_order_idempotency_and_prices(conn: sqlite3.Connection) -> None:
    _add_column(conn, "orders", "idempotency_key", "VARCHAR(64)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_store_idem "
        "ON orders (api_store_id, idempotency_key)"
    )
    _add_column(conn, "order_items", "unit_price", "FLOAT")


def _m004_fulfillment_tracking(conn: sqlite3.Connection) -> None:
    _add_column(conn, "supplier_fulfillments", "dispatch_attempts", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "supplier_fulfillments", "next_attempt_at", "DATETIME")
    _add_column(conn, "supplier_fulfillments", "dispatched_at", "DATETIME")
    _add_column(conn, "supplier_fulfillments", "responded_at", "DATETIME")
    _add_column(conn, "supplier_fulfillments", "proof_file_id", "VARCHAR(256)")
    # Existing delivered parts: the best available delivery time is their creation time.
    conn.execute("""
        UPDATE supplier_fulfillments SET dispatched_at = created_at
        WHERE dispatched_at IS NULL AND supplier_msg_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE supplier_fulfillments SET responded_at = updated_at
        WHERE responded_at IS NULL AND status IN ('completed', 'failed')
          AND changed_by LIKE 'supplier:%'
    """)


def _m005_hashed_store_keys(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "api_stores")
    if "api_key" in columns:
        rows = conn.execute("""
            SELECT id, name, api_key, is_active, daily_limit, orders_today, last_reset, created_at
            FROM api_stores
        """).fetchall()
        conn.execute("""
            CREATE TABLE api_stores__new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(64) NOT NULL,
                api_key_hash VARCHAR(64) NOT NULL,
                api_key_prefix VARCHAR(16) NOT NULL,
                is_active BOOLEAN NOT NULL,
                daily_limit INTEGER NOT NULL,
                orders_today INTEGER NOT NULL,
                last_reset DATETIME,
                webhook_url VARCHAR(512),
                webhook_secret VARCHAR(64),
                created_at DATETIME NOT NULL,
                UNIQUE (name)
            )
        """)
        conn.executemany(
            """
            INSERT INTO api_stores__new
                (id, name, api_key_hash, api_key_prefix, is_active, daily_limit,
                 orders_today, last_reset, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (row[0], row[1], hash_api_key(row[2]), api_key_prefix(row[2]),
                 row[3], row[4], row[5], row[6], row[7])
                for row in rows
            ],
        )
        conn.execute("DROP TABLE api_stores")
        conn.execute("ALTER TABLE api_stores__new RENAME TO api_stores")
    else:
        _add_column(conn, "api_stores", "webhook_url", "VARCHAR(512)")
        _add_column(conn, "api_stores", "webhook_secret", "VARCHAR(64)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_api_stores_api_key_hash ON api_stores (api_key_hash)"
    )


MIGRATIONS = [
    (1, _m001_legacy_columns),
    (2, _m002_product_price_optional),
    (3, _m003_order_idempotency_and_prices),
    (4, _m004_fulfillment_tracking),
    (5, _m005_hashed_store_keys),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def _read_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (VERSION_KEY,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except ValueError:
        return 0


def _write_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (VERSION_KEY, str(version)),
    )


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def migrate_sqlite_file(path: str) -> int:
    """Create missing tables and apply pending migrations. Returns the final version."""
    with file_lock(path + ".migrate.lock"):
        conn = _connect(path)
        try:
            existed = _has_table(conn, "orders")
        finally:
            conn.close()

        engine = create_engine(f"sqlite:///{path}")
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()

        conn = _connect(path)
        try:
            if not existed:
                _write_version(conn, LATEST_VERSION)
                return LATEST_VERSION

            version = _read_version(conn)
            pending = [(number, step) for number, step in MIGRATIONS if number > version]
            if not pending:
                return version

            backup_path = f"{path}.pre-schema-v{LATEST_VERSION}.bak"
            if not os.path.exists(backup_path):
                with sqlite3.connect(backup_path) as backup:
                    conn.backup(backup)
                logger.info("Backed up database to %s before migrating", backup_path)

            for number, step in pending:
                conn.execute("PRAGMA foreign_keys=OFF")
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    # Another process may have finished this step while we waited.
                    if _read_version(conn) >= number:
                        conn.execute("COMMIT")
                        continue
                    step(conn)
                    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise RuntimeError(
                            f"Foreign-key check failed in migration {number}: {violations[:3]}"
                        )
                    _write_version(conn, number)
                    conn.execute("COMMIT")
                    logger.info("Applied schema migration %s (%s)", number, step.__name__)
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                finally:
                    conn.execute("PRAGMA foreign_keys=ON")
            return _read_version(conn)
        finally:
            conn.close()
