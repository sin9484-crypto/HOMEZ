"""
=========================================================
Homez OS

File : app/domains/decision/model.py

HOMEZ V4 Decision AI — Model

5개 테이블로 구성한다(요청에서 열거한 8개 개념 — DecisionPolicy/
DecisionEvaluation/DecisionScore/DecisionEvidence/
DecisionRecommendation/DecisionReview/DecisionOverride/
DecisionAuditLog — 를 불필요하게 쪼개지 않기 위해 통합한 설계다):

  - DecisionPolicy: 정책 세트(가중치·임계값 버전 관리) — Coupang의
    CoupangPolicySet과 동일한 거버넌스 패턴(VERIFIED만 사용 가능).
  - DecisionEvaluation: 평가 1건(축별 점수 대신 총점/신뢰도/추천/
    입력 스냅샷/멱등키를 보관) — DecisionEvidence·
    DecisionRecommendation을 별도 테이블로 쪼개지 않고 이 테이블의
    필드로 흡수했다(추천 결과·사유는 평가 자체의 속성이지 별도
    엔터티가 아니라고 판단).
  - DecisionScore: 항목별 점수+근거+위험 플래그(축마다 1행,
    append-only) — "항목별 점수"와 "근거"를 분리하지 않고 한 행에
    묶었다(요청의 "불필요한 테이블 분리는 피하되" 지침 반영).
  - DecisionReview: 사람의 승인·보류·거절 **및** override를 함께
    기록한다(append-only) — override는 별도 테이블 대신 이 테이블의
    action="OVERRIDE" + previous_value/new_value/override_reason
    컬럼으로 표현한다(개념적으로 override도 "사람의 결정 이력"의
    한 종류이기 때문).
  - DecisionAuditLog: AI 평가 생성과 사람의 결정을 모두 포괄하는
    단일 감사 이벤트 스트림(append-only) — "변경 이력"이라는 요청
    요구사항을 하나의 조회 가능한 타임라인으로 충족한다.

ProductCandidate와 동일하게 FK를 쓰지 않고 전부 논리 참조 컬럼만
사용한다. Funding/Settlement/Order/Purchase Model은 import하지 않는다
— 평가와 추천만 하고 실제 자금 이동·주문·발주를 실행하지 않는다.

2026-08-14 테넌트 격리 감사 — 회사 스코프 판단:
  - 회사 스코프(company_id 추가): DecisionEvaluation(+Score/Review/
    AuditLog 전부 비정규화). 평가 입력·점수·예상매출·가용자금·승인/
    보류/거절/override는 전부 그 평가를 요청한 회사만의 판단·데이터다
    — 이것이 이번 감사에서 지적된 실제 노출 대상이다.
  - 전역 유지(company_id 없음): DecisionPolicy — CoupangPolicySet과
    동일한 거버넌스 패턴(플랫폼이 배포하는 전역 기본 템플릿)을 따른다.

2026-08-15 V7 Gate 2 — DecisionPolicy 전역/회사 분리(CTO 지시 원문
요구사항 1):
  - DecisionPolicy는 "전역 시스템 기본 템플릿"으로 의미를 그대로
    유지한다(위 R13 판단 그대로) — 모든 회사가 기본으로 쓸 수 있는
    플랫폼 제공 정책이다.
  - 신규 CompanyDecisionPolicy: "회사별 적용 정책" — 회사가 전역
    템플릿을 커스터마이징(가중치·임계값 조정)하고 싶을 때 만드는
    회사 전용 정책이다. UNIQUE(company_id, policy_set_id)로 같은
    회사 안에서 policy_set_id 중복을 막는다. DecisionPolicy와 완전히
    동일한 필드 shape(가중치 JSON, 임계값, 유효기간, VERIFIED 상태
    거버넌스)를 그대로 재사용해 "사용 가능(VERIFIED·활성·완전·
    유효기간 내)" 판단 로직(DecisionService._is_policy_usable)을
    두 클래스 모두에 duck-typing으로 그대로 적용할 수 있게 했다.
  - DecisionEvaluation.policy_id(decision_policies.id 논리 참조)는
    이제 nullable이다 — 이 평가가 전역 템플릿으로 평가됐으면 이 값을
    채우고, 회사 커스텀 정책으로 평가됐으면 NULL로 둔 채 신규
    company_policy_id(company_decision_policies.id 논리 참조)를
    채운다. 신규 policy_source("GLOBAL"|"COMPANY") 컬럼이 어느
    테이블을 참조해야 하는지 판별하는 명시적 판별자(discriminator)
    다 — policy_id/company_policy_id 두 컬럼은 서로 다른 테이블의
    autoincrement id라 판별자 없이는 값만으로 어느 테이블 소속인지
    알 수 없다(다형 연관관계, polymorphic association). 이 세 컬럼
    조합이 "이 평가가 정확히 어느 정책 버전을 근거로 이뤄졌는가"를
    감사 가능하게(auditable) 만든다 — 요청 원문의 명시적 요구사항.
  - 정책 해석 우선순위(DecisionService._resolve_policy): 이 평가를
    요청한 회사에 사용 가능한 CompanyDecisionPolicy가 있으면 그것을
    우선 사용하고(회사가 자기 정책으로 "선택"한 것), 없으면 전역
    DecisionPolicy로 폴백한다(회사가 아무 커스텀도 만들지 않았으면
    전역 템플릿을 그대로 쓰는 것 — 요청 원문 "전역 템플릿을 그대로
    쓸 수도, 자기 회사용 커스텀 정책을 만들 수도 있게"). 회사
    정책도 전역 정책도 없으면 기존과 동일하게 fail-closed로 평가
    자체를 차단한다(임의의 기본 가중치로 평가하지 않는다).
=========================================================
"""

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

SCORE = Numeric(9, 4, asdecimal=True)
MONEY = Numeric(14, 2, asdecimal=True)


class DecisionPolicy(Base):
    """
    평가 정책 세트 — 축별 가중치, 최소 점수/신뢰도 임계값의 버전.

    사용 가능(VERIFIED·활성·완전·유효기간 내) 조건은
    app/domains/coupang의 CoupangPolicySet과 동일한 판단 기준을
    따른다(서비스 레벨에서 구현). 사용 가능한 정책이 없으면 평가
    자체를 만들지 않는다(fail-closed) — 임의의 기본 가중치로
    평가하지 않는다.
    """

    __tablename__ = "decision_policies"
    __table_args__ = (
        UniqueConstraint(
            "policy_set_id",
            name="uq_decision_policies_policy_set_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    policy_set_id: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", index=True,
    )
    is_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )

    # 축별 가중치를 JSON 문자열로 보관한다(합이 1.0000이어야 함,
    # 서비스 레벨에서 검증). 예: {"REVENUE_POTENTIAL": 0.15, ...}
    axis_weights_json: Mapped[str] = mapped_column(Text, nullable=False)

    min_approve_total_score: Mapped[object] = mapped_column(
        SCORE, nullable=False,
    )
    min_confidence_for_recommendation: Mapped[object] = mapped_column(
        SCORE, nullable=False,
    )

    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class CompanyDecisionPolicy(Base):
    """
    회사별 적용 정책(2026-08-15 V7 Gate 2) — DecisionPolicy(전역 기본
    템플릿)와 완전히 동일한 shape의 회사 전용 정책.

    "사용 가능(VERIFIED·활성·완전·유효기간 내)" 판단은
    DecisionService._is_policy_usable()이 DecisionPolicy와 동일하게
    적용한다(필드 이름이 같아 duck-typing으로 재사용). 사용 가능한
    회사 정책이 있으면 그 회사의 평가는 항상 이 정책을 전역 템플릿보다
    우선 사용한다(DecisionService._resolve_policy).

    base_policy_id는 선택적 계보(lineage) 정보다 — 이 회사 정책이
    특정 전역 템플릿을 복제·수정해서 만들어졌다면 그 근원을 기록하되,
    이 컬럼이 평가 시점의 정책 선택 로직에 관여하지는 않는다(순수
    감사/추적용).
    """

    __tablename__ = "company_decision_policies"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "policy_set_id",
            name="uq_company_decision_policies_company_policy_set",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 이 정책을 소유한 회사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    policy_set_id: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", index=True,
    )
    is_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )

    axis_weights_json: Mapped[str] = mapped_column(Text, nullable=False)

    min_approve_total_score: Mapped[object] = mapped_column(
        SCORE, nullable=False,
    )
    min_confidence_for_recommendation: Mapped[object] = mapped_column(
        SCORE, nullable=False,
    )

    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 논리 참조 (decision_policies.id) — 이 회사 정책이 파생된 전역
    # 템플릿(선택적, 순수 계보 정보 — 평가 로직에 관여하지 않음).
    base_policy_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class DecisionEvaluation(Base):
    """
    평가 1건(현재 상태 투영). 동일 candidate_id + policy_version +
    input_fingerprint 조합은 idempotency_key로 멱등 처리된다.
    """

    __tablename__ = "decision_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_decision_evaluations_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 이 평가를 요청한 회사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (product_candidates.id)
    candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (decision_policies.id) — policy_source="GLOBAL"일 때만
    # 채워진다(2026-08-15 V7 Gate 2, DecisionPolicy 분리로 nullable로
    # 전환 — 이전에는 항상 전역 정책만 있어 NOT NULL이었다).
    policy_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    # 논리 참조 (company_decision_policies.id) — policy_source=
    # "COMPANY"일 때만 채워진다(2026-08-15 V7 Gate 2 신규).
    company_policy_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    # "GLOBAL" | "COMPANY" — policy_id/company_policy_id 중 어느
    # 컬럼이 유효한지 가리키는 판별자(2026-08-15 V7 Gate 2 신규).
    policy_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="GLOBAL",
    )
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # 평가에 사용된 입력 원본 스냅샷(JSON 문자열) — 재현·감사용.
    input_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 입력 스냅샷의 결정적 해시(SHA-256 hex) — 동일 입력+정책 버전
    # 재호출을 멱등 처리하기 위한 키의 구성요소다.
    input_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )

    total_score: Mapped[object] = mapped_column(SCORE, nullable=False)
    confidence: Mapped[object] = mapped_column(SCORE, nullable=False)
    recommendation: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )
    recommendation_reason: Mapped[str] = mapped_column(
        String(1000), nullable=False,
    )

    # Emergency Stop 등 안전 장치에 의해 평가 자체가 차단됐는지 여부.
    blocked_by_safety: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    safety_block_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    # PENDING_REVIEW / REVIEWED — 사람의 결정이 기록되면 REVIEWED.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING_REVIEW", index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    # 생성 주체 — "AI" 고정 문자열(사람이 직접 만든 평가는 없음) 또는
    # 향후 다른 평가기 식별자.
    created_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="AI",
    )
    # AI 경계 계약 — 어떤 evaluator/prompt/model 버전이 이 평가를
    # 만들었는지 기록한다(가짜 AI를 실제 AI로 보고하지 않기 위해
    # evaluator_kind를 명시적으로 남긴다: 예 "fixture" | "deterministic").
    evaluator_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class DecisionScore(Base):
    """항목별 점수 + 근거 + 위험 플래그(축마다 1행, append-only)."""

    __tablename__ = "decision_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모(decision_evaluations)에서 비정규화.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (decision_evaluations.id)
    evaluation_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    axis: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_score: Mapped[object] = mapped_column(SCORE, nullable=False)
    weight: Mapped[object] = mapped_column(SCORE, nullable=False)
    weighted_score: Mapped[object] = mapped_column(SCORE, nullable=False)
    confidence: Mapped[object] = mapped_column(SCORE, nullable=False)

    data_sufficient: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    risk_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    evidence_text: Mapped[str] = mapped_column(String(1000), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class DecisionReview(Base):
    """
    사람의 승인/보류/거절 및 override(append-only).

    override인 경우 previous_value/new_value/override_reason을 함께
    남긴다 — 권한, 사유, 이전 값, 새 값, 시각을 전부 이 한 행에서
    추적할 수 있다.
    """

    __tablename__ = "decision_reviews"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_decision_reviews_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모에서 비정규화.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (decision_evaluations.id)
    evaluation_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # APPROVE / HOLD / REJECT / OVERRIDE
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # 논리 참조 (users.id)
    reviewer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    memo: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # OVERRIDE 전용(그 외 액션은 전부 NULL)
    override_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )
    previous_value: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    new_value: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    decided_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class DecisionAuditLog(Base):
    """AI 평가 생성과 사람의 결정을 모두 포괄하는 단일 감사 이벤트 스트림."""

    __tablename__ = "decision_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모에서 비정규화. evaluation_id가
    # 없을 수 있는 EVALUATION_BLOCKED_BY_SAFETY 행도 candidate_id와
    # 함께 항상 company_id를 채운다(어느 회사의 시도인지는 항상 안다).
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (decision_evaluations.id). Emergency Stop 등으로 평가
    # 자체가 생성되지 못하고 차단된 경우에는 evaluation_id가 없을 수
    # 있어(EVALUATION_BLOCKED_BY_SAFETY) nullable로 둔다.
    evaluation_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    # 논리 참조 (product_candidates.id) — evaluation_id 없이도 후보
    # 기준으로 감사 이력을 조회할 수 있도록 중복 보관.
    candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # "AI" 또는 논리 참조(users.id)를 문자열로 기록.
    actor: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_summary: Mapped[str] = mapped_column(
        String(1000), nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


__all__ = [
    "DecisionPolicy",
    "CompanyDecisionPolicy",
    "DecisionEvaluation",
    "DecisionScore",
    "DecisionReview",
    "DecisionAuditLog",
]
