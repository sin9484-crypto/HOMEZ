"""
=========================================================
Homez OS

Brand Domain
repository.py
=========================================================
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base_repository import (
    BaseRepository,
)

from app.domains.brand.model import (
    Brand,
)


class BrandRepository(
    BaseRepository[Brand]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(
            db,
            Brand,
        )


    def get_by_id(
        self,
        brand_id: int,
    ) -> Brand | None:

        return self.db.scalar(
            select(Brand).where(
                Brand.id == brand_id
            )
        )


    def get_active_brands(
        self,
    ) -> list[Brand]:

        return list(
            self.db.scalars(
                select(Brand).where(
                    Brand.is_active.is_(True)
                )
            )
        )


    def get_by_slug(
        self,
        slug: str,
    ) -> Brand | None:

        return self.db.scalar(
            select(Brand).where(
                Brand.slug == slug
            )
        )
    def get_by_name(
        self,
        name: str,
    ) -> Brand | None:

        return self.db.scalar(
            select(Brand).where(
                Brand.name == name
            )
        )


    def get_verified_brands(
        self,
    ) -> list[Brand]:

        return list(
            self.db.scalars(
                select(Brand)
                .where(
                    Brand.is_verified.is_(True)
                )
                .where(
                    Brand.is_active.is_(True)
                )
            )
        )


    def get_official_brands(
        self,
    ) -> list[Brand]:

        return list(
            self.db.scalars(
                select(Brand)
                .where(
                    Brand.is_official.is_(True)
                )
                .where(
                    Brand.is_active.is_(True)
                )
            )
        )


    def search(
        self,
        keyword: str,
    ) -> list[Brand]:

        return list(
            self.db.scalars(
                select(Brand).where(
                    Brand.name.contains(
                        keyword
                    )
                )
            )
        )
    def get_popular_brands(
        self,
        limit: int = 20,
    ) -> list[Brand]:

        return list(
            self.db.scalars(
                select(Brand)
                .where(
                    Brand.is_active.is_(True)
                )
                .order_by(
                    Brand.product_count.desc()
                )
                .limit(limit)
            )
        )


    def get_ai_recommended_brands(
        self,
        limit: int = 20,
    ) -> list[Brand]:

        return list(
            self.db.scalars(
                select(Brand)
                .where(
                    Brand.is_active.is_(True)
                )
                .order_by(
                    Brand.ai_score.desc()
                )
                .limit(limit)
            )
        )


    def increment_product_count(
        self,
        brand: Brand,
    ) -> Brand:

        brand.product_count += 1

        self.db.commit()
        self.db.refresh(
            brand,
        )

        return brand
    def decrement_product_count(
        self,
        brand: Brand,
    ) -> Brand:

        if brand.product_count > 0:
            brand.product_count -= 1

        self.db.commit()
        self.db.refresh(
            brand,
        )

        return brand


    def deactivate(
        self,
        brand: Brand,
    ) -> Brand:

        brand.is_active = False

        self.db.commit()
        self.db.refresh(
            brand,
        )

        return brand


    def delete(
        self,
        brand: Brand,
    ) -> None:

        self.db.delete(
            brand,
        )

        self.db.commit()


__all__ = [
    "BrandRepository",
]   