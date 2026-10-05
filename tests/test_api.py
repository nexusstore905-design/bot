"""REST API behavior, using a fake Telegram bot."""
import pytest

import api.flask_app as flask_app
from conftest import (
    SUPPLIER_A,
    FakeBot,
    make_product,
    make_store,
    make_user,
    run,
    sql,
)
from database.database import AsyncSessionLocal
from database.repositories.api_store_repo import ApiStoreRepository
from services import app_settings


class FakeRequestClient:
    async def shutdown(self):
        pass


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBot()
    monkeypatch.setattr(flask_app, "make_bot", lambda: (fake, FakeRequestClient()))
    return fake


@pytest.fixture
def client():
    return flask_app.app.test_client()


def post_order(client, key, body, idem=None):
    headers = {"X-API-Key": key}
    if idem:
        headers["Idempotency-Key"] = idem
    return client.post("/orders/", json=body, headers=headers)


def test_store_order_is_created_delivered_and_scoped(client, bot):
    run(make_user(111))
    product = run(make_product())
    _, key = run(make_store("A"))
    _, other_key = run(make_store("B"))

    response = post_order(client, key, {"telegram_user_id": 111, "product_id": product.id, "player_id": "5123456789"})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["status"] == "pending"
    assert body["supplier_notified"] is True and body["delivery"] == "sent"
    assert len(bot.sent_to(SUPPLIER_A)) == 1

    assert client.get(f"/orders/{body['order_id']}", headers={"X-API-Key": key}).status_code == 200
    assert client.get(f"/orders/{body['order_id']}", headers={"X-API-Key": other_key}).status_code == 404
    assert client.get(f"/orders/{body['order_id']}", headers={"X-API-Key": "master-test-key"}).status_code == 200


def test_idempotency_key_replays_and_rejects_mismatch(client, bot):
    run(make_user(111))
    product = run(make_product())
    _, key = run(make_store())
    payload = {"telegram_user_id": 111, "product_id": product.id, "player_id": "5123456789"}

    first = post_order(client, key, payload, idem="checkout-42").get_json()
    second = post_order(client, key, payload, idem="checkout-42").get_json()
    assert second["order_id"] == first["order_id"]
    assert second["idempotent_replay"] is True
    assert sql("SELECT COUNT(*) FROM orders")[0][0] == 1
    assert len(bot.sent_to(SUPPLIER_A)) == 1

    conflict = post_order(client, key, {**payload, "player_id": "5999999999"}, idem="checkout-42")
    assert conflict.status_code == 409


@pytest.mark.parametrize("kwargs,status", [
    ({"revoked": True}, 403),
    ({"member": False}, 403),
])
def test_rejects_customers_who_are_not_members(client, bot, kwargs, status):
    run(make_user(111, **kwargs))
    product = run(make_product())
    _, key = run(make_store())
    response = post_order(client, key, {"telegram_user_id": 111, "product_id": product.id, "player_id": "5123456789"})
    assert response.status_code == status
    assert bot.sent == []


def test_unknown_customer_and_bad_input(client, bot):
    product = run(make_product())
    _, key = run(make_store())
    assert post_order(client, key, {"telegram_user_id": 42, "product_id": product.id, "player_id": "5123"}).status_code == 400
    assert post_order(client, key, {"telegram_user_id": 42, "product_id": product.id, "player_id": "has space"}).status_code == 400
    assert client.post("/orders/", data="nope", headers={"X-API-Key": key}).status_code == 400


def test_store_customer_links_restrict_orders(client, bot):
    run(make_user(111))
    product = run(make_product())
    store, key = run(make_store())
    payload = {"telegram_user_id": 111, "product_id": product.id, "player_id": "5123456789"}

    async def link(ids):
        async with AsyncSessionLocal() as session:
            await ApiStoreRepository(session).link_customers(store.id, ids)

    run(link([222]))
    assert post_order(client, key, payload).status_code == 403
    run(link([111]))
    assert post_order(client, key, payload).status_code == 200


def test_auth_errors(client, bot):
    store, key = run(make_store())
    assert client.get("/orders/NX1", headers={}).status_code == 401
    assert client.get("/orders/NX1", headers={"X-API-Key": "nxs_wrong"}).status_code == 401
    sql("UPDATE api_stores SET is_active = 0")
    assert client.get("/orders/NX1", headers={"X-API-Key": key}).status_code == 403


def test_keys_are_stored_hashed(client, bot):
    _, key = run(make_store())
    stored = sql("SELECT api_key_hash, api_key_prefix FROM api_stores")[0]
    assert key not in stored[0] and stored[1] == key[:12]


def test_internal_errors_do_not_leak_details(client, bot, monkeypatch):
    run(make_user(111))
    _, key = run(make_store())

    async def explode(self, product_id):
        raise RuntimeError("secret database path /home/x/nexus_bot.db")

    monkeypatch.setattr("database.repositories.product_repo.ProductRepository.get_by_id", explode)
    response = post_order(client, key, {"telegram_user_id": 111, "product_id": 1, "player_id": "5123456789"})
    assert response.status_code == 500
    body = response.get_json()
    assert body["detail"] == "Internal server error" and body["error_id"]
    assert "secret" not in response.get_data(as_text=True)


def test_cors_only_for_listed_origins(client):
    allowed = client.get("/", headers={"Origin": "https://shop.example"})
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://shop.example"
    assert "Access-Control-Allow-Origin" not in client.get("/", headers={"Origin": "null"}).headers
    assert "Access-Control-Allow-Origin" not in client.get("/", headers={"Origin": "https://evil.example"}).headers


def test_products_and_v1_routes(client):
    run(make_product(name="660 UC"))
    _, key = run(make_store())
    for path in ("/v1/products/", "/products/"):
        products = client.get(path, headers={"X-API-Key": key}).get_json()["products"]
        assert products == [{"product_id": products[0]["product_id"], "category": "PUBG UC", "name": "660 UC"}]


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(flask_app, "API_RATE_LIMIT_PER_MINUTE", 3)
    _, key = run(make_store())
    statuses = [client.get("/v1/products/", headers={"X-API-Key": key}).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    limited = client.get("/v1/products/", headers={"X-API-Key": key})
    assert 0 < int(limited.headers["Retry-After"]) <= 60
    # A different key has its own budget.
    _, other_key = run(make_store("Other"))
    assert client.get("/v1/products/", headers={"X-API-Key": other_key}).status_code == 200


def test_failed_first_delivery_is_retried_not_failed(client, monkeypatch):
    failing = FakeBot(fail_chats={SUPPLIER_A})
    monkeypatch.setattr(flask_app, "make_bot", lambda: (failing, FakeRequestClient()))
    run(make_user(111))
    product = run(make_product())
    _, key = run(make_store())
    body = post_order(client, key, {"telegram_user_id": 111, "product_id": product.id, "player_id": "5123456789"}).get_json()
    assert body["status"] == "pending"
    assert body["supplier_notified"] is False and body["delivery"] == "retrying"
    assert sql("SELECT status, dispatch_attempts FROM supplier_fulfillments") == [("queued", 1)]


def test_health_reports_bot_heartbeat(client):
    unknown = client.get("/health")
    assert unknown.status_code == 200 and unknown.get_json()["bot"] == "unknown"
    assert client.get("/health?require_bot=1").status_code == 503
    run(app_settings.beat())
    assert client.get("/health?require_bot=1").get_json()["bot"] == "ok"
