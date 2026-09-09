from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base_repository import (
    BaseRepository,
)

from app.domains.product.model import (
    Product,
)


class ProductRepository(
    BaseRepository[Product]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(
            db,
            Product,
        )


    def get_by_id(
        self,
        product_id: int,
    ) -> Product | None:

        return self.db.scalar(
            select(Product).where(
                Product.id == product_id
            )
        )


    def get_active_products(
        self,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product).where(
                    Product.is_active.is_(True)
                )
            )
        )


    def get_by_sku(
        self,
        sku: str,
    ) -> Product | None:

        return self.db.scalar(
            select(Product).where(
                Product.sku == sku
            )
        )
    def get_by_category(
        self,
        category_id: int,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product).where(
                    Product.category_id == category_id
                )
            )
        )


    def get_by_brand(
        self,
        brand_id: int,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product).where(
                    Product.brand_id == brand_id
                )
            )
        )


    def get_by_supplier(
        self,
        supplier_id: int,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product).where(
                    Product.supplier_id == supplier_id
                )
            )
        )


    def get_by_status(
        self,
        status: str,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product).where(
                    Product.status == status
                )
            )
        )    
    def search(
        self,
        keyword: str,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product).where(
                    Product.name.contains(
                        keyword
                    )
                )
            )
        )


    def get_best_products(
        self,
        limit: int = 20,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product)
                .where(
                    Product.is_active.is_(True)
                )
                .order_by(
                    Product.sales_count.desc()
                )
                .limit(limit)
            )
        )


    def get_ai_recommended_products(
        self,
        limit: int = 20,
    ) -> list[Product]:

        return list(
            self.db.scalars(
                select(Product)
                .where(
                    Product.is_active.is_(True)
                )
                .order_by(
                    Product.ai_score.desc()
                )
                .limit(limit)
            )
        )
    def update_price(
        self,
        product: Product,
        price: float,
    ) -> Product:

        product.price = price

        self.db.commit()
        self.db.refresh(
            product,
        )

        return product


    def increment_sales(
        self,
        product: Product,
    ) -> Product:

        product.sales_count += 1

        self.db.commit()
        self.db.refresh(
            product,
        )

        return product


    def deactivate(
        self,
        product: Product,
    ) -> Product:

        product.is_active = False
        product.status = "INACTIVE"

        self.db.commit()
        self.db.refresh(
            product,
        )

        return product


__all__ = [
    "ProductRepository",
]            