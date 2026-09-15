"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/repository.py

2026-09-15 전면 감사 후속(Phase 9G, 10-4) — 저장소 계층.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.domains.product_attribute_match.model import ProductAttributeComparisonItem
from app.domains.product_attribute_match.model import ProductAttributeComparisonRun


class ProductAttributeMatchRepository:

    def __init__(self, db: Session):

        self.db = db

    def add_run(
        self, run: ProductAttributeComparisonRun,
    ) -> ProductAttributeComparisonRun:

        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(
        self, run_id: int, company_id: int,
    ) -> ProductAttributeComparisonRun | None:

        return (
            self.db.query(ProductAttributeComparisonRun)
            .options(joinedload(ProductAttributeComparisonRun.items))
            .filter(
                ProductAttributeComparisonRun.id == run_id,
                ProductAttributeComparisonRun.company_id == company_id,
            )
            .first()
        )

    def get_latest_run_for_product(
        self, company_id: int, product_identifier: str,
    ) -> ProductAttributeComparisonRun | None:

        return (
            self.db.query(ProductAttributeComparisonRun)
            .filter(
                ProductAttributeComparisonRun.company_id == company_id,
                ProductAttributeComparisonRun.product_identifier
                == product_identifier,
            )
            .order_by(ProductAttributeComparisonRun.created_at.desc())
            .first()
        )

    def list_runs(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ProductAttributeComparisonRun]:

        query = self.db.query(ProductAttributeComparisonRun).filter(
            ProductAttributeComparisonRun.company_id == company_id,
        )
        if status is not None:
            query = query.filter(
                ProductAttributeComparisonRun.overall_status == status,
            )
        return query.order_by(
            ProductAttributeComparisonRun.created_at.desc(),
        ).all()

    def get_item(
        self, item_id: int, run_id: int,
    ) -> ProductAttributeComparisonItem | None:

        return (
            self.db.query(ProductAttributeComparisonItem)
            .filter(
                ProductAttributeComparisonItem.id == item_id,
                ProductAttributeComparisonItem.run_id == run_id,
            )
            .first()
        )


__all__ = ["ProductAttributeMatchRepository"]
