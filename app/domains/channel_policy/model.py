"""
=========================================================
Homez OS

File : app/domains/channel_policy/model.py

채널 정책 엔진(CP-2, 2026-08-21 CTO 지시) — SQLAlchemy Model.
FK를 쓰지 않는다(이 코드베이스 전역 컨벤션 — 전부 논리 참조 정수
컬럼). company_id는 해당 회사가 실제로 소유하는 테이블에만 둔다
— ChannelPolicyRule은 전역 정책 카탈로그(모든 회사 공유)라
company_id가 없다(MarketplaceFulfillmentCapability와 동일한 원칙).
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

MONEY = Numeric(14, 2, asdecimal=True)
RATE = Numeric(6, 4, asdecimal=True)


class ChannelPolicyRule(Base):
    """
    채널 정책 규칙 카탈로그 — 전역 공유(회사별 데이터 아님). 실제
    공식 채널 정책·공식 API 문서로 확인된 근거만 담는다.

    `active=True`는 반드시 `official_source_url`과 `verified_at`이
    모두 채워져 있을 때만 서비스 레벨에서 허용한다(seed 시점에
    강제, DB CHECK 제약은 두지 않는다 — 이 코드베이스 전역 컨벤션).
    공식 근거를 아직 확인하지 못한 규칙은 `active=False`로 두고
    `source_title`에 "POLICY_EVIDENCE_REQUIRED"를 기록한다 — 마커가
    없다는 이유로 이 카탈로그에서 완전히 빼지 않는다(문서화만 하고
    실제 판정에는 관여시키지 않는다).
    """

    __tablename__ = "channel_policy_rules"
    __table_args__ = (
        UniqueConstraint(
            "channel", "rule_code",
            name="uq_channel_policy_rules_channel_rule_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    rule_code: Mapped[str] = mapped_column(String(80), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )

    # JSON 문자열 배열. ["ALL"]이면 카테고리 무관 항상 적용.
    category_scope_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='["ALL"]',
    )

    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    validation_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # JSON 문자열 배열(nullable — 해당 검증 유형에 필요 없으면 없음).
    required_fields_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    required_evidence_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    official_source_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    source_title: Mapped[str | None] = mapped_column(
        String(300), nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 채널별 정책 프로필 버전 — 규칙 집합이 바뀌면(추가/삭제/severity
    # 변경) 이 값을 올린다. ChannelPolicyEvaluation이 평가 시점의
    # 이 값을 스냅샷으로 저장해, 이후 버전이 달라지면 STALE 판정의
    # 근거가 된다.
    profile_version: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CompanyChannelPolicySettings(Base):
    """
    회사별 수익성·구매 한도 기준 — 정책 적합성(ChannelPolicyRule)과
    완전히 분리된 축이다. 이 설정은 마진 계산 결과를 "회사 기준
    충족 여부"로 판정하는 데만 쓰이고, 채널 정책 판정에는 전혀
    관여하지 않는다.

    설정하지 않은 값(NULL)은 "이 한도를 아직 정하지 않았다"는 뜻이지
    "제한 없음"이 아니다 — margin_estimator.py가 NULL 항목은 "미설정"
    으로 명시하고 임의로 통과시키지 않는다.
    """

    __tablename__ = "company_channel_policy_settings"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            name="uq_company_channel_policy_settings_company",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    min_target_margin_rate: Mapped[object | None] = mapped_column(
        RATE, nullable=True,
    )
    min_profit_per_order: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )
    max_initial_purchase_amount: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )
    max_moq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_lead_time_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    max_return_shipping_cost: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )

    # JSON 문자열 배열(카테고리 키워드) — 둘 다 기본 빈 배열.
    allowed_categories_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )
    forbidden_categories_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )

    safety_stock_buffer: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # 논리 참조 (users.id)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)

    # 낙관적 동시성 — PATCH 요청은 항상 이 값을 함께 보내야 하고,
    # 불일치하면 409로 거부한다(다른 도메인의 version 컬럼과 동일 패턴).
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class ChannelPolicyEvaluation(Base):
    """
    채널 정책 평가 이력(append-only — 이 행 자체가 감사 기록이자
    "정책 스냅샷"). 같은 (product_candidate, channel, company) 조합을
    다시 평가하면 새 행이 추가될 뿐 기존 행은 절대 수정하지 않는다
    — MarketplaceFulfillmentEligibility와 동일한 설계 철학. "현재"
    판정은 항상 created_at 기준 최신 행이다.
    """

    __tablename__ = "channel_policy_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (product_candidates.id)
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    channel: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )

    # 5종 판정 결과(ChannelPolicyResult).
    result: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True,
    )

    # 평가 시점 이 채널의 활성 규칙 집합 profile_version — 이후 규칙이
    # 바뀌면(버전이 달라지면) 이 평가는 STALE 취급된다.
    policy_profile_version: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )

    # 평가에 사용된 규칙별 결과 스냅샷(JSON 배열) — 재현 가능성을
    # 위해 완성 문장이 아니라 구조화된 값만 담는다: [{rule_code,
    # severity, matched, satisfied, missing_fields, missing_evidence}]
    rule_results_json: Mapped[str] = mapped_column(
        Text, nullable=False,
    )

    # CA-1(2026-08-21) — 평가 당시 입력(상품 콘텐츠·가격·옵션·배송·
    # 이미지)의 SHA-256 지문(app/domains/channel_policy/fingerprint.py).
    # 제출 직전 이 값이 "지금 다시 계산한 지문"과 정확히 일치해야만
    # 이 평가를 근거로 제출을 진행할 수 있다 — 지문이 다르면(상품·
    # 가격·옵션·이미지·배송정보가 바뀌었다는 뜻) 이 평가는 자동으로
    # 무효 취급된다(재평가 필요).
    input_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    # 논리 참조 (users.id) — 수동 재평가를 요청한 운영자. 자동
    # 트리거(채널 선택 시 자동 활성화)는 NULL.
    evaluated_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


__all__ = [
    "ChannelPolicyRule",
    "CompanyChannelPolicySettings",
    "ChannelPolicyEvaluation",
]
