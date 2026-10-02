import re
from sqlalchemy import delete, exists, select, func, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AppSetting, OrderItem, Product


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

    async def get_inactive_cleanup_counts(self) -> tuple[int, int]:
        """Return (inactive products safe to delete, products referenced by old orders)."""
        used_by_order = exists(
            select(OrderItem.id)
            .where(OrderItem.product_id == Product.id)
            .correlate(Product)
        )
        inactive_result = await self.session.execute(
            select(func.count()).select_from(Product).where(Product.is_active.is_(False))
        )
        inactive_count = inactive_result.scalar_one() or 0
        referenced_result = await self.session.execute(
            select(func.count()).select_from(Product).where(
                Product.is_active.is_(False),
                used_by_order,
            )
        )
        referenced_count = referenced_result.scalar_one() or 0
        return inactive_count - referenced_count, referenced_count

    async def cleanup_inactive_products(self) -> tuple[int, int]:
        """Permanently delete removed products only when no order history uses them."""
        used_by_order = exists(
            select(OrderItem.id)
            .where(OrderItem.product_id == Product.id)
            .correlate(Product)
        )
        result = await self.session.execute(
            delete(Product).where(
                Product.is_active.is_(False),
                ~used_by_order,
            )
        )
        deleted_count = result.rowcount or 0
        if deleted_count:
            await self.session.commit()
        _, preserved_count = await self.get_inactive_cleanup_counts()
        return deleted_count, preserved_count

    async def get_full_reset_counts(self) -> tuple[int, int, int]:
        """Return product rows, linked historical order items, and affected orders."""
        product_count = await self.session.scalar(select(func.count()).select_from(Product))
        item_count = await self.session.scalar(
            select(func.count()).select_from(OrderItem).where(OrderItem.product_id.is_not(None))
        )
        order_count = await self.session.scalar(
            select(func.count(func.distinct(OrderItem.order_id)))
            .select_from(OrderItem)
            .where(OrderItem.product_id.is_not(None))
        )
        return int(product_count or 0), int(item_count or 0), int(order_count or 0)

    async def reset_all_products(self) -> tuple[int, int]:
        """Delete every product, detach historical order items, and reset SQLite IDs."""
        _, linked_items, _ = await self.get_full_reset_counts()
        await self.session.execute(
            update(OrderItem)
            .where(OrderItem.product_id.is_not(None))
            .values(product_id=None)
        )
        result = await self.session.execute(delete(Product))
        deleted_count = result.rowcount or 0

        if self.session.get_bind().dialect.name == "sqlite":
            sequence_table = await self.session.scalar(text(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
            ))
            if sequence_table:
                await self.session.execute(text(
                    "DELETE FROM sqlite_sequence WHERE name = 'products'"
                ))

        seed_marker = await self.session.get(AppSetting, "products_seeded")
        if seed_marker is None:
            self.session.add(AppSetting(key="products_seeded", value="true"))
        else:
            seed_marker.value = "true"

        await self.session.commit()
        return deleted_count, linked_items

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

    async def get_by_id(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def add(self, category: str, name: str, supplier_chat_id: int | None = None) -> Product:
        product = Product(category=category, name=name, supplier_chat_id=supplier_chat_id)
        self.session.add(product)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def add_many(self, category: str, names: list[str]) -> tuple[list[Product], list[str]]:
        """Add package names, inheriting a route only when active packages agree on one."""
        category = category.strip()
        if not category:
            return [], []

        result = await self.session.execute(
            select(Product).where(func.lower(Product.category) == category.casefold())
        )
        existing_products = list(result.scalars().all())
        saved_category = existing_products[0].category if existing_products else category
        active_routes = {product.supplier_chat_id for product in existing_products if product.is_active}
        supplier_chat_id = (
            next(iter(active_routes))
            if len(active_routes) == 1 and None not in active_routes
            else None
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

    async def set_product_supplier(self, product_id: int, supplier_chat_id: int | None) -> Product | None:
        """Set the supplier route for one active package only."""
        product = await self.get_by_id(product_id)
        if product is None or not product.is_active:
            return None
        product.supplier_chat_id = supplier_chat_id
        await self.session.commit()
        return product

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
        seed_marker = await self.session.get(AppSetting, "products_seeded")
        if seed_marker is not None and seed_marker.value == "true":
            return
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
        self.session.add(AppSetting(key="products_seeded", value="true"))
        await self.session.commit()
