"""
=========================================================
Homez OS

File : app/domains/retail_purchase/product_matching.py

Gate RP-1(2026-08-22) — 동일상품 판정(지시문 D 섹션). 이미지 유사도
만으로 확정하지 않는다. 상품명 일부 일치만으로 자동구매하지 않는다.
핵심 기준(바코드/GTIN, 옵션) 불일치는 다른 항목이 전부 일치해도
항상 차단한다 — 순수 함수(DB/네트워크 접근 없음)라 결정론적으로
테스트 가능하다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.retail_purchase.constants import SameProductConfidenceTier

# 가중치 합 = 1.0. GTIN은 별도로 hard-match 취급(가중치에 포함하되,
# 존재하고 일치하면 사실상 확정으로 취급하도록 가중치를 가장 높게
# 둔다) — 존재하지 않으면 그 배점을 제외하고 나머지로 재정규화한다.
_CRITERION_WEIGHTS: dict[str, float] = {
    "gtin": 0.30,
    "brand": 0.12,
    "manufacturer": 0.08,
    "model_name": 0.20,
    "capacity": 0.08,
    "quantity": 0.07,
    "color_or_scent": 0.07,
    "options": 0.05,
    "components": 0.03,
}

# 둘 다 값이 있는데 다르면(빈 값이 아니라 명시적으로 다르면) 전체
# 판정을 즉시 차단하는 핵심 기준 — 다른 항목이 전부 일치해도 뒤집지
# 않는다.
_HARD_BLOCK_CRITERIA = ("gtin", "color_or_scent", "capacity")

# 실제로 비교 가능했던 기준의 가중치 비율(coverage) 최소 기준.
# "상품명 일부 일치만으로 자동구매하지 않는다"는 원칙을 수치로
# 강제한다 — 비교 항목이 몇 개 안 되는데 우연히 다 일치했다고
# AUTO_CANDIDATE로 올리지 않는다.
_MIN_COVERAGE_FOR_AUTO = 0.70
_MIN_COVERAGE_FOR_REVIEW = 0.30


@dataclass(frozen=True)
class ProductAttributes:
    """쿠팡 원 주문 품목과 구매처 후보 상품 양쪽에 동일하게 쓰는
    속성 묶음 — provider.ProductDetail에서 그대로 채울 수 있다."""

    brand: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    gtin: str | None = None
    capacity: str | None = None
    quantity: int | None = None
    color_or_scent: str | None = None
    options: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    is_authorized_dealer: bool | None = None
    certification_info: str | None = None


@dataclass(frozen=True)
class MatchCriterionEvidence:

    criterion: str
    source_value: str | None
    candidate_value: str | None
    matched: bool | None  # None = 비교 불가(한쪽 값 없음)


@dataclass(frozen=True)
class SameProductMatchResult:

    confidence: float
    tier: str
    evidence: tuple[MatchCriterionEvidence, ...]
    blocked_reason: str | None
    missing_attributes: tuple[str, ...]


def _normalize(value: str | None) -> str | None:

    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _compare_text(source: str | None, candidate: str | None) -> bool | None:

    s, c = _normalize(source), _normalize(candidate)
    if s is None or c is None:
        return None
    return s == c


def _compare_tuple(source: tuple[str, ...], candidate: tuple[str, ...]) -> bool | None:

    if not source or not candidate:
        return None
    return {_normalize(x) for x in source} == {_normalize(x) for x in candidate}


def evaluate_same_product(
    source: ProductAttributes, candidate: ProductAttributes,
) -> SameProductMatchResult:
    """
    순수 함수 — DB/네트워크를 건드리지 않는다. 신뢰도는 실제 비교
    가능했던 기준들의 가중치만으로 재정규화한 가중 평균이다(비교
    불가능한 기준은 "확인 필요"로 남기고 점수 계산에서 제외한다 —
    0점으로 임의 처리하지 않는다).
    """

    evidence: list[MatchCriterionEvidence] = []
    missing: list[str] = []

    source_map = {
        "gtin": source.gtin, "brand": source.brand,
        "manufacturer": source.manufacturer, "model_name": source.model_name,
        "capacity": source.capacity, "color_or_scent": source.color_or_scent,
    }
    candidate_map = {
        "gtin": candidate.gtin, "brand": candidate.brand,
        "manufacturer": candidate.manufacturer,
        "model_name": candidate.model_name, "capacity": candidate.capacity,
        "color_or_scent": candidate.color_or_scent,
    }

    comparisons: dict[str, bool | None] = {}

    for criterion in (
        "gtin", "brand", "manufacturer", "model_name", "capacity",
        "color_or_scent",
    ):
        matched = _compare_text(source_map[criterion], candidate_map[criterion])
        comparisons[criterion] = matched
        evidence.append(MatchCriterionEvidence(
            criterion=criterion,
            source_value=source_map[criterion],
            candidate_value=candidate_map[criterion],
            matched=matched,
        ))
        if matched is None:
            missing.append(criterion)

    quantity_matched = (
        None if source.quantity is None or candidate.quantity is None
        else source.quantity == candidate.quantity
    )
    comparisons["quantity"] = quantity_matched
    evidence.append(MatchCriterionEvidence(
        criterion="quantity",
        source_value=(None if source.quantity is None else str(source.quantity)),
        candidate_value=(None if candidate.quantity is None else str(candidate.quantity)),
        matched=quantity_matched,
    ))
    if quantity_matched is None:
        missing.append("quantity")

    options_matched = _compare_tuple(source.options, candidate.options)
    comparisons["options"] = options_matched
    evidence.append(MatchCriterionEvidence(
        criterion="options",
        source_value=", ".join(source.options) or None,
        candidate_value=", ".join(candidate.options) or None,
        matched=options_matched,
    ))
    if options_matched is None:
        missing.append("options")

    components_matched = _compare_tuple(source.components, candidate.components)
    comparisons["components"] = components_matched
    evidence.append(MatchCriterionEvidence(
        criterion="components",
        source_value=", ".join(source.components) or None,
        candidate_value=", ".join(candidate.components) or None,
        matched=components_matched,
    ))
    if components_matched is None:
        missing.append("components")

    # 핵심 기준(바코드/GTIN, 색상·향, 용량) 중 하나라도 "둘 다 값이
    # 있는데 다름"이면 즉시 차단 — 다른 항목이 전부 일치해도 뒤집지
    # 않는다.
    for hard_criterion in _HARD_BLOCK_CRITERIA:
        if comparisons.get(hard_criterion) is False:
            return SameProductMatchResult(
                confidence=0.0,
                tier=SameProductConfidenceTier.BLOCKED,
                evidence=tuple(evidence),
                blocked_reason=f"CORE_ATTRIBUTE_MISMATCH:{hard_criterion}",
                missing_attributes=tuple(missing),
            )

    # 옵션 불일치도 핵심 기준으로 취급(지시문: "바코드 또는 핵심
    # 옵션 불일치는 항상 차단").
    if comparisons.get("options") is False:
        return SameProductMatchResult(
            confidence=0.0,
            tier=SameProductConfidenceTier.BLOCKED,
            evidence=tuple(evidence),
            blocked_reason="CORE_ATTRIBUTE_MISMATCH:options",
            missing_attributes=tuple(missing),
        )

    comparable_weight_sum = sum(
        _CRITERION_WEIGHTS[c] for c, matched in comparisons.items() if matched is not None
    )
    total_weight_sum = sum(_CRITERION_WEIGHTS.values())
    coverage = comparable_weight_sum / total_weight_sum

    if comparable_weight_sum <= 0 or coverage < _MIN_COVERAGE_FOR_REVIEW:
        # 비교 가능한 기준 자체가 거의 없다 — "이미지 유사도만으로",
        # "상품명 일부 일치만으로" 확정하지 않는다는 원칙과 동일하게,
        # 근거가 부족하면 신뢰도를 계산조차 하지 않고 정직하게
        # 차단한다(빈 값을 0점으로 임의 채우지 않는다).
        return SameProductMatchResult(
            confidence=0.0,
            tier=SameProductConfidenceTier.BLOCKED,
            evidence=tuple(evidence),
            blocked_reason="EVIDENCE_REQUIRED",
            missing_attributes=tuple(missing),
        )

    matched_weight_sum = sum(
        _CRITERION_WEIGHTS[c] for c, matched in comparisons.items() if matched is True
    )

    confidence = matched_weight_sum / comparable_weight_sum

    if (
        confidence >= SameProductConfidenceTier.AUTO_CANDIDATE_THRESHOLD
        and coverage >= _MIN_COVERAGE_FOR_AUTO
    ):
        tier = SameProductConfidenceTier.AUTO_CANDIDATE
        blocked_reason = None
    elif confidence >= SameProductConfidenceTier.NEEDS_REVIEW_THRESHOLD:
        # coverage가 낮아 AUTO_CANDIDATE 기준(비교 가능 항목의 70%
        # 이상)을 못 채웠다면, 일치율 자체가 98% 이상이어도 사람
        # 확인 단계로 낮춘다 — 비교한 항목이 적을수록 우연히 전부
        # 일치했을 위험이 크다.
        tier = SameProductConfidenceTier.NEEDS_REVIEW
        blocked_reason = None
    else:
        tier = SameProductConfidenceTier.BLOCKED
        blocked_reason = "PRODUCT_MATCH_INSUFFICIENT"

    return SameProductMatchResult(
        confidence=confidence, tier=tier, evidence=tuple(evidence),
        blocked_reason=blocked_reason, missing_attributes=tuple(missing),
    )


__all__ = [
    "ProductAttributes",
    "MatchCriterionEvidence",
    "SameProductMatchResult",
    "evaluate_same_product",
]
