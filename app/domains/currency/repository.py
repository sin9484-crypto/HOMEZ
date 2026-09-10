"""
=========================================================
Homez OS

File : app/domains/currency/repository.py

2026-09-10 Phase 9 — ExchangeRate/ExchangeRateToleranceSetting
저장소 계층. 둘 다 append-only라 "가장 최근 행 조회"만 있으면
충분하다 — 조건부 UPDATE가 필요 없다(payment/repository.py의
PaymentAutoLimit과 동일한 이유).
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.currency.model import ExchangeRate
from app.domains.currency.model import ExchangeRateToleranceSetting


class CurrencyRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------
    # ExchangeRate
    # ------------------------------

    def add_rate(self, rate: ExchangeRate) -> ExchangeRate:

        self.db.add(rate)
        self.db.commit()
        self.db.refresh(rate)

        return rate

    def get_latest_rate(
        self, base_currency: str, quote_currency: str,
    ) -> ExchangeRate | None:

        return (
            self.db.query(ExchangeRate)
            .filter(
                ExchangeRate.base_currency == base_currency,
                ExchangeRate.quote_currency == quote_currency,
            )
            .order_by(ExchangeRate.id.desc())
            .first()
        )

    def list_rate_history(
        self, base_currency: str, quote_currency: str, *, limit: int = 50,
    ) -> list[ExchangeRate]:

        return (
            self.db.query(ExchangeRate)
            .filter(
                ExchangeRate.base_currency == base_currency,
                ExchangeRate.quote_currency == quote_currency,
            )
            .order_by(ExchangeRate.id.desc())
            .limit(limit)
            .all()
        )

    # ------------------------------
    # ExchangeRateToleranceSetting
    # ------------------------------

    def add_tolerance(
        self, setting: ExchangeRateToleranceSetting,
    ) -> ExchangeRateToleranceSetting:

        self.db.add(setting)
        self.db.commit()
        self.db.refresh(setting)

        return setting

    def get_latest_tolerance(
        self, company_id: int,
    ) -> ExchangeRateToleranceSetting | None:

        return (
            self.db.query(ExchangeRateToleranceSetting)
            .filter(ExchangeRateToleranceSetting.company_id == company_id)
            .order_by(ExchangeRateToleranceSetting.id.desc())
            .first()
        )


__all__ = ["CurrencyRepository"]
