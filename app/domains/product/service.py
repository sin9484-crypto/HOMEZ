from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_service import (
    BaseService,
)

from app.domains.inventory.model import (
    Inventory,
)

from app.domains.product.model import (
    Product,
)

from app.domains.product.repository import (
    ProductRepository,
)


class ProductService(
    BaseService[Product]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        self.repository = ProductRepository(
            db,
        )


    def get(
        self,
        product_id: int,
    ) -> Product | None:

        return self.repository.get_by_id(
            product_id,
        )


    def list_active(
        self,
    ) -> list[Product]:

        return self.repository.get_active_products()


    def get_by_sku(
        self,
        sku: str,
    ) -> Product | None:

        return self.repository.get_by_sku(
            sku,
        )
    def get_by_category(
        self,
        category_id: int,
    ) -> list[Product]:

        return self.repository.get_by_category(
            category_id,
        )


    def get_by_brand(
        self,
        brand_id: int,
    ) -> list[Product]:

        return self.repository.get_by_brand(
            brand_id,
        )


    def get_by_supplier(
        self,
        supplier_id: int,
    ) -> list[Product]:

        return self.repository.get_by_supplier(
            supplier_id,
        )


    def get_by_status(
        self,
        status: str,
    ) -> list[Product]:

        return self.repository.get_by_status(
            status,
        )


    def search(
        self,
        keyword: str,
    ) -> list[Product]:

        return self.repository.search(
            keyword,
        )
    def get_best_products(
        self,
        limit: int = 20,
    ) -> list[Product]:

        return self.repository.get_best_products(
            limit,
        )


    def get_ai_recommended_products(
        self,
        limit: int = 20,
    ) -> list[Product]:

        return self.repository.get_ai_recommended_products(
            limit,
        )


    def create(
        self,
        product: Product,
    ) -> Product:
        """
        Product와 기본 Inventory를 동일 Session에서 생성한다.

        중간 commit 없이 최종 commit 1회만 수행한다.
        """

        db = self.repository.db

        product.stock_quantity = 0

        db.add(product)
        db.flush()

        inventory = Inventory(
            product_id=product.id,
            supplier_id=product.supplier_id,
            sku=product.sku,
            quantity=0,
            min_quantity=0,
            safety_quantity=0,
            reserved_quantity=0,
            available_quantity=0,
            forecast_quantity=0,
            status="AVAILABLE",
            is_active=True,
        )

        db.add(inventory)
        db.commit()
        db.refresh(product)

        return product


    def update(
        self,
        product: Product,
    ) -> Product:

        return self.repository.update(
            product,
        )
    def update_price(
        self,
        product: Product,
        price: float,
    ) -> Product:

        return self.repository.update_price(
            product,
            price,
        )


    def increment_sales(
        self,
        product: Product,
    ) -> Product:

        return self.repository.increment_sales(
            product,
        )


    def deactivate(
        self,
        product: Product,
    ) -> Product:

        return self.repository.deactivate(
            product,
        )


    def delete(
        self,
        product: Product,
    ) -> None:

        self.repository.delete(
            product,
        )


__all__ = [
    "ProductService",
]            