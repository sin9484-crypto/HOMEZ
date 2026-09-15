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

from app.domains.price_stock_safety.model import PriceCacheTtlSetting
from app.domains.price_stock_safety.model import PriceReviewCycleSetting
from app.domains.price_stock_safety.model import PriceStockQuoteCache
from app.domains.price_stock_safety.model import StockCacheTtlSetting
from app.domains.price_stock_safety.model import VirtualStockThreshold
from app.domains.price_stock_safety.model import VirtualStockZeroProposal
from app.domains.price_stock_safety.model import VirtualStockZeroProposalStatus


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

    # ------------------------------
    # PriceCacheTtlSetting / StockCacheTtlSetting (Phase 9D/9E)
    # ------------------------------

    def add_price_cache_ttl_setting(
        self, record: PriceCacheTtlSetting,
    ) -> PriceCacheTtlSetting:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def get_latest_price_cache_ttl_setting(
        self, company_id: int,
    ) -> PriceCacheTtlSetting | None:

        return (
            self.db.query(PriceCacheTtlSetting)
            .filter(PriceCacheTtlSetting.company_id == company_id)
            .order_by(PriceCacheTtlSetting.id.desc())
            .first()
        )

    def add_stock_cache_ttl_setting(
        self, record: StockCacheTtlSetting,
    ) -> StockCacheTtlSetting:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def get_latest_stock_cache_ttl_setting(
        self, company_id: int,
    ) -> StockCacheTtlSetting | None:

        return (
            self.db.query(StockCacheTtlSetting)
            .filter(StockCacheTtlSetting.company_id == company_id)
            .order_by(StockCacheTtlSetting.id.desc())
            .first()
        )

    # ------------------------------
    # PriceStockQuoteCache (Phase 9A)
    # ------------------------------

    def get_quote_cache(
        self, company_id: int, connection_id: int, product_code: str,
        option_id: str,
    ) -> PriceStockQuoteCache | None:

        return (
            self.db.query(PriceStockQuoteCache)
            .filter(
                PriceStockQuoteCache.company_id == company_id,
                PriceStockQuoteCache.connection_id == connection_id,
                PriceStockQuoteCache.product_code == product_code,
                PriceStockQuoteCache.option_id == option_id,
            )
            .first()
        )

    def upsert_quote_cache(
        self, *, company_id: int, connection_id: int, product_code: str,
        option_id: str, price_amount: float | None = None,
        price_confirmed_at=None, price_expires_at=None,
        in_stock: bool | None = None, stock_confirmed_at=None,
        stock_expires_at=None,
    ) -> PriceStockQuoteCache:
        """호출부가 넘긴 값만 갱신한다 — price_*와 stock_*는 각각
        독립적으로 갱신될 수 있다(가격만 새로 조회했다면 재고 쪽
        기존 값은 그대로 둔다)."""

        record = self.get_quote_cache(
            company_id, connection_id, product_code, option_id,
        )
        if record is None:
            record = PriceStockQuoteCache(
                company_id=company_id, connection_id=connection_id,
                product_code=product_code, option_id=option_id,
            )
            self.db.add(record)

        if price_amount is not None:
            record.price_amount = price_amount
            record.price_confirmed_at = price_confirmed_at
            record.price_expires_at = price_expires_at
        if in_stock is not None:
            record.in_stock = in_stock
            record.stock_confirmed_at = stock_confirmed_at
            record.stock_expires_at = stock_expires_at

        self.db.commit()
        self.db.refresh(record)

        return record

    # ------------------------------
    # VirtualStockZeroProposal (Phase 9F)
    # ------------------------------

    def add_zero_stock_proposal(
        self, record: VirtualStockZeroProposal,
    ) -> VirtualStockZeroProposal:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def get_pending_zero_stock_proposal(
        self, company_id: int, connection_id: int, product_code: str,
    ) -> VirtualStockZeroProposal | None:

        return (
            self.db.query(VirtualStockZeroProposal)
            .filter(
                VirtualStockZeroProposal.company_id == company_id,
                VirtualStockZeroProposal.connection_id == connection_id,
                VirtualStockZeroProposal.product_code == product_code,
                VirtualStockZeroProposal.status
                == VirtualStockZeroProposalStatus.PENDING,
            )
            .first()
        )

    def has_pending_zero_stock_proposal(
        self, company_id: int, product_code: str,
    ) -> bool:
        """연결계정과 무관하게 이 상품에 대한 PENDING 제안이 하나라도
        있으면 True — 자동발주 차단 판정은 연결 단위가 아니라 상품
        단위여야 한다(같은 상품을 다른 연결로 다시 시도해도 여전히
        같은 판매불가 위험이다)."""

        return (
            self.db.query(VirtualStockZeroProposal)
            .filter(
                VirtualStockZeroProposal.company_id == company_id,
                VirtualStockZeroProposal.product_code == product_code,
                VirtualStockZeroProposal.status
                == VirtualStockZeroProposalStatus.PENDING,
            )
            .first()
        ) is not None

    def get_zero_stock_proposal(
        self, proposal_id: int, company_id: int,
    ) -> VirtualStockZeroProposal | None:

        return (
            self.db.query(VirtualStockZeroProposal)
            .filter(
                VirtualStockZeroProposal.id == proposal_id,
                VirtualStockZeroProposal.company_id == company_id,
            )
            .first()
        )

    def list_zero_stock_proposals(
        self, company_id: int, *, status: str | None = None,
    ) -> list[VirtualStockZeroProposal]:

        query = self.db.query(VirtualStockZeroProposal).filter(
            VirtualStockZeroProposal.company_id == company_id,
        )
        if status is not None:
            query = query.filter(VirtualStockZeroProposal.status == status)
        return query.order_by(VirtualStockZeroProposal.id.desc()).all()


__all__ = ["PriceStockSafetyRepository"]
