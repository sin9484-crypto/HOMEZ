"""
=========================================================
Homez OS

File : app/domains/currency/service.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 6번) — 환율 기록
(수동 입력만)·조회·변환, 허용률 판정.

**절대 하지 않는 것**: 실제 외부 환율 API 호출. `record_rate()`는
호출자가 이미 확인한 숫자를 그대로 저장할 뿐이다 — 이 서비스
어디에서도 `requests`/`httpx`/`urllib`을 쓰지 않는다.

이 서비스는 "환율이 기준값 대비 허용률을 넘었는지"만 판정한다
(`check_rate_within_tolerance`) — Phase 4의 가격 인상 감지와 같은
종류의 판정이다. **다만 Phase 4에서 발견한 것과 동일한 한계가
그대로 있다**: 이 판정이 실제로 발동하려면 "후보 선정 시점의
환율"을 기록해 둔 기준값이 필요한데, 그 기준값을 실제로 채우는
호출부(purchase_task 후보 평가 흐름 어딘가)는 이번 Phase에서 만들지
않았다 — Phase 4가 `PurchaseTaskPolicyCheckInput.expected_amount_
at_creation`에 대해 이미 정직하게 기록한 것과 동일한 한계를 이번에도
그대로 남긴다(정직하게 문서화, docs/HOMEZ_PROJECT_STATE.md Phase 9
절 참고).
=========================================================
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.currency.constants import DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT
from app.domains.currency.model import ExchangeRate
from app.domains.currency.model import ExchangeRateToleranceSetting
from app.domains.currency.repository import CurrencyRepository


class ExchangeRateNotFoundError(Exception):
    pass


class ExchangeRateService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = CurrencyRepository(db)

    # ------------------------------
    # 환율 기록·조회·변환
    # ------------------------------

    def record_rate(
        self,
        *,
        base_currency: str,
        quote_currency: str,
        rate: float,
        source: str = "MANUAL_ADMIN_ENTRY",
        recorded_by: int | None = None,
    ) -> ExchangeRate:

        if rate <= 0:
            raise BadRequestException("환율은 0보다 커야 합니다.")

        if not base_currency or not quote_currency:
            raise BadRequestException("통화 코드가 필요합니다.")

        if base_currency == quote_currency:
            raise BadRequestException(
                "기준 통화와 대상 통화는 서로 달라야 합니다.",
            )

        record = ExchangeRate(
            base_currency=base_currency.upper(),
            quote_currency=quote_currency.upper(),
            rate=rate,
            source=source,
            recorded_by=recorded_by,
        )

        return self.repository.add_rate(record)

    def get_latest_rate(
        self, base_currency: str, quote_currency: str,
    ) -> ExchangeRate | None:

        return self.repository.get_latest_rate(
            base_currency.upper(), quote_currency.upper(),
        )

    def convert(
        self, amount: Decimal, base_currency: str, quote_currency: str,
    ) -> Decimal:
        """
        base_currency 금액을 quote_currency로 환산한다. 환율을 한
        번도 기록한 적이 없으면 절대 추측하지 않고 예외를 던진다
        (fail-closed — "환율 미확인"을 1.0 같은 임의값으로 대체하지
        않는다).
        """

        if base_currency.upper() == quote_currency.upper():
            return amount

        rate = self.get_latest_rate(base_currency, quote_currency)

        if rate is None:
            raise ExchangeRateNotFoundError(
                f"{base_currency}->{quote_currency} 환율이 기록된 적이 "
                "없습니다 — 추측으로 대체하지 않습니다.",
            )

        return amount * Decimal(str(rate.rate))

    # ------------------------------
    # 허용률
    # ------------------------------

    def get_tolerance_percent(self, company_id: int) -> float:

        setting = self.repository.get_latest_tolerance(company_id)

        if setting is None:
            return DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT

        return setting.tolerance_percent

    def set_tolerance_percent(
        self,
        *,
        company_id: int,
        user_id: int,
        is_admin: bool,
        tolerance_percent: float,
    ) -> ExchangeRateToleranceSetting:

        if not is_admin:
            raise ForbiddenException(
                "환율 허용률 변경은 관리자만 가능합니다.",
            )

        if tolerance_percent <= 0:
            raise BadRequestException("허용률은 0보다 커야 합니다.")

        setting = ExchangeRateToleranceSetting(
            company_id=company_id,
            tolerance_percent=tolerance_percent,
            set_by=user_id,
        )

        return self.repository.add_tolerance(setting)

    # ------------------------------
    # 허용률 판정 — Phase 4 가격 인상 감지와 동일한 성격의 판정.
    # ------------------------------

    def check_rate_within_tolerance(
        self,
        company_id: int,
        rate_at_baseline: float,
        current_rate: float,
    ) -> tuple[bool, str | None]:
        """
        `rate_at_baseline`(후보 선정/발주 시점에 기록해 둔 환율)과
        `current_rate`(지금 환율)의 변동률이 회사의 허용률을
        넘는지만 판정한다 — 실행(발주 중지 등)은 호출자 책임이다.

        `rate_at_baseline`을 실제로 어디서 기록해 채울지는 이번
        Phase에서 연결하지 않았다(모듈 docstring 참고) — 이 함수는
        "그 값이 있다면" 정확하게 판정한다.
        """

        if rate_at_baseline <= 0:
            raise BadRequestException("기준 환율은 0보다 커야 합니다.")

        tolerance = self.get_tolerance_percent(company_id)
        change_percent = (
            abs(current_rate - rate_at_baseline) / rate_at_baseline * 100
        )

        if change_percent > tolerance:
            return False, (
                f"환율이 기준({rate_at_baseline:,.2f}) 대비 "
                f"{change_percent:.2f}% 변동해 허용률({tolerance:.1f}%)을 "
                "초과했습니다."
            )

        return True, None


__all__ = ["ExchangeRateService", "ExchangeRateNotFoundError"]
