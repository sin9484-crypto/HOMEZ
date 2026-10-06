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

2026-09-28(45차) — `cost_of_goods`/`sale_price`를 제외한 7개 입력은
`None`(미확인)일 수 있다. `None`은 공식의 합산·곱셈에서 0으로
연산하되(계산 자체는 계속 진행), 그 필드명을 `missing_cost_fields`에
기록하고 `is_provisional=True`를 세운다 — "미확인 = 0원"이 아니라
"미확인 비용은 이 합계에 없다"는 뜻이다. 호출부(서비스·화면)는
`is_provisional`이 True인 결과를 "확정 마진"이 아니라 "확인된 비용
기준 잠정값"으로만 표시해야 한다.
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


_ASSUMPTION_FIELDS = (
    "channel_fee_rate", "payment_fee_rate", "shipping_cost",
    "packaging_cost", "ad_cost", "return_reserve_rate", "tax_basis_rate",
)

# 2026-09-28(47차) — HOMEZ_USER_OPERATION_SETTINGS.md §6(수익성 기준,
# 기존 확정 정책)이 명시한 "광고비는 초기 이익 계산에서 제외한다"는
# 데이터가 없어서 비워둔 미확인 상태가 아니라, 회사가 이미 결정해 둔
# 계산 범위 제외다. 둘을 같은 missing_cost_fields로 섞으면 (1) 사용자
# 에게 이미 답이 정해진 광고비를 다시 확정해 달라고 요구하게 되고,
# (2) 이 필드 하나 때문에 다른 비용이 전부 확인돼도 영원히
# is_provisional=True로 남는다(정책상 절대 채워지지 않는 필드이므로).
# 정책상 제외된 필드는 별도의 excluded_cost_fields로 분리한다.
_POLICY_EXCLUDED_FIELDS = ("ad_cost",)


def _confirmed(value: Decimal | None) -> Decimal:
    """None(미확인 또는 정책상 제외)을 연산용 0으로 치환한다 — 결과가
    "0원 확정"이라는 뜻은 아니며, 호출부가 missing_cost_fields/
    excluded_cost_fields로 그 사실을 함께 받는다."""

    return value if value is not None else Decimal("0")


def _raw_amounts(item: EconomicsInputItem) -> tuple[Decimal, Decimal, Decimal]:
    """(예상매출, 총비용, 마진금액) — 반올림 전 정확한 Decimal 값."""

    channel_fee_rate = _confirmed(item.channel_fee_rate)
    payment_fee_rate = _confirmed(item.payment_fee_rate)
    shipping_cost = _confirmed(item.shipping_cost)
    packaging_cost = _confirmed(item.packaging_cost)
    ad_cost = _confirmed(item.ad_cost)
    return_reserve_rate = _confirmed(item.return_reserve_rate)
    tax_basis_rate = _confirmed(item.tax_basis_rate)

    channel_fee = item.sale_price * channel_fee_rate
    payment_fee = item.sale_price * payment_fee_rate
    return_reserve = item.sale_price * return_reserve_rate
    tax = item.sale_price * tax_basis_rate

    total_cost = (
        item.cost_of_goods
        + channel_fee
        + payment_fee
        + shipping_cost
        + packaging_cost
        + ad_cost
        + return_reserve
        + tax
    )

    expected_revenue = item.sale_price
    return expected_revenue, total_cost, expected_revenue - total_cost


def min_margin_shortfall(
    item: EconomicsInputItem, target_margin_rate: Decimal,
) -> Decimal:
    """최소마진 기준에 모자란 금액(반올림 전). 0 이하면 기준 충족, 양수면
    미달이다. 나눗셈이나 반올림을 거치지 않고 `기준율 × 판매가 −
    마진금액`을 Decimal로 직접 비교하므로, 화면에 반올림된 마진율이
    기준과 같아 보여도(예: 17.995% → 18.00%) 실제 미달이면 양수가
    나온다. 승인·실행 판정은 반드시 이 값으로 하고, 반올림된
    margin_rate는 화면 표시에만 쓴다."""

    expected_revenue, _total_cost, margin_amount = _raw_amounts(item)
    return target_margin_rate * expected_revenue - margin_amount


def calculate_economics(item: EconomicsInputItem) -> EconomicsResultItem:

    missing_cost_fields = [
        name for name in _ASSUMPTION_FIELDS
        if name not in _POLICY_EXCLUDED_FIELDS and getattr(item, name) is None
    ]
    excluded_cost_fields = [
        name for name in _POLICY_EXCLUDED_FIELDS
        if getattr(item, name) is None
    ]

    channel_fee_rate = _confirmed(item.channel_fee_rate)
    payment_fee_rate = _confirmed(item.payment_fee_rate)
    shipping_cost = _confirmed(item.shipping_cost)
    packaging_cost = _confirmed(item.packaging_cost)
    ad_cost = _confirmed(item.ad_cost)
    return_reserve_rate = _confirmed(item.return_reserve_rate)
    tax_basis_rate = _confirmed(item.tax_basis_rate)

    expected_revenue, total_cost, margin_amount = _raw_amounts(item)

    if expected_revenue == 0:
        margin_rate = Decimal("0")
    else:
        margin_rate = margin_amount / expected_revenue

    fixed_costs = (
        item.cost_of_goods
        + shipping_cost
        + packaging_cost
        + ad_cost
    )
    rate_sum = (
        channel_fee_rate
        + payment_fee_rate
        + return_reserve_rate
        + tax_basis_rate
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
        is_provisional=bool(missing_cost_fields),
        missing_cost_fields=missing_cost_fields,
        excluded_cost_fields=excluded_cost_fields,
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

    2026-09-28(45차) — 다른 7개 입력과 마찬가지로 None(미확인)을
    허용한다. calculate_economics()와 동일하게 None은 연산용 0으로
    치환한다(이 역산 결과 자체가 "미확인 비용을 반영하지 않은 잠정
    판매가"라는 것은 호출부가 필요하면 item의 None 필드를 직접 확인해
    판단해야 한다 — 이 함수는 EconomicsResultItem을 반환하지 않으므로
    is_provisional을 실어 보낼 자리가 없다).
    """

    fixed_costs = (
        item.cost_of_goods
        + _confirmed(item.shipping_cost)
        + _confirmed(item.packaging_cost)
        + _confirmed(item.ad_cost)
    )
    rate_sum = (
        _confirmed(item.channel_fee_rate)
        + _confirmed(item.payment_fee_rate)
        + _confirmed(item.return_reserve_rate)
        + _confirmed(item.tax_basis_rate)
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
