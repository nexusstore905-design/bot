import os
import json
import secrets
import asyncio
import logging
from flask import Flask, request, jsonify

from config.settings import API_KEY, SUPPLIER_CHAT_ID, BOT_TOKEN
from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.user_repo import UserRepository
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository

logger = logging.getLogger(__name__)

app = Flask(__name__)


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


def run_async(coro):
    return asyncio.run(coro)


def authenticate_request():
    api_key = request.headers.get("X-API-Key", "").strip()
    if not api_key:
        return False, ("Missing X-API-Key header", 401)
    
    # 1. Master key check
    if API_KEY and secrets.compare_digest(api_key, API_KEY):
        return True, None
        
    # 2. Per-store key check
    async def _check_store():
        async with AsyncSessionLocal() as session:
            store_repo = ApiStoreRepository(session)
            store = await store_repo.get_by_api_key(api_key)
            if not store:
                return False, ("Invalid API Key", 401)
            if not store.is_active:
                return False, (f"API store '{store.name}' is disabled by admin", 403)
            allowed, reason = await store_repo.check_and_increment(store)
            if not allowed:
                return False, (reason, 429)
            return True, None
            
    return run_async(_check_store())


@app.route("/orders/", methods=["POST"])
def create_order():
    auth_ok, auth_err = authenticate_request()
    if not auth_ok:
        return jsonify({"detail": auth_err[0]}), auth_err[1]
        
    data = request.get_json(silent=True) or {}
    telegram_user_id = data.get("telegram_user_id")
    product_id = data.get("product_id")
    player_id = str(data.get("player_id", "")).strip()
    
    if not telegram_user_id or not product_id or not player_id:
        return jsonify({"detail": "telegram_user_id, product_id, and player_id are required"}), 400
        
    if not (player_id.startswith("5") and player_id.isdigit() and 5 <= len(player_id) <= 16):
        return jsonify({"detail": "Invalid player_id. Must start with 5 and be numeric"}), 400

    async def _process():
        async with AsyncSessionLocal() as session:
            # Check user limit
            limit_repo = UserOrderLimitRepository(session)
            allowed, reason = await limit_repo.check_and_increment(int(telegram_user_id))
            if not allowed:
                return None, (reason, 429)
                
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
                
            supplier_id = await product_repo.get_supplier_for_category(product.category)
            target_chat = supplier_id or SUPPLIER_CHAT_ID
            
            # Create order
            order_repo = OrderRepository(session)
            order = await order_repo.create(
                user_id=user.id,
                product_id=product.id,
                product_name=product.name,
                quantity=1,
                player_id=player_id
            )
            order_id = order.order_id
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
            "category": prod_cat
        }, None

    result, err = run_async(_process())
    if err:
        return jsonify({"detail": err[0]}), err[1]
        
    # Send Telegram notification to supplier chat
    target_chat = result.pop("target_chat", None)
    prod_cat = result.pop("category", "")
    if target_chat and BOT_TOKEN:
        try:
            import urllib.request
            supplier_text = (
                f"┌──────────────────────────┐\n"
                f"│    🆕  API ORDER             │\n"
                f"└──────────────────────────┘\n\n"
                f"  🆔  Order:     <b>{result['order_id']}</b>\n"
                f"  💎  Product:   <b>{result['product_name']}</b>\n"
                f"  📂  Category:  <b>{prod_cat}</b>\n"
                f"  🎯  PUBG UID:  <code>{result['player_id']}</code>\n"
                f"  📦  Quantity:  1\n\n"
                f"Mark as <b>DONE</b> or <b>ERROR</b>:"
            )
            keyboard = {
                "inline_keyboard": [[
                    {"text": "✅  Done", "callback_data": f"sup_done:{result['order_id']}"},
                    {"text": "❌  Error", "callback_data": f"sup_err:{result['order_id']}"}
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
                            o = await repo.get_by_order_id(result["order_id"])
                            if o:
                                await repo.set_supplier_msg(o, msg_id)
                    run_async(_save_msg())
        except Exception as e:
            logger.error(f"Failed to forward API order to supplier: {e}")

    return jsonify(result), 200


@app.route("/orders/<order_id>", methods=["GET"])
def get_order_status(order_id):
    auth_ok, auth_err = authenticate_request()
    if not auth_ok:
        return jsonify({"detail": auth_err[0]}), auth_err[1]

    async def _fetch():
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            order = await repo.get_by_order_id(order_id)
            if not order:
                return None
            return {"order_id": order.order_id, "status": order.status.value}

    res = run_async(_fetch())
    if not res:
        return jsonify({"detail": "Order not found"}), 404
    return jsonify(res), 200
