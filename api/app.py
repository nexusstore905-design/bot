import logging
from typing import Optional, Dict, Any
import secrets
from fastapi import FastAPI, Depends, HTTPException, status, Security, Request
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.user_repo import UserRepository
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository
from database.models import OrderStatus
from config.settings import API_KEY, SUPPLIER_CHAT_ID

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Nexus Store API",
    description="REST API for placing and tracking orders",
    version="2.0.0"
)

# ─── API Security Middleware ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Authentication — checks both legacy master key AND per-store keys
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def get_api_key_and_store(api_key_header: str = Security(api_key_header)):
    """
    Validates the API key. Checks:
    1. Per-store API keys (from database) — with daily limits
    2. Master API key (from .env) — unlimited, for backward compatibility
    Returns (api_key, store_or_none)
    """
    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key. Provide X-API-Key header."
        )

    # Check if it's a store key
    async with AsyncSessionLocal() as session:
        store_repo = ApiStoreRepository(session)
        store = await store_repo.get_by_api_key(api_key_header)
        if store:
            if not store.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"API store '{store.name}' is disabled by admin."
                )
            # Check daily limit
            allowed, reason = await store_repo.check_and_increment(store)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=reason
                )
            return api_key_header

    # Check master key (backward compatible)
    if secrets.compare_digest(api_key_header, API_KEY):
        return api_key_header

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API Key."
    )


# Pydantic Schemas
class OrderCreate(BaseModel):
    telegram_user_id: int = Field(..., description="The Telegram ID of the user placing the order")
    product_id: int = Field(..., description="ID of the product to order")
    player_id: str = Field(..., description="PUBG Player ID (Starts with 5, numbers only)", pattern=r"^5\d{4,15}$")

class OrderResponse(BaseModel):
    order_id: str
    status: str
    product_name: str
    player_id: str
    created_at: str

class OrderStatusResponse(BaseModel):
    order_id: str
    status: str

# API Routes
@app.post("/orders/", response_model=OrderResponse, dependencies=[Depends(get_api_key_and_store)])
async def create_order(order_req: OrderCreate):
    async with AsyncSessionLocal() as session:
        # Check user order limit
        limit_repo = UserOrderLimitRepository(session)
        allowed, reason = await limit_repo.check_and_increment(order_req.telegram_user_id)
        if not allowed:
            raise HTTPException(status_code=429, detail=reason)

        # Verify user
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(order_req.telegram_user_id)
        if not user:
            raise HTTPException(status_code=400, detail="User not found in system. They must use the bot at least once.")
        
        # Verify product
        product_repo = ProductRepository(session)
        product = await product_repo.get_by_id(order_req.product_id)
        if not product or not product.is_active:
            raise HTTPException(status_code=400, detail="Invalid or inactive product ID")
        
        # Look up supplier for this category
        supplier_id = await product_repo.get_supplier_for_category(product.category)
        target_chat = supplier_id or SUPPLIER_CHAT_ID

        # Create order
        order_repo = OrderRepository(session)
        order = await order_repo.create(
            user_id=user.id,
            product_id=product.id,
            product_name=product.name,
            quantity=1,
            player_id=order_req.player_id,
        )

    # Forward to supplier via Telegram bot
    if target_chat:
        from bot.keyboards.admin_kb import supplier_done_error_kb
        from telegram import Bot
        from config.settings import BOT_TOKEN

        # If running in PythonAnywhere Web Tab, app.state.bot won't exist, so we create one
        bot_instance = getattr(app.state, "bot", None)
        if not bot_instance:
            bot_instance = Bot(token=BOT_TOKEN)

        supplier_text = (
            f"┌──────────────────────────┐\n"
            f"│    🆕  API ORDER             │\n"
            f"└──────────────────────────┘\n\n"
            f"  🆔  Order:     <b>{order.order_id}</b>\n"
            f"  💎  Product:   <b>{product.name}</b>\n"
            f"  📂  Category:  <b>{product.category}</b>\n"
            f"  🎯  PUBG UID:  <code>{order_req.player_id}</code>\n"
            f"  📦  Quantity:  1\n\n"
            f"Mark as <b>DONE</b> or <b>ERROR</b>:"
        )
        try:
            msg = await bot_instance.send_message(
                chat_id=target_chat,
                text=supplier_text,
                parse_mode="HTML",
                reply_markup=supplier_done_error_kb(order.order_id),
            )
            async with AsyncSessionLocal() as session:
                repo = OrderRepository(session)
                fresh = await repo.get_by_order_id(order.order_id)
                if fresh:
                    await repo.set_supplier_msg(fresh, msg.message_id)
        except Exception as e:
            logger.error(f"Failed to send API order to supplier {target_chat}: {e}")

    return {
        "order_id": order.order_id,
        "status": order.status.value,
        "product_name": product.name,
        "player_id": order.player_id,
        "created_at": order.created_at.isoformat()
    }

@app.get("/orders/{order_id}", response_model=OrderStatusResponse, dependencies=[Depends(get_api_key_and_store)])
async def get_order_status(order_id: str):
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id)
        
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
            
        return {
            "order_id": order.order_id,
            "status": order.status.value
        }
