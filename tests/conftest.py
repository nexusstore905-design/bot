"""Test setup: an isolated SQLite database and a fake Telegram bot.

Environment variables are set before any project module is imported, because
config.settings reads them at import time.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="nexus-tests-"))
TEST_DB = _TMP / "test.db"
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DB.as_posix()}",
    "BOT_TOKEN": "123:test-token",
    "ADMIN_IDS": "999",
    "SUPPLIER_CHAT_ID": "-100500",
    "API_KEY": "master-test-key",
    "API_CORS_ORIGINS": "https://shop.example",
    "SUPPLIER_TIMEOUT_MINUTES": "10",
})

from database.database import AsyncSessionLocal, init_db  # noqa: E402
from database.models import (  # noqa: E402
    AuthStatus, Product, User,
)
from database.repositories.api_store_repo import ApiStoreRepository  # noqa: E402
from database.repositories.order_repo import OrderRepository  # noqa: E402
from services import app_settings  # noqa: E402
from utils.helpers import utcnow  # noqa: E402

asyncio.run(init_db())

SUPPLIER_A = -100111
SUPPLIER_B = -100222
ADMIN_ID = 999


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_database():
    """Empty every table (except the schema version) before each test."""
    conn = sqlite3.connect(TEST_DB)
    conn.execute("PRAGMA foreign_keys=OFF")
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in tables:
        if table == "app_settings":
            conn.execute("DELETE FROM app_settings WHERE key != 'schema_version'")
        else:
            conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    app_settings._cache.clear()
    yield


class FakeMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id


class FakeBot:
    """Records Telegram calls. Chats in `fail_chats` raise on send."""

    def __init__(self, fail_chats=()):
        self.fail_chats = set(fail_chats)
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.photos: list[dict] = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_chats:
            raise RuntimeError("Forbidden: bot was kicked from the group")
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return FakeMessage(self._next_id)

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kwargs):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})
        return True

    async def send_photo(self, chat_id, photo, **kwargs):
        self.photos.append({"chat_id": chat_id, "photo": photo, **kwargs})
        return FakeMessage(1)

    def sent_to(self, chat_id) -> list[dict]:
        return [message for message in self.sent if message["chat_id"] == chat_id]


# ─── Data helpers ─────────────────────────────────────────────────────

async def make_user(telegram_id: int = 111, member: bool = True, revoked: bool = False) -> User:
    async with AsyncSessionLocal() as session:
        user = User(
            telegram_id=telegram_id,
            full_name=f"User {telegram_id}",
            auth_status=AuthStatus.revoked if revoked else (
                AuthStatus.authenticated if member else AuthStatus.unauthenticated
            ),
            last_login=utcnow() if member or revoked else None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def make_product(name="60 UC", category="PUBG UC", price=None, chat=SUPPLIER_A) -> Product:
    async with AsyncSessionLocal() as session:
        product = Product(category=category, name=name, price=price, supplier_chat_id=chat)
        session.add(product)
        await session.commit()
        await session.refresh(product)
        return product


async def make_store(name="Shop", daily_limit=0):
    async with AsyncSessionLocal() as session:
        return await ApiStoreRepository(session).create(name, daily_limit)


async def make_order(user, parts: list[tuple[int, str]], player_id="5123456789"):
    """Create an order with one supplier part per (chat_id, category)."""
    async with AsyncSessionLocal() as session:
        return await OrderRepository(session).create_order(
            user_id=user.id,
            items=[{"product_id": None, "product_name": f"{category} pack", "quantity": 1} for _, category in parts],
            player_id=player_id,
            fulfillments=[
                {"supplier_chat_id": chat, "category": category,
                 "items": [{"product_name": f"{category} pack", "quantity": 1}]}
                for chat, category in parts
            ],
        )


def sql(statement: str, params=()):
    conn = sqlite3.connect(TEST_DB)
    try:
        rows = conn.execute(statement, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def minutes_ago(minutes: int) -> str:
    return (utcnow() - timedelta(minutes=minutes)).replace(tzinfo=None).isoformat(sep=" ")


def fake_user(user_id=555, full_name="Supplier Sam"):
    return SimpleNamespace(id=user_id, full_name=full_name, username=None)
