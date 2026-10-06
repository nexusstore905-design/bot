"""Regression tests for bugs found in review."""
import api.flask_app as flask_app
from bot.keyboards.customer_kb import CALLBACK_DATA_MAX_BYTES, categories_kb
from conftest import SUPPLIER_A, FakeBot, make_order, make_product, make_store, make_user, run, sql
from database.database import AsyncSessionLocal
from database.models import OrderStatus
from database.repositories.order_repo import OrderRepository
from services.dispatch import SKIPPED, dispatch_order


def test_long_category_names_fit_telegram_callback_limit():
    long_name = "PUBG Mobile UC Global Top-Up — Instant Delivery via Player ID (Pakistan)"
    keyboard = categories_kb(["Short", long_name, "ببجی یو سی ٹاپ اپ " * 3], "en")
    data = [row[0].callback_data for row in keyboard.inline_keyboard[:3]]
    assert data[0] == "cat:Short"
    assert data[1] == "cat_i:1" and data[2] == "cat_i:2"
    assert all(len(d.encode("utf-8")) <= CALLBACK_DATA_MAX_BYTES for d in data)


class ClosingBot(FakeBot):
    """Simulates an admin closing the order while the supplier card is in flight."""

    def __init__(self, order_pk):
        super().__init__()
        self.order_pk = order_pk

    async def send_message(self, chat_id, text, **kwargs):
        if kwargs.get("reply_markup") is not None:
            async with AsyncSessionLocal() as session:
                repo = OrderRepository(session)
                order = await repo.get_by_order_id(
                    sql("SELECT order_id FROM orders WHERE id = ?", (self.order_pk,))[0][0]
                )
                await repo.admin_close_order(order, OrderStatus.cancelled, "admin:test")
        return await super().send_message(chat_id, text, **kwargs)


def test_card_sent_after_order_closed_is_withdrawn():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = ClosingBot(order.id)
    outcomes = run(dispatch_order(bot, order.id))
    assert outcomes[0].result == SKIPPED
    card_id = 1001  # the first FakeBot message
    assert any(edit["message_id"] == card_id and edit["reply_markup"] is None for edit in bot.edited)
    assert "Do not process" in bot.edited[-1]["text"]
    assert sql("SELECT status FROM orders")[0][0] == "cancelled"


def test_api_rejects_boolean_and_fractional_ids():
    run(make_user(111))
    product = run(make_product())
    _, key = run(make_store())
    client = flask_app.app.test_client()
    for bad in ({"telegram_user_id": True}, {"telegram_user_id": 111.9}, {"product_id": "1.5"}):
        body = {"telegram_user_id": 111, "product_id": product.id, "player_id": "5123456789", **bad}
        response = client.post("/v1/orders/", json=body, headers={"X-API-Key": key})
        assert response.status_code == 400, (bad, response.get_json())
    assert sql("SELECT COUNT(*) FROM orders")[0][0] == 0
