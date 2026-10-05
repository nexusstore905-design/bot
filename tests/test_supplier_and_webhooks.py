"""Supplier buttons, delivery proof, and store webhooks."""
import json
from types import SimpleNamespace

import httpx

from bot.handlers.supplier import cb_supplier_action, handle_supplier_proof
from conftest import (
    SUPPLIER_A,
    FakeBot,
    fake_user,
    make_order,
    make_store,
    make_user,
    run,
    sql,
)
from database.database import AsyncSessionLocal
from database.models import OrderStatus
from database.repositories.api_store_repo import ApiStoreRepository
from database.repositories.order_repo import OrderRepository
from services.dispatch import dispatch_order
from services.messages import supplier_order_text
from services.webhooks import deliver_due
from utils.security import sign_webhook


class FakeTelegramMessage:
    def __init__(self, chat_id, text_html="", message_id=1, photo=None, reply_to=None):
        self.chat = SimpleNamespace(id=chat_id)
        self.chat_id = chat_id
        self.text_html = text_html
        self.message_id = message_id
        self.photo = photo
        self.reply_to_message = reply_to
        self.edits: list[tuple[str, dict]] = []
        self.replies: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message

    async def answer(self, *args, **kwargs):
        pass


def test_supplier_button_handles_special_characters_and_notifies_customer():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")], player_id="a<b&c"))
    bot = FakeBot()
    run(dispatch_order(bot, order.id))
    part_id = sql("SELECT id FROM supplier_fulfillments")[0][0]
    # What Telegram returns as text_html for the delivered message (entities escaped).
    message_html = supplier_order_text(order.order_id, "UC", "a<b&c", [{"product_name": "UC pack", "quantity": 1}])
    assert "a&lt;b&amp;c" in message_html

    message = FakeTelegramMessage(SUPPLIER_A, text_html=message_html)
    update = SimpleNamespace(
        callback_query=FakeQuery(f"sup_done:{order.order_id}:{part_id}", message),
        effective_user=fake_user(),
    )
    run(cb_supplier_action(update, SimpleNamespace(bot=bot)))

    edited_text, kwargs = message.edits[0]
    assert "a&lt;b&amp;c" in edited_text and "a<b" not in edited_text
    assert kwargs["parse_mode"] == "HTML" and kwargs["reply_markup"] is None
    assert "Mark this group" not in edited_text
    assert sql("SELECT status FROM orders")[0][0] == "completed"
    assert any("complete" in m["text"] for m in bot.sent_to(111))


def test_supplier_button_from_wrong_group_is_rejected():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    run(dispatch_order(FakeBot(), order.id))
    part_id = sql("SELECT id FROM supplier_fulfillments")[0][0]
    message = FakeTelegramMessage(-100999, text_html="x")
    update = SimpleNamespace(
        callback_query=FakeQuery(f"sup_done:{order.order_id}:{part_id}", message),
        effective_user=fake_user(),
    )
    run(cb_supplier_action(update, SimpleNamespace(bot=FakeBot())))
    assert "different supplier group" in message.replies[0]
    assert sql("SELECT status FROM orders")[0][0] == "pending"


def test_photo_reply_is_forwarded_as_proof():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot()
    run(dispatch_order(bot, order.id))
    supplier_msg_id = sql("SELECT supplier_msg_id FROM supplier_fulfillments")[0][0]

    message = FakeTelegramMessage(
        SUPPLIER_A,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        reply_to=SimpleNamespace(message_id=supplier_msg_id),
    )
    run(handle_supplier_proof(SimpleNamespace(message=message), SimpleNamespace(bot=bot)))
    assert bot.photos[0]["chat_id"] == 111 and bot.photos[0]["photo"] == "large"
    assert sql("SELECT proof_file_id FROM supplier_fulfillments")[0][0] == "large"


def test_status_change_webhook_is_queued_signed_and_delivered():
    user = run(make_user(111))
    store, _ = run(make_store())

    async def setup():
        async with AsyncSessionLocal() as session:
            repo = ApiStoreRepository(session)
            loaded = await repo.get_by_id(store.id)
            secret = await repo.set_webhook(loaded, "https://shop.example/hooks")
        async with AsyncSessionLocal() as session:
            order = await OrderRepository(session).create_order(
                user_id=user.id, items=[{"product_id": None, "product_name": "UC", "quantity": 1}],
                player_id="5123456789", api_store_id=store.id,
            )
            await OrderRepository(session).update_status(order, OrderStatus.completed, "test")
        return secret, order.order_id

    secret, order_id = run(setup())
    received = []

    def handler(request: httpx.Request):
        received.append(request)
        return httpx.Response(200)

    async def deliver():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await deliver_due(client)

    assert run(deliver()) == 1
    request = received[0]
    payload = json.loads(request.content)
    assert payload["order_id"] == order_id and payload["status"] == "completed"
    timestamp, signature = (part.split("=", 1)[1] for part in request.headers["X-Nexus-Signature"].split(","))
    assert signature == sign_webhook(secret, int(timestamp), request.content)
    assert run(deliver()) == 0  # delivered once


def test_failed_webhook_backs_off():
    user = run(make_user(111))
    store, _ = run(make_store())

    async def setup():
        async with AsyncSessionLocal() as session:
            repo = ApiStoreRepository(session)
            await repo.set_webhook(await repo.get_by_id(store.id), "https://shop.example/hooks")
        async with AsyncSessionLocal() as session:
            order = await OrderRepository(session).create_order(
                user_id=user.id, items=[{"product_id": None, "product_name": "UC", "quantity": 1}],
                player_id="5123456789", api_store_id=store.id,
            )
            await OrderRepository(session).update_status(order, OrderStatus.failed, "test")

    run(setup())

    async def deliver():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            return await deliver_due(client)

    assert run(deliver()) == 0
    attempts, error, delivered = sql("SELECT attempts, last_error, delivered_at FROM webhook_events")[0]
    assert attempts == 1 and error == "HTTP 500" and delivered is None
    assert run(deliver()) == 0
    assert sql("SELECT attempts FROM webhook_events")[0][0] == 1  # not due again yet
