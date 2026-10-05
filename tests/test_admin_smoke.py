"""Drive every admin screen and action once to catch runtime errors."""
from types import SimpleNamespace

import pytest

from bot.handlers.admin import (
    access, customers, dashboard, limits, menu, orders, products, stores,
)
from conftest import (
    ADMIN_ID, SUPPLIER_A, FakeBot, make_order, make_product, make_store, make_user, run, sql,
)
from services.dispatch import dispatch_order


class AdminBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.documents = []

    async def get_chat(self, chat_id):
        return SimpleNamespace(id=chat_id, title=f"Group {chat_id}")

    async def get_me(self):
        return SimpleNamespace(username="nexus_test_bot")

    async def send_document(self, chat_id, document, **kwargs):
        self.documents.append({"chat_id": chat_id, "document": document, **kwargs})


class AdminMessage:
    def __init__(self, text=""):
        self.text = text
        self.chat_id = ADMIN_ID
        self.message_id = 1
        self.forward_origin = None
        self.forward_from_chat = None
        self.outputs: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.outputs.append(text)

    async def reply_text(self, text, **kwargs):
        self.outputs.append(text)

    async def edit_reply_markup(self, **kwargs):
        self.outputs.append("<markup>")


class AdminQuery:
    def __init__(self, data):
        self.data = data
        self.message = AdminMessage()
        self.answers = 0

    async def answer(self, *args, **kwargs):
        self.answers += 1
        assert self.answers == 1, "callback answered twice"

    async def edit_message_text(self, text, **kwargs):
        self.message.outputs.append(text)


ADMIN = SimpleNamespace(id=ADMIN_ID, full_name="Admin <One>", username="boss")


def press(handler, data, bot, user_data=None):
    update = SimpleNamespace(
        callback_query=AdminQuery(data), message=None, effective_user=ADMIN,
        effective_chat=SimpleNamespace(id=ADMIN_ID), effective_message=None,
    )
    context = SimpleNamespace(bot=bot, user_data=user_data if user_data is not None else {})
    run(handler(update, context))
    return update.callback_query.message.outputs


def send(handler, text, bot, user_data):
    message = AdminMessage(text)
    update = SimpleNamespace(
        callback_query=None, message=message, effective_user=ADMIN,
        effective_chat=SimpleNamespace(id=ADMIN_ID), effective_message=message,
    )
    run(handler(update, SimpleNamespace(bot=bot, user_data=user_data)))
    return message.outputs


@pytest.fixture
def world():
    user = run(make_user(111))
    product = run(make_product(price=1.5))
    store, _ = run(make_store("Shop <&>"))
    order = run(make_order(user, [(SUPPLIER_A, "UC <b>")], player_id="p<1>"))
    bot = AdminBot()
    run(dispatch_order(bot, order.id))
    return SimpleNamespace(user=user, product=product, store=store, order=order, bot=bot)


def test_admin_screens_render(world):
    bot = world.bot
    screens = [
        (menu.cb_admin_menu, "admin_menu"),
        (menu.cb_admin_advanced, "adm_advanced"),
        (menu.cb_admin_api_info, "adm_api_info"),
        (dashboard.cb_admin_stats, "adm_stats"),
        (dashboard.cb_supplier_stats, "adm_supplier_stats"),
        (dashboard.cb_audit_log, "adm_audit"),
        (orders.cb_admin_orders, "adm_orders"),
        (orders.cb_orders_by_status, "adm_orders_pending"),
        (customers.cb_admin_users, "adm_users"),
        (customers.cb_customer_details, "adm_customer:111"),
        (customers.cb_customer_orders, "adm_customer_orders:111:0"),
        (customers.cb_customer_unsettled, "adm_customer_unsettled:111:0"),
        (customers.cb_customer_settle_start, "adm_customer_settle:111"),
        (products.cb_admin_products, "adm_products"),
        (products.cb_admin_product_advanced, "adm_product_advanced"),
        (products.cb_list_products, "adm_list_products"),
        (products.cb_set_price_start, "adm_set_price"),
        (products.cb_set_supplier_start, "adm_set_supplier"),
        (products.cb_edit_name_start, "adm_edit_name"),
        (access.cb_admin_pin, "adm_pin"),
        (access.cb_list_codes, "adm_list_codes"),
        (stores.cb_api_stores, "adm_api_stores"),
        (stores.cb_list_stores, "adm_list_stores"),
        (stores.cb_store_view, f"store_view:{world.store.id}"),
        (limits.cb_user_limits, "adm_user_limits"),
        (limits.cb_list_user_limits, "adm_list_user_limits"),
    ]
    for handler, data in screens:
        outputs = press(handler, data, bot)
        assert outputs, f"{handler.__name__} produced no output"
    # User-controlled names are escaped wherever they appear.
    store_view = press(stores.cb_store_view, f"store_view:{world.store.id}", bot)[0]
    assert "Shop &lt;&amp;&gt;" in store_view


def test_admin_order_actions(world):
    bot = world.bot
    order_id = world.order.order_id
    view = send(orders.admin_search_order, order_id, bot, {})[0]
    assert "p&lt;1&gt;" in view and "UC &lt;b&gt;" in view

    # Resend closes the old supplier message and delivers a new one.
    press(orders.cb_resend, f"adm_resend:{order_id}", bot)
    assert len(bot.sent_to(SUPPLIER_A)) == 2
    assert sql("SELECT status FROM supplier_fulfillments") == [("pending",)]

    # Reassign moves the open part to another group.
    data = {}
    press(orders.cb_reassign_start, f"adm_reassign:{order_id}", bot, data)
    send(orders.admin_reassign_value, "-100777", bot, data)
    assert sql("SELECT supplier_chat_id, status FROM supplier_fulfillments") == [(-100777, "pending")]

    press(orders.cb_set_status, f"set_status:{order_id}:completed", bot)
    assert sql("SELECT status FROM orders") == [("completed",)]
    assert sql("SELECT status FROM supplier_fulfillments") == [("completed",)]
    assert any("complete" in m["text"] for m in bot.sent_to(111))
    assert sql("SELECT action FROM admin_audit_log ORDER BY id") == [
        ("resend_order",), ("reassign_order",), ("set_order_status",),
    ]


def test_admin_settings_and_store_management(world):
    bot = world.bot
    press(menu.cb_toggle_power, "adm_toggle_power", bot)
    press(menu.cb_toggle_prices, "adm_toggle_prices", bot)
    assert dict(sql("SELECT key, value FROM app_settings WHERE key IN ('maintenance', 'show_prices')")) == {
        "maintenance": "on", "show_prices": "on",
    }

    data = {}
    press(products.cb_set_price_start, "adm_set_price", bot, data)
    send(products.admin_set_price_value, f"#{world.product.id} | 2,50", bot, data)
    assert sql("SELECT price FROM products") == [(2.5,)]

    old_hash = sql("SELECT api_key_hash FROM api_stores")[0][0]
    rotated = press(stores.cb_store_rotate_confirm, f"store_rotate_ok:{world.store.id}", bot)[0]
    assert "nxs_" in rotated and sql("SELECT api_key_hash FROM api_stores")[0][0] != old_hash

    data = {}
    press(stores.cb_store_webhook_start, f"store_webhook:{world.store.id}", bot, data)
    assert "https://" in send(stores.admin_store_webhook_value, "http://insecure.example", bot, data)[0]
    reply = send(stores.admin_store_webhook_value, "https://shop.example/hook", bot, data)[0]
    assert "whsec_" in reply

    data = {}
    press(stores.cb_store_customers_start, f"store_customers:{world.store.id}", bot, data)
    send(stores.admin_store_customers_value, "111 222", bot, data)
    assert sql("SELECT telegram_id FROM api_store_customers ORDER BY telegram_id") == [(111,), (222,)]

    data = {}
    press(limits.cb_set_user_limit_start, "adm_set_user_limit", bot, data)
    send(limits.admin_set_user_limit_id, "111", bot, data)
    assert "Enter -1" in send(limits.admin_set_user_limit_value, "-7", bot, data)[0]
    send(limits.admin_set_user_limit_value, "0", bot, data)
    assert "BLOCKED" in press(limits.cb_list_user_limits, "adm_list_user_limits", bot)[0]

    press(stores.cb_store_delete_confirm, f"store_delete_ok:{world.store.id}", bot)
    assert sql("SELECT COUNT(*) FROM api_stores") == [(0,)]
    assert sql("SELECT COUNT(*) FROM api_store_customers") == [(0,)]


def test_exports_send_csv(world):
    bot = world.bot
    press(dashboard.cb_export_orders, "adm_export_orders", bot)
    press(customers.cb_customer_export, "adm_customer_export:111", bot)
    assert len(bot.documents) == 2
    csv_bytes = bot.documents[0]["document"].input_file_content
    assert world.order.order_id.encode() in csv_bytes


def test_non_admins_are_rejected(world):
    outsider = SimpleNamespace(id=12345, full_name="Mallory", username=None)
    query = AdminQuery("adm_stats")
    alerts = []

    async def answer(text=None, show_alert=False):
        alerts.append(text)

    query.answer = answer
    update = SimpleNamespace(callback_query=query, effective_user=outsider, effective_message=None)
    run(dashboard.cb_admin_stats(update, SimpleNamespace(bot=world.bot, user_data={})))
    assert alerts == ["⛔ Admins only."] and query.message.outputs == []
