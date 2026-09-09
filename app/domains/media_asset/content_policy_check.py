"""
=========================================================
Homez OS

File : app/domains/media_asset/content_policy_check.py

V7 Gate 6 — 이미지 생성/후보 파이프라인 진입 전 저작권/금지품목 간단
체크리스트(규칙 기반).

실제 이미지 인식 AI나 상표 데이터베이스 조회는 이번 범위 밖이다 —
candidate의 텍스트 필드(product_name/category_hint/brand_hint)만
소문자 부분 문자열로 대조하는 결정론적 규칙이다(app/domains/decision
와 동일한 철학: 실제로 하지 않는 검사를 하는 것처럼 보고하지 않는다
— 이 체크는 "생성된 이미지 콘텐츠 자체"를 보지 않고 후보 메타데이터
텍스트만 본다).

BLOCKING(금지 품목 의심) — 파이프라인/이미지 Job 제출 자체를 막는다
(정책 위반은 점수로 상쇄하지 않는다는 이 저장소의 반복된 원칙,
app/domains/decision::_POLICY_HARD_BLOCK_THRESHOLD와 동일한 사상).
WARNING(제3자 브랜드/IP 가능성) — 제출은 막지 않되 결과에 경고
플래그로 남겨 운영자가 눈으로 확인하게 한다(정상적인 리셀러 표기일
수 있어 과잉 차단하지 않는다).

이 목록은 예시 수준의 최소 목록이다 — 실제 운영에서는 법무 검토를
거친 목록으로 교체해야 한다(이번 Phase는 "규칙 기반 체크리스트가
존재하고 실제로 검사한다"는 계약만 만든다, CTO 확인 필요 사항으로
보고).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass

PROHIBITED_ITEM_KEYWORDS: tuple[str, ...] = (
    "총기", "화약", "실탄", "마약", "대마", "필로폰",
    "도검", "환각제", "위조지폐", "밀수",
    "firearm", "gunpowder", "narcotics",
)

HIGH_IP_RISK_BRAND_KEYWORDS: tuple[str, ...] = (
    "나이키", "루이비통", "샤넬", "구찌", "애플", "롤렉스",
    "nike", "louis vuitton", "chanel", "gucci", "apple", "rolex",
)

CHECKLIST_VERSION = "content_policy_check.v1"


@dataclass(frozen=True)
class ContentPolicyCheckResult:

    passed: bool
    blocking_flags: tuple[str, ...]
    warning_flags: tuple[str, ...]
    checklist_version: str = CHECKLIST_VERSION


def _contains_any(haystack: str, needles: tuple[str, ...]) -> tuple[str, ...]:

    lowered = haystack.lower()

    return tuple(needle for needle in needles if needle.lower() in lowered)


def run_content_policy_checklist(
    product_name: str,
    category_hint: str | None,
    brand_hint: str | None,
) -> ContentPolicyCheckResult:
    """
    순수 함수 — 동일 입력은 항상 동일 결과를 반환한다(결정적, 외부
    호출 없음).
    """

    combined = " ".join(
        part for part in (product_name, category_hint, brand_hint) if part
    )

    blocking = _contains_any(combined, PROHIBITED_ITEM_KEYWORDS)
    warning = _contains_any(combined, HIGH_IP_RISK_BRAND_KEYWORDS)

    return ContentPolicyCheckResult(
        passed=len(blocking) == 0,
        blocking_flags=blocking,
        warning_flags=warning,
    )


__all__ = [
    "PROHIBITED_ITEM_KEYWORDS",
    "HIGH_IP_RISK_BRAND_KEYWORDS",
    "CHECKLIST_VERSION",
    "ContentPolicyCheckResult",
    "run_content_policy_checklist",
]
