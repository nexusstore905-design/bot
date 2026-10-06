"""REST API for connected stores.

PythonAnywhere runs this under WSGI without threads, so each request does all
of its async work (database and Telegram) inside a single event loop run.
"""
import asyncio
import logging
import secrets
import uuid
from datetime import timedelta

from flask import Flask, jsonify, request
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from telegram import Bot
from telegram.request import HTTPXRequest
from werkzeug.exceptions import HTTPException

from config.settings import API_CORS_ORIGINS, API_KEY, API_RATE_LIMIT_PER_MINUTE, BOT_TOKEN
from database.database import AsyncSessionLocal, init_db
from database.models import ApiRateCounter, AuthStatus, SupplierFulfillmentStatus
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository
from database.repositories.order_repo import OrderRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.user_repo import UserRepository
from services import app_settings
from services.dispatch import dispatch_order
from utils.helpers import utcnow
from utils.security import hash_api_key
from utils.supplier_routing import resolve_supplier_chat

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.json.sort_keys = False

BOT_HEARTBEAT_STALE = timedelta(minutes=2)
MAX_IDEMPOTENCY_KEY_LENGTH = 64


def run_async(coro):
    return asyncio.run(coro)


def make_bot() -> tuple[Bot, HTTPXRequest]:
    """A short-lived Bot for one request. Tests replace this."""
    request_client = HTTPXRequest(connection_pool_size=4)
    return Bot(BOT_TOKEN, request=request_client), request_client


# Apply schema migrations when a worker starts, so the API never depends on the
# bot task having been restarted after a deploy.
try:
    run_async(init_db())
except Exception:
    logger.exception("Database initialization failed in the API worker")


class ApiError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


@app.errorhandler(ApiError)
def api_error(exc: ApiError):
    response = jsonify({"detail": exc.detail})
    if exc.retry_after is not None:
        response.headers["Retry-After"] = str(exc.retry_after)
    return response, exc.status


@app.errorhandler(HTTPException)
def http_error(exc: HTTPException):
    return jsonify({"detail": exc.description}), exc.code


@app.errorhandler(Exception)
def unhandled_error(exc: Exception):
    error_id = uuid.uuid4().hex[:12]
    logger.exception("Unhandled API error %s", error_id)
    return jsonify({"detail": "Internal server error", "error_id": error_id}), 500


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    # "null" (file:// pages, sandboxed frames) is allowed only if listed explicitly.
    if origin and ("*" in API_CORS_ORIGINS or origin in API_CORS_ORIGINS):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Idempotency-Key"
        response.headers["Access-Control-Expose-Headers"] = "Retry-After"
        response.headers["Access-Control-Max-Age"] = "600"
        response.vary.add("Origin")
    return response


async def _count_request(session, bucket: str) -> int:
    window = utcnow().replace(second=0, microsecond=0)
    await session.execute(
        sqlite_insert(ApiRateCounter)
        .values(bucket=bucket, window_start=window, count=1)
        .on_conflict_do_update(
            index_elements=["bucket", "window_start"],
            set_={"count": ApiRateCounter.count + 1},
        )
    )
    await session.commit()
    return int(await session.scalar(
        select(ApiRateCounter.count).where(
            ApiRateCounter.bucket == bucket, ApiRateCounter.window_start == window,
        )
    ) or 0)


async def _rate_limit(session, api_key: str, client_ip: str) -> None:
    """Fixed one-minute windows per key, plus a looser per-IP cap against key guessing."""
    checks = [(f"ip:{client_ip}", API_RATE_LIMIT_PER_MINUTE * 5)]
    if api_key:
        checks.append((f"key:{hash_api_key(api_key)[:24]}", API_RATE_LIMIT_PER_MINUTE))
    for bucket, limit in checks:
        if await _count_request(session, bucket) > limit:
            raise ApiError(
                429, f"Rate limit exceeded ({limit} requests per minute). Slow down and retry.",
                retry_after=60 - utcnow().second,
            )


def _client_ip() -> str:
    # PythonAnywhere puts the real client address first in X-Forwarded-For.
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "unknown")[:45]


async def _authenticate(session, api_key: str) -> int | None:
    """Return the store ID for a store key, None for the master key, or raise."""
    if not api_key:
        raise ApiError(401, "Missing X-API-Key header")
    if API_KEY and secrets.compare_digest(api_key, API_KEY):
        return None
    store = await ApiStoreRepository(session).get_by_api_key(api_key)
    if store is None:
        raise ApiError(401, "Invalid API key")
    if not store.is_active:
        raise ApiError(403, f"API store '{store.name}' is disabled by admin")
    return store.id


def _api_key() -> str:
    return request.headers.get("X-API-Key", "").strip()


def _delivery_state(order) -> tuple[bool, str]:
    """(supplier_notified, delivery) for an order's supplier parts."""
    parts = order.fulfillments
    if not parts:
        return False, "none"
    if all(part.dispatched_at is not None for part in parts):
        return True, "sent"
    if any(part.status in (SupplierFulfillmentStatus.queued, SupplierFulfillmentStatus.sending) for part in parts):
        return False, "retrying"
    return False, "failed"


def _order_payload(order) -> dict:
    notified, delivery = _delivery_state(order)
    item = order.items[0] if order.items else None
    return {
        "order_id": order.order_id,
        "status": order.status.value,
        "product_id": item.product_id if item else None,
        "product_name": item.product_name if item else None,
        "quantity": item.quantity if item else None,
        "player_id": order.player_id,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        "supplier_notified": notified,
        "delivery": delivery,
    }


def _as_int(value) -> int | None:
    """An integer, or a string of digits. Booleans and floats are rejected."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _valid_player_id(value: str) -> bool:
    return 3 <= len(value) <= 20 and value.isprintable() and not any(ch.isspace() for ch in value)


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "online",
        "service": "Nexus Store API",
        "version": "v1",
        "endpoints": {
            "GET /v1/products/": "List active products",
            "POST /v1/orders/": "Place order (send Idempotency-Key to make retries safe)",
            "GET /v1/orders/<order_id>": "Check status",
            "GET /health": "Database and bot status",
        },
        "note": "Unversioned paths (/orders/, /products/) remain as aliases of /v1/.",
    })


@app.route("/health", methods=["GET"])
def health():
    async def _check():
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return await app_settings.last_heartbeat()

    try:
        heartbeat = run_async(_check())
    except Exception as exc:
        logger.error("Flask API health check failed (%s)", type(exc).__name__)
        return jsonify({"status": "error", "database": "unavailable"}), 503

    if heartbeat is None:
        bot_state = "unknown"
    elif utcnow() - heartbeat <= BOT_HEARTBEAT_STALE:
        bot_state = "ok"
    else:
        bot_state = "stale"
    payload = {
        "status": "ok" if bot_state == "ok" else "degraded",
        "database": "ok",
        "bot": bot_state,
        "bot_last_seen": heartbeat.isoformat() if heartbeat else None,
    }
    strict = request.args.get("require_bot") in ("1", "true", "yes")
    return jsonify(payload), 503 if strict and bot_state != "ok" else 200


@app.route("/v1/products/", methods=["GET"])
@app.route("/products/", methods=["GET"])
def list_products():
    api_key, client_ip = _api_key(), _client_ip()

    async def _list():
        async with AsyncSessionLocal() as session:
            await _rate_limit(session, api_key, client_ip)
            await _authenticate(session, api_key)
            products = await ProductRepository(session).get_all_active()
        return [
            {"product_id": product.id, "category": product.category, "name": product.name}
            for product in products
        ]

    return jsonify({"products": run_async(_list())}), 200


@app.route("/v1/orders/", methods=["POST"])
@app.route("/orders/", methods=["POST"])
def create_order():
    api_key, client_ip = _api_key(), _client_ip()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Request body must be a JSON object")
    telegram_user_id = data.get("telegram_user_id")
    product_id = data.get("product_id")
    player_id = str(data.get("player_id", "")).strip()
    if not telegram_user_id or not product_id or not player_id:
        raise ApiError(400, "telegram_user_id, product_id, and player_id are required")
    if not _valid_player_id(player_id):
        raise ApiError(400, "Invalid player_id. Must be 3-20 characters with no spaces")
    telegram_user_id, product_id = _as_int(telegram_user_id), _as_int(product_id)
    if telegram_user_id is None or product_id is None or telegram_user_id <= 0 or product_id <= 0:
        raise ApiError(400, "telegram_user_id and product_id must be positive integers")
    idempotency_key = request.headers.get("Idempotency-Key", "").strip() or None
    if idempotency_key and (
        len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH
        or not idempotency_key.isascii() or not idempotency_key.isprintable()
    ):
        raise ApiError(400, f"Idempotency-Key must be 1-{MAX_IDEMPOTENCY_KEY_LENGTH} printable ASCII characters")

    async def _replay(store_id):
        async with AsyncSessionLocal() as session:
            existing = await OrderRepository(session).get_by_idempotency_key(store_id, idempotency_key)
            if existing is None:
                return None
            existing = await OrderRepository(session).get_by_order_id(existing.order_id)
        item = existing.items[0] if existing.items else None
        if (
            existing.user.telegram_id != telegram_user_id
            or (item and item.product_id != product_id)
            or existing.player_id != player_id
        ):
            raise ApiError(409, "Idempotency-Key was already used for a different order")
        return {**_order_payload(existing), "idempotent_replay": True}

    async def _process():
        async with AsyncSessionLocal() as session:
            await _rate_limit(session, api_key, client_ip)
            store_id = await _authenticate(session, api_key)
        if idempotency_key:
            replay = await _replay(store_id)
            if replay:
                return replay

        async with AsyncSessionLocal() as session:
            user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
            if user is None:
                raise ApiError(400, "User not found. They must start the Telegram bot and sign in with an access code.")
            if user.auth_status == AuthStatus.revoked:
                raise ApiError(403, "This customer's access has been revoked.")
            if not UserRepository.is_member(user):
                raise ApiError(403, "This customer has not signed in to the bot with an access code yet.")

            product = await ProductRepository(session).get_by_id(product_id)
            if not product or not product.is_active:
                raise ApiError(400, "Invalid or inactive product ID")
            target_chat, _ = resolve_supplier_chat(product.supplier_chat_id)
            if not target_chat:
                raise ApiError(503, "No supplier is configured for this product")

            store_repo = ApiStoreRepository(session)
            if store_id is not None and not await store_repo.may_order_for(store_id, telegram_user_id):
                raise ApiError(403, "This API store is not allowed to order for this customer.")

            # Quota reservations commit together with the order below.
            allowed, reason = await UserOrderLimitRepository(session).check_and_increment(telegram_user_id)
            if not allowed:
                raise ApiError(429, reason)
            if store_id is not None:
                store = await store_repo.get_by_id(store_id)
                if store is None:
                    raise ApiError(401, "API store is no longer available")
                allowed, reason = await store_repo.check_and_increment(store)
                if not allowed:
                    raise ApiError(429, reason)

            try:
                order = await OrderRepository(session).create_order(
                    user_id=user.id,
                    items=[{"product_id": product.id, "product_name": product.name, "quantity": 1}],
                    player_id=player_id,
                    api_store_id=store_id,
                    idempotency_key=idempotency_key,
                    fulfillments=[{
                        "supplier_chat_id": target_chat,
                        "category": product.category,
                        "items": [{"product_name": product.name, "quantity": 1}],
                    }],
                )
            except IntegrityError:
                # Another request with the same Idempotency-Key won the race.
                await session.rollback()
                replay = await _replay(store_id) if idempotency_key else None
                if replay:
                    return replay
                raise

        bot, request_client = make_bot()
        try:
            await dispatch_order(bot, order.id)
        finally:
            await request_client.shutdown()

        async with AsyncSessionLocal() as session:
            order = await OrderRepository(session).get_by_order_id(order.order_id)
        return {**_order_payload(order), "idempotent_replay": False}

    return jsonify(run_async(_process())), 200


@app.route("/v1/orders/<order_id>", methods=["GET"])
@app.route("/orders/<order_id>", methods=["GET"])
def get_order_status(order_id):
    api_key, client_ip = _api_key(), _client_ip()

    async def _fetch():
        async with AsyncSessionLocal() as session:
            await _rate_limit(session, api_key, client_ip)
            store_id = await _authenticate(session, api_key)
            order = await OrderRepository(session).get_by_order_id(order_id.strip().upper(), api_store_id=store_id)
        if order is None:
            raise ApiError(404, "Order not found")
        return _order_payload(order)

    return jsonify(run_async(_fetch())), 200
