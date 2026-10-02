import json
import html
import secrets
import asyncio
import logging
from flask import Flask, request, jsonify
from sqlalchemy import text

from config.settings import API_KEY, API_CORS_ORIGINS, BOT_TOKEN
from utils.supplier_routing import resolve_supplier_chat
from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.user_repo import UserRepository
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin in API_CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        vary_values = [value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()]
        if "Origin" not in vary_values:
            vary_values.append("Origin")
        response.headers["Vary"] = ", ".join(vary_values)
    return response


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "online",
        "service": "Nexus Store API",
        "endpoints": {
            "POST /orders/": "Place order",
            "GET /orders/<order_id>": "Check status"
        }
    })


@app.route("/health", methods=["GET"])
def health():
    """Check that the Flask API can reach its database."""
    async def _check_database():
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))

    try:
        run_async(_check_database())
    except Exception as exc:
        logger.error("Flask API health check failed (%s)", type(exc).__name__)
        return jsonify({"status": "error", "database": "unavailable"}), 503
    return jsonify({"status": "ok", "database": "ok"}), 200


def run_async(coro):
    return asyncio.run(coro)


def authenticate_request():
    api_key = request.headers.get("X-API-Key", "").strip()
    if not api_key:
        return False, ("Missing X-API-Key header", 401), None
    
    # 1. Master key check
    if API_KEY and secrets.compare_digest(api_key, API_KEY):
        return True, None, None
        
    # 2. Per-store key check
    async def _check_store():
        async with AsyncSessionLocal() as session:
            store_repo = ApiStoreRepository(session)
            store = await store_repo.get_by_api_key(api_key)
            if not store:
                return False, ("Invalid API Key", 401), None
            if not store.is_active:
                return False, (f"API store '{store.name}' is disabled by admin", 403), None
            return True, None, store.id
            
    return run_async(_check_store())


@app.route("/orders/", methods=["POST"])
def create_order():
    auth_ok, auth_err, api_store_id = authenticate_request()
    if not auth_ok:
        return jsonify({"detail": auth_err[0]}), auth_err[1]
        
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"detail": "Request body must be a JSON object"}), 400
    telegram_user_id = data.get("telegram_user_id")
    product_id = data.get("product_id")
    player_id = str(data.get("player_id", "")).strip()
    
    if not telegram_user_id or not product_id or not player_id:
        return jsonify({"detail": "telegram_user_id, product_id, and player_id are required"}), 400
        
    if not (player_id.startswith("5") and player_id.isdigit() and 5 <= len(player_id) <= 16):
        return jsonify({"detail": "Invalid player_id. Must start with 5 and be numeric"}), 400
    try:
        telegram_user_id = int(telegram_user_id)
        product_id = int(product_id)
    except (TypeError, ValueError):
        return jsonify({"detail": "telegram_user_id and product_id must be integers"}), 400

    async def _process():
        async with AsyncSessionLocal() as session:
            # Verify user
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(int(telegram_user_id))
            if not user:
                return None, ("User not found. They must start the Telegram bot at least once.", 400)
                
            # Verify product
            product_repo = ProductRepository(session)
            product = await product_repo.get_by_id(int(product_id))
            if not product or not product.is_active:
                return None, ("Invalid or inactive product ID", 400)

            target_chat, route_source = resolve_supplier_chat(product.supplier_chat_id)
            if not target_chat:
                return None, ("No supplier is configured for this product", 503)

            # Count only validated order submissions. These reservations are
            # committed together with the order below.
            limit_repo = UserOrderLimitRepository(session)
            allowed, reason = await limit_repo.check_and_increment(telegram_user_id)
            if not allowed:
                return None, (reason, 429)
            if api_store_id is not None:
                store_repo = ApiStoreRepository(session)
                store = await store_repo.get_by_id(api_store_id)
                if not store:
                    return None, ("API store is no longer available", 401)
                allowed, reason = await store_repo.check_and_increment(store)
                if not allowed:
                    return None, (reason, 429)
                
            # Create order
            order_repo = OrderRepository(session)
            order = await order_repo.create(
                user_id=user.id,
                product_id=product.id,
                product_name=product.name,
                quantity=1,
                player_id=player_id,
                api_store_id=api_store_id,
                supplier_fulfillment={
                    "supplier_chat_id": target_chat,
                    "category": product.category,
                    "items": [{"product_name": product.name, "quantity": 1}],
                },
            )
            fulfillment = (await order_repo.get_fulfillments(order.id))[0]
            order_id = order.order_id
            fulfillment_id = fulfillment.id
            created_at_iso = order.created_at.isoformat()
            status_val = order.status.value
            prod_name = product.name
            prod_cat = product.category
            
        return {
            "order_id": order_id,
            "status": status_val,
            "product_name": prod_name,
            "player_id": player_id,
            "created_at": created_at_iso,
            "target_chat": target_chat,
            "route_source": route_source,
            "category": prod_cat,
            "api_store_id": api_store_id,
            "fulfillment_id": fulfillment_id,
        }, None

    result, err = run_async(_process())
    if err:
        return jsonify({"detail": err[0]}), err[1]
        
    # Send Telegram notification to supplier chat.
    target_chat = result.pop("target_chat", None)
    route_source = result.pop("route_source", "unconfigured")
    prod_cat = result.pop("category", "")
    result.pop("api_store_id", None)
    supplier_notified = False
    if target_chat and BOT_TOKEN:
        try:
            async def _mark_sending():
                async with AsyncSessionLocal() as session:
                    return await OrderRepository(session).mark_fulfillment_sending(
                        result["fulfillment_id"]
                    )
            if not run_async(_mark_sending()):
                raise RuntimeError("Supplier fulfillment is no longer queued")

            import urllib.request
            safe_order_id = html.escape(str(result['order_id']))
            safe_prod_name = html.escape(str(result['product_name']))
            safe_cat = html.escape(str(prod_cat))
            safe_uid = html.escape(str(result['player_id']))

            supplier_text = (
                f"┌──────────────────────────┐\n"
                f"│    🆕  API ORDER             │\n"
                f"└──────────────────────────┘\n\n"
                f"  🆔  Order:     <b>{safe_order_id}</b>\n"
                f"  💎  Product:   <b>{safe_prod_name}</b>\n"
                f"  📂  Category:  <b>{safe_cat}</b>\n"
                f"  🎯  PUBG UID:  <code>{safe_uid}</code>\n"
                f"  📦  Quantity:  1\n\n"
                f"Mark as <b>DONE</b> or <b>ERROR</b>:"
            )
            keyboard = {
                "inline_keyboard": [[
                    {"text": "✅  Done", "callback_data": f"sup_done:{result['order_id']}:{result['fulfillment_id']}"},
                    {"text": "❌  Error", "callback_data": f"sup_err:{result['order_id']}:{result['fulfillment_id']}"}
                ]]
            }
            req_data = json.dumps({
                "chat_id": target_chat,
                "text": supplier_text,
                "parse_mode": "HTML",
                "reply_markup": keyboard
            }).encode("utf-8")
            t_req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(t_req, timeout=5) as t_resp:
                res_obj = json.loads(t_resp.read().decode("utf-8"))
                msg_id = res_obj.get("result", {}).get("message_id")
                if msg_id:
                    async def _save_msg():
                        async with AsyncSessionLocal() as session:
                            repo = OrderRepository(session)
                            await repo.set_fulfillment_dispatched(result["fulfillment_id"], msg_id)
                            o = await repo.get_by_order_id(result["order_id"])
                            if o:
                                await repo.set_supplier_msg(o, msg_id)
                    run_async(_save_msg())
            supplier_notified = True
            logger.info(
                "Delivered Flask API order %s to supplier chat %s via %s routing",
                result["order_id"], target_chat, route_source,
            )
        except Exception as e:
            logger.error("Failed to forward API order to supplier (%s)", type(e).__name__)
            from config.settings import ADMIN_IDS
            for admin_id in ADMIN_IDS:
                try:
                    admin_alert = json.dumps({
                        "chat_id": admin_id,
                        "text": (
                            f"⚠️ <b>API ORDER DELIVERY FAILED</b>\n\n"
                            f"Order: <b>{html.escape(str(result['order_id']))}</b> "
                            f"(Category: {html.escape(str(prod_cat))})\n"
                            f"Target Chat: <code>{target_chat}</code>\n\n"
                            "<b>Telegram Error:</b> delivery failed; check the bot logs.\n\n"
                            f"👉 <i>Make sure the bot is an Administrator in that group!</i>"
                        ),
                        "parse_mode": "HTML"
                    }).encode("utf-8")
                    a_req = urllib.request.Request(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        data=admin_alert,
                        headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(a_req, timeout=3)
                except Exception:
                    pass

    if not supplier_notified:
        async def _mark_failed():
            async with AsyncSessionLocal() as session:
                repo = OrderRepository(session)
                failed_order = await repo.get_by_order_id(result["order_id"])
                if failed_order:
                    await repo.set_fulfillment_failed(
                        result["fulfillment_id"],
                        "Supplier notification failed; manual follow-up is required.",
                    )
                    await repo.refresh_order_status_from_fulfillments(
                        failed_order.id,
                        changed_by="api",
                        note="Supplier notification failed; manual follow-up is required.",
                    )
        run_async(_mark_failed())
        result["status"] = "failed"

    async def _read_final_status():
        async with AsyncSessionLocal() as session:
            order = await OrderRepository(session).get_by_order_id(result["order_id"])
            return order.status.value if order else result["status"]
    result["status"] = run_async(_read_final_status())

    result["supplier_notified"] = supplier_notified
    result.pop("fulfillment_id", None)

    return jsonify(result), 200


@app.route("/orders/<order_id>", methods=["GET"])
def get_order_status(order_id):
    auth_ok, auth_err, api_store_id = authenticate_request()
    if not auth_ok:
        return jsonify({"detail": auth_err[0]}), auth_err[1]

    async def _fetch():
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            order = await repo.get_by_order_id(order_id, api_store_id=api_store_id)
            if not order:
                return None
            return {"order_id": order.order_id, "status": order.status.value}

    res = run_async(_fetch())
    if not res:
        return jsonify({"detail": "Order not found"}), 404
    return jsonify(res), 200
