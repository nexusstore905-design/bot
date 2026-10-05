"""Supplier delivery retries and the supplier timeout."""
from conftest import (
    ADMIN_ID,
    SUPPLIER_A,
    SUPPLIER_B,
    FakeBot,
    make_order,
    make_user,
    minutes_ago,
    run,
    sql,
)
from database.database import AsyncSessionLocal
from database.models import SupplierFulfillmentStatus
from database.repositories.order_repo import OrderRepository
from services.dispatch import RETRY, SENT, dispatch_due, dispatch_order
from services.expiry import expire_stale_orders


def order_status(order_id):
    return sql("SELECT status FROM orders WHERE order_id = ?", (order_id,))[0][0]


def test_delivery_retries_then_fails_and_alerts():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot(fail_chats={SUPPLIER_A})

    assert [o.result for o in run(dispatch_order(bot, order.id))] == [RETRY]
    # Not due yet: nothing happens.
    assert run(dispatch_due(bot)) == 0
    sql("UPDATE supplier_fulfillments SET next_attempt_at = datetime('now', '-1 second')")
    assert run(dispatch_due(bot)) == 1
    sql("UPDATE supplier_fulfillments SET next_attempt_at = datetime('now', '-1 second')")
    assert run(dispatch_due(bot)) == 1

    assert sql("SELECT status, dispatch_attempts FROM supplier_fulfillments") == [("failed", 3)]
    assert order_status(order.order_id) == "failed"
    assert any("SUPPLIER DELIVERY FAILED" in m["text"] for m in bot.sent_to(ADMIN_ID))
    assert any("needs support" in m["text"] for m in bot.sent_to(111))


def test_successful_delivery_records_timestamps():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot()
    assert [o.result for o in run(dispatch_order(bot, order.id))] == [SENT]
    status, msg_id, dispatched = sql("SELECT status, supplier_msg_id, dispatched_at FROM supplier_fulfillments")[0]
    assert status == "pending" and msg_id and dispatched
    # A second dispatch never sends the same part twice.
    assert [o.result for o in run(dispatch_order(bot, order.id))] == ["skipped"]
    assert len(bot.sent_to(SUPPLIER_A)) == 1


def test_orphaned_queued_parts_are_picked_up_but_fresh_ones_are_not():
    user = run(make_user(111))
    run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot()
    assert run(dispatch_due(bot)) == 0  # just created: the creating process will send it
    sql("UPDATE supplier_fulfillments SET updated_at = ?", (minutes_ago(1),))
    assert run(dispatch_due(bot)) == 1


def _deliver(order, bot):
    run(dispatch_order(bot, order.id))


def test_timeout_counts_from_delivery_not_creation():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot()
    _deliver(order, bot)
    sql("UPDATE orders SET created_at = ?", (minutes_ago(30),))
    sql("UPDATE supplier_fulfillments SET dispatched_at = ?", (minutes_ago(5),))
    assert run(expire_stale_orders(bot)) == 0
    assert order_status(order.order_id) == "pending"


def test_timeout_with_no_completed_parts_cancels_once():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot()
    _deliver(order, bot)
    sql("UPDATE orders SET customer_msg_id = 77")
    sql("UPDATE supplier_fulfillments SET dispatched_at = ?", (minutes_ago(11),))
    bot.sent.clear()

    assert run(expire_stale_orders(bot)) == 1
    assert order_status(order.order_id) == "cancelled"
    # Supplier message edited (not duplicated), customer card edited plus ONE notice, ONE admin summary.
    assert [e["chat_id"] for e in bot.edited] == [SUPPLIER_A, 111]
    assert len(bot.sent_to(SUPPLIER_A)) == 0
    assert len(bot.sent_to(111)) == 1 and "auto-cancelled" in bot.sent_to(111)[0]["text"]
    assert len(bot.sent_to(ADMIN_ID)) == 1
    # Already closed: a second sweep does nothing.
    assert run(expire_stale_orders(bot)) == 0


def test_timeout_keeps_completed_parts_and_marks_order_failed():
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC"), (SUPPLIER_B, "Gems")]))
    bot = FakeBot()
    _deliver(order, bot)

    async def complete_first():
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            part = (await repo.get_fulfillments(order.id))[0]
            await repo.update_fulfillment_status(part, SupplierFulfillmentStatus.completed, "supplier:test")
            await repo.refresh_order_status_from_fulfillments(order.id, "supplier:test")

    run(complete_first())
    assert order_status(order.order_id) == "processing"
    sql("UPDATE supplier_fulfillments SET dispatched_at = ?", (minutes_ago(11),))

    assert run(expire_stale_orders(bot)) == 1
    assert order_status(order.order_id) == "failed"
    statuses = sql("SELECT category, status FROM supplier_fulfillments ORDER BY id")
    assert statuses == [("UC", "completed"), ("Gems", "failed")]


def test_expiry_never_overwrites_a_part_answered_meanwhile(monkeypatch):
    """The timeout UPDATE is guarded by the status it read, so a supplier's Done wins the race."""
    user = run(make_user(111))
    order = run(make_order(user, [(SUPPLIER_A, "UC")]))
    bot = FakeBot()
    _deliver(order, bot)
    sql("UPDATE supplier_fulfillments SET dispatched_at = ?", (minutes_ago(11),))

    original_execute = None

    async def scenario():
        nonlocal original_execute
        async with AsyncSessionLocal() as session:
            repo = OrderRepository(session)
            original_execute = session.execute
            calls = {"n": 0}

            async def execute_with_race(statement, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    # The supplier presses Done right after the sweep loaded the order.
                    sql("UPDATE supplier_fulfillments SET status = 'completed'")
                return await original_execute(statement, *args, **kwargs)

            session.execute = execute_with_race
            from datetime import timedelta

            from utils.helpers import utcnow
            return await repo.expire_stale(utcnow(), timedelta(minutes=10))

    assert run(scenario()) == []
    assert sql("SELECT status FROM supplier_fulfillments") == [("completed",)]
