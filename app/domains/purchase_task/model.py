"""
=========================================================
Homez OS

File : app/domains/purchase_task/model.py

Gate PT-1(2026-08-22 15차 지시) — A+E+F 혼합형 매입·발주 Workflow.
FK를 쓰지 않는다(이 저장소 전역 컨벤션 — 모든 참조는 논리 참조).
소비자 쇼핑몰 계정 ID·비밀번호·카드번호·CVC·PIN은 이 스키마 어디에도
없다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.purchase_task.constants import BudgetReservationStatus
from app.domains.purchase_task.constants import ChannelConnectionStatus
from app.domains.purchase_task.constants import ConnectionMethod
from app.domains.purchase_task.constants import PurchaseTaskCreationSource
from app.domains.purchase_task.constants import PurchaseTaskStatus


class PurchaseTask(Base):
    """쿠팡 등 원 주문(또는 주문 품목) 1건에 대응하는 구매 작업.
    원 주문 1건이 품목별로 여러 PurchaseTask를 가질 수 있다(기존
    retail_purchase/purchase 도메인과 동일한 설계 원칙)."""

    __tablename__ = "purchase_tasks"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_purchase_tasks_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    source_order_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    source_order_item_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    # Gate PT-2 — 이 작업이 쿠팡 등 주문 수집에서 자동 생성됐는지,
    # 운영자가 수동으로 만들었는지(목록 필터·표시용, 상태 전이에는
    # 관여하지 않는다).
    creation_source: Mapped[str] = mapped_column(
        String(20), nullable=False,
        default=PurchaseTaskCreationSource.MANUAL,
    )

    product_title: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    color_or_scent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    options_json: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="[]",
    )
    components_json: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="[]",
    )

    # 고객 개인정보(이름/주소/전화번호)는 여기 저장하지 않는다 —
    # "배송 가능 지역" 같은 참고용 텍스트만(예: "서울/경기 당일배송
    # 권장"), 실제 배송지는 사용자가 쇼핑몰에서 직접 확인·입력한다.
    shippable_region_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False,
        default=PurchaseTaskStatus.SEARCH_REQUIRED, index=True,
    )

    # 낙관적 동시성 — 상태 전이 시 WHERE version=:expected로 검증한다.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    purchase_deadline: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    selected_candidate_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    coupang_sale_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    coupang_fee_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_margin_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    block_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    caution_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    budget_reservation_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    policy_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Gate PT-3(2026-09-08, item 7) — 이 작업이 어느 연결된 매입처
    # 계정(PurchaseChannelConnection)으로 처리될지. 논리 참조(FK
    # 없음, 이 저장소 전역 컨벤션). 과거 작업에는 값이 없을 수 있어
    # nullable — 서비스 계층이 실행 시점에 같은 회사 소유의 활성
    # 연결인지 검증한다(연결 미배정 상태로는 실행을 막는 것은 이
    # 컬럼이 아니라 서비스 로직의 책임).
    channel_connection_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseTaskCandidate(Base):
    """구매 작업 1건에 대해 운영자가 등록한 후보 상품 URL(작업 B) +
    동일상품 판정 결과(작업 C)."""

    __tablename__ = "purchase_task_candidates"
    __table_args__ = (
        UniqueConstraint(
            "purchase_task_id", "product_url",
            name="uq_purchase_task_candidates_task_url",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    shopping_mall_code: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )
    product_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    candidate_title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    color_or_scent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    options_json: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="[]",
    )

    estimated_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_shipping_fee: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    # 결제 전 확정된 추가비용/할인만 담는다 — 적립예정 포인트·조건부
    # 쿠폰·카드행사·추후지급 캐시백은 여기 담지 않는다는 것이
    # margin_calculator.py의 계약이다(호출부가 지켜야 하는 원칙).
    confirmed_additional_cost: Mapped[float] = mapped_column(
        Float, nullable=False, default=0,
    )
    confirmed_discount: Mapped[float] = mapped_column(
        Float, nullable=False, default=0,
    )
    estimated_delivery_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    seller_trust_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    match_evidence_json: Mapped[str] = mapped_column(
        String(4000), nullable=False, default="[]",
    )
    match_confirmed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 후보 URL·속성이 등록/판정 시점과 달라지면 무효화 판단에 쓰는
    # fingerprint(SHA-256) — service.py 참고.
    match_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    is_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    # 2026-09-10 Phase 10 — 이 후보가 처음 평가돼 실질 매입비가 처음
    # 계산된 시점의 값(가격 인상 감지 기준선). service.py::
    # evaluate_and_prepare()가 이 값이 아직 None일 때만 채운다 — 한
    # 번 채워지면 그 후보의 평가 이력 내내 불변이다. Phase 4에서
    # 이 컬럼이 없어 PRICE_INCREASE_RATE_EXCEEDED 판정이 항상
    # 건너뛰어지던 Critical 결함 #21을 여기서 해결한다.
    expected_amount_at_creation: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseTaskBudgetReservation(Base):
    """FundingAccount 예산 예약 — 만료 개념이 있다(작업 H, 사람이
    결제하는 동안 무기한 점유되지 않도록). retail_purchase의 조건부
    UPDATE 원자적 예약 패턴을 그대로 재사용한다(FundingHold는 여기서도
    재사용하지 않음 — 1주문 1Hold 설계가 이 도메인과 keyspace가 맞지
    않는 것은 retail_purchase와 동일한 이유)."""

    __tablename__ = "purchase_task_budget_reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False,
        default=BudgetReservationStatus.RESERVED, index=True,
    )

    reserved_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    extended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseRecord(Base):
    """작업 E/F — 사용자가 직접 입력한 실제 구매 결과. Provider가
    자동으로 채우지 않는다(그런 Provider가 없다는 것이 이 Workflow의
    전제)."""

    __tablename__ = "purchase_records"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "shopping_mall_code", "external_order_number",
            name="uq_purchase_records_company_mall_order_number",
        ),
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_purchase_records_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    shopping_mall_code: Mapped[str] = mapped_column(String(30), nullable=False)

    # Gate PT-3(2026-09-08, item 7) — 실제 이 구매에 쓰인 연결
    # 계정(PurchaseChannelConnection.id, 논리 참조). PurchaseTask.
    # channel_connection_id("배정된" 계정)와 별개다 — 이 필드는
    # "실제로 그 계정으로 샀다"는 불변 사실 기록이라, 연결이 나중에
    # 비활성화(연결 해제)돼도 절대 바꾸지 않는다(과거 매입 기록의
    # 계정 추적을 유지하기 위함).
    channel_connection_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    external_order_number: Mapped[str] = mapped_column(
        String(200), nullable=False,
    )
    actual_amount: Mapped[float] = mapped_column(Float, nullable=False)
    actual_shipping_fee: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    purchased_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    selected_option_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    recorded_by: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class PurchaseTaskTrackingInfo(Base):
    """작업 F — 송장·배송·취소반품환불 상태. purchase_task_id와
    1:1(가장 최근 값만 유지 — 이력이 필요하면 향후 이벤트 테이블로
    확장 가능하나 이번 라운드는 현재 상태 스냅샷만)."""

    __tablename__ = "purchase_task_tracking_infos"
    __table_args__ = (
        UniqueConstraint(
            "purchase_task_id",
            name="uq_purchase_task_tracking_infos_task",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    courier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 송장번호 형식만으로 택배사를 임의 확정하지 않는다는 원칙 —
    # 사용자가 명시적으로 확인/선택했을 때만 True.
    courier_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    tracking_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expected_arrival_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    delivery_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_partial_shipment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    cancel_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    return_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    refund_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    refund_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 2026-09-11 후속(운영 전 최종 검증 라운드, "송장 다시 조회") —
    # 값이 바뀌지 않은 재조회도 시각을 남겨야 UI가 "마지막으로 언제
    # 확인했는지"를 보여줄 수 있다. updated_at은 실제 컬럼 값이
    # 바뀔 때만 갱신되므로 별도 컬럼이 필요하다.
    last_live_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    last_live_refresh_result: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseTaskEmailPreference(Base):
    """사용자별 이메일 알림 켜기/끄기(작업 G) — 회사당 사용자 1행."""

    __tablename__ = "purchase_task_email_preferences"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "user_id",
            name="uq_purchase_task_email_preferences_company_user",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # {"TASK_CREATED": false, ...} 형태 — 키가 없으면 enabled 값을
    # 그대로 따른다(명시적으로 끈 이벤트만 예외 처리).
    event_toggles_json: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="{}",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseTaskEmailLog(Base):
    """이메일 발송 결과 감사 이력(작업 G/K) — append-only."""

    __tablename__ = "purchase_task_email_logs"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_purchase_task_email_logs_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="ko-KR")
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PurchaseTaskPolicySetting(Base):
    """작업 H — 회사별 예산·안전정책. retail_purchase_policy_settings와
    필드 모양은 비슷하지만 의도적으로 별도 테이블이다 — 이 도메인은
    Provider 계약을 전제하지 않는 별개 Workflow이므로 그 테이블을
    공유하면 두 Workflow의 정책이 뒤섞인다(재설계 조사 결론:
    retail_purchase는 미래 확장용으로 그대로 보존, 이 테이블을 건드리지
    않는다)."""

    __tablename__ = "purchase_task_policy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True,
    )

    per_order_max_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_purchase_limit_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    monthly_purchase_budget_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    max_quantity_per_product: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    min_net_profit: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    min_margin_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_price_increase_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.05,
    )
    max_delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    require_return_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    min_match_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.98,
    )
    max_concurrent_tasks: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    default_additional_shipping_fee: Mapped[float] = mapped_column(
        Float, nullable=False, default=0,
    )
    default_return_risk_reserve: Mapped[float] = mapped_column(
        Float, nullable=False, default=0,
    )
    # 2026-09-11 후속(반자동 완료 라운드, Phase 7) — 온채널 실제
    # 발주 직전 게이트 전용 필드. None이면 constants.py의
    # RECOMMENDED_* 상수를 대신 쓴다(이 두 필드가 회사별로 아직
    # 설정된 적이 없다는 뜻 — "무제한"으로 읽지 않는다, 위 필드들과
    # 규칙이 다르다는 점을 주석으로 명시해 둔다).
    min_residual_points: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    order_approval_validity_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    # 사람이 결제하는 동안 예산 예약을 얼마나 유지할지(작업 H) —
    # 기본 24시간.
    budget_reservation_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseTaskEmailProviderSetting(Base):
    """Gate PT-2E — 실제 SMTP/Transactional Email Adapter의 "설정
    계약"만 담는다. 이 테이블에 값이 있어도 실제 발송 경로
    (`router.py::_get_email_provider()`)는 여전히
    `NullPurchaseTaskEmailProvider`를 반환한다(정직 공개 — 실제
    연결은 별도 승인 대상). Secret 원문은 어디에도 없다 —
    `credential_reference`는 store_connection.model과 동일하게
    Windows Credential Manager의 target name일 뿐이다
    (app/core/windows_credential_store.py 참고)."""

    __tablename__ = "purchase_task_email_provider_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True,
    )

    provider_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NONE",
    )
    smtp_host: Mapped[str | None] = mapped_column(String(200), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    from_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Windows Credential Manager target name — Secret 원문이 아니다.
    # 설정 전(NONE) 또는 Credential 해제 후에는 NULL.
    credential_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    # 값이 채워져 있어도 이 필드가 False면 여전히 미연결로 취급한다 —
    # "설정만 해두고 실제 연결은 별도 승인" 상태를 명시적으로 표현.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseTaskCsvImportLog(Base):
    """CSV 가져오기 배치 이력(작업 F/K 감사) — 행별 상세는 응답으로만
    반환하고(대용량 방지), 배치 요약만 append-only로 남긴다."""

    __tablename__ = "purchase_task_csv_import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    uploaded_by: Mapped[int] = mapped_column(Integer, nullable=False)

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class PurchaseChannelConnection(Base):
    """Gate PT-3(2026-09-08, item 7 "사용자 계정 기반 매입 연결") —
    회사가 HOMEZ에 등록해 둔 매입처 계정 카탈로그.

    비밀번호·쿠키·카드정보 원문은 이 스키마 어디에도 없다.
    `credential_reference`는 다른 도메인과 동일하게 Windows
    Credential Manager의 target name일 뿐이다(app/core/
    windows_credential_store.py 참고) — CREDENTIAL 방식이 아닌
    연결(예: 네이버쇼핑처럼 사람이 브라우저에서 직접 로그인하는
    매입처)에서는 항상 NULL이다. `browser_profile_reference`는
    "전용 브라우저 프로필" 개념을 위한 자리만 미리 만들어 둔
    것으로, 이번 라운드에는 그런 자동화가 전혀 없으므로 항상
    NULL이다(지시문 3번: 지원하지 않는 연결 방식은 형식적인 필드만
    채워 성공으로 표시하지 않는다).

    `account_label`은 사람이 알아보기 위한 표시값일 뿐 실제 계정
    식별자가 아니다 — 그래서 UNIQUE 제약을 두지 않는다(라벨 변경이
    다른 행과의 식별 충돌을 일으켜서는 안 된다). 실제 식별은 항상
    변하지 않는 `id`로 한다.

    `status`/`verified_at`은 Adapter가 스스로 정하지 않는다 —
    channel_adapter.py의 check_connection()이 이 두 값을 그대로
    입력받아 CONNECTED 여부를 계산할 뿐, 이 테이블에 실제로
    verified_at을 채우는 것은 사람의 명시적 확인 행동(서비스의
    "연결 확인 완료" 호출)뿐이다 — 자격증명 등록 사실만으로 자동
    승격되지 않는다."""

    __tablename__ = "purchase_channel_connections"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_purchase_channel_connections_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    mall_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    connection_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ConnectionMethod.UNKNOWN,
    )

    account_label: Mapped[str] = mapped_column(String(200), nullable=False)

    credential_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    browser_profile_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False,
        default=ChannelConnectionStatus.NOT_CONNECTED, index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 2026-09-15 전면 감사 후속(Phase 9, HOMEZ_USER_OPERATION_SETTINGS.md
    # 8-16 — "매입처 조회 실패가 계속되면 사용자에게 알린다") — 실제
    # 조회(lookup_product/list_products 등)가 실패할 때마다(인증
    # 오류뿐 아니라 네트워크·형식오류 등 모든 실패 종류) 증가하고,
    # 성공하면 0으로 되돌린다. 이 값이 임계치(consecutive_failure_
    # notify_threshold, service.py)에 도달하는 "그 순간"에만 알림을
    # 보낸다 — 매 실패마다 반복 알림을 보내지 않는다.
    consecutive_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    memo: Mapped[str | None] = mapped_column(String(500), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(
        String(150), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseChannelConnectionEvent(Base):
    """append-only 감사 로그 — 연결 생성·검증완료·라벨변경·상태변경·
    비활성화·재활성화. 지시문 7번(격리검증: "필요한 감사 기록")과
    이 저장소의 다른 append-only 로그(PurchaseTaskEmailLog 등)와
    동일한 패턴이다."""

    __tablename__ = "purchase_channel_connection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class PurchaseOrderSubmissionAttempt(Base):
    """2026-09-08 후속("확인된 발주 계약 구현", item 7) — 실제
    발주(POST seller/order/regist) 시도 1건의 이력. "내부 중복 실행
    잠금"과 "재시작 복구"를 이 테이블 하나로 구현한다:

    - **중복 잠금**: `(company_id, idempotency_key)` UNIQUE 제약이
      곧 잠금이다. 같은 idempotency_key로 두 번째 행을 만들려는
      시도는 DB가 직접 거부한다(애플리케이션 코드의 사전 조회에
      의존하지 않는다 — 동시 요청 두 개가 동시에 "아직 없음"을 보고
      둘 다 진행하는 경쟁 상태까지 막는다).
    - **재시작 복구**: status가 IN_FLIGHT인 채로 프로세스가
      죽어도(정확히 이 순간이 "온채널 서버에 도달했는지조차 알 수
      없는" 결과 불명 구간이다) 이 행은 DB에 그대로 남는다. 재시작
      후 이 행을 다시 조회하면 여전히 "이 idempotency_key는 이미
      쓰였다"는 사실을 그대로 알 수 있다 — 자동으로 성공/실패를
      추정하거나 자동 재시도하지 않는다(OrderSubmissionStatus
      docstring 참고).

    이 테이블은 PurchaseChannelConnectionEvent와 달리 append-only가
    아니다 — 같은 행이 PENDING -> IN_FLIGHT -> 종결 상태로 갱신된다
    (그래야 "몇 번 재전송했는지"가 아니라 "이 시도가 지금 어디까지
    갔는지"를 정확히 추적할 수 있다).

    개인정보(수취인명·연락처·주소)는 이 테이블 어디에도 저장하지
    않는다 — `product_code`/`options_json`(둘 다 개인정보 아님)만
    남기고, 실제 요청 바디는 저장하지 않는다(호출 시점에만 메모리에
    존재하다가 사라진다)."""

    __tablename__ = "purchase_order_submission_attempts"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_purchase_order_submission_attempts_company_idempotency",
        ),
        # 2026-09-15 전면 감사 후속(Phase 3) — 위 idempotency_key
        # UNIQUE만으로는 "같은 purchase_task_id에 대해 동시에 서로
        # 다른 idempotency_key(예: 옵션이 다른 요청)로 들어온 두 요청"
        # 까지는 막지 못한다. _has_blocking_task_attempt()는 INSERT
        # 이전 SELECT라 애플리케이션 프로세스가 둘 이상이면(또는 같은
        # 프로세스 안에서도 네트워크 호출 대기 중 경쟁 요청이 끼어들면)
        # 둘 다 "아직 없음"을 관측하고 통과할 수 있다 — 이 부분
        # UNIQUE INDEX가 최종 방어선이다: 같은 (company_id,
        # purchase_task_id)에 대해 "차단 대상" 상태(PENDING/IN_FLIGHT/
        # SUCCEEDED, 또는 RESULT_UNKNOWN이면서 ORDER_NOT_CONFIRMED로
        # 아직 확정되지 않음)인 행은 동시에 하나만 존재할 수 있다.
        Index(
            "uq_purchase_order_submission_attempts_active_task",
            "company_id", "purchase_task_id",
            unique=True,
            sqlite_where=text(
                "purchase_task_id IS NOT NULL AND ("
                "status IN ('PENDING', 'IN_FLIGHT', 'SUCCEEDED') OR "
                "(status = 'RESULT_UNKNOWN' AND "
                "unknown_resolution_status != 'ORDER_NOT_CONFIRMED')"
                ")",
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)
    mall_code: Mapped[str] = mapped_column(String(30), nullable=False)
    product_code: Mapped[str] = mapped_column(String(100), nullable=False)
    # [{"id": int, "qty": int}] — 개인정보 아님, 그대로 저장 가능.
    options_json: Mapped[str] = mapped_column(
        String(2000), nullable=False, default="[]",
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True,
    )
    external_order_code: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    # 실패·결과불명 사유 — 온채널 쪽 정적 오류 메시지나 예외 종류
    # 이름만 담는다(개인정보·JWT 없음, onchannel_client.py가 이미
    # 그렇게 만들어 둔 예외 메시지를 그대로 옮긴다).
    failure_detail: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    triggered_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 2026-09-11 후속(운영 전 최종 검증 라운드) — status=RESULT_UNKNOWN인
    # 시도에만 의미가 있다. 기본값 UNRESOLVED는 "아직 사람이 확인하지
    # 않음"을 뜻하며, 이 값이 UNRESOLVED나 STILL_UNCLEAR인 동안은
    # order_submission_service.py가 이 purchase_task_id에 대한 모든
    # 새 발주 시도를 차단한다(개별 idempotency_key와 무관하게 작업
    # 단위로 차단 — "해당 PurchaseTask 후속 자동화 중지" 요구사항).
    unknown_resolution_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNRESOLVED",
    )
    # ORDER_CONFIRMED로 확정할 때만 채운다(온채널 관리자 화면에서
    # 사람이 직접 읽어 옮긴 값 — HOMEZ가 추측하지 않는다).
    unknown_resolved_order_code: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    # ORDER_NOT_CONFIRMED로 확정할 때 근거를 남긴다(개인정보 없음).
    unknown_resolution_basis: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    unknown_resolved_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    unknown_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseOrderUnknownResolutionEvent(Base):
    """append-only 감사 로그 — RESULT_UNKNOWN 발주 시도를 사람이
    확인·확정한 이력. `PurchaseOrderSubmissionAttempt.unknown_
    resolution_status`(현재 상태 1값)와 달리 이 테이블은 매 확정
    시도를 전부 별도 행으로 남긴다 — 나중에 확정을 번복하더라도
    이전 기록을 지우거나 덮어쓰지 않는다(지시문 5번, "기존 UNKNOWN
    기록을 삭제하거나 덮어쓰지 않음")."""

    __tablename__ = "purchase_order_unknown_resolution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    attempt_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    resolution_status: Mapped[str] = mapped_column(String(30), nullable=False)
    order_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    basis: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_by: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class PurchaseSalesApplicationAttempt(Base):
    """2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수"
    확정) — 판매신청(POST seller/product/apply) 시도의 **현재 상태**.

    PurchaseOrderSubmissionAttempt와 다르게 (company_id,
    connection_id, product_code) UNIQUE다 — idempotency_key가 아니다.
    이유: 판매신청은 발주와 달리 금전·중복 위험이 없다(요청 바디에
    결제·금액 필드가 없다, docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_
    20260908.md 참고) — 그래서 "이 상품을 이 연결로 이미 신청한 적이
    있는가"라는 사실 하나만 행 하나로 추적하면 충분하고, 실패했던
    시도를 같은 행 위에서 다시 시도할 수 있어야 한다(발주처럼 새
    idempotency_key를 매번 새로 발급할 이유가 없다).

    `status`가 SUBMITTED라는 것은 "온채널이 접수를 확인했다"는
    뜻일 뿐 "승인됐다"는 뜻이 아니다 — 승인 상태를 조회하는 API
    자체가 없다고 공식 답변으로 확정됐으므로, 이 테이블에는 애초에
    "승인 여부" 컬럼을 두지 않는다(없는 사실을 있는 것처럼 컬럼으로
    만들지 않는다). 발주 전 게이트는 오직 이 status가 SUBMITTED인
    행이 있는지만 확인한다(SalesApplicationStatus.SATISFIES_ORDER_
    GATE 참고)."""

    __tablename__ = "purchase_sales_application_attempts"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "connection_id", "product_code",
            name="uq_purchase_sales_application_attempts_company_connection_product",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    mall_code: Mapped[str] = mapped_column(String(30), nullable=False)
    product_code: Mapped[str] = mapped_column(String(100), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True,
    )
    # 온채널이 실제로 돌려준 prd_code — 요청한 product_code와 항상
    # 같아야 정상이지만(응답 형식 오류 검증은 client 계층이 이미
    # 수행), 실제 관측값을 그대로 남겨 둔다.
    applied_product_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    failure_detail: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    triggered_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PurchaseOrderApproval(Base):
    """2026-09-11 후속(반자동 완료 라운드, Phase 5·7) — 실제 온채널
    발주 직전 "사용자 최종 승인" 1건의 현재 상태. 배송비를 사전
    확정할 공식 API가 없다는 사실이 확정됐으므로(docs/HOMEZ_
    ONCHANNEL_OPENAPI_FINDINGS_20260908.md), 사용자가 외부 화면에서
    직접 확인한 배송비를 근거와 함께 입력하는 반자동 보완책 + 가격/
    포인트/한도/마진 최종 재확인을 한 행에 함께 기록한다.

    (company_id, connection_id, purchase_task_id) UNIQUE — 작업
    1건당 승인 1건만 "현재 상태"로 존재한다(append-only가 아니라
    PurchaseSalesApplicationAttempt와 같은 현재상태 갱신형 — 배송비
    재입력·재승인은 같은 행을 갱신한다, 매번 새 행을 만들지 않는다).
    이 행 자체는 실제 온채널 발주를 실행하지 않는다 — order_
    submission_service.py의 Gate D/E가 이 행의 status가 ACTIVE이고
    아직 만료되지 않았을 때만 그 값을 신뢰해 통과시킨다.

    개인정보는 이 테이블 어디에도 없다 — product_code·금액·시각·
    사용자ID(논리 참조)만 저장한다."""

    __tablename__ = "purchase_order_approvals"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "connection_id", "purchase_task_id",
            name="uq_purchase_order_approvals_company_connection_task",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    purchase_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(100), nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING_SHIPPING_COST", index=True,
    )

    # ---- Phase 5: 배송비 수동 확인(증거 기반) ----
    shipping_cost_amount: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    shipping_cost_is_free_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    shipping_cost_source: Mapped[str | None] = mapped_column(
        String(40), nullable=True,
    )
    shipping_cost_basis_memo: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    shipping_cost_confirmed_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    shipping_cost_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # ---- Phase 7: 최종 승인 시점 스냅샷(재확인 기준값) ----
    item_amount_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    required_points_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    current_points_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    margin_amount_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    margin_rate_snapshot: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    # 2026-09-15 후속(전면 감사 Phase 2 — 승인-실행 결합 완성) —
    # 승인 시점에 검토된 옵션 구성(id·수량만, PII 아님)의 정규화된
    # 스냅샷. JSON 배열, id 기준 정렬 후 저장해 "순서만 다른 같은
    # 요청"과 "실제 옵션이 바뀐 요청"을 구분할 수 있게 한다. 이
    # 필드가 None인 기존 행(이 Migration 이전에 만들어진 승인)은
    # 옵션 재검증을 생략한다(추측으로 채우지 않는다) — 잔여 위험으로
    # 별도 기록한다. 실행 직전 재검증(revalidate_before_submission)
    # 이 이 스냅샷과 실제 제출 시점 옵션을 대조한다.
    options_snapshot_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    invalidated_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "PurchaseTask",
    "PurchaseTaskCandidate",
    "PurchaseTaskBudgetReservation",
    "PurchaseRecord",
    "PurchaseTaskTrackingInfo",
    "PurchaseTaskEmailPreference",
    "PurchaseTaskEmailLog",
    "PurchaseTaskEmailProviderSetting",
    "PurchaseTaskPolicySetting",
    "PurchaseTaskCsvImportLog",
    "PurchaseChannelConnection",
    "PurchaseChannelConnectionEvent",
    "PurchaseOrderSubmissionAttempt",
    "PurchaseOrderUnknownResolutionEvent",
    "PurchaseSalesApplicationAttempt",
    "PurchaseOrderApproval",
]
