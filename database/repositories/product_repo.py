import re
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Product


def _product_name_sort_key(name: str) -> tuple[int, int, str]:
    """Sort numeric denominations by amount, then other names alphabetically."""
    match = re.match(r"\s*(\d+)", name)
    if match:
        return 0, int(match.group(1)), name.casefold()
    return 1, 0, name.casefold()


class ProductRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active(self) -> list[Product]:
        result = await self.session.execute(
            select(Product).where(Product.is_active == True).order_by(Product.category)
        )
        products = list(result.scalars().all())
        return sorted(products, key=lambda p: (p.category.casefold(), *_product_name_sort_key(p.name)))

    async def get_all(self) -> list[Product]:
        result = await self.session.execute(
            select(Product).order_by(Product.category)
        )
        products = list(result.scalars().all())
        return sorted(products, key=lambda p: (p.category.casefold(), *_product_name_sort_key(p.name)))

    async def get_categories(self) -> list[str]:
        result = await self.session.execute(
            select(Product.category)
            .where(Product.is_active == True)
            .distinct()
            .order_by(func.lower(Product.category))
        )
        return [r[0] for r in result.all()]

    async def get_by_category(self, category: str) -> list[Product]:
        result = await self.session.execute(
            select(Product)
            .where(Product.is_active == True, Product.category == category)
        )
        products = list(result.scalars().all())
        return sorted(products, key=lambda p: _product_name_sort_key(p.name))

    async def get_supplier_for_category(self, category: str) -> int | None:
        """Return the supplier chat saved for this exact product group."""
        cat_clean = category.strip().lower()
        result = await self.session.execute(
            select(Product.supplier_chat_id)
            .where(func.lower(Product.category) == cat_clean, Product.supplier_chat_id.isnot(None))
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

    async def add_many(self, category: str, names: list[str]) -> tuple[list[Product], list[str]]:
        """Add package names to a category, skipping duplicates and inheriting its supplier."""
        category = category.strip()
        if not category:
            return [], []

        result = await self.session.execute(
            select(Product).where(func.lower(Product.category) == category.casefold())
        )
        existing_products = list(result.scalars().all())
        saved_category = existing_products[0].category if existing_products else category
        supplier_chat_id = next(
            (product.supplier_chat_id for product in existing_products if product.supplier_chat_id is not None),
            None,
        )
        existing_names = {product.name.strip().casefold() for product in existing_products}
        seen_names: set[str] = set()
        products: list[Product] = []
        skipped: list[str] = []

        for raw_name in names:
            name = raw_name.strip()
            name_key = name.casefold()
            if not name:
                continue
            if name_key in existing_names or name_key in seen_names:
                skipped.append(name)
                continue
            seen_names.add(name_key)
            products.append(
                Product(
                    category=saved_category,
                    name=name,
                    supplier_chat_id=supplier_chat_id,
                )
            )

        if products:
            self.session.add_all(products)
            await self.session.commit()
        return products, skipped

    async def update_name(self, product_id: int, new_name: str) -> bool:
        product = await self.get_by_id(product_id)
        if product is None:
            return False
        product.name = new_name
        await self.session.commit()
        return True

    async def set_category_supplier(self, category: str, supplier_chat_id: int | None) -> tuple[int, str]:
        """Set supplier_chat_id for ALL products matching category (case-insensitive). Returns (count, matched_cat)."""
        cat_clean = category.strip().lower()
        result = await self.session.execute(
            select(Product).where(func.lower(Product.category) == cat_clean)
        )
        products = list(result.scalars().all())

        if not products:
            return 0, category

        matched_cat = products[0].category
        for p in products:
            p.supplier_chat_id = supplier_chat_id
        await self.session.commit()
        return len(products), matched_cat

    async def rename_category(self, old_category: str, new_category: str) -> tuple[int, str]:
        """Rename a product group without changing its packages or supplier IDs."""
        old_clean = old_category.strip().casefold()
        new_category = new_category.strip()
        result = await self.session.execute(
            select(Product).where(func.lower(Product.category) == old_clean)
        )
        products = list(result.scalars().all())
        if not products or not new_category:
            return 0, "not_found"

        if new_category.casefold() != old_clean:
            result = await self.session.execute(
                select(Product.id).where(func.lower(Product.category) == new_category.casefold()).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                return 0, "already_exists"

        for product in products:
            product.category = new_category
        await self.session.commit()
        return len(products), products[0].category

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
                ("PUBG UC Top Up", "60 UC"),
                ("PUBG UC Top Up", "325 UC"),
                ("PUBG UC Top Up", "660 UC"),
                ("PUBG UC Top Up", "1800 UC"),
                ("PUBG UC Top Up", "3850 UC"),
                ("PUBG UC Top Up", "8100 UC"),
            ]
            for cat, name in defaults:
                self.session.add(Product(category=cat, name=name))
            await self.session.commit()
