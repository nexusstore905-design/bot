"""Daily limits. The store test reproduces the production 500 (naive vs aware datetimes)."""
import pytest

from conftest import make_store, run, sql
from database.database import AsyncSessionLocal
from database.repositories.api_store_repo import ApiStoreRepository, UserOrderLimitRepository


def test_store_limit_resets_daily_then_blocks():
    store, _ = run(make_store(daily_limit=2))
    # Yesterday's counter is full; it must reset instead of blocking today's orders.
    sql("UPDATE api_stores SET orders_today = 2, last_reset = datetime('now', '-1 day')")

    async def reserve():
        async with AsyncSessionLocal() as session:
            repo = ApiStoreRepository(session)
            loaded = await repo.get_by_id(store.id)  # loaded row in the session = the old crash
            allowed, reason = await repo.check_and_increment(loaded)
            await session.commit()
            return allowed, reason

    assert run(reserve())[0] is True
    assert run(reserve())[0] is True
    allowed, reason = run(reserve())
    assert allowed is False
    assert "Daily order limit reached (2/2)" in reason


def test_user_limit_semantics():
    async def check(telegram_id):
        async with AsyncSessionLocal() as session:
            result = await UserOrderLimitRepository(session).check_and_increment(telegram_id)
            await session.commit()
            return result

    assert run(check(1))[0] is True  # no row = unlimited

    async def set_limit(telegram_id, value):
        async with AsyncSessionLocal() as session:
            await UserOrderLimitRepository(session).set_limit(telegram_id, value)

    run(set_limit(2, 0))
    allowed, reason = run(check(2))
    assert allowed is False and "disabled" in reason

    run(set_limit(3, 1))
    sql("UPDATE user_order_limits SET last_reset = datetime('now', '-2 day'), orders_today = 1")
    assert run(check(3))[0] is True
    assert run(check(3))[0] is False

    with pytest.raises(ValueError):
        run(set_limit(4, -5))


def test_blocked_users_are_listed():
    async def scenario():
        async with AsyncSessionLocal() as session:
            repo = UserOrderLimitRepository(session)
            await repo.set_limit(10, 0)
            await repo.set_limit(11, 3)
            return [(row.telegram_id, row.daily_limit) for row in await repo.get_all()]

    assert run(scenario()) == [(10, 0), (11, 3)]
