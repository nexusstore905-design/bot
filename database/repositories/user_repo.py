import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import LOCKOUT_MINUTES, MAX_PIN_ATTEMPTS, SESSION_HOURS
from database.models import AccessCode, AuthStatus, Order, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_access_code(length: int = 8) -> str:
    """Generate a random alphanumeric access code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


class UserRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, telegram_id: int, username: str | None, full_name: str | None) -> User:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram_id, username=username, full_name=full_name)
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
        else:
            changed = False
            if user.username != username:
                user.username = username
                changed = True
            if user.full_name != full_name:
                user.full_name = full_name
                changed = True
            if changed:
                await self.session.commit()
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def get_all(self) -> list[User]:
        result = await self.session.execute(select(User).order_by(User.created_at.desc()))
        return list(result.scalars().all())

    async def get_customer_page(
        self, page: int = 0, page_size: int = 10
    ) -> tuple[list[tuple[User, int]], int]:
        """Return customers with lifetime order counts and total customer count."""
        page = max(0, page)
        result = await self.session.execute(
            select(User, func.count(distinct(Order.id)))
            .outerjoin(Order, Order.user_id == User.id)
            .group_by(User.id)
            .order_by(User.created_at.desc(), User.telegram_id)
            .limit(page_size)
            .offset(page * page_size)
        )
        customers = [(user, int(order_count or 0)) for user, order_count in result.all()]
        total_result = await self.session.execute(select(func.count()).select_from(User))
        return customers, int(total_result.scalar_one() or 0)

    async def get_broadcast_recipients(self, exclude: list[int]) -> list[int]:
        """Telegram IDs of members who have signed in at least once and are not revoked."""
        statement = select(User.telegram_id).where(
            User.last_login.is_not(None),
            User.auth_status != AuthStatus.revoked,
        )
        if exclude:
            statement = statement.where(User.telegram_id.not_in(exclude))
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    @staticmethod
    def is_member(user: User) -> bool:
        """True for users who redeemed an access code at some point and are not revoked."""
        return user.last_login is not None and user.auth_status != AuthStatus.revoked

    async def is_locked(self, user: User) -> bool:
        if user.locked_until and user.locked_until > _utcnow():
            return True
        return False

    async def is_session_valid(self, user: User) -> bool:
        if user.auth_status != AuthStatus.authenticated:
            return False
        if SESSION_HOURS > 0 and user.session_expires:
            if user.session_expires < _utcnow():
                user.auth_status = AuthStatus.unauthenticated
                await self.session.commit()
                return False
        return True

    async def try_register_with_code(self, user: User, code_input: str) -> tuple[bool, str, dict]:
        """
        Try to sign in with an admin-created access code.
        Returns (success, i18n key, params) so callers can show it in the user's language.
        """
        if user.auth_status == AuthStatus.revoked:
            return False, "access_revoked", {}

        if await self.is_locked(user):
            minutes = int((user.locked_until - _utcnow()).total_seconds() // 60) + 1
            return False, "code_locked", {"minutes": minutes}

        code_input = code_input.strip().upper()
        result = await self.session.execute(
            select(AccessCode).where(AccessCode.code == code_input)
        )
        access_code = result.scalar_one_or_none()
        usable = (
            access_code is not None and access_code.is_active
            and access_code.used_by in (None, user.telegram_id)
        )
        if not usable:
            # Wrong, revoked, or someone else's code: count it (brute-force protection).
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_PIN_ATTEMPTS:
                user.locked_until = _utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_attempts = 0
                await self.session.commit()
                return False, "code_locked", {"minutes": LOCKOUT_MINUTES}
            await self.session.commit()
            return False, "code_invalid", {"remaining": MAX_PIN_ATTEMPTS - user.failed_attempts}

        if access_code.used_by is None:
            access_code.used_by = user.telegram_id
            access_code.used_at = _utcnow()

        user.auth_status = AuthStatus.authenticated
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login = _utcnow()
        user.session_expires = (
            _utcnow() + timedelta(hours=SESSION_HOURS) if SESSION_HOURS > 0 else None
        )
        await self.session.commit()
        return True, "signin_ok", {}

    async def set_language(self, user: User, language: str) -> None:
        user.language = language
        await self.session.commit()

    async def logout(self, user: User) -> None:
        user.auth_status = AuthStatus.unauthenticated
        user.session_expires = None
        await self.session.commit()

    async def revoke(self, telegram_id: int) -> bool:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            return False
        user.auth_status = AuthStatus.revoked
        await self.session.commit()
        return True

    async def reset_auth(self, telegram_id: int) -> bool:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            return False
        user.auth_status = AuthStatus.unauthenticated
        user.failed_attempts = 0
        user.locked_until = None
        user.session_expires = None
        await self.session.commit()
        return True


class AccessCodeRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, label: str | None = None, custom_code: str | None = None) -> AccessCode:
        """Create a new access code. Auto-generates if no custom code given."""
        for _ in range(10):
            code = custom_code.strip().upper() if custom_code else generate_access_code(8)
            existing = await self.session.execute(select(AccessCode).where(AccessCode.code == code))
            if existing.scalar_one_or_none() is None:
                break

        ac = AccessCode(code=code, label=label)
        self.session.add(ac)
        await self.session.commit()
        await self.session.refresh(ac)
        return ac

    async def get_all(self) -> list[AccessCode]:
        result = await self.session.execute(select(AccessCode).order_by(AccessCode.created_at.desc()))
        return list(result.scalars().all())

    async def get_active_unused(self) -> list[AccessCode]:
        result = await self.session.execute(
            select(AccessCode)
            .where(AccessCode.is_active.is_(True), AccessCode.used_by.is_(None))
            .order_by(AccessCode.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_code(self, code: str) -> bool:
        result = await self.session.execute(select(AccessCode).where(AccessCode.code == code.upper()))
        ac = result.scalar_one_or_none()
        if ac is None:
            return False
        ac.is_active = False
        await self.session.commit()
        return True

    async def delete_unused(self, code: str) -> bool:
        result = await self.session.execute(select(AccessCode).where(AccessCode.code == code.upper()))
        ac = result.scalar_one_or_none()
        if ac is None or ac.used_by is not None:
            return False
        await self.session.delete(ac)
        await self.session.commit()
        return True
