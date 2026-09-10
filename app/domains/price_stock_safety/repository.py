"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/repository.py

2026-09-10 Phase 10 — VirtualStockThreshold/PriceReviewCycleSetting
저장소 계층. 둘 다 append-only라 "가장 최근 행 조회"만 있으면
충분하다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.price_stock_safety.model import PriceReviewCycleSetting
from app.domains.price_stock_safety.model import VirtualStockThreshold


class PriceStockSafetyRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------
    # VirtualStockThreshold
    # ------------------------------

    def add_virtual_stock_threshold(
        self, record: VirtualStockThreshold,
    ) -> VirtualStockThreshold:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def get_latest_virtual_stock_threshold(
        self, company_id: int,
    ) -> VirtualStockThreshold | None:

        return (
            self.db.query(VirtualStockThreshold)
            .filter(VirtualStockThreshold.company_id == company_id)
            .order_by(VirtualStockThreshold.id.desc())
            .first()
        )

    # ------------------------------
    # PriceReviewCycleSetting
    # ------------------------------

    def add_review_cycle_setting(
        self, record: PriceReviewCycleSetting,
    ) -> PriceReviewCycleSetting:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def get_latest_review_cycle_setting(
        self, company_id: int,
    ) -> PriceReviewCycleSetting | None:

        return (
            self.db.query(PriceReviewCycleSetting)
            .filter(PriceReviewCycleSetting.company_id == company_id)
            .order_by(PriceReviewCycleSetting.id.desc())
            .first()
        )


__all__ = ["PriceStockSafetyRepository"]
