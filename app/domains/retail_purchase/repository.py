"""
=========================================================
Homez OS

File : app/domains/retail_purchase/repository.py

Gate RP-1(2026-08-22) — RetailPurchaseOrder/PaymentAccountReference/
RetailPurchasePolicySetting DB 접근. 모든 조회는 company_id로
스코프한다 — 클라이언트가 준 company_id를 신뢰하지 않고, 항상
호출자(Service)가 current_user.company_id를 전달한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.retail_purchase.constants import RetailPurchaseOrderStatus
from app.domains.retail_purchase.model import PaymentAccountReference
from app.domains.retail_purchase.model import RetailPurchaseOrder
from app.domains.retail_purchase.model import RetailPurchasePolicySetting


class RetailPurchaseRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # RetailPurchaseOrder
    # --------------------------------------------------

    def add_order(self, order: RetailPurchaseOrder) -> RetailPurchaseOrder:

        self.db.add(order)
        self.db.flush()
        return order

    def get_order(self, order_id: int, company_id: int) -> RetailPurchaseOrder | None:

        return (
            self.db.query(RetailPurchaseOrder)
            .filter(
                RetailPurchaseOrder.id == order_id,
                RetailPurchaseOrder.company_id == company_id,
            )
            .first()
        )

    def get_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> RetailPurchaseOrder | None:

        return (
            self.db.query(RetailPurchaseOrder)
            .filter(
                RetailPurchaseOrder.company_id == company_id,
                RetailPurchaseOrder.idempotency_key == idempotency_key,
            )
            .first()
        )

    def list_orders(
        self, company_id: int, *, status: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[RetailPurchaseOrder]:

        query = self.db.query(RetailPurchaseOrder).filter(
            RetailPurchaseOrder.company_id == company_id,
        )
        if status is not None:
            query = query.filter(RetailPurchaseOrder.status == status)

        return (
            query.order_by(RetailPurchaseOrder.created_at.desc())
            .offset(skip).limit(limit).all()
        )

    def count_open_orders(self, company_id: int) -> int:
        """예산을 점유 중인(BUDGET_HOLDING) 주문 수 — 동시 구매 건수
        한도 판정에 쓴다."""

        return (
            self.db.query(RetailPurchaseOrder)
            .filter(
                RetailPurchaseOrder.company_id == company_id,
                RetailPurchaseOrder.status.in_(
                    RetailPurchaseOrderStatus.BUDGET_HOLDING,
                ),
            )
            .count()
        )

    def sum_ordered_amount_since(
        self, company_id: int, since: datetime,
    ) -> float:
        """`since` 이후 실제로 확정 지출된(ORDERED 이상 진행된) 금액
        합계 — 일일/월간 구매한도 판정에 쓴다. actual_amount가 아직
        없는(예산만 예약된) 건은 포함하지 않는다 — 확정 지출만
        집계한다."""

        rows = (
            self.db.query(RetailPurchaseOrder.actual_amount)
            .filter(
                RetailPurchaseOrder.company_id == company_id,
                RetailPurchaseOrder.created_at >= since,
                RetailPurchaseOrder.actual_amount.is_not(None),
            )
            .all()
        )
        return sum(float(r[0]) for r in rows)

    # --------------------------------------------------
    # PaymentAccountReference
    # --------------------------------------------------

    def get_payment_account(
        self, company_id: int, provider_code: str,
    ) -> PaymentAccountReference | None:

        return (
            self.db.query(PaymentAccountReference)
            .filter(
                PaymentAccountReference.company_id == company_id,
                PaymentAccountReference.provider_code == provider_code,
            )
            .first()
        )

    def list_payment_accounts(
        self, company_id: int,
    ) -> list[PaymentAccountReference]:

        return (
            self.db.query(PaymentAccountReference)
            .filter(PaymentAccountReference.company_id == company_id)
            .all()
        )

    def add_payment_account(
        self, account: PaymentAccountReference,
    ) -> PaymentAccountReference:

        self.db.add(account)
        self.db.flush()
        return account

    # --------------------------------------------------
    # RetailPurchasePolicySetting
    # --------------------------------------------------

    def get_policy_setting(
        self, company_id: int,
    ) -> RetailPurchasePolicySetting | None:

        return (
            self.db.query(RetailPurchasePolicySetting)
            .filter(RetailPurchasePolicySetting.company_id == company_id)
            .first()
        )

    def add_policy_setting(
        self, setting: RetailPurchasePolicySetting,
    ) -> RetailPurchasePolicySetting:

        self.db.add(setting)
        self.db.flush()
        return setting


__all__ = ["RetailPurchaseRepository"]
