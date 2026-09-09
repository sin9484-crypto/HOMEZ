"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/margin_calculator.py

Gate I(2026-08-08) — 채널별 가격·마진 계산기. 순수 함수만 담는다(DB
접근·Service 의존 없음, import만으로 부작용 없음) — 전부 Decimal이며
locale에 영향받지 않는다(문자열 포맷은 화면 전담, 이 모듈은 값만
계산).

계산은 "판매 단위 1개" 기준이다 — 수량 곱셈 확장은 이번 Gate 범위
밖이다(설계 문서에 명시).

공식:
  channel_fee/payment_fee/return_reserve/tax = sale_price × 해당 rate
  total_cost = cost_of_goods + channel_fee + payment_fee + shipping_cost
             + packaging_cost + ad_cost + return_reserve + tax
  expected_revenue = sale_price
  margin_amount = expected_revenue - total_cost
  margin_rate = margin_amount / expected_revenue (sale_price가 0이면
    나눗셈을 피하기 위해 0으로 고정 — "마진율 0%"라는 실제 값이 아니라
    계산 불능을 나타내는 sentinel이다. 사전검사 엔진이 sale_price<=0을
    별도 BLOCKING 사유로 이미 잡으므로, 이 화면까지 도달하는 정상
    흐름에서는 발생하지 않는다).
  break_even_price = fixed_costs / (1 - rate_sum), rate_sum >= 1이면
    수학적으로 손익분기가 불가능하므로 None(0으로 추측하지 않음).
=========================================================
"""

from decimal import ROUND_HALF_UP
from decimal import Decimal

from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsResultItem,
)

_MONEY_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:

    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:

    return value.quantize(_RATE_QUANT, rounding=ROUND_HALF_UP)


def calculate_economics(item: EconomicsInputItem) -> EconomicsResultItem:

    channel_fee = item.sale_price * item.channel_fee_rate
    payment_fee = item.sale_price * item.payment_fee_rate
    return_reserve = item.sale_price * item.return_reserve_rate
    tax = item.sale_price * item.tax_basis_rate

    total_cost = (
        item.cost_of_goods
        + channel_fee
        + payment_fee
        + item.shipping_cost
        + item.packaging_cost
        + item.ad_cost
        + return_reserve
        + tax
    )

    expected_revenue = item.sale_price
    margin_amount = expected_revenue - total_cost

    if expected_revenue == 0:
        margin_rate = Decimal("0")
    else:
        margin_rate = margin_amount / expected_revenue

    fixed_costs = (
        item.cost_of_goods
        + item.shipping_cost
        + item.packaging_cost
        + item.ad_cost
    )
    rate_sum = (
        item.channel_fee_rate
        + item.payment_fee_rate
        + item.return_reserve_rate
        + item.tax_basis_rate
    )

    if rate_sum >= 1:
        break_even_price = None
    else:
        break_even_price = _money(fixed_costs / (Decimal("1") - rate_sum))

    return EconomicsResultItem(
        marketplace_account_id=item.marketplace_account_id,
        expected_revenue=_money(expected_revenue),
        total_cost=_money(total_cost),
        margin_amount=_money(margin_amount),
        margin_rate=_rate(margin_rate),
        break_even_price=break_even_price,
    )


def calculate_economics_batch(
    items: list[EconomicsInputItem],
) -> list[EconomicsResultItem]:

    return [calculate_economics(item) for item in items]


def derive_sale_price_for_margin_rate(
    item: EconomicsInputItem, target_margin_rate: Decimal,
) -> Decimal | None:
    """
    item.sale_price는 무시하고, 원가·비율 필드만으로 목표 마진율을
    달성하는 판매가를 역산한다 — break_even_price(목표 마진율 0%인
    특수 케이스)와 같은 공식을 일반화한 것이다:

        sale_price = fixed_costs / (1 - rate_sum - target_margin_rate)

    분모가 0 이하이면(수수료 합+목표 마진율이 이미 100% 이상이라
    구조적으로 달성 불가능) None을 반환한다 — break_even_price와
    동일하게 0으로 추측하지 않는다(fail-closed).
    """

    fixed_costs = (
        item.cost_of_goods
        + item.shipping_cost
        + item.packaging_cost
        + item.ad_cost
    )
    rate_sum = (
        item.channel_fee_rate
        + item.payment_fee_rate
        + item.return_reserve_rate
        + item.tax_basis_rate
    )
    denominator = Decimal("1") - rate_sum - target_margin_rate
    if denominator <= 0:
        return None
    return _money(fixed_costs / denominator)


__all__ = [
    "calculate_economics",
    "calculate_economics_batch",
    "derive_sale_price_for_margin_rate",
]
