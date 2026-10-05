"""Customer checkout, reorder, and balance through the real handlers."""
from types import SimpleNamespace

from bot.handlers import customer
from bot.middlewares import auth_middleware
from bot.states.states import ORDER_CONFIRM
from conftest import SUPPLIER_A, FakeBot, make_product, make_user, run, sql
from services import app_settings


class FakeCallbackMessage:
    def __init__(self):
        self.message_id = 4242
        self.texts: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeCallbackMessage()

    async def answer(self, *args, **kwargs):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.message.texts.append(text)


def callback(data, telegram_id=111):
    auth_middleware.USER_LAST_REQUEST.clear()  # skip the anti-flood delay between test steps
    user = SimpleNamespace(id=telegram_id, username=None, full_name="Test User", first_name="Test")
    return SimpleNamespace(callback_query=FakeCallbackQuery(data), message=None, effective_user=user)


def test_checkout_reorder_and_balance():
    run(make_user(111))
    product = run(make_product(name="660 UC", price=9.99))
    run(app_settings.set_setting(app_settings.SHOW_PRICES, "on"))
    bot = FakeBot()
    context = SimpleNamespace(bot=bot, user_data={
        "cart": [{"product_id": product.id, "product_name": product.name, "category": product.category,
                  "quantity": 2, "unit_price": product.price}],
        "player_id": "5123456789",
    })

    update = callback("confirm_order")
    run(customer.cb_confirm_order(update, context))
    final_text = update.callback_query.message.texts[-1]
    assert "Order sent to suppliers" in final_text
    assert "19.98" in final_text  # 2 × 9.99 total shown when prices are on

    order_id, unit_price = sql("SELECT o.order_id, i.unit_price FROM orders o JOIN order_items i ON i.order_id = o.id")[0]
    assert unit_price == 9.99
    assert sql("SELECT customer_msg_id FROM orders")[0][0] == 4242
    assert sql("SELECT player_id FROM saved_player_ids") == [("5123456789",)]
    assert len(bot.sent_to(SUPPLIER_A)) == 1
    assert "cart" not in context.user_data

    # Reorder loads the same cart and player ID straight to the review step.
    sql("UPDATE orders SET status = 'completed'")
    reorder = callback(f"reorder:{order_id}")
    state = run(customer.cb_reorder(reorder, context))
    assert state == ORDER_CONFIRM
    assert context.user_data["player_id"] == "5123456789"
    assert context.user_data["cart"][0]["quantity"] == 2

    balance = callback("balance")
    run(customer.cb_balance(balance, SimpleNamespace(bot=bot, user_data={})))
    balance_text = balance.callback_query.message.texts[-1]
    assert "awaiting payment: <b>1</b>" in balance_text and "19.98" in balance_text


def test_other_customers_cannot_reorder_or_view_an_order():
    owner = run(make_user(111))
    run(make_user(222))
    product = run(make_product())
    context = SimpleNamespace(bot=FakeBot(), user_data={
        "cart": [{"product_id": product.id, "product_name": product.name, "category": product.category, "quantity": 1}],
        "player_id": "5123456789",
    })
    run(customer.cb_confirm_order(callback("confirm_order", owner.telegram_id), context))
    order_id = sql("SELECT order_id FROM orders")[0][0]

    intruder = callback(f"reorder:{order_id}", 222)
    run(customer.cb_reorder(intruder, SimpleNamespace(bot=FakeBot(), user_data={})))
    assert "could not be found" in intruder.callback_query.message.texts[-1]

    view = callback(f"view_order:{order_id}:0", 222)
    run(customer.cb_view_order(view, SimpleNamespace(bot=FakeBot(), user_data={})))
    assert "could not be found" in view.callback_query.message.texts[-1]
