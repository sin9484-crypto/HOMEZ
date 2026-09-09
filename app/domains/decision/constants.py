"""
=========================================================
Homez OS

File : app/domains/decision/constants.py

HOMEZ V4 Decision AI — 고정 상수

V4는 자동 판매 실행 단계가 아니다. AI는 분석·점수·추천·근거를
생성하지만, 최종 상태 전환(APPROVED 등)은 사람이 승인해야 한다
(app/domains/product_candidate와 동일한 "운영자 승인 없이 자동
전환 불가" 원칙을 그대로 따른다).
=========================================================
"""

from decimal import ROUND_HALF_UP


class DecisionPolicyStatus:
    """
    정책 세트 인증 상태(app/domains/coupang/constants.py의
    PolicySetStatus와 동일한 철학) — VERIFIED만 자동 평가에 사용된다.
    """

    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"

    ALL = (DRAFT, VERIFIED, EXPIRED, REVOKED)


class PolicySource:
    """
    DecisionEvaluation.policy_source 값(2026-08-15 V7 Gate 2) —
    이 평가가 전역 DecisionPolicy로 이뤄졌는지, 회사 전용
    CompanyDecisionPolicy로 이뤄졌는지 가리키는 판별자.
    """

    GLOBAL = "GLOBAL"
    COMPANY = "COMPANY"

    ALL = (GLOBAL, COMPANY)


class ScoreAxis:
    """평가 축 12종 — 고정, 버전 관리되는 가중치와 짝지어 사용한다."""

    REVENUE_POTENTIAL = "REVENUE_POTENTIAL"
    MARGIN_AND_FEES = "MARGIN_AND_FEES"
    PRICE_COMPETITIVENESS = "PRICE_COMPETITIVENESS"
    DEMAND_TREND_STRENGTH = "DEMAND_TREND_STRENGTH"
    COMPETITION_INTENSITY = "COMPETITION_INTENSITY"
    SUPPLY_STABILITY = "SUPPLY_STABILITY"
    INVENTORY_SHIPPING_RISK = "INVENTORY_SHIPPING_RISK"
    RETURN_CLAIM_RISK = "RETURN_CLAIM_RISK"
    POLICY_PROHIBITED_RISK = "POLICY_PROHIBITED_RISK"
    BRAND_IP_RISK = "BRAND_IP_RISK"
    DATA_RELIABILITY = "DATA_RELIABILITY"
    FUNDING_LIMIT_IMPACT = "FUNDING_LIMIT_IMPACT"

    ALL = (
        REVENUE_POTENTIAL,
        MARGIN_AND_FEES,
        PRICE_COMPETITIVENESS,
        DEMAND_TREND_STRENGTH,
        COMPETITION_INTENSITY,
        SUPPLY_STABILITY,
        INVENTORY_SHIPPING_RISK,
        RETURN_CLAIM_RISK,
        POLICY_PROHIBITED_RISK,
        BRAND_IP_RISK,
        DATA_RELIABILITY,
        FUNDING_LIMIT_IMPACT,
    )

    # 이 두 축은 "위험"이 아니라 정책/안전 차단 신호다 — 점수로 상쇄되지
    # 않고 이 축 자체가 임계 미달이면 평가 전체가 REVIEW_REQUIRED로
    # 강등된다(서비스 레벨에서 강제).
    HARD_BLOCK_AXES = (POLICY_PROHIBITED_RISK,)


class EvaluationRecommendation:
    """
    AI가 낼 수 있는 추천 결과. RECOMMEND_APPROVE라도 상태를 자동으로
    APPROVED로 바꾸지 않는다 — 사람의 DecisionReview가 있어야 한다.
    """

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    RECOMMEND_REJECT = "RECOMMEND_REJECT"
    RECOMMEND_HOLD = "RECOMMEND_HOLD"
    RECOMMEND_APPROVE = "RECOMMEND_APPROVE"

    ALL = (
        INSUFFICIENT_DATA,
        REVIEW_REQUIRED,
        RECOMMEND_REJECT,
        RECOMMEND_HOLD,
        RECOMMEND_APPROVE,
    )


class EvaluationStatus:

    PENDING_REVIEW = "PENDING_REVIEW"
    REVIEWED = "REVIEWED"

    ALL = (PENDING_REVIEW, REVIEWED)


class ReviewAction:

    APPROVE = "APPROVE"
    HOLD = "HOLD"
    REJECT = "REJECT"
    OVERRIDE = "OVERRIDE"

    ALL = (APPROVE, HOLD, REJECT, OVERRIDE)

    # 사람의 결정이 반영되었다고 볼 수 있는 액션 — 한 번 결정되면
    # 재평가 없이는 다시 결정할 수 없다.
    TERMINAL = (APPROVE, HOLD, REJECT)


class AuditEventType:

    EVALUATION_CREATED = "EVALUATION_CREATED"
    EVALUATION_BLOCKED_BY_SAFETY = "EVALUATION_BLOCKED_BY_SAFETY"
    REVIEW_DECIDED = "REVIEW_DECIDED"
    OVERRIDE_APPLIED = "OVERRIDE_APPLIED"

    ALL = (
        EVALUATION_CREATED,
        EVALUATION_BLOCKED_BY_SAFETY,
        REVIEW_DECIDED,
        OVERRIDE_APPLIED,
    )


# --------------------------------------------------
# 계산 정책
# --------------------------------------------------

MONEY_QUANTIZE = "0.01"
SCORE_QUANTIZE = "0.0001"
ROUNDING = ROUND_HALF_UP

# 항목 점수 범위 — 0(최악)~100(최상)로 고정한다.
SCORE_MIN = 0
SCORE_MAX = 100

# 총점이 이 값 미만이면 RECOMMEND_APPROVE를 낼 수 없다(정책값).
MIN_APPROVE_TOTAL_SCORE = "70.0000"

# 신뢰도가 이 값 미만이면(축별 confidence 평균) 자동으로
# INSUFFICIENT_DATA로 강등한다.
MIN_CONFIDENCE_FOR_RECOMMENDATION = "0.5000"


__all__ = [
    "DecisionPolicyStatus",
    "PolicySource",
    "ScoreAxis",
    "EvaluationRecommendation",
    "EvaluationStatus",
    "ReviewAction",
    "AuditEventType",
    "MONEY_QUANTIZE",
    "SCORE_QUANTIZE",
    "ROUNDING",
    "SCORE_MIN",
    "SCORE_MAX",
    "MIN_APPROVE_TOTAL_SCORE",
    "MIN_CONFIDENCE_FOR_RECOMMENDATION",
]
