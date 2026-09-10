"""
=========================================================
Homez OS

File : app/domains/payment/repository.py

2026-09-10 Phase 7 — PaymentMethod/PaymentAutoLimit 저장소 계층.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.payment.model import PaymentAutoLimit
from app.domains.payment.model import PaymentMethod


class PaymentRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------
    # PaymentMethod
    # ------------------------------

    def create_method(self, method: PaymentMethod) -> PaymentMethod:

        self.db.add(method)
        self.db.commit()
        self.db.refresh(method)

        return method

    def get_method(
        self, company_id: int, method_id: int,
    ) -> PaymentMethod | None:

        return (
            self.db.query(PaymentMethod)
            .filter(
                PaymentMethod.id == method_id,
                PaymentMethod.company_id == company_id,
            )
            .first()
        )

    def list_methods(
        self, company_id: int, *, include_inactive: bool = False,
    ) -> list[PaymentMethod]:

        query = self.db.query(PaymentMethod).filter(
            PaymentMethod.company_id == company_id,
        )

        if not include_inactive:
            query = query.filter(PaymentMethod.active.is_(True))

        return query.order_by(PaymentMethod.created_at.desc()).all()

    def clear_default_for_company(self, company_id: int) -> None:
        """set_default_method()가 새 기본 수단을 지정하기 직전에
        호출한다 — 회사당 기본 수단은 항상 최대 1개다."""

        self.db.query(PaymentMethod).filter(
            PaymentMethod.company_id == company_id,
            PaymentMethod.is_default.is_(True),
        ).update({PaymentMethod.is_default: False})
        self.db.commit()

    # ------------------------------
    # PaymentAutoLimit (append-only — 최신 행이 현재 값)
    # ------------------------------

    def add_limit(self, limit: PaymentAutoLimit) -> PaymentAutoLimit:

        self.db.add(limit)
        self.db.commit()
        self.db.refresh(limit)

        return limit

    def get_latest_limit(
        self, company_id: int,
    ) -> PaymentAutoLimit | None:

        return (
            self.db.query(PaymentAutoLimit)
            .filter(PaymentAutoLimit.company_id == company_id)
            .order_by(PaymentAutoLimit.id.desc())
            .first()
        )


__all__ = ["PaymentRepository"]
