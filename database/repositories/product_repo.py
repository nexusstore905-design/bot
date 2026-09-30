from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Product


class ProductRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active(self) -> list[Product]:
        result = await self.session.execute(
            select(Product).where(Product.is_active == True).order_by(Product.category, Product.name)
        )
        return list(result.scalars().all())

    async def get_all(self) -> list[Product]:
        result = await self.session.execute(
            select(Product).order_by(Product.category, Product.name)
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
            .order_by(Product.name)
        )
        return list(result.scalars().all())

    async def get_supplier_for_category(self, category: str) -> int | None:
        """Return the supplier_chat_id for this category (from any active product in it)."""
        result = await self.session.execute(
            select(Product.supplier_chat_id)
            .where(Product.category == category, Product.supplier_chat_id.isnot(None))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return int(row) if row else None

    async def get_by_id(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def add(self, category: str, name: str, supplier_chat_id: int | None = None) -> Product:
        product = Product(category=category, name=name, supplier_chat_id=supplier_chat_id)
        self.session.add(product)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def update_name(self, product_id: int, new_name: str) -> bool:
        product = await self.get_by_id(product_id)
        if product is None:
            return False
        product.name = new_name
        await self.session.commit()
        return True

    async def set_category_supplier(self, category: str, supplier_chat_id: int | None) -> int:
        """Set supplier_chat_id for ALL products in a category. Returns how many updated."""
        result = await self.session.execute(
            select(Product).where(Product.category == category)
        )
        products = list(result.scalars().all())
        for p in products:
            p.supplier_chat_id = supplier_chat_id
        await self.session.commit()
        return len(products)

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
                ("PUBG UC", "60 UC"),
                ("PUBG UC", "325 UC"),
                ("PUBG UC", "660 UC"),
                ("PUBG UC", "1800 UC"),
                ("PUBG UC", "3850 UC"),
                ("PUBG UC", "8100 UC"),
            ]
            for cat, name in defaults:
                self.session.add(Product(category=cat, name=name))
            await self.session.commit()
