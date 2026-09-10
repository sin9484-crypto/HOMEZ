"""
=========================================================
Homez OS

File : app/domains/purchase_task/international_margin.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 4·6·7번 — "해외
매입처는 카드 또는 PayPal", "국제 거래는 환율·관세·배송기간을
수익성 계산에 포함") — 해외 매입처 원가를 KRW로 환산해 기존
`margin_calculator.py`에 그대로 넘기는 얇은 어댑터.

**기존 `margin_calculator.py`(154줄, 5개 이상 테스트 파일이 참조하는
이미 검증된 순수 함수)는 이번에 한 글자도 고치지 않았다** — 그
파일의 `calculate_margin()`/`CandidateCostInput` 시그니처를 바꾸면
그 전체 회귀에 영향을 준다. 대신 이 파일은 해외 원가(외화 가격+
배송비+관세)를 `ExchangeRateService`로 KRW 환산한 뒤, 기존
`CandidateCostInput`을 그대로 만들어 반환한다 — 호출자는 이후
`calculate_margin()`을 평소와 똑같이 그대로 쓴다.

관세는 `confirmed_additional_cost`(기존 필드, "확정된 추가비용")에
더해 넣는다 — 새 필드를 추가하지 않고 기존 계약 안에서 표현한다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domains.currency.service import ExchangeRateNotFoundError
from app.domains.currency.service import ExchangeRateService
from app.domains.purchase_task.margin_calculator import CandidateCostInput


class InternationalCostConversionError(Exception):
    pass


@dataclass(frozen=True)
class InternationalCostInput:

    candidate_id: int
    foreign_price: Decimal
    foreign_shipping_fee: Decimal
    foreign_currency: str
    # 예: Decimal("0.08") = 관세율 8%. 미확인이면 None — 절대
    # 0으로 추측하지 않는다(호출자가 명시적으로 0을 넣지 않는 한).
    customs_duty_rate: Decimal | None = None
    international_shipping_days: int | None = None
    confirmed_additional_cost_krw: Decimal = Decimal("0")
    confirmed_discount_krw: Decimal = Decimal("0")
    return_allowed: bool | None = None
    seller_trust_score: float | None = None
    match_confidence: float | None = None


def convert_international_cost_to_krw(
    cost: InternationalCostInput,
    *,
    exchange_rate_service: ExchangeRateService,
) -> CandidateCostInput:
    """
    환율이 한 번도 기록된 적이 없으면(ExchangeRateService.convert()가
    ExchangeRateNotFoundError를 던지면) 절대 추측하지 않고 그대로
    전파한다 — 관세율도 None이면 0으로 추측하지 않고 예외를 던진다
    (호출자가 "관세 미확인 상태로 진행"을 원하면 명시적으로
    `customs_duty_rate=Decimal("0")`을 넣어야 한다).
    """

    if cost.customs_duty_rate is None:
        raise InternationalCostConversionError(
            "관세율이 확인되지 않았습니다 — 추측으로 대체하지 않습니다"
            "(확정되면 customs_duty_rate를 명시적으로 넘겨 주세요, "
            "관세가 정말 0이면 Decimal('0')을 명시적으로 넘겨 주세요).",
        )

    try:
        price_krw = exchange_rate_service.convert(
            cost.foreign_price, cost.foreign_currency, "KRW",
        )
        shipping_krw = exchange_rate_service.convert(
            cost.foreign_shipping_fee, cost.foreign_currency, "KRW",
        )
    except ExchangeRateNotFoundError as exc:
        raise InternationalCostConversionError(str(exc)) from exc

    customs_krw = price_krw * cost.customs_duty_rate

    return CandidateCostInput(
        candidate_id=cost.candidate_id,
        estimated_price=price_krw,
        estimated_shipping_fee=shipping_krw,
        confirmed_additional_cost=(
            cost.confirmed_additional_cost_krw + customs_krw
        ),
        confirmed_discount=cost.confirmed_discount_krw,
        estimated_delivery_days=cost.international_shipping_days,
        return_allowed=cost.return_allowed,
        seller_trust_score=cost.seller_trust_score,
        match_confidence=cost.match_confidence,
    )


__all__ = [
    "InternationalCostConversionError",
    "InternationalCostInput",
    "convert_international_cost_to_krw",
]
