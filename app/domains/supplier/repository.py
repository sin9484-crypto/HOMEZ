"""
=========================================================
Homez OS

File : app/domains/supplier/repository.py

Supplier Repository
=========================================================
"""

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.base_repository import BaseRepository

from app.domains.supplier.model import (
    Supplier,
)


class SupplierRepository(
    BaseRepository[Supplier]
):

    def __init__(
        self,
        db: Session,
    ):

        super().__init__(
            db,
            Supplier,
        )


    def get_by_id(
        self,
        supplier_id: int,
    ) -> Supplier | None:

        return self.db.scalar(
            select(Supplier)
            .where(
                Supplier.id == supplier_id
            )
        )


    def get_active_suppliers(
        self,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.is_active.is_(True)
                )
            )
        )


    def get_by_code(
        self,
        code: str,
    ) -> Supplier | None:

        return self.db.scalar(
            select(Supplier)
            .where(
                Supplier.code == code
            )
        )
    def get_by_name(
        self,
        name: str,
    ) -> Supplier | None:

        return self.db.scalar(
            select(Supplier)
            .where(
                Supplier.name == name
            )
        )


    def get_verified_suppliers(
        self,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.is_verified.is_(True)
                )
                .where(
                    Supplier.is_active.is_(True)
                )
            )
        )


    def get_by_type(
        self,
        supplier_type: str,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.supplier_type == supplier_type
                )
            )
        )


    def search(
        self,
        keyword: str,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.name.contains(
                        keyword
                    )
                )
            )
        )
    def get_high_trust_suppliers(
        self,
        limit: int = 20,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.is_active.is_(True)
                )
                .order_by(
                    Supplier.trust_score.desc()
                )
                .limit(limit)
            )
        )


    def get_best_delivery_suppliers(
        self,
        limit: int = 20,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.is_active.is_(True)
                )
                .order_by(
                    Supplier.delivery_score.desc()
                )
                .limit(limit)
            )
        )


    def get_ai_recommended_suppliers(
        self,
        limit: int = 20,
    ) -> list[Supplier]:

        return list(
            self.db.scalars(
                select(Supplier)
                .where(
                    Supplier.is_active.is_(True)
                )
                .order_by(
                    Supplier.ai_score.desc()
                )
                .limit(limit)
            )
        )
    def increment_order_count(
        self,
        supplier: Supplier,
    ) -> Supplier:

        supplier.order_count += 1

        self.db.commit()
        self.db.refresh(
            supplier,
        )

        return supplier


    def increment_success_count(
        self,
        supplier: Supplier,
    ) -> Supplier:

        supplier.success_count += 1

        self.db.commit()
        self.db.refresh(
            supplier,
        )

        return supplier


    def deactivate(
        self,
        supplier: Supplier,
    ) -> Supplier:

        supplier.is_active = False

        self.db.commit()
        self.db.refresh(
            supplier,
        )

        return supplier


    def delete(
        self,
        supplier: Supplier,
    ) -> None:

        self.db.delete(
            supplier,
        )

        self.db.commit()


__all__ = [
    "SupplierRepository",
]