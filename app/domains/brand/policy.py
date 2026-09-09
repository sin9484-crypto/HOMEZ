"""
=========================================================
Homez OS

Brand Domain
policy.py
=========================================================
"""

from __future__ import annotations

from app.domains.brand.model import (
    Brand,
)


class BrandPolicy:
    """
    Brand Domain Policy

    브랜드 등록, 노출,
    검증 및 관리 규칙
    """


    @staticmethod
    def can_view(
        brand: Brand,
    ) -> bool:

        return (
            brand.is_active
            and brand.deleted_at is None
        )


    @staticmethod
    def can_sell_product(
        brand: Brand,
    ) -> bool:

        return (
            brand.is_active
            and brand.is_verified
            and brand.deleted_at is None
        )


    @staticmethod
    def can_update(
        brand: Brand,
    ) -> bool:

        return (
            brand.deleted_at is None
        )
    @staticmethod
    def can_delete(
        brand: Brand,
    ) -> bool:

        return (
            brand.product_count == 0
            and brand.deleted_at is None
        )


    @staticmethod
    def can_verify(
        brand: Brand,
    ) -> bool:

        return (
            brand.is_active
            and not brand.is_verified
        )


    @staticmethod
    def can_set_official(
        brand: Brand,
    ) -> bool:

        return (
            brand.is_verified
            and brand.is_active
        )


    @staticmethod
    def can_recommend(
        brand: Brand,
    ) -> bool:

        return (
            brand.is_active
            and brand.ai_score is not None
            and brand.deleted_at is None
        )
    @staticmethod
    def validate_name(
        name: str,
    ) -> bool:

        return (
            len(name.strip()) >= 2
        )


    @staticmethod
    def validate_rating(
        rating: float,
    ) -> bool:

        return (
            0 <= rating <= 5
        )


    @staticmethod
    def validate_product_count(
        count: int,
    ) -> bool:

        return (
            count >= 0
        )


    @staticmethod
    def can_modify_brand_status(
        brand: Brand,
    ) -> bool:

        return (
            brand.deleted_at is None
        )


__all__ = [
    "BrandPolicy",
]       