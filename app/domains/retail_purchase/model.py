"""
=========================================================
Homez OS

File : app/domains/retail_purchase/model.py

Gate RP-1(2026-08-22) — 대형 쇼핑몰 구매 실행 기록. 소비자 계정
Credential·개인정보·결제정보는 이 테이블 어디에도 저장하지 않는다
(카드번호·CVC·결제 PIN·쇼핑몰 비밀번호 전부 금지 — 지시문 16번
"금지" 항목).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.retail_purchase.constants import RetailPurchaseOrderStatus
from app.domains.retail_purchase.constants import (
    RetailPurchaseUncertainHandlingPolicy,
)


class RetailPurchaseOrder(Base):
    """
    쿠팡 등 원 주문(source_order_id) 1건에 대응하는 대형 쇼핑몰 구매
    실행 1건. 하나의 원 주문이 품목별로 여러 RetailPurchaseOrder를
    가질 수 있다(app/domains/purchase/model.py::Purchase와 동일한
    설계 원칙).
    """

    __tablename__ = "retail_purchase_orders"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_retail_purchase_orders_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 논리 참조 (orders.id) — 쿠팡 등 원 주문.
    source_order_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    provider_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # 논리 참조 (payment_account_references.id), nullable — 실행 전
    # 정책 검사 단계에서는 아직 확정 안 될 수 있다.
    payment_account_reference_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    product_url: Mapped[str] = mapped_column(String(1000), nullable=False)

    external_product_id: Mapped[str] = mapped_column(String(200), nullable=False)

    selected_option: Mapped[str | None] = mapped_column(String(500), nullable=True)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # 동일상품 판정 신뢰도(0.0~1.0)와 근거 요약 — 이미지 유사도·상품명
    # 부분일치만으로 확정하지 않는다는 원칙의 감사 흔적.
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    match_evidence_json: Mapped[str] = mapped_column(
        String(4000), nullable=False, default="[]",
    )

    expected_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    actual_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    expected_net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)

    external_order_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    external_order_number: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False,
        default=RetailPurchaseOrderStatus.PROPOSED, index=True,
    )

    tracking_company: Mapped[str | None] = mapped_column(String(100), nullable=True)

    tracking_number: Mapped[str | None] = mapped_column(String(200), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)

    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 논리 참조 (funding_holds.id) — 예산 예약 추적.
    funding_hold_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 이 주문의 POLICY_CHECKED 판정에 실제로 쓰인 정책 설정의
    # fingerprint(SHA-256) — 정책 검사 이후 관리자가 정책을 바꾸면
    # 이 값이 최신 정책과 달라지고, 이후 예산예약/견적/발주 단계에서
    # 재검증 없이 예전 판정을 그대로 밀어붙이지 못하도록 차단하는 데
    # 쓴다(app/domains/retail_purchase/service.py::_current_policy_
    # fingerprint 참고).
    policy_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    # UNCERTAIN 상태에서 "정책상 자동 예산 반환 시점"이 지나 이미
    # 예산을 반환했는지 — 구매 자체는 절대 재시도하지 않으며, 이
    # 플래그는 오직 예산 반환 여부만 추적한다(app/domains/retail_
    # purchase/constants.py::RetailPurchaseUncertainHandlingPolicy).
    uncertain_budget_released: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PaymentAccountReference(Base):
    """
    구매대행/공식 API Provider 쪽 계정 메타데이터만 저장한다 — 실제
    API 키·Secret은 app/core/windows_credential_store.py를 그대로
    재사용해 별도 저장하고(external_account_reference가 그 target
    name), 이 테이블에는 참조만 남는다(app/domains/store_connection/
    model.py의 credential_reference 패턴과 동일). 소비자 쇼핑몰
    계정 아이디·비밀번호는 이 테이블의 대상이 아니다(지시문 범위
    축소로 인해 이 테이블은 오직 공식 API/구매대행 계약 계정만
    다룬다).
    """

    __tablename__ = "payment_account_references"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "provider_code",
            name="uq_payment_account_references_company_provider",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    provider_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Windows Credential Manager target name — 실제 Secret은 여기
    # 없음.
    external_account_reference: Mapped[str | None] = mapped_column(
        String(300), nullable=True,
    )

    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    balance_or_credit_snapshot: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class RetailPurchasePolicySetting(Base):
    """
    회사별 자동구매 정책(지시문 E 섹션) — 1행 = 1회사. 기존
    ExecutionLimit(app/domains/automation_safety)은 전역(회사 구분
    없음)이라 이 도메인의 회사별 한도 요구사항을 대신할 수 없다
    (읽기 전용 기준선 조사에서 확인한 사실 — 그래서 재사용하지 않고
    이 회사 스코프 테이블을 신설한다). EStop은 여전히 기존 전역
    SafetyService를 그대로 재사용한다(중복 구현하지 않음).
    """

    __tablename__ = "retail_purchase_policy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True,
    )

    min_net_profit: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    min_margin_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    max_purchase_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    max_price_increase_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.05,
    )

    max_delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    require_return_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    min_seller_trust_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.8,
    )

    min_match_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.98,
    )

    # JSON 배열 문자열(provider_code 목록) — 빈 배열이면 "허용된
    # 쇼핑몰 없음"(전부 차단)을 의미한다, None이 아니라 명시적 빈
    # 목록으로 안전측 기본값을 강제한다.
    allowed_provider_codes_json: Mapped[str] = mapped_column(
        String(500), nullable=False, default="[]",
    )

    per_order_max_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    daily_purchase_limit_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )

    monthly_purchase_budget_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )

    max_concurrent_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 2026-08-22 14차 지시(작업 2) — 운영자 입력 UI가 실제로 설정할
    # 수 있어야 하는 항목으로 추가 요청됨.
    max_quantity_per_product: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # False면 ALLOW 판정이 나와도 개별 승인 없이 실행하지 않는다
    # (전사 차원의 "자동 실행 끄기" 스위치 — 기본값 True는 지시문의
    # "정책 범위 내 무인 자동구매" 원칙을 그대로 반영, 관리자가
    # 명시적으로 꺼야 사람 승인이 매 건 필요해진다).
    auto_execute_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    # 이 금액을 초과하는 필요예산(required_budget_amount)은
    # auto_execute_enabled가 True여도 REQUIRE_REVIEW로 낮춘다 —
    # None이면 금액 기준 강등을 적용하지 않는다.
    approval_required_amount_threshold: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )

    uncertain_handling_policy: Mapped[str] = mapped_column(
        String(30), nullable=False,
        default=RetailPurchaseUncertainHandlingPolicy.HOLD_INDEFINITELY,
    )

    # AUTO_RELEASE_AFTER_TIMEOUT일 때만 의미가 있다 — HOLD_INDEFINITELY
    # 에서는 무시된다. 구매 자체를 재시도하지 않고 예산만 반환한다.
    uncertain_auto_release_after_hours: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class RetailPurchaseWebhookEvent(Base):
    """Provider Webhook 재사용(replay) 방지용 이벤트 로그 —
    (provider_code, event_id) UNIQUE로 같은 이벤트의 중복 처리를
    막는다(app/domains/retail_purchase/webhook.py 참고). 서명 검증을
    통과한 이벤트만 여기 기록된다 — 검증 실패는 애초에 기록되지
    않는다."""

    __tablename__ = "retail_purchase_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider_code", "event_id",
            name="uq_retail_purchase_webhook_events_provider_event",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    provider_code: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )

    event_id: Mapped[str] = mapped_column(String(200), nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


__all__ = [
    "RetailPurchaseOrder",
    "PaymentAccountReference",
    "RetailPurchasePolicySetting",
    "RetailPurchaseWebhookEvent",
]
