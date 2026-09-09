"""
=========================================================
Homez OS

File : app/domains/supplier/service.py

Supplier Service
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.base_service import BaseService

from app.domains.supplier.model import (
    Supplier,
)

from app.domains.supplier.repository import (
    SupplierRepository,
)


class SupplierService(
    BaseService[Supplier]
):

    def __init__(
        self,
        db: Session,
    ):

        self.repository = SupplierRepository(
            db,
        )


    def get(
        self,
        supplier_id: int,
    ) -> Supplier | None:

        return self.repository.get_by_id(
            supplier_id,
        )


    def list_active(
        self,
    ) -> list[Supplier]:

        return self.repository.get_active_suppliers()


    def get_by_code(
        self,
        code: str,
    ) -> Supplier | None:

        return self.repository.get_by_code(
            code,
        )


    def get_by_name(
        self,
        name: str,
    ) -> Supplier | None:

        return self.repository.get_by_name(
            name,
        )
    def get_verified_suppliers(
        self,
    ) -> list[Supplier]:

        return self.repository.get_verified_suppliers()


    def get_by_type(
        self,
        supplier_type: str,
    ) -> list[Supplier]:

        return self.repository.get_by_type(
            supplier_type,
        )


    def search(
        self,
        keyword: str,
    ) -> list[Supplier]:

        return self.repository.search(
            keyword,
        )


    def get_high_trust_suppliers(
        self,
        limit: int = 20,
    ) -> list[Supplier]:

        return self.repository.get_high_trust_suppliers(
            limit,
        )


    def get_best_delivery_suppliers(
        self,
        limit: int = 20,
    ) -> list[Supplier]:

        return self.repository.get_best_delivery_suppliers(
            limit,
        )
    def get_ai_recommended_suppliers(
        self,
        limit: int = 20,
    ) -> list[Supplier]:

        return self.repository.get_ai_recommended_suppliers(
            limit,
        )


    def create(
        self,
        supplier: Supplier,
    ) -> Supplier:

        return self.repository.create(
            supplier,
        )


    def update(
        self,
        supplier: Supplier,
    ) -> Supplier:

        return self.repository.update(
            supplier,
        )


    def increment_order_count(
        self,
        supplier: Supplier,
    ) -> Supplier:

        return self.repository.increment_order_count(
            supplier,
        )


    def increment_success_count(
        self,
        supplier: Supplier,
    ) -> Supplier:

        return self.repository.increment_success_count(
            supplier,
        )
    def calculate_success_rate(
        self,
        supplier: Supplier,
    ) -> float:

        if supplier.order_count == 0:
            return 0

        return (
            supplier.success_count
            /
            supplier.order_count
        ) * 100


    def deactivate(
        self,
        supplier: Supplier,
    ) -> Supplier:

        return self.repository.deactivate(
            supplier,
        )


    def delete(
        self,
        supplier: Supplier,
    ) -> None:

        self.repository.delete(
            supplier,
        )


    def can_use_supplier(
        self,
        supplier: Supplier,
    ) -> bool:

        return (
            supplier.is_active
            and supplier.deleted_at is None
        )


__all__ = [
    "SupplierService",
]        