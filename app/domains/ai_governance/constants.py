"""
=========================================================
Homez OS

File : app/domains/ai_governance/constants.py

AI Capability Registry(CA-5, 2026-08-21 CTO 지시) — 고정 상수.
=========================================================
"""


class CapabilityType:
    """이 코드베이스에 실제로 존재하는 5가지 유형만 허용한다 — 실제
    구현과 다른 유형으로 등록하지 않는다(예: FAKE_PROVIDER를
    GENERATIVE_AI로 자칭하지 않는다)."""

    RULE_ENGINE = "RULE_ENGINE"
    CALCULATOR = "CALCULATOR"
    GENERATIVE_AI = "GENERATIVE_AI"
    PREDICTIVE_MODEL = "PREDICTIVE_MODEL"
    FAKE_PROVIDER = "FAKE_PROVIDER"
    # 2026-09-07 추가 — 판단·생성 로직 없이 외부 공식 API에서 원시
    # 신호(검색량 시계열 등)만 가져오는 Adapter. 결정은 별도
    # RULE_ENGINE(예: PRODUCT_ANALYSIS)이 담당하고, 이 유형은 그
    # 입력 데이터의 출처가 Fixture가 아니라 실제 외부 API임을
    # 나타낸다(FAKE_PROVIDER와 구분 — 첫 실사례: PRODUCT_DISCOVERY의
    # NaverDataLabTrendAdapter).
    EXTERNAL_DATA_PROVIDER = "EXTERNAL_DATA_PROVIDER"

    ALL = (
        RULE_ENGINE, CALCULATOR, GENERATIVE_AI, PREDICTIVE_MODEL,
        FAKE_PROVIDER, EXTERNAL_DATA_PROVIDER,
    )


class AutomationLevel:
    """V7 자동화 수준 — 실제 상품 제출·가격 변경·발주·환불·정산
    확정은 어떤 capability도 L3을 넘어설 수 없다(AI가 직접 수행하지
    않는다 — 서버의 정책·Permission·EStop·승인 Service가 최종 실행)."""

    L0_VIEW = "L0_VIEW"
    L1_ANALYZE_RECOMMEND = "L1_ANALYZE_RECOMMEND"
    L2_DRAFT_PREPARE = "L2_DRAFT_PREPARE"
    L3_APPROVAL_REQUEST = "L3_APPROVAL_REQUEST"

    ALL = (L0_VIEW, L1_ANALYZE_RECOMMEND, L2_DRAFT_PREPARE, L3_APPROVAL_REQUEST)


class AIResultType:
    """공통 결과 계약 — 모든 capability의 반환값은 이 7종 중 하나로
    분류된다. execution_allowed는 이 결과값이 아니라 항상 별도의
    서버측 정책·Permission·EStop·승인 Service가 결정한다."""

    CONFIRMED_DATA = "CONFIRMED_DATA"
    CALCULATED_RESULT = "CALCULATED_RESULT"
    AI_ESTIMATE = "AI_ESTIMATE"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    EXECUTION_APPROVAL_REQUIRED = "EXECUTION_APPROVAL_REQUIRED"

    ALL = (
        CONFIRMED_DATA, CALCULATED_RESULT, AI_ESTIMATE, EVIDENCE_REQUIRED,
        HUMAN_REVIEW_REQUIRED, POLICY_BLOCKED, EXECUTION_APPROVAL_REQUIRED,
    )


class CapabilityCode:
    """
    Audit(2026-08-21, CTO 후속 지시) — "capability_code 하드코딩
    분산 금지". `capability_catalog.py`의 13개 항목과 이 값들을
    실제로 호출하는 각 도메인 Service가 전부 이 상수를 통해서만
    참조한다 — 문자열 리터럴을 파일마다 따로 적지 않는다.
    """

    PRODUCT_DISCOVERY = "PRODUCT_DISCOVERY"
    PRODUCT_ANALYSIS = "PRODUCT_ANALYSIS"
    PRODUCT_SELECTION = "PRODUCT_SELECTION"
    PROFITABILITY_CALCULATION = "PROFITABILITY_CALCULATION"
    CONTENT_GENERATION = "CONTENT_GENERATION"
    IMAGE_PROCESSING = "IMAGE_PROCESSING"
    CHANNEL_POLICY_ASSIST = "CHANNEL_POLICY_ASSIST"
    SUPPLIER_RECOMMENDATION = "SUPPLIER_RECOMMENDATION"
    PRICING_INVENTORY = "PRICING_INVENTORY"
    ORDER_SHIPMENT_RETURN = "ORDER_SHIPMENT_RETURN"
    SETTLEMENT = "SETTLEMENT"
    OPERATIONS_COORDINATION = "OPERATIONS_COORDINATION"
    USER_GUIDANCE = "USER_GUIDANCE"

    ALL = (
        PRODUCT_DISCOVERY, PRODUCT_ANALYSIS, PRODUCT_SELECTION,
        PROFITABILITY_CALCULATION, CONTENT_GENERATION, IMAGE_PROCESSING,
        CHANNEL_POLICY_ASSIST, SUPPLIER_RECOMMENDATION, PRICING_INVENTORY,
        ORDER_SHIPMENT_RETURN, SETTLEMENT, OPERATIONS_COORDINATION,
        USER_GUIDANCE,
    )


class ExecutionOrigin:
    """
    AG-1(2026-08-21) — 어떤 요청이든 "누가/무엇이 이 실행을 시작했나"
    를 명시적으로 표시한다. AI Capability Registry 통과 여부와
    execution_origin은 서로 다른 축이다 — HUMAN 요청은 AI Capability
    활성 여부와 무관하게 기존 Permission/EStop 계약대로만 동작해야
    한다(AG-0 원칙)."""

    # 사람이 UI/API를 통해 직접 수행하는 일반 업무.
    HUMAN = "HUMAN"
    # 도메인 내부 규칙 엔진이 부작용 없이 스스로 판단(예: 상태 머신
    # 전이 검증) — AI 판단이 아니다.
    INTERNAL_RULE = "INTERNAL_RULE"
    # AI Capability가 생성한 추천/분석 결과 — 그 자체로는 실행되지
    # 않는다(ProposedAction까지만 생성).
    AI_RECOMMENDATION = "AI_RECOMMENDATION"
    # 사용자가 승인한 ProposedAction이 실제 Domain Service를 통해
    # 실행되는 단계.
    APPROVED_AUTOMATION = "APPROVED_AUTOMATION"
    # 부팅 시딩 등 시스템 자체 프로세스(사람 개입 없음, AI 판단도
    # 아님).
    SYSTEM = "SYSTEM"

    ALL = (HUMAN, INTERNAL_RULE, AI_RECOMMENDATION, APPROVED_AUTOMATION, SYSTEM)


class ProposedActionStatus:
    """AG-4(2026-08-21) — ProposedAction 상태 머신. AI는 DRAFT/
    REVIEW_REQUIRED까지만 만들 수 있다 — APPROVED/EXECUTED로의 전이는
    사람의 명시적 승인 또는 그 승인을 반영하는 서버 로직만 수행한다."""

    DRAFT = "DRAFT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"
    INVALIDATED = "INVALIDATED"

    ALL = (
        DRAFT, REVIEW_REQUIRED, APPROVED, REJECTED, EXPIRED, EXECUTED,
        INVALIDATED,
    )
    # AI가 스스로 만들 수 있는 상태(생성 시점) — 그 이후 전이는 전부
    # 사람의 명시적 액션(승인/거절) 또는 시스템의 시간 기반 만료만
    # 수행한다.
    AI_CREATABLE = (DRAFT, REVIEW_REQUIRED)
    OPEN = (DRAFT, REVIEW_REQUIRED, APPROVED)


# Gate AI-F(2026-08-22) — 이 action_type들은 금액·재고를 직접 움직이는
# 실행으로 이어지므로, ProposedActionService.approve()가 recent-auth
# (현재 비밀번호 재확인) 없이는 승인 자체를 거부한다. 새 고위험
# action_type을 추가하는 도메인은 이 집합에 등록해야 한다 — 등록하지
# 않으면 fail-open이 아니라 단지 recent-auth 요구가 누락될 뿐이므로,
# 신규 고위험 제안을 추가할 때는 항상 이 목록도 함께 갱신해야 한다.
HIGH_RISK_ACTION_TYPES = (
    "PRICE_CHANGE_PROPOSAL",
    "INVENTORY_REPLENISHMENT_PROPOSAL",
    "PURCHASE_ORDER_PROPOSAL",
    "REFUND_REVIEW",
    "SETTLEMENT_DIFFERENCE_REVIEW",
)


__all__ = [
    "CapabilityType", "AutomationLevel", "AIResultType", "CapabilityCode",
    "ExecutionOrigin", "ProposedActionStatus",
]
