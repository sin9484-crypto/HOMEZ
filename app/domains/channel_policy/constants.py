"""
=========================================================
Homez OS

File : app/domains/channel_policy/constants.py

채널 정책 엔진(CP-2, 2026-08-21 CTO 지시) — 고정 상수.

이 Domain은 app/domains/marketplace_listing의 기존 인프라(Capability/
Selection/required_fields_schemas.py — API payload 구조 검증)를
대체하지 않는다. 그 위에 얹는 "상품·카테고리 정책 계층"이다:
required_fields_schemas.py는 "쿠팡 API가 이 필드를 요구한다"를
검증하고, 이 Domain은 "이 카테고리는 애초에 판매 금지다" /
"이 카테고리는 인증서가 있어야 한다" / "상품정보제공고시가 필요하다"
같은 상품 콘텐츠·법규 정책을 검증한다. 정책 적합성(이 Domain)과
수익성(margin_estimator.py)은 결과를 절대 섞지 않는다 — BLOCK된
상품은 마진이 아무리 높아도 추천·제출 대상에서 제외된다.
=========================================================
"""


class ChannelPolicyResult:
    """5종 정책 판정 결과. 정책 적합성 전용 — 수익성 점수는 이 값에
    전혀 영향을 주지 않는다(섞지 않는다)."""

    CHANNEL_ELIGIBLE = "CHANNEL_ELIGIBLE"
    CHANNEL_ELIGIBLE_WITH_ACTIONS = "CHANNEL_ELIGIBLE_WITH_ACTIONS"
    CHANNEL_DATA_REQUIRED = "CHANNEL_DATA_REQUIRED"
    CHANNEL_POLICY_BLOCKED = "CHANNEL_POLICY_BLOCKED"
    CHANNEL_POLICY_STALE = "CHANNEL_POLICY_STALE"

    ALL = (
        CHANNEL_ELIGIBLE,
        CHANNEL_ELIGIBLE_WITH_ACTIONS,
        CHANNEL_DATA_REQUIRED,
        CHANNEL_POLICY_BLOCKED,
        CHANNEL_POLICY_STALE,
    )

    # 이 상태들만 제출(승인 요청) 진행이 허용된다 — 서비스 레벨에서
    # 강제(엔진 자체가 아니라 submission 경계에서 재확인).
    SUBMITTABLE = (CHANNEL_ELIGIBLE, CHANNEL_ELIGIBLE_WITH_ACTIONS)


class PolicySeverity:
    """규칙 하나가 위반됐을 때의 심각도. BLOCKING은 절대 우회 불가."""

    BLOCKING = "BLOCKING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    ADVISORY = "ADVISORY"

    ALL = (BLOCKING, ACTION_REQUIRED, ADVISORY)


class PolicyValidationType:
    """규칙이 무엇을 근거로 판정하는지 — engine.py가 이 값으로 실제
    평가 로직을 분기한다."""

    # category_scope 키워드가 category_hint/product_name에 매치되면
    # 그 자체로 위반(절대 판매 금지 카테고리).
    CATEGORY_PROHIBITED = "CATEGORY_PROHIBITED"

    # category_scope 매치 시 required_evidence가 전부 확인돼야 통과
    # (인증서·허가·신고 등).
    CATEGORY_RESTRICTED_EVIDENCE = "CATEGORY_RESTRICTED_EVIDENCE"

    # 원산지가 금지국이면 위반.
    ORIGIN_COUNTRY_PROHIBITED = "ORIGIN_COUNTRY_PROHIBITED"

    # 카테고리와 무관하게 항상 적용, required_fields가 채널 제출
    # 데이터(예: MarketplaceFulfillmentSelection.required_fields_json)에
    # 전부 있어야 통과.
    STRUCTURAL_FIELD_REQUIRED = "STRUCTURAL_FIELD_REQUIRED"

    # 카테고리와 무관하게 항상 적용, required_evidence가 전부 확인돼야
    # 통과(예: 상품정보제공고시).
    EVIDENCE_REQUIRED_GENERIC = "EVIDENCE_REQUIRED_GENERIC"

    ALL = (
        CATEGORY_PROHIBITED,
        CATEGORY_RESTRICTED_EVIDENCE,
        ORIGIN_COUNTRY_PROHIBITED,
        STRUCTURAL_FIELD_REQUIRED,
        EVIDENCE_REQUIRED_GENERIC,
    )


class PolicyRuleStatus:
    """규칙 카탈로그 행 자체의 신뢰 상태. 공식 근거를 확인하지 못한
    규칙은 EVIDENCE_REQUIRED로 남기고 active=True로 켜지 않는다 —
    마커가 없다는 이유로 삭제하지 않는 것과 동일한 원칙(문서에는
    남기되 실제 판정에는 관여하지 않게 한다)."""

    ACTIVE = "ACTIVE"
    POLICY_EVIDENCE_REQUIRED = "POLICY_EVIDENCE_REQUIRED"
    RETIRED = "RETIRED"

    ALL = (ACTIVE, POLICY_EVIDENCE_REQUIRED, RETIRED)


# category_scope에 이 값이 들어있으면 카테고리와 무관하게 항상 매치.
CATEGORY_SCOPE_ALL = "ALL"

CHANNEL_POLICY_ENGINE_VERSION = "1.0.0"

# Audit(2026-08-21) — 이 채널에 대해 규칙 카탈로그 행이 단 하나도
# 없을 때(active 무관) `evaluate_and_record()`가 채우는 합성 rule_
# code. 실제 규칙 위반이 아니라 "카탈로그 시딩 자체가 안 됐다"는
# 사실을 rule_results에 정직하게 남기기 위함 — 화면에서 "일반 정책
# 위반"과 구분해 안내할 수 있게 한다.
CHANNEL_POLICY_CATALOG_NOT_SEEDED_RULE_CODE = "CHANNEL_POLICY_CATALOG_NOT_SEEDED"


__all__ = [
    "ChannelPolicyResult",
    "PolicySeverity",
    "PolicyValidationType",
    "PolicyRuleStatus",
    "CATEGORY_SCOPE_ALL",
    "CHANNEL_POLICY_ENGINE_VERSION",
    "CHANNEL_POLICY_CATALOG_NOT_SEEDED_RULE_CODE",
]
