"""
=========================================================
Homez OS

File : app/domains/coupang/constants.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation — 고정 상수

이번 단계(Foundation)에서는 SUBMITTED 이후 실제 쿠팡 등록 상태로
전이하는 서비스 로직을 구현하지 않는다. 아래 상수에 정의는 하되
Service의 어떤 메서드도 이 상태들로 갱신하지 않는다(테스트로 강제).
=========================================================
"""

from decimal import ROUND_HALF_UP


class SalesMethod:

    MARKETPLACE = "MARKETPLACE"
    ROCKET_GROWTH = "ROCKET_GROWTH"

    ALL = (MARKETPLACE, ROCKET_GROWTH)


class IntegrationStatus:

    # 이번 단계에서 실제로 전이하는 상태
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED_FOR_DRY_RUN = "APPROVED_FOR_DRY_RUN"
    DRY_RUN_PASSED = "DRY_RUN_PASSED"
    DRY_RUN_FAILED = "DRY_RUN_FAILED"
    READY_FOR_SUBMISSION = "READY_FOR_SUBMISSION"

    # 계약에만 존재 — 실제 쿠팡 등록 이후 상태. 이번 단계에서 어떤 서비스
    # 메서드도 이 상태로 전이시키지 않는다(향후 실제 연동 단계에서 구현).
    SUBMITTED = "SUBMITTED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"

    ALL = (
        DRAFT,
        VALIDATING,
        VALIDATION_FAILED,
        READY_FOR_REVIEW,
        APPROVED_FOR_DRY_RUN,
        DRY_RUN_PASSED,
        DRY_RUN_FAILED,
        READY_FOR_SUBMISSION,
        SUBMITTED,
        REVIEWING,
        APPROVED,
        REJECTED,
        SUSPENDED,
    )

    # 이번 단계에서 서비스가 실제로 도달시킬 수 있는 상태(상한선).
    # READY_FOR_SUBMISSION까지만 허용 — 이후 상태는 계약에만 존재.
    REACHABLE_THIS_PHASE = (
        DRAFT,
        VALIDATING,
        VALIDATION_FAILED,
        READY_FOR_REVIEW,
        APPROVED_FOR_DRY_RUN,
        DRY_RUN_PASSED,
        DRY_RUN_FAILED,
        READY_FOR_SUBMISSION,
    )

    NOT_YET_IMPLEMENTED = (
        SUBMITTED,
        REVIEWING,
        APPROVED,
        REJECTED,
        SUSPENDED,
    )


class ValidationStatus:

    NOT_VALIDATED = "NOT_VALIDATED"
    PASSED = "PASSED"
    FAILED = "FAILED"

    ALL = (NOT_VALIDATED, PASSED, FAILED)


class RiskLevel:
    """
    상품정보/판매금지 정책 위험 분류. 심각도 내림차순.

    AI는 PROHIBITED 판정을 임의로 해제할 수 없다 — CoupangIntegrationService에는
    risk_level을 낮추는(완화하는) 어떤 메서드도 존재하지 않는다. 정책 자체를
    바꾸려면 CoupangPolicyRule 데이터를 별도 승인 하에 직접 갱신해야 한다.

    POLICY_DATA_UNAVAILABLE(fail-closed): 사용 가능한(VERIFIED·활성·완전·
    유효기간 내) 정책 세트가 하나도 없으면 이 값으로 판정하고 검증을
    차단한다 — 재감사 지적사항 반영: 정책이 0건이어도 SELLABLE로
    fail-open하지 않는다.
    """

    POLICY_DATA_UNAVAILABLE = "POLICY_DATA_UNAVAILABLE"
    PROHIBITED = "PROHIBITED"
    CERTIFICATION_REQUIRED = "CERTIFICATION_REQUIRED"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    SELLABLE = "SELLABLE"

    ALL = (
        POLICY_DATA_UNAVAILABLE,
        PROHIBITED,
        CERTIFICATION_REQUIRED,
        OPERATOR_REVIEW_REQUIRED,
        SELLABLE,
    )

    # 숫자가 클수록 심각 — 여러 규칙이 매칭되면 가장 심각한 것을 채택한다.
    # POLICY_DATA_UNAVAILABLE은 규칙 평가 자체를 막는 최상위 차단 상태다
    # (실제로는 규칙 매칭 이전에 단락 반환되므로 이 SEVERITY 값이 다른
    # 값과 직접 비교되는 경로는 없지만, 방어적으로 최댓값을 부여한다).
    SEVERITY = {
        SELLABLE: 0,
        OPERATOR_REVIEW_REQUIRED: 1,
        CERTIFICATION_REQUIRED: 2,
        PROHIBITED: 3,
        POLICY_DATA_UNAVAILABLE: 4,
    }


class PolicySetStatus:
    """
    정책 세트(CoupangPolicySet)의 인증 상태.

    VERIFIED만 자동 판단(정책 검증)에 사용될 수 있다. DRAFT/EXPIRED/
    REVOKED 상태의 세트만 존재하면 fail-closed한다(POLICY_DATA_UNAVAILABLE).
    """

    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"

    ALL = (DRAFT, VERIFIED, EXPIRED, REVOKED)


class PolicyMatchField:
    """
    CoupangPolicyRule이 매칭하는 대상 필드.

    구조화된 필드(STRUCTURED)가 우선이며, KEYWORD(자유문자 포함 검색)는
    보조 위험 탐지로만 사용한다 — KEYWORD 매칭만으로는 SELLABLE을
    확정할 수 없다(서비스 레벨에서 강제).
    """

    DISPLAY_CATEGORY_CODE = "display_category_code"
    BRAND = "brand"
    OVERSEAS_PURCHASE_AGENCY = "overseas_purchase_agency"
    PCC_NEEDED = "pcc_needed"
    GTIN_MPN_MISSING = "gtin_mpn_missing"
    KEYWORD = "keyword"

    STRUCTURED = (
        DISPLAY_CATEGORY_CODE,
        BRAND,
        OVERSEAS_PURCHASE_AGENCY,
        PCC_NEEDED,
        GTIN_MPN_MISSING,
    )

    ALL = STRUCTURED + (KEYWORD,)


class NoticeStatus:
    """
    상품 고시정보(CoupangProductNotice) 상태. VERIFIED만 Dry Run의
    고시정보 완전성 검증에 사용된다 — 빈 문자열/임시 문자열/자동 생성된
    허위 정보를 허용하지 않기 위해 status와 content 완전성을 모두
    검사한다.
    """

    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"

    ALL = (DRAFT, VERIFIED)


class DryRunOutcome:

    PASSED = "PASSED"
    FAILED = "FAILED"

    ALL = (PASSED, FAILED)


class IntegrationDecisionAction:

    APPROVE_FOR_SUBMISSION = "APPROVE_FOR_SUBMISSION"


# --------------------------------------------------
# 금액/비율 계산 정책
# --------------------------------------------------

# 모든 금액 계산은 Decimal만 사용한다(Float 신규 사용 금지). 원 단위(KRW)
# 소수점 처리 없이도 재사용 가능하도록 2자리까지 반올림하되, 통화 단위
# 자체는 이번 계약에 없으므로(향후 확장 대비) 소수 2자리로 고정한다.
MONEY_QUANTIZE = "0.01"
RATE_QUANTIZE = "0.0001"
ROUNDING = ROUND_HALF_UP

# 마진율 미달 시 정책 검증을 통과시키지 않는다(자동 등록 차단).
# 정책값 — 필요 시 운영 승인 하에 조정한다.
MIN_ACCEPTABLE_MARGIN_RATE = "0.05"

# 공급처 재고 확인이 이 시간(시간 단위)보다 오래되면 신뢰할 수 없다고
# 판단해 노출 재고를 0으로 강제하고 운영자 확인 대상으로 표시한다.
STOCK_FRESHNESS_THRESHOLD_HOURS = 24


__all__ = [
    "SalesMethod",
    "IntegrationStatus",
    "ValidationStatus",
    "RiskLevel",
    "DryRunOutcome",
    "IntegrationDecisionAction",
    "MONEY_QUANTIZE",
    "RATE_QUANTIZE",
    "ROUNDING",
    "MIN_ACCEPTABLE_MARGIN_RATE",
    "STOCK_FRESHNESS_THRESHOLD_HOURS",
]
