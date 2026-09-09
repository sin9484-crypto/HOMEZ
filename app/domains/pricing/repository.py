"""
=========================================================
Homez OS

File : app/domains/pricing/repository.py

Pricing & Margin Reconciliation Repository — V7 Gate 5(2026-08-15).

모든 조회는 company_id 스코프를 반드시 포함한다(이 저장소 전역
테넌트 격리 관례 — Gate 1/2/3/4에서 반복 발견된 크로스테넌트 결함
클래스를 처음부터 차단). 상태 전이는 조건부 UPDATE + rowcount
반환으로만 수행한다(Settlement Fixed Rules와 동일 원칙).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import PriceChangeStatusEvent
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation


class PricingRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # ProductPricing
    # --------------------------------------------------

    def get_pricing_by_id(
        self, pricing_id: int, company_id: int,
    ) -> ProductPricing | None:

        return (
            self.db.query(ProductPricing)
            .filter(ProductPricing.id == pricing_id)
            .filter(ProductPricing.company_id == company_id)
            .first()
        )

    def get_pricing_by_listing(
        self, listing_id: int, company_id: int,
    ) -> ProductPricing | None:

        return (
            self.db.query(ProductPricing)
            .filter(ProductPricing.listing_id == listing_id)
            .filter(ProductPricing.company_id == company_id)
            .first()
        )

    def list_pricings_for_company(
        self, company_id: int, skip: int = 0, limit: int = 100,
    ) -> list[ProductPricing]:

        return (
            self.db.query(ProductPricing)
            .filter(ProductPricing.company_id == company_id)
            .order_by(ProductPricing.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_pricing_no_commit(
        self, pricing: ProductPricing,
    ) -> ProductPricing:

        self.db.add(pricing)
        self.db.flush()

        return pricing

    def save_pricing_no_commit(
        self, pricing: ProductPricing,
    ) -> ProductPricing:

        self.db.add(pricing)
        self.db.flush()

        return pricing

    def update_pricing_conditional(
        self,
        pricing_id: int,
        company_id: int,
        expected_version: int,
        values: dict,
    ) -> int:
        """
        읽은 시점 버전(expected_version)이 여전히 유효할 때만 조건부
        UPDATE — version을 함께 +1 올린다. rowcount!=1이면 그 사이
        다른 요청이 먼저 갱신한 것이다(호출자가 ConflictException 처리).
        원가 갱신/가격변경 요청 연결/승인 시 가격 반영 등 ProductPricing
        의 모든 조건부 갱신 경로가 이 메서드 하나를 공유한다.
        """

        result = self.db.execute(
            update(ProductPricing)
            .where(ProductPricing.id == pricing_id)
            .where(ProductPricing.company_id == company_id)
            .where(ProductPricing.version == expected_version)
            .values(version=expected_version + 1, **values)
        )

        return result.rowcount

    # --------------------------------------------------
    # PriceChangeRequest
    # --------------------------------------------------

    def get_price_change_by_id(
        self, request_id: int, company_id: int,
    ) -> PriceChangeRequest | None:

        return (
            self.db.query(PriceChangeRequest)
            .filter(PriceChangeRequest.id == request_id)
            .filter(PriceChangeRequest.company_id == company_id)
            .first()
        )

    def get_price_change_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> PriceChangeRequest | None:

        return (
            self.db.query(PriceChangeRequest)
            .filter(PriceChangeRequest.company_id == company_id)
            .filter(
                PriceChangeRequest.idempotency_key == idempotency_key,
            )
            .first()
        )

    def list_price_changes_for_listing(
        self, listing_id: int, company_id: int,
        skip: int = 0, limit: int = 100,
    ) -> list[PriceChangeRequest]:

        return (
            self.db.query(PriceChangeRequest)
            .filter(PriceChangeRequest.listing_id == listing_id)
            .filter(PriceChangeRequest.company_id == company_id)
            .order_by(PriceChangeRequest.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_price_change_no_commit(
        self, request: PriceChangeRequest,
    ) -> PriceChangeRequest:

        self.db.add(request)
        self.db.flush()

        return request

    def decide_price_change_conditional(
        self,
        request_id: int,
        company_id: int,
        new_status: str,
        decided_by: int,
        decided_at: datetime,
        decision_reason: str | None,
    ) -> int:
        """PENDING → APPROVED/REJECTED/CANCELLED 조건부 UPDATE."""

        result = self.db.execute(
            update(PriceChangeRequest)
            .where(PriceChangeRequest.id == request_id)
            .where(PriceChangeRequest.company_id == company_id)
            .where(PriceChangeRequest.status == "PENDING")
            .values(
                status=new_status,
                decided_by=decided_by,
                decided_at=decided_at,
                decision_reason=decision_reason,
            )
        )

        return result.rowcount

    def add_status_event_no_commit(
        self, event: PriceChangeStatusEvent,
    ) -> PriceChangeStatusEvent:

        self.db.add(event)
        self.db.flush()

        return event

    def list_status_events_for_request(
        self, price_change_request_id: int, company_id: int,
    ) -> list[PriceChangeStatusEvent]:

        return (
            self.db.query(PriceChangeStatusEvent)
            .filter(
                PriceChangeStatusEvent.price_change_request_id
                == price_change_request_id,
            )
            .filter(PriceChangeStatusEvent.company_id == company_id)
            .order_by(PriceChangeStatusEvent.id.asc())
            .all()
        )

    # --------------------------------------------------
    # MarginSnapshot
    # --------------------------------------------------

    def add_margin_snapshot_no_commit(
        self, snapshot: MarginSnapshot,
    ) -> MarginSnapshot:

        self.db.add(snapshot)
        self.db.flush()

        return snapshot

    def get_latest_snapshot(
        self,
        listing_id: int,
        company_id: int,
        margin_type: str,
        order_id: int | None = None,
    ) -> MarginSnapshot | None:

        query = (
            self.db.query(MarginSnapshot)
            .filter(MarginSnapshot.listing_id == listing_id)
            .filter(MarginSnapshot.company_id == company_id)
            .filter(MarginSnapshot.margin_type == margin_type)
        )

        if order_id is not None:
            query = query.filter(MarginSnapshot.order_id == order_id)

        return query.order_by(MarginSnapshot.id.desc()).first()

    def list_snapshots_for_listing(
        self, listing_id: int, company_id: int,
        margin_type: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[MarginSnapshot]:

        query = (
            self.db.query(MarginSnapshot)
            .filter(MarginSnapshot.listing_id == listing_id)
            .filter(MarginSnapshot.company_id == company_id)
        )

        if margin_type is not None:
            query = query.filter(
                MarginSnapshot.margin_type == margin_type,
            )

        return (
            query.order_by(MarginSnapshot.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    # --------------------------------------------------
    # SettlementReconciliation
    # --------------------------------------------------

    def get_reconciliation_by_order(
        self, order_id: int, company_id: int,
    ) -> SettlementReconciliation | None:

        return (
            self.db.query(SettlementReconciliation)
            .filter(SettlementReconciliation.order_id == order_id)
            .filter(SettlementReconciliation.company_id == company_id)
            .first()
        )

    def add_reconciliation_no_commit(
        self, reconciliation: SettlementReconciliation,
    ) -> SettlementReconciliation:

        self.db.add(reconciliation)
        self.db.flush()

        return reconciliation

    def update_reconciliation_conditional(
        self,
        reconciliation_id: int,
        company_id: int,
        values: dict,
    ) -> int:

        result = self.db.execute(
            update(SettlementReconciliation)
            .where(SettlementReconciliation.id == reconciliation_id)
            .where(SettlementReconciliation.company_id == company_id)
            .values(**values)
        )

        return result.rowcount

    def list_reconciliations_for_company(
        self, company_id: int,
        status: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[SettlementReconciliation]:

        query = self.db.query(SettlementReconciliation).filter(
            SettlementReconciliation.company_id == company_id,
        )

        if status is not None:
            query = query.filter(
                SettlementReconciliation.status == status,
            )

        return (
            query.order_by(SettlementReconciliation.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )


__all__ = [
    "PricingRepository",
]
