"""Upgrading a real pre-versioning production schema."""
import sqlite3
from pathlib import Path

from database.migrations import LATEST_VERSION, migrate_sqlite_file
from utils.security import hash_api_key

LEGACY_SCHEMA = Path(__file__).parent / "fixtures" / "legacy_schema.sql"


def build_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA.read_text(encoding="utf-8"))
    conn.execute("""INSERT INTO products (id, category, name, price, is_active, created_at, updated_at, supplier_chat_id)
                    VALUES (1, 'PUBG UC', '60 UC', 0.99, 1, '2026-01-01', '2026-01-01', -100)""")
    conn.execute("""INSERT INTO api_stores (name, api_key, is_active, daily_limit, orders_today, last_reset, created_at)
                    VALUES ('Shop', 'nxs_plaintextkey123', 1, 5, 0, '2026-01-01', '2026-01-01')""")
    conn.execute("""INSERT INTO users (id, telegram_id, auth_status, failed_attempts, created_at, updated_at)
                    VALUES (1, 111, 'authenticated', 0, '2026-01-01', '2026-01-01')""")
    conn.execute("""INSERT INTO orders (id, order_id, user_id, status, player_id, created_at, updated_at)
                    VALUES (1, 'NX123456', 1, 'pending', '5123', '2026-01-01', '2026-01-01')""")
    conn.execute("""INSERT INTO supplier_fulfillments
                    (id, order_id, supplier_chat_id, category, items_snapshot, status, supplier_msg_id, created_at, updated_at)
                    VALUES (1, 1, -100, 'UC', '[]', 'pending', 55, '2026-01-01 10:00:00', '2026-01-01 10:00:00')""")
    conn.commit()
    conn.close()


def columns(conn, table):
    return {row[1]: row[3] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_legacy_database_upgrades_in_place(tmp_path):
    db = tmp_path / "legacy.db"
    build_legacy_db(db)

    assert migrate_sqlite_file(str(db)) == LATEST_VERSION
    assert migrate_sqlite_file(str(db)) == LATEST_VERSION  # idempotent
    assert (tmp_path / f"legacy.db.pre-schema-v{LATEST_VERSION}.bak").exists()

    conn = sqlite3.connect(db)
    assert columns(conn, "products")["price"] == 0  # nullable now
    assert conn.execute("SELECT price FROM products").fetchone() == (0.99,)
    # Products can be inserted again without a price (the old NOT NULL broke this).
    conn.execute("INSERT INTO products (category, name, is_active, created_at, updated_at) VALUES ('X', 'Y', 1, '2026', '2026')")

    store_columns = columns(conn, "api_stores")
    assert "api_key" not in store_columns and {"api_key_hash", "webhook_url"} <= set(store_columns)
    assert conn.execute("SELECT api_key_hash, api_key_prefix FROM api_stores").fetchone() == (
        hash_api_key("nxs_plaintextkey123"), "nxs_plaintex",
    )
    assert {"idempotency_key"} <= set(columns(conn, "orders"))
    assert "language" in columns(conn, "users")
    assert conn.execute("SELECT dispatched_at FROM supplier_fulfillments").fetchone()[0] is not None
    for table in ("saved_player_ids", "webhook_events", "admin_audit_log", "support_messages", "api_store_customers"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_fresh_database_starts_at_latest_version(tmp_path):
    db = tmp_path / "fresh.db"
    assert migrate_sqlite_file(str(db)) == LATEST_VERSION
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone() == (str(LATEST_VERSION),)
    conn.close()
