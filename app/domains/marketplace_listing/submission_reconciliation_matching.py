"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/submission_reconciliation_matching.py

2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) — 순수 함수만
담는다(DB·네트워크 접근 없음). 운영자가 입력한 sellerProductId 하나만
믿고 정합화하면, 오타나 다른 상품 ID를 잘못 입력했을 때 완전히 다른
제출 건에 엉뚱한 상품이 연결될 수 있다 — 상품명·vendorUserId·
카테고리 중 실제로 비교 가능한 값이 있으면 전부 대조하고, 하나라도
모순되면 fail-closed로 막는다("추정으로 제출 기록을 성공 처리하지
않음" 원칙).

세 값 모두 coupang_live_provider.py::ProductStatusResult가 이미
CONFIRMED 필드로 노출한다(추정 계약 아님). 상품명은 정규화(공백 정리·
대소문자 무시) 후 완전 일치만 인정한다 — 부분/유사 일치는 하지 않는다
(유사도 매칭은 "그럴듯하면 통과"가 되어 이 기능의 안전 목적과 反한다).
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field

# 이 tier들은 preview 단계의 안내용 분류일 뿐이다 — apply 실행 여부는
# 항상 운영자(SuperAdminGuard)의 명시적 호출이 결정한다(자동 실행
# 없음). BLOCKED만 apply 자체를 막는다.
MATCH_TIER_BLOCKED = "BLOCKED"
MATCH_TIER_MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
MATCH_TIER_AUTO_ELIGIBLE = "AUTO_ELIGIBLE"

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_name(value: str) -> str:

    return _WHITESPACE_RE.sub(" ", value).strip().casefold()


@dataclass(frozen=True)
class IdentifierComparison:

    field_name: str
    expected: str | None
    observed: str | None
    outcome: str  # "MATCH" | "MISMATCH" | "UNAVAILABLE"


@dataclass(frozen=True)
class MatchAssessment:

    tier: str
    comparisons: tuple[IdentifierComparison, ...] = field(default_factory=tuple)

    @property
    def matched_fields(self) -> tuple[str, ...]:
        return tuple(c.field_name for c in self.comparisons if c.outcome == "MATCH")

    @property
    def mismatched_fields(self) -> tuple[str, ...]:
        return tuple(c.field_name for c in self.comparisons if c.outcome == "MISMATCH")

    @property
    def unavailable_fields(self) -> tuple[str, ...]:
        return tuple(c.field_name for c in self.comparisons if c.outcome == "UNAVAILABLE")


def _compare_exact(field_name: str, expected: str | None, observed: str | None) -> IdentifierComparison:

    if not expected or not observed:
        return IdentifierComparison(field_name, expected, observed, "UNAVAILABLE")
    if str(expected).strip() == str(observed).strip():
        return IdentifierComparison(field_name, expected, observed, "MATCH")
    return IdentifierComparison(field_name, expected, observed, "MISMATCH")


def _compare_name(field_name: str, expected: str | None, observed: str | None) -> IdentifierComparison:

    if not expected or not observed:
        return IdentifierComparison(field_name, expected, observed, "UNAVAILABLE")
    if _normalize_name(expected) == _normalize_name(observed):
        return IdentifierComparison(field_name, expected, observed, "MATCH")
    return IdentifierComparison(field_name, expected, observed, "MISMATCH")


def evaluate_identifier_match(
    *,
    expected_product_name: str | None,
    expected_vendor_user_id: str | None,
    expected_display_category_code: str | None,
    observed_seller_product_name: str | None,
    observed_vendor_user_id: str | None,
    observed_display_category_code: str | None,
) -> MatchAssessment:
    """
    비교 가능한 식별값들을 전부 대조한다.

    - 하나라도 MISMATCH면 BLOCKED(apply 자체를 막는다) — mismatch는
      "증거 부족"이 아니라 "반대 증거"이므로 다른 UNAVAILABLE 필드가
      더 있어도 절대 격상하지 않는다.
    - MISMATCH가 전혀 없고, 실제로 비교된(MATCH) 필드가 2개 이상이면
      AUTO_ELIGIBLE — 두 개 이상의 독립된 식별값이 동시에 우연히
      일치할 확률은 낮다고 보되, 이 결과 자체가 apply를 자동 실행하는
      것은 아니다(항상 운영자가 apply를 별도로 호출해야 한다).
    - 그 외(비교 가능한 값이 아예 없거나 1개만 일치)는
      MANUAL_REVIEW_REQUIRED — 운영자가 나머지 근거(WING 화면 등)를
      직접 확인했다는 사실을 알고 진행해야 한다.
    """

    comparisons = (
        _compare_name(
            "product_name", expected_product_name, observed_seller_product_name,
        ),
        _compare_exact(
            "vendor_user_id", expected_vendor_user_id, observed_vendor_user_id,
        ),
        _compare_exact(
            "display_category_code",
            expected_display_category_code, observed_display_category_code,
        ),
    )

    if any(c.outcome == "MISMATCH" for c in comparisons):
        return MatchAssessment(tier=MATCH_TIER_BLOCKED, comparisons=comparisons)

    matched_count = sum(1 for c in comparisons if c.outcome == "MATCH")
    if matched_count >= 2:
        return MatchAssessment(tier=MATCH_TIER_AUTO_ELIGIBLE, comparisons=comparisons)

    return MatchAssessment(
        tier=MATCH_TIER_MANUAL_REVIEW_REQUIRED, comparisons=comparisons,
    )


__all__ = [
    "MATCH_TIER_BLOCKED",
    "MATCH_TIER_MANUAL_REVIEW_REQUIRED",
    "MATCH_TIER_AUTO_ELIGIBLE",
    "IdentifierComparison",
    "MatchAssessment",
    "evaluate_identifier_match",
]
