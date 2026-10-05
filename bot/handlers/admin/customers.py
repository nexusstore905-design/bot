"""Customer list, details, order history, payment clearance, and per-customer exports."""
import re
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import InputFile, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.admin.common import (
    PAKISTAN_TZ,
    STATUS_ICONS,
    admin_label,
    admin_only,
    local_datetime,
    logger,
    team_only,
)
from bot.keyboards.admin_kb import (
    admin_customer_detail_kb,
    admin_customer_orders_kb,
    admin_customer_unsettled_kb,
    admin_customers_kb,
    admin_main_kb,
    cancel_conv_kb,
    confirm_customer_settlement_kb,
    customer_date_result_kb,
)
from bot.middlewares.auth_middleware import is_admin
from bot.states.states import ADMIN_CUSTOMER_DATE_RANGE
from database.database import AsyncSessionLocal
from database.models import ApiStore, Order, OrderStatus
from database.repositories.order_repo import OrderRepository
from database.repositories.user_repo import UserRepository
from services.audit import audit
from utils.exports import orders_csv
from utils.ui import esc

CUSTOMER_PAGE_SIZE = 10
CUSTOMER_ORDERS_PAGE_SIZE = 5


def _customer_page_text(customers, page: int, total: int) -> str:
    if not customers:
        return "No customers found."
    total_pages = max(1, (total + CUSTOMER_PAGE_SIZE - 1) // CUSTOMER_PAGE_SIZE)
    return (
        "👥 <b>Customers</b>\n"
        f"Page {page + 1} of {total_pages} · {total} customers\n\n"
        "Choose a customer to see their order totals and history."
    )


def _customer_summary_lines(summary: dict[str, dict[str, int]]) -> list[str]:
    total_orders = sum(values["orders"] for values in summary.values())
    total_packages = sum(values["packages"] for values in summary.values())
    labels = [
        ("pending", "⏳ Pending"),
        ("processing", "⚙️ Processing"),
        ("completed", "✅ Completed"),
        ("failed", "❌ Failed"),
        ("cancelled", "🚫 Cancelled"),
    ]
    lines = [f"Total orders: <b>{total_orders}</b> · Packages: <b>{total_packages}</b>"]
    for status, label in labels:
        values = summary.get(status, {"orders": 0, "packages": 0})
        lines.append(f"{label}: <b>{values['orders']}</b> orders · {values['packages']} packages")
    return lines


def customer_date_bounds(text: str) -> tuple[datetime | None, datetime, str]:
    """Parse a single cutoff date or an inclusive date range in Pakistan time."""
    parts = re.split(r"\s+(?:to|through|until)\s+", text.strip(), maxsplit=1, flags=re.IGNORECASE)
    try:
        if len(parts) == 1:
            cutoff = date.fromisoformat(parts[0].strip())
            end_date = cutoff + timedelta(days=1)
            label = f"All dates through {cutoff.isoformat()} (Pakistan time)"
            start_at = None
        else:
            start_date = date.fromisoformat(parts[0].strip())
            end_date_inclusive = date.fromisoformat(parts[1].strip())
            if start_date > end_date_inclusive:
                raise ValueError
            end_date = end_date_inclusive + timedelta(days=1)
            label = (
                f"{start_date.isoformat()} through {end_date_inclusive.isoformat()} "
                "(Pakistan time)"
            )
            start_at = datetime.combine(start_date, time.min, tzinfo=PAKISTAN_TZ).astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("Enter YYYY-MM-DD or YYYY-MM-DD to YYYY-MM-DD.") from exc

    end_at = datetime.combine(end_date, time.min, tzinfo=PAKISTAN_TZ).astimezone(timezone.utc)
    return start_at, end_at, label


async def _get_customer_page(session, page: int):
    users, total = await UserRepository(session).get_customer_page(page, CUSTOMER_PAGE_SIZE)
    return users, total, admin_customers_kb(users, page, total, CUSTOMER_PAGE_SIZE)


@team_only
async def cb_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        customers, total, keyboard = await _get_customer_page(session, 0)

    if not total:
        await update.callback_query.message.edit_text(
            "No users yet.", reply_markup=admin_main_kb(is_admin(update.effective_user.id)),
        )
        return

    await update.callback_query.message.edit_text(
        _customer_page_text(customers, 0, total), parse_mode="HTML", reply_markup=keyboard
    )


@team_only
async def cb_customer_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    page = max(0, int(update.callback_query.data.split(":", 1)[1]))
    async with AsyncSessionLocal() as session:
        customers, total, keyboard = await _get_customer_page(session, page)
    if page * CUSTOMER_PAGE_SIZE >= total and total:
        page = max(0, (total - 1) // CUSTOMER_PAGE_SIZE)
        async with AsyncSessionLocal() as session:
            customers, total, keyboard = await _get_customer_page(session, page)
    await update.callback_query.message.edit_text(
        _customer_page_text(customers, page, total), parse_mode="HTML", reply_markup=keyboard
    )


@team_only
async def cb_customer_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    telegram_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if user is None:
            await update.callback_query.message.edit_text("Customer not found.")
            return
        orders = OrderRepository(session)
        summary = await orders.summarize_user_orders(user.id)
        latest_orders = await orders.get_by_user(user.id, limit=1)
        unsettled_count = await orders.count_unsettled_by_user(user.id)
        summary_stats = await orders.customer_summary(user.id)

    name = esc(user.full_name or "Unknown")
    username = f"@{esc(user.username)}" if user.username else "Not set"
    last_order = "—"
    if latest_orders:
        last_order = f"{local_datetime(latest_orders[0].created_at)} · {latest_orders[0].status.value}"
    text = (
        "👤 <b>Customer details</b>\n\n"
        f"Name: <b>{name}</b>\n"
        f"Username: {username}\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n"
        f"Access: {esc(user.auth_status.value)}\n"
        f"Joined: {local_datetime(user.created_at)}\n"
        f"Last order: {last_order}\n"
        f"Language: {esc(user.language or 'not chosen')}\n"
        f"Last payment cleared: {local_datetime(summary_stats['last_settled_at'])}\n\n"
        f"💰 Completed orders with payment not cleared: <b>{unsettled_count}</b>\n\n"
        "<b>All-time orders</b>\n"
        + "\n".join(_customer_summary_lines(summary))
    )
    await update.callback_query.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=admin_customer_detail_kb(telegram_id, unsettled_count, is_admin(update.effective_user.id)),
    )


@team_only
async def cb_customer_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, telegram_id_text, page_text = update.callback_query.data.split(":", 2)
    telegram_id, page = int(telegram_id_text), max(0, int(page_text))
    offset = page * CUSTOMER_ORDERS_PAGE_SIZE
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if user is None:
            await update.callback_query.message.edit_text("Customer not found.")
            return
        orders_repo = OrderRepository(session)
        total = await orders_repo.count_by_user(user.id)
        orders = await orders_repo.get_by_user(
            user.id, limit=CUSTOMER_ORDERS_PAGE_SIZE, offset=offset
        )

    if not orders:
        await update.callback_query.message.edit_text(
            "This customer has no orders yet.",
            reply_markup=admin_customer_detail_kb(telegram_id),
        )
        return

    lines = [f"🧾 <b>Order history</b> · {total} total · Page {page + 1}\n"]
    for order in orders:
        items = ", ".join(
            f"{esc(item.product_name)} ×{item.quantity}"
            for item in order.items
        ) or "—"
        icon = STATUS_ICONS.get(order.status.value, "📦")
        if order.settled_at:
            payment_label = f"✅ Payment cleared · {local_datetime(order.settled_at)}"
        elif order.status == OrderStatus.completed:
            payment_label = "💰 Payment not cleared"
        else:
            payment_label = "ℹ️ Payment tracking starts after completion"
        lines.append(
            f"{icon} <b>{esc(order.order_id)}</b> · {order.status.value.upper()}\n"
            f"{payment_label}\n"
            f"{local_datetime(order.created_at)}\n"
            f"Product: {items}\n"
            f"Player ID: <code>{esc(order.player_id)}</code>"
        )
    await update.callback_query.message.edit_text(
        "\n\n".join(lines), parse_mode="HTML",
        reply_markup=admin_customer_orders_kb(
            telegram_id, page, offset + len(orders) < total
        ),
    )


@team_only
async def cb_customer_unsettled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, telegram_id_text, page_text = update.callback_query.data.split(":", 2)
    telegram_id, page = int(telegram_id_text), max(0, int(page_text))
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if user is None:
            await update.callback_query.message.edit_text("Customer not found.")
            return
        orders_repo = OrderRepository(session)
        total = await orders_repo.count_unsettled_by_user(user.id)
        page_count = max(1, (total + CUSTOMER_ORDERS_PAGE_SIZE - 1) // CUSTOMER_ORDERS_PAGE_SIZE)
        page = min(page, page_count - 1)
        offset = page * CUSTOMER_ORDERS_PAGE_SIZE
        orders = await orders_repo.get_unsettled_by_user(
            user.id, limit=CUSTOMER_ORDERS_PAGE_SIZE, offset=offset
        )

    name = esc(user.full_name or user.username or str(telegram_id))
    if not orders:
        text = (
            f"💰 <b>Unsettled orders · {name}</b>\n──────────────\n\n"
            "✅ No completed orders are waiting for payment clearance.\n\n"
            "Completed recharges will appear here until they are marked paid."
        )
    else:
        lines = [
            f"💰 <b>Unsettled orders · {name}</b> · {total} total · Page {page + 1} of {page_count}\n",
            "These completed recharges have not been marked paid.",
        ]
        for order in orders:
            items = ", ".join(
                f"{esc(item.product_name)} ×{item.quantity}"
                for item in order.items
            ) or "—"
            icon = STATUS_ICONS.get(order.status.value, "📦")
            lines.append(
                f"\n{icon} <b>{esc(order.order_id)}</b> · {order.status.value.upper()}\n"
                f"{local_datetime(order.created_at)} · 💰 Payment not cleared\n"
                f"Packages: {items}\n"
                f"Player ID: <code>{esc(order.player_id)}</code>"
            )
        text = "\n".join(lines)
    await update.callback_query.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=admin_customer_unsettled_kb(
            telegram_id, page, offset + len(orders) < total
        ),
    )


@admin_only
async def cb_customer_settle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    telegram_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if user is None:
            await update.callback_query.message.edit_text("Customer not found.")
            return
        repo = OrderRepository(session)
        high_watermark = await repo.get_unsettled_high_watermark_by_user(user.id)
        unsettled_count = await repo.count_unsettled_by_user(
            user.id, through_order_id=high_watermark
        )

    if not unsettled_count:
        await update.callback_query.message.edit_text(
            "✅ <b>No unsettled orders to clear.</b>\n\n"
            "Completed recharges will appear here until they are marked paid.",
            parse_mode="HTML",
            reply_markup=admin_customer_detail_kb(telegram_id, 0),
        )
        return

    name = esc(user.full_name or user.username or str(telegram_id))
    await update.callback_query.message.edit_text(
        f"✅ <b>Mark orders as paid for {name}?</b>\n──────────────\n\n"
        f"Orders to clear: <b>{unsettled_count}</b>\n\n"
        "This marks the customer’s current completed, unsettled recharges as paid. Their full order history stays saved. "
        "New completed recharges after this confirmation screen will remain marked payment not cleared.",
        parse_mode="HTML",
        reply_markup=confirm_customer_settlement_kb(telegram_id, high_watermark),
    )


@admin_only
async def cb_customer_settle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Saving payment status…")
    _, telegram_id_text, high_watermark_text = update.callback_query.data.split(":", 2)
    telegram_id, high_watermark = int(telegram_id_text), int(high_watermark_text)
    settled_at = datetime.now(timezone.utc)

    try:
        async with AsyncSessionLocal() as session:
            user = await UserRepository(session).get_by_telegram_id(telegram_id)
            if user is None:
                await query.message.edit_text("Customer not found.")
                return
            repo = OrderRepository(session)
            settled_count = await repo.settle_unsettled_by_user(
                user.id, admin_label(update.effective_user), settled_at, high_watermark
            )
            unsettled_count = await repo.count_unsettled_by_user(user.id)
    except Exception:
        logger.exception(
            "Failed to mark completed orders as paid for customer telegram_id=%s",
            telegram_id,
        )
        await query.message.edit_text(
            "❌ <b>Could not confirm the payment update.</b> Refresh the customer’s unsettled orders to see the saved status, then check the bot/database logs if the orders are still unsettled.",
            parse_mode="HTML",
            reply_markup=admin_customer_detail_kb(telegram_id),
        )
        return

    if settled_count:
        await audit(update.effective_user, "settle_customer", f"{telegram_id}: {settled_count} order(s)")
        text = (
            f"✅ <b>{settled_count} order(s) marked paid.</b>\n\n"
            "The orders remain in full history. Any newer completed recharges are still marked payment not cleared."
        )
    else:
        text = (
            "ℹ️ <b>No orders were changed.</b>\n\n"
            "Another admin may already have cleared these orders. Newer completed recharges remain unsettled."
        )
    await query.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=admin_customer_detail_kb(telegram_id, unsettled_count),
    )


@admin_only
async def cb_customer_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Preparing export…")
    telegram_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if user is None:
            await update.callback_query.message.reply_text("Customer not found.")
            return
        result = await session.execute(
            select(Order)
            .where(Order.user_id == user.id)
            .options(selectinload(Order.items), selectinload(Order.user))
            .order_by(Order.created_at.desc())
        )
        orders = list(result.scalars().all())
        store_names = {store.id: store.name for store in (await session.execute(select(ApiStore))).scalars()}
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=InputFile(orders_csv(orders, store_names), filename=f"customer-{telegram_id}-orders.csv"),
        caption=f"📤 {len(orders)} orders for {esc(user.full_name or telegram_id)} (times in PKT).",
    )


@team_only
async def cb_customer_dates_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    telegram_id = int(update.callback_query.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if user is None:
        await update.callback_query.message.edit_text("Customer not found.")
        return ConversationHandler.END
    context.user_data["admin_customer_date_telegram_id"] = telegram_id
    await update.callback_query.message.edit_text(
        "📅 Enter a date to count all orders up to that day, or enter a date range.\n\n"
        "Examples:\n"
        "• <code>2026-10-01</code> (from the beginning through this date)\n"
        "• <code>2026-09-01 to 2026-10-01</code> (inclusive)\n\n"
        "Dates use Pakistan time.",
        parse_mode="HTML",
        reply_markup=cancel_conv_kb(),
    )
    return ADMIN_CUSTOMER_DATE_RANGE


@team_only
async def admin_customer_date_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        start_at, end_at, label = customer_date_bounds(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(
            f"❌ {esc(exc)}\nPlease enter a valid date or date range, for example <code>2026-10-01</code>.",
            parse_mode="HTML",
        )
        return ADMIN_CUSTOMER_DATE_RANGE

    telegram_id = context.user_data.pop("admin_customer_date_telegram_id", None)
    if telegram_id is None:
        await update.message.reply_text("Customer selection expired. Open Customers and select them again.")
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if user is None:
            await update.message.reply_text("Customer not found.")
            return ConversationHandler.END
        summary = await OrderRepository(session).summarize_user_orders(
            user.id, start_at=start_at, end_at=end_at
        )

    name = esc(user.full_name or str(telegram_id))
    report = (
        f"📅 <b>Orders for {name}</b>\n"
        f"Period: {label}\n\n"
        + "\n".join(_customer_summary_lines(summary))
    )
    await update.message.reply_text(
        report, parse_mode="HTML", reply_markup=customer_date_result_kb(telegram_id)
    )
    return ConversationHandler.END
