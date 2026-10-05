"""Customer flows through the real handlers: browse, search, checkout, reorder, balance, language, invites."""
import string
from types import SimpleNamespace

from bot.handlers import auth, customer, home
from bot.i18n import TEXTS
from bot.middlewares import auth_middleware
from bot.states.states import ORDER_CONFIRM, ORDER_SELECT_PRODUCT
from conftest import SUPPLIER_A, FakeBot, make_product, make_user, run, sql
from database.database import AsyncSessionLocal
from database.repositories.user_repo import AccessCodeRepository


class FakeChatMessage:
    def __init__(self, text=""):
        self.message_id = 4242
        self.text = text
        self.texts: list[str] = []
        self.markups: list = []

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)

    async def delete(self):
        pass


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeChatMessage()
        self.toasts: list = []

    async def answer(self, text=None, *args, **kwargs):
        self.toasts.append(text)

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.message.texts.append(text)
        self.message.markups.append(reply_markup)


class FakeChat:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, text, **kwargs):
        self.sent.append(text)


def tg_user(telegram_id=111, language_code="en"):
    return SimpleNamespace(
        id=telegram_id, username=None, full_name="Test User", first_name="Test", language_code=language_code,
    )


def callback(data, telegram_id=111):
    auth_middleware.USER_LAST_REQUEST.clear()  # skip the anti-flood delay between test steps
    return SimpleNamespace(
        callback_query=FakeCallbackQuery(data), message=None,
        effective_user=tg_user(telegram_id), effective_chat=FakeChat(),
    )


def text_message(text, telegram_id=111):
    auth_middleware.USER_LAST_REQUEST.clear()
    message = FakeChatMessage(text)
    return SimpleNamespace(
        callback_query=None, message=message, effective_user=tg_user(telegram_id), effective_chat=FakeChat(),
    )


def button_labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_checkout_reorder_and_balance():
    run(make_user(111))
    product = run(make_product(name="660 UC"))
    bot = FakeBot()
    context = SimpleNamespace(bot=bot, user_data={
        "cart": [{"product_id": product.id, "product_name": product.name, "category": product.category, "quantity": 2}],
        "player_id": "5123456789",
    })

    update = callback("confirm_order")
    run(customer.cb_confirm_order(update, context))
    final_text = update.callback_query.message.texts[-1]
    assert "Order sent" in final_text
    assert "▰▰▰▱" in final_text and "Sent to supplier" in final_text  # live timeline
    assert "USDT" not in final_text and "Total" not in final_text  # never any prices

    order_id = sql("SELECT order_id FROM orders")[0][0]
    assert sql("SELECT customer_msg_id FROM orders")[0][0] == 4242
    assert sql("SELECT player_id FROM saved_player_ids") == [("5123456789",)]
    assert len(bot.sent_to(SUPPLIER_A)) == 1
    assert "cart" not in context.user_data

    sql("UPDATE orders SET status = 'completed'")
    reorder = callback(f"reorder:{order_id}")
    assert run(customer.cb_reorder(reorder, context)) == ORDER_CONFIRM
    assert context.user_data["player_id"] == "5123456789"
    assert context.user_data["cart"][0]["quantity"] == 2

    balance = callback("balance")
    run(customer.cb_balance(balance, SimpleNamespace(bot=bot, user_data={})))
    balance_text = balance.callback_query.message.texts[-1]
    assert "awaiting payment: <b>1</b>" in balance_text and "USDT" not in balance_text


def test_adding_to_cart_stays_in_package_list_with_cart_button():
    run(make_user(111))
    product = run(make_product(name="325 UC"))
    context = SimpleNamespace(bot=FakeBot(), user_data={"cart": [], "temp_cat": "PUBG UC", "temp_item": {
        "product_id": product.id, "product_name": product.name, "category": product.category,
    }})
    update = callback("qty:3")
    assert run(customer.cb_select_quantity(update, context)) == ORDER_SELECT_PRODUCT
    assert update.callback_query.toasts[0] == "Added to cart ✓"
    labels = button_labels(update.callback_query.message.markups[-1])
    assert "🛒 Cart · 3" in labels and "💎 325 UC" in labels


def test_search_finds_packages_by_name():
    run(make_user(111))
    run(make_product(name="8100 UC"))
    run(make_product(name="Royale Pass", category="PUBG Extras"))
    context = SimpleNamespace(bot=FakeBot(), user_data={"cart": []})
    update = text_message("royale")
    assert run(customer.msg_search(update, context)) == ORDER_SELECT_PRODUCT
    assert button_labels(update.message.markups[-1])[0] == "💎 Royale Pass"


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
    assert "couldn't be found" in intruder.callback_query.message.texts[-1]

    view = callback(f"view_order:{order_id}:0", 222)
    run(customer.cb_view_order(view, SimpleNamespace(bot=FakeBot(), user_data={})))
    assert "couldn't be found" in view.callback_query.message.texts[-1]


def test_urdu_customer_sees_urdu_home_and_order_card():
    run(make_user(111, language="ur"))
    product = run(make_product())
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    menu = callback("main_menu")
    run(home.cb_main_menu(menu, context))
    assert "خوش آمدید" in menu.callback_query.message.texts[-1]
    assert "✨ نیا آرڈر" in button_labels(menu.callback_query.message.markups[-1])

    context.user_data.update({
        "cart": [{"product_id": product.id, "product_name": product.name, "category": product.category, "quantity": 1}],
        "player_id": "5123456789",
    })
    confirm = callback("confirm_order")
    run(customer.cb_confirm_order(confirm, context))
    assert "آرڈر بھیج دیا گیا" in confirm.callback_query.message.texts[-1]


def test_language_switch_is_saved():
    run(make_user(111))
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    update = callback("set_lang:ur")
    run(home.cb_set_language(update, context))
    assert sql("SELECT language FROM users WHERE telegram_id = 111") == [("ur",)]
    assert update.callback_query.toasts[0] == "✅ زبان اردو کر دی گئی۔"


def test_invite_link_signs_in_with_one_tap():
    async def create_code():
        async with AsyncSessionLocal() as session:
            return (await AccessCodeRepository(session).create(label="Ali")).code

    code = run(create_code())
    update = text_message(f"/start {code}", telegram_id=4321)
    context = SimpleNamespace(bot=FakeBot(), user_data={}, args=[code])
    run(auth.cmd_start(update, context))
    assert sql("SELECT auth_status, last_login IS NOT NULL FROM users WHERE telegram_id = 4321") == [("authenticated", 1)]
    assert "You're in" in update.effective_chat.sent[-1]

    wrong = text_message("/start NOPE", telegram_id=5555)
    run(auth.cmd_start(wrong, SimpleNamespace(bot=FakeBot(), user_data={}, args=["NOPE"])))
    assert "4 attempt(s) left" in wrong.effective_chat.sent[-1]


def test_every_text_has_both_languages_with_matching_placeholders():
    english, urdu = TEXTS["en"], TEXTS["ur"]
    assert set(english) == set(urdu)
    fields = string.Formatter()
    for key in english:
        en_fields = {name for _, name, _, _ in fields.parse(english[key]) if name}
        ur_fields = {name for _, name, _, _ in fields.parse(urdu[key]) if name}
        assert en_fields == ur_fields, key
