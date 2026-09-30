import logging
import secrets
from fastapi import FastAPI, Depends, HTTPException, status, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from database.database import AsyncSessionLocal
from database.repositories.order_repo import OrderRepository
from database.repositories.product_repo import ProductRepository
from database.repositories.user_repo import UserRepository
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository
from database.models import OrderStatus
from config.settings import API_KEY, API_CORS_ORIGINS
from utils.supplier_routing import resolve_supplier_chat

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Nexus Store API",
    description="REST API for placing and tracking orders",
    version="2.0.0"
)

# ─── API Security Middleware ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Authentication — checks both legacy master key AND per-store keys
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def get_api_key_and_store(api_key_header: str = Security(api_key_header)) -> int | None:
    """
    Validates the API key. Checks:
    1. Per-store API keys (from database) — with daily limits
    2. Master API key (from .env) — unlimited, for backward compatibility
    Returns the store ID for a per-store key, or None for the master key.
    Quotas are counted only when a valid order is placed, never on GET.
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
            return store.id

    # Check master key (backward compatible)
    if API_KEY and secrets.compare_digest(api_key_header, API_KEY):
        return None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API Key."
    )


# Pydantic Schemas
class OrderCreate(BaseModel):
    telegram_user_id: int = Field(..., description="The Telegram ID of the user placing the order")
    product_id: int = Field(..., description="ID of the product to order")
    player_id: str = Field(..., description="PUBG Player ID (5–16 digits, starting with 5)")

class OrderResponse(BaseModel):
    order_id: str
    status: str
    product_name: str
    player_id: str
    created_at: str
    supplier_notified: bool

class OrderStatusResponse(BaseModel):
    order_id: str
    status: str

# API Routes
@app.get("/health")
async def health_check():
    """Unauthenticated liveness/readiness probe for the API and its database."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("API health check could not reach the database (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="API database is unavailable") from None
    return {"status": "ok", "database": "ok"}


@app.post("/orders/", response_model=OrderResponse)
async def create_order(
    order_req: OrderCreate,
    api_store_id: int | None = Depends(get_api_key_and_store),
):
    # Validate explicitly so the rule works across supported Pydantic versions.
    if not order_req.player_id.isdigit() or not order_req.player_id.startswith("5") or not 5 <= len(order_req.player_id) <= 16:
        raise HTTPException(
            status_code=422,
            detail="player_id must be 5–16 digits and start with 5",
        )

    async with AsyncSessionLocal() as session:
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

        # Reserve quotas only after input, user, and product validation. The
        # repository leaves reservations in this transaction; order creation
        # commits them together.
        limit_repo = UserOrderLimitRepository(session)
        allowed, reason = await limit_repo.check_and_increment(order_req.telegram_user_id)
        if not allowed:
            raise HTTPException(status_code=429, detail=reason)

        if api_store_id is not None:
            store_repo = ApiStoreRepository(session)
            store = await store_repo.get_by_id(api_store_id)
            if not store:
                raise HTTPException(status_code=401, detail="API store is no longer available")
            allowed, reason = await store_repo.check_and_increment(store)
            if not allowed:
                raise HTTPException(status_code=429, detail=reason)
        
        # Look up supplier for this category
        supplier_id = await product_repo.get_supplier_for_category(product.category)
        target_chat, route_source = resolve_supplier_chat(supplier_id)
        if not target_chat:
            raise HTTPException(status_code=503, detail="No supplier is configured for this product")

        # Create order
        order_repo = OrderRepository(session)
        order = await order_repo.create(
            user_id=user.id,
            product_id=product.id,
            product_name=product.name,
            quantity=1,
            player_id=order_req.player_id,
            api_store_id=api_store_id,
        )

    # Forward to supplier via Telegram bot.
    from bot.keyboards.admin_kb import supplier_done_error_kb
    from telegram import Bot
    from config.settings import BOT_TOKEN
    import html

    bot_instance = getattr(app.state, "bot", None)
    owns_bot = bot_instance is None
    if owns_bot:
        bot_instance = Bot(token=BOT_TOKEN)

    supplier_notified = False
    try:
        if owns_bot:
            await bot_instance.initialize()
        supplier_text = (
            f"┌──────────────────────────┐\n"
            f"│    🆕  API ORDER             │\n"
            f"└──────────────────────────┘\n\n"
            f"  🆔  Order:     <b>{html.escape(order.order_id)}</b>\n"
            f"  💎  Product:   <b>{html.escape(product.name, quote=False)}</b>\n"
            f"  📂  Category:  <b>{html.escape(product.category, quote=False)}</b>\n"
            f"  🎯  PUBG UID:  <code>{html.escape(order_req.player_id)}</code>\n"
            f"  📦  Quantity:  1\n\n"
            "Mark as <b>DONE</b> or <b>ERROR</b>:"
        )
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
        supplier_notified = True
    except Exception as exc:
        # Avoid logging exception text because Telegram client exceptions can
        # include request URLs containing the bot token.
        logger.error(
            "Failed to send API order %s to supplier %s (%s)",
            order.order_id, target_chat, type(exc).__name__,
        )
    else:
        logger.info(
            "Delivered API order %s to supplier chat %s via %s routing",
            order.order_id, target_chat, route_source,
        )
    finally:
        if owns_bot:
            try:
                await bot_instance.shutdown()
            except Exception:
                logger.warning("Could not shut down the temporary Telegram client")

    if not supplier_notified:
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            failed_order = await repo.get_by_order_id(order.order_id)
            if failed_order:
                await repo.update_status(
                    failed_order,
                    OrderStatus.failed,
                    changed_by="api",
                    note="Supplier notification failed; manual follow-up is required.",
                )

    return {
        "order_id": order.order_id,
        "status": order.status.value if supplier_notified else OrderStatus.failed.value,
        "product_name": product.name,
        "player_id": order.player_id,
        "created_at": order.created_at.isoformat(),
        "supplier_notified": supplier_notified,
    }

@app.get("/orders/{order_id}", response_model=OrderStatusResponse)
async def get_order_status(
    order_id: str,
    api_store_id: int | None = Depends(get_api_key_and_store),
):
    async with AsyncSessionLocal() as session:
        repo = OrderRepository(session)
        order = await repo.get_by_order_id(order_id, api_store_id=api_store_id)
        
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
            
        return {
            "order_id": order.order_id,
            "status": order.status.value
        }
