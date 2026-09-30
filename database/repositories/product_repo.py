from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Product


class ProductRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active(self) -> list[Product]:
        result = await self.session.execute(
            select(Product).where(Product.is_active == True).order_by(Product.category, Product.price)
        )
        return list(result.scalars().all())

    async def get_all(self) -> list[Product]:
        result = await self.session.execute(
            select(Product).order_by(Product.category, Product.price)
        )
        return list(result.scalars().all())

    async def get_categories(self) -> list[str]:
        result = await self.session.execute(
            select(Product.category).where(Product.is_active == True).distinct()
        )
        return [r[0] for r in result.all()]

    async def get_by_category(self, category: str) -> list[Product]:
        result = await self.session.execute(
            select(Product)
            .where(Product.is_active == True, Product.category == category)
            .order_by(Product.price)
        )
        return list(result.scalars().all())

    async def get_by_id(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def add(self, category: str, name: str, price: float) -> Product:
        product = Product(category=category, name=name, price=price)
        self.session.add(product)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def update_price(self, product_id: int, new_price: float) -> bool:
        product = await self.get_by_id(product_id)
        if product is None:
            return False
        product.price = new_price
        await self.session.commit()
        return True

    async def deactivate(self, product_id: int) -> bool:
        product = await self.get_by_id(product_id)
        if product is None:
            return False
        product.is_active = False
        await self.session.commit()
        return True

    async def seed_defaults(self) -> None:
        count_result = await self.session.execute(select(func.count()).select_from(Product))
        if count_result.scalar() == 0:
            defaults = [
                ("PUBG UC", "60 UC", 0.99),
                ("PUBG UC", "325 UC", 4.99),
                ("PUBG UC", "660 UC", 9.99),
                ("PUBG UC", "1800 UC", 24.99),
                ("PUBG UC", "3850 UC", 49.99),
                ("PUBG UC", "8100 UC", 99.99),
            ]
            for cat, name, price in defaults:
                self.session.add(Product(category=cat, name=name, price=price))
            await self.session.commit()
