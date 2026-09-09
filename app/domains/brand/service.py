"""
=========================================================
Homez OS

Brand Domain
service.py
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_service import (
    BaseService,
)

from app.domains.brand.model import (
    Brand,
)

from app.domains.brand.repository import (
    BrandRepository,
)


class BrandService(
    BaseService[Brand]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        self.repository = BrandRepository(
            db,
        )


    def get(
        self,
        brand_id: int,
    ) -> Brand | None:

        return self.repository.get_by_id(
            brand_id,
        )


    def list_active(
        self,
    ) -> list[Brand]:

        return self.repository.get_active_brands()


    def get_by_slug(
        self,
        slug: str,
    ) -> Brand | None:

        return self.repository.get_by_slug(
            slug,
        )


    def get_by_name(
        self,
        name: str,
    ) -> Brand | None:

        return self.repository.get_by_name(
            name,
        )
    def get_verified_brands(
        self,
    ) -> list[Brand]:

        return self.repository.get_verified_brands()


    def get_official_brands(
        self,
    ) -> list[Brand]:

        return self.repository.get_official_brands()


    def search(
        self,
        keyword: str,
    ) -> list[Brand]:

        return self.repository.search(
            keyword,
        )


    def get_popular_brands(
        self,
        limit: int = 20,
    ) -> list[Brand]:

        return self.repository.get_popular_brands(
            limit,
        )


    def get_ai_recommended_brands(
        self,
        limit: int = 20,
    ) -> list[Brand]:

        return self.repository.get_ai_recommended_brands(
            limit,
        )
    def create(
        self,
        brand: Brand,
    ) -> Brand:

        return self.repository.create(
            brand,
        )


    def update(
        self,
        brand: Brand,
    ) -> Brand:

        return self.repository.update(
            brand,
        )


    def increment_product_count(
        self,
        brand: Brand,
    ) -> Brand:

        return self.repository.increment_product_count(
            brand,
        )


    def decrement_product_count(
        self,
        brand: Brand,
    ) -> Brand:

        return self.repository.decrement_product_count(
            brand,
        )
    def deactivate(
        self,
        brand: Brand,
    ) -> Brand:

        return self.repository.deactivate(
            brand,
        )


    def delete(
        self,
        brand: Brand,
    ) -> None:

        self.repository.delete(
            brand,
        )


    def can_use_brand(
        self,
        brand: Brand,
    ) -> bool:

        return (
            brand.is_active
            and brand.deleted_at is None
        )


__all__ = [
    "BrandService",
]        