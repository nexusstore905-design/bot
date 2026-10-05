import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Store naive UTC and always return timezone-aware UTC.

    SQLite drops tzinfo, which previously made Python-side comparisons between
    loaded rows and aware datetimes raise TypeError.
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class AuthStatus(str, enum.Enum):
    unauthenticated = "unauthenticated"
    authenticated = "authenticated"
    locked = "locked"
    revoked = "revoked"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_ORDER_STATUSES = (OrderStatus.completed, OrderStatus.failed, OrderStatus.cancelled)
OPEN_ORDER_STATUSES = (OrderStatus.pending, OrderStatus.processing)


class SupplierFulfillmentStatus(str, enum.Enum):
    queued = "queued"
    sending = "sending"
    pending = "pending"
    completed = "completed"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Interface language for customer screens ("en" or "ur"); None = not chosen yet.
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    auth_status: Mapped[AuthStatus] = mapped_column(
        Enum(AuthStatus), default=AuthStatus.unauthenticated, nullable=False
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    session_expires: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user", lazy="select")


class PinConfig(Base):
    __tablename__ = "pin_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    hashed_pin: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    invalidate_sessions: Mapped[bool] = mapped_column(Boolean, default=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)


class AccessCode(Base):
    """Admin-created invite codes — one per trusted user."""
    __tablename__ = "access_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "John's code"
    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # telegram_id
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Each package can route to its own supplier Telegram group/chat.
    supplier_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    order_items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="product")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ux_orders_store_idem", "api_store_id", "idempotency_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    # Keep the issuing store ID as an audit value without tying historical
    # orders to a deletable API-store row.
    api_store_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.pending, nullable=False
    )
    player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    settled_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="order", lazy="selectin")
    history: Mapped[list["OrderStatusHistory"]] = relationship(
        "OrderStatusHistory", back_populates="order", order_by="OrderStatusHistory.created_at"
    )
    fulfillments: Mapped[list["SupplierFulfillment"]] = relationship(
        "SupplierFulfillment", back_populates="order", cascade="all, delete-orphan",
        order_by="SupplierFulfillment.id", lazy="selectin",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False)
    # Keep the product name snapshot for order history if products are reset.
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="items")
    product: Mapped["Product"] = relationship("Product", back_populates="order_items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    order: Mapped["Order"] = relationship("Order", back_populates="history")


class SupplierFulfillment(Base):
    """One supplier's durable part of an order, with its own response state."""
    __tablename__ = "supplier_fulfillments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    supplier_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    items_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SupplierFulfillmentStatus] = mapped_column(
        Enum(SupplierFulfillmentStatus), default=SupplierFulfillmentStatus.queued, nullable=False
    )
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failure_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # The supplier timeout counts from delivery, not from order creation.
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    proof_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    order: Mapped["Order"] = relationship("Order", back_populates="fulfillments")


class ApiStore(Base):
    """API store clients — each gets its own key, name, and daily limit."""
    __tablename__ = "api_stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Only a SHA-256 hash of the key is stored; the full key is shown once.
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = unlimited
    orders_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reset: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=utcnow)
    webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ApiStoreCustomer(Base):
    """Customers an API store may order for. A store with no rows may order for any member."""
    __tablename__ = "api_store_customers"
    __table_args__ = (UniqueConstraint("store_id", "telegram_id", name="ux_store_customer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("api_stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UserOrderLimit(Base):
    """Per-user daily order limits set by admin. No row = unlimited, 0 = blocked."""
    __tablename__ = "user_order_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 0 = blocked
    orders_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reset: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SavedPlayerId(Base):
    __tablename__ = "saved_player_ids"
    __table_args__ = (UniqueConstraint("user_id", "player_id", name="ux_saved_player"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WebhookEvent(Base):
    """Outbox of order status events for API stores, delivered by the bot worker."""
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    admin_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class SupportMessage(Base):
    """Maps a support message shown to an admin back to the customer who sent it."""
    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_admin_msg", "admin_chat_id", "admin_message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    admin_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ApiRateCounter(Base):
    """Fixed one-minute request windows per API caller, shared by all WSGI workers."""
    __tablename__ = "api_rate_counters"
    __table_args__ = (UniqueConstraint("bucket", "window_start", name="ux_rate_bucket_window"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(80), nullable=False)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
