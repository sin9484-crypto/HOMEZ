"""
=========================================================
Homez OS

File : app/domains/supplier/policy.py

Supplier Policy
=========================================================
"""

from app.domains.supplier.model import (
    Supplier,
)


class SupplierPolicy:
    """
    Supplier Domain Policy

    공급처 등록, 거래,
    평가 및 사용 규칙
    """


    @staticmethod
    def can_view(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.is_active
            and supplier.deleted_at is None
        )


    @staticmethod
    def can_trade(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.is_active
            and supplier.is_verified
            and supplier.deleted_at is None
        )


    @staticmethod
    def can_update(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.deleted_at is None
        )
    @staticmethod
    def can_delete(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.order_count == 0
            and supplier.deleted_at is None
        )


    @staticmethod
    def validate_trust_score(
        score: float,
    ) -> bool:

        return (
            0 <= score <= 100
        )


    @staticmethod
    def validate_delivery_score(
        score: float,
    ) -> bool:

        return (
            0 <= score <= 100
        )


    @staticmethod
    def validate_return_rate(
        rate: float,
    ) -> bool:

        return (
            0 <= rate <= 100
        )


    @staticmethod
    def can_recommend(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.is_active
            and supplier.is_verified
            and supplier.ai_score > 0
            and supplier.deleted_at is None
        )
    @staticmethod
    def can_place_order(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.is_active
            and supplier.is_verified
            and supplier.order_count >= 0
            and supplier.deleted_at is None
        )


    @staticmethod
    def has_api_connection(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.api_url is not None
            and supplier.api_key is not None
        )


    @staticmethod
    def validate_supplier_type(
        supplier_type: str,
    ) -> bool:

        allowed_types = {
            "GENERAL",
            "MANUFACTURER",
            "WHOLESALE",
            "DISTRIBUTOR",
            "OVERSEAS",
        }

        return (
            supplier_type in allowed_types
        )


    @staticmethod
    def can_modify_status(
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.deleted_at is None
        )


__all__ = [
    "SupplierPolicy",
]    