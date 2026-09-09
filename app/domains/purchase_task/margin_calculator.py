"""
=========================================================
Homez OS

File : app/domains/purchase_task/margin_calculator.py

작업 D — 실질 매입비·예상 순이익·예상 마진율 계산 + 후보 순위.
순수 함수만 둔다(DB·네트워크 접근 없음) — 서비스 계층이 이 결과를
저장한다. 확인되지 않은 할인(적립 포인트/조건부 쿠폰/카드 행사/
캐시백)은 절대 반영하지 않는다(지시문 4번 문제) — 호출자가 이미
"결제 전 확정"된 값만 넘겨야 한다는 계약이다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CandidateCostInput:

    candidate_id: int
    estimated_price: Decimal
    estimated_shipping_fee: Decimal
    confirmed_additional_cost: Decimal = Decimal("0")
    confirmed_discount: Decimal = Decimal("0")
    estimated_delivery_days: int | None = None
    return_allowed: bool | None = None
    seller_trust_score: float | None = None
    match_confidence: float | None = None


@dataclass(frozen=True)
class MarginResult:

    candidate_id: int
    actual_purchase_cost: Decimal
    expected_net_profit: Decimal
    expected_margin_rate: Decimal


def calculate_actual_purchase_cost(candidate: CandidateCostInput) -> Decimal:
    """실질 매입비 = 상품가격 + 배송비 + 확정된 추가비용 − 결제 전
    확정된 할인. 적립예정 포인트·조건부 쿠폰·카드행사·추후지급
    캐시백은 이 계산에 들어오지 않는다(호출자가 애초에 confirmed_
    discount에 담지 않아야 한다 — 이 함수는 강제할 수 없으므로
    호출부 docstring/스키마 설명으로 원칙을 못 박는다)."""

    cost = (
        candidate.estimated_price
        + candidate.estimated_shipping_fee
        + candidate.confirmed_additional_cost
        - candidate.confirmed_discount
    )
    return cost if cost > 0 else Decimal("0")


def calculate_margin(
    candidate: CandidateCostInput, *, coupang_sale_amount: Decimal,
    coupang_fee_amount: Decimal, additional_shipping_fee: Decimal = Decimal("0"),
    return_risk_reserve: Decimal = Decimal("0"),
    other_confirmed_cost: Decimal = Decimal("0"),
) -> MarginResult:

    actual_cost = calculate_actual_purchase_cost(candidate)

    net_profit = (
        coupang_sale_amount
        - coupang_fee_amount
        - actual_cost
        - additional_shipping_fee
        - return_risk_reserve
        - other_confirmed_cost
    )

    margin_rate = (
        (net_profit / coupang_sale_amount * Decimal("100"))
        if coupang_sale_amount > 0 else Decimal("0")
    )

    return MarginResult(
        candidate_id=candidate.candidate_id,
        actual_purchase_cost=actual_cost,
        expected_net_profit=net_profit,
        expected_margin_rate=margin_rate,
    )


def rank_candidates(
    results: list[MarginResult], candidates: list[CandidateCostInput],
    *, min_net_profit: Decimal | None = None,
    min_margin_rate: Decimal | None = None,
) -> list[MarginResult]:
    """단순 최저가 정렬이 아니다(지시문 명시) — 정책(최소 순이익·
    최소 마진율)을 통과한 후보만 우선 후보군으로 두고, 그 안에서
    예상 순이익 내림차순으로 정렬한다. 정책 미통과 후보는 뒤로
    밀리되 제거하지는 않는다(운영자가 그래도 볼 수 있어야 한다)."""

    candidate_by_id = {c.candidate_id: c for c in candidates}

    def passes_policy(r: MarginResult) -> bool:
        if min_net_profit is not None and r.expected_net_profit < min_net_profit:
            return False
        if min_margin_rate is not None and r.expected_margin_rate < min_margin_rate:
            return False
        return True

    def sort_key(r: MarginResult):
        c = candidate_by_id.get(r.candidate_id)
        trust = (c.seller_trust_score if c and c.seller_trust_score is not None
                 else 0.0)
        return (
            0 if passes_policy(r) else 1,
            -float(r.expected_net_profit),
            -trust,
        )

    return sorted(results, key=sort_key)


def format_recommendation_reason(
    result: MarginResult, candidate: CandidateCostInput, *, rank: int,
) -> str:
    """운영자가 이해할 수 있는 문장으로 추천 근거를 만든다(지시문
    명시 — 최저가만으로 선정하지 않는다는 것을 사람이 읽을 수 있게
    보여준다)."""

    parts = [
        f"예상 순이익 {result.expected_net_profit:,.0f}원",
        f"마진율 {result.expected_margin_rate:.1f}%",
    ]
    if candidate.estimated_delivery_days is not None:
        parts.append(f"배송 {candidate.estimated_delivery_days}일")
    if candidate.return_allowed:
        parts.append("반품 가능")
    if candidate.seller_trust_score is not None:
        parts.append(f"판매자 신뢰도 {candidate.seller_trust_score:.2f}")
    if candidate.match_confidence is not None:
        parts.append(f"동일상품 신뢰도 {candidate.match_confidence * 100:.0f}%")

    prefix = "최적 후보" if rank == 0 else f"{rank + 1}순위 후보"
    return f"{prefix} — " + " · ".join(parts)


__all__ = [
    "CandidateCostInput",
    "MarginResult",
    "calculate_actual_purchase_cost",
    "calculate_margin",
    "rank_candidates",
    "format_recommendation_reason",
]
