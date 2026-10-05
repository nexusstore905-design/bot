"""Dashboard, supplier performance, CSV export, and the admin activity log."""
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from telegram import InputFile, Update
from telegram.ext import ContextTypes

from bot.handlers.admin.common import PAKISTAN_TZ, admin_only, advanced_kb, local_datetime
from bot.keyboards.admin_kb import admin_dashboard_kb
from database.database import AsyncSessionLocal
from database.models import ApiStore, Order, SupplierFulfillment, SupplierFulfillmentStatus
from database.repositories.stats_repo import StatsRepository
from services.audit import audit
from utils.exports import orders_csv
from utils.helpers import utcnow
from utils.ui import esc


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _day_bounds_pkt(days_back: int = 0):
    today = datetime.now(PAKISTAN_TZ).date() - timedelta(days=days_back)
    start = datetime.combine(today, time.min, tzinfo=PAKISTAN_TZ)
    # Aware PKT datetimes; UTCDateTime converts them to UTC when binding.
    return start, start + timedelta(days=1)


@admin_only
async def cb_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    today_start, today_end = _day_bounds_pkt()
    week_start = today_start - timedelta(days=6)
    async with AsyncSessionLocal() as session:
        stats = StatsRepository(session)
        today = await stats.order_counts(today_start, today_end)
        week = await stats.order_counts(week_start, today_end)
        sources = await stats.store_counts(today_start, today_end)
        suppliers = await stats.supplier_stats(week_start, today_end)
        customers = await stats.customer_counts()
        retrying = await session.scalar(
            select(func.count()).select_from(SupplierFulfillment).where(
                SupplierFulfillment.status == SupplierFulfillmentStatus.queued,
                SupplierFulfillment.dispatch_attempts > 0,
            )
        )
        waiting = await session.scalar(
            select(func.count()).select_from(SupplierFulfillment).where(
                SupplierFulfillment.status == SupplierFulfillmentStatus.pending,
            )
        )

    finished = week["completed"] + week["failed"] + week["cancelled"]
    success = f"{week['completed'] / finished * 100:.0f}%" if finished else "—"
    response_times = [
        row["avg_response_seconds"] * (row["completed"] + row["failed"])
        for row in suppliers if row["avg_response_seconds"] is not None
    ]
    responded = sum(row["completed"] + row["failed"] for row in suppliers if row["avg_response_seconds"] is not None)
    avg_response = sum(response_times) / responded if responded else None

    lines = [
        f"📊 <b>Dashboard</b> · {today_start.strftime('%d %b %Y')} (PKT)",
        "──────────────",
        "",
        f"<b>Today</b>: {sum(today.values())} orders",
        f"  ✅ {today['completed']}  ⚙️ {today['processing']}  ⏳ {today['pending']}  ❌ {today['failed']}  🚫 {today['cancelled']}",
        f"<b>Last 7 days</b>: {sum(week.values())} orders · success rate <b>{success}</b>",
        f"<b>Avg supplier response</b> (7d): {_duration(avg_response)}",
        "",
        f"📬 Waiting on suppliers now: <b>{waiting or 0}</b>",
        f"♻️ Deliveries being retried: <b>{retrying or 0}</b>",
        "",
        f"👥 Customers: {customers['total']} · signed in {customers['signed_in']} · revoked {customers['revoked']}",
    ]
    if sources:
        lines += ["", "<b>Orders by source today</b>"]
        lines += [f"• {esc(name)}: {count}" for name, count in sources]
    await update.callback_query.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=admin_dashboard_kb(),
    )


@admin_only
async def cb_supplier_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    end = utcnow()
    start = end - timedelta(days=7)
    async with AsyncSessionLocal() as session:
        rows = await StatsRepository(session).supplier_stats(start, end)
    if not rows:
        text = "🤝 <b>Supplier stats</b> (7 days)\n\nNo supplier work in the last 7 days."
    else:
        lines = ["🤝 <b>Supplier stats</b> (last 7 days)", "──────────────"]
        for row in rows[:15]:
            title = str(row["chat_id"])
            try:
                chat = await context.bot.get_chat(row["chat_id"])
                title = chat.title or title
            except Exception:
                pass
            lines.append(
                f"\n<b>{esc(title)}</b> <code>{row['chat_id']}</code>\n"
                f"Parts {row['parts']} · ✅ {row['completed']} · ❌ {row['failed']} · "
                f"⏱️ {row['timed_out']} · open {row['open']}\n"
                f"Avg response: {_duration(row['avg_response_seconds'])}"
            )
        text = "\n".join(lines)
    await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=admin_dashboard_kb())


@admin_only
async def cb_export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Preparing export…")
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user))
            .order_by(Order.created_at.desc())
        )
        orders = list(result.scalars().all())
        store_names = {store.id: store.name for store in (await session.execute(select(ApiStore))).scalars()}
    data = orders_csv(orders, store_names)
    stamp = datetime.now(PAKISTAN_TZ).strftime("%Y%m%d-%H%M")
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=InputFile(data, filename=f"orders-{stamp}.csv"),
        caption=f"📤 {len(orders)} orders exported (times in PKT).",
    )
    await audit(update.effective_user, "export_orders", f"{len(orders)} orders")


@admin_only
async def cb_audit_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    async with AsyncSessionLocal() as session:
        entries = await StatsRepository(session).recent_audit(20)
    if not entries:
        text = "🧾 <b>Admin activity log</b>\n\nNo admin actions recorded yet."
    else:
        lines = ["🧾 <b>Admin activity log</b> (latest 20)", "──────────────"]
        for entry in entries:
            detail = f" · {esc(entry.detail[:80])}" if entry.detail else ""
            lines.append(
                f"{local_datetime(entry.created_at)} · <b>{esc(entry.admin_name or entry.admin_id)}</b>\n"
                f"  {esc(entry.action)}{detail}"
            )
        text = "\n".join(lines)
    await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=await advanced_kb())
