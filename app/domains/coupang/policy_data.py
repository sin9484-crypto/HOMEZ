"""
=========================================================
Homez OS

File : app/domains/coupang/policy_data.py

기본 정책 Fixture 데이터 초안.

이 파일이 정의하는 정책 세트는 status="DRAFT"로 고정되어 있다 —
DRAFT 세트는 CoupangIntegrationService의 정책 검증에서 "사용
가능"으로 취급되지 않으므로(app/domains/coupang/constants.py의
PolicySetStatus 참고), 이 Fixture를 그대로 사용해도 시스템은
POLICY_DATA_UNAVAILABLE로 fail-closed한다.

이 파일은 실제 homez.db에 자동으로 시딩되지 않는다(app/main.py나
app/database/seed_*.py 어디에서도 import하지 않음). 정책 세트를
실제로 VERIFIED로 승격하는 것은 admin_guard가 걸린
`POST /coupang-policy-sets` 등 API를 통해 실제 운영자가 명시적으로
수행해야 하는 별도 작업이다 — 이 Fixture는 그 작업을 시작할 때 참고할
초안 데이터일 뿐이다.

출처: 사용자 제공 요구사항 섹션 6(HOMEZ 판매 금지·주의 상품 분류).
실제 쿠팡 공식 정책 문서(marketplace.coupang.com/information-center/
marketplace3p-product-info-update-uid)는 이번 세션에서 직접 열람하지
못해 미대조 상태 — 그래서 status가 VERIFIED가 아니라 DRAFT다.
=========================================================
"""

from datetime import datetime
from datetime import timezone

from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel

DRAFT_POLICY_SET_ID = "2026-07-29-draft-1"
DRAFT_POLICY_VERSION = "2026-07-29-draft-1"
DRAFT_POLICY_SOURCE = (
    "사용자 제공 요구사항 섹션 6 (HOMEZ 판매 금지·주의 상품 분류) — "
    "쿠팡 공식 정책 원문은 이번 세션에서 직접 열람하지 못해 미대조 상태"
)
_CHECKED_AT = datetime(2026, 7, 29, tzinfo=timezone.utc).replace(tzinfo=None)


def build_draft_policy_set() -> dict:
    """
    CoupangPolicySet 생성용 kwargs. status는 항상 DRAFT다 — 이 함수는
    "사용 가능한" 정책 세트를 만들지 않는다(의도적).
    """

    return {
        "policy_set_id": DRAFT_POLICY_SET_ID,
        "policy_version": DRAFT_POLICY_VERSION,
        "source_reference": DRAFT_POLICY_SOURCE,
        "status": PolicySetStatus.DRAFT,
        "is_complete": True,
        "is_active": True,
        "checked_at": _CHECKED_AT,
        "effective_at": _CHECKED_AT,
        "expires_at": None,
    }


def _keyword_rule(keyword: str, risk_level: str, reason: str) -> dict:

    return {
        "match_field": PolicyMatchField.KEYWORD,
        "match_value": keyword,
        "risk_level": risk_level,
        "reason": reason,
    }


# 인증/허가 필요 대상 — 자동 등록 불가, 서류 확인 후에만 진행 가능
_CERTIFICATION_REQUIRED_KEYWORDS = (
    "의료기기",
    "의약품",
    "건강기능식품",
    "전기용품",
    "생활용품 인증",
    "화학제품",
)

# 운영자 직접 검토가 필요한 대상 — 금지는 아니지만 자동 승인 불가
_OPERATOR_REVIEW_KEYWORDS = (
    "식품",
    "아동",
    "유아",
    "해외구매대행 제한",
    "위조품",
    "설치 서비스",
    "냉장",
    "냉동",
    "신선",
    "크기·중량 제한",
    "브랜드 및 지식재산권 위험",
)

# 판매 자체를 금지하는 대상
_PROHIBITED_KEYWORDS = (
    "위험물",
    "에어로졸",
    "고압가스",
)


def build_draft_policy_rules() -> list[dict]:
    """
    CoupangPolicyRule 생성용 kwargs 목록(policy_set_id는 호출자가
    실제 CoupangPolicySet.id로 채워야 한다). 전부 match_field=KEYWORD —
    자유문자 매칭이므로 이 규칙들만으로는 SELLABLE을 확정할 수 없다
    (service.py에서 강제). 명시적으로 SELLABLE을 확정하려면 구조화된
    필드(display_category_code 등) 기반 규칙을 별도로 추가해야 한다.
    """

    rules = []

    for keyword in _CERTIFICATION_REQUIRED_KEYWORDS:
        rules.append(
            _keyword_rule(
                keyword, RiskLevel.CERTIFICATION_REQUIRED,
                f"'{keyword}' 카테고리는 인증·허가 확인 전까지 등록할 수 "
                "없다.",
            ),
        )

    for keyword in _OPERATOR_REVIEW_KEYWORDS:
        rules.append(
            _keyword_rule(
                keyword, RiskLevel.OPERATOR_REVIEW_REQUIRED,
                f"'{keyword}' 카테고리는 운영자 직접 검토 없이는 자동 "
                "진행할 수 없다.",
            ),
        )

    for keyword in _PROHIBITED_KEYWORDS:
        rules.append(
            _keyword_rule(
                keyword, RiskLevel.PROHIBITED,
                f"'{keyword}' 카테고리는 판매가 금지된다.",
            ),
        )

    return rules


__all__ = [
    "DRAFT_POLICY_SET_ID",
    "DRAFT_POLICY_VERSION",
    "DRAFT_POLICY_SOURCE",
    "build_draft_policy_set",
    "build_draft_policy_rules",
]
