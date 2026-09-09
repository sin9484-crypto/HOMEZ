"""
=========================================================
Homez OS

File : app/domains/coupang/model.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation — 상품 계약

ProductCandidate/Funding/Settlement과 동일한 설계 원칙을 따른다:
FK를 사용하지 않고 전부 논리 참조 컬럼(정수 id)만 사용한다. 이 Domain은
`app/domains/product_candidate`의 ProductCandidate를 읽기 전용으로만
참조하며(승인 상태 확인), Funding/Order/Purchase Model은 import하지
않는다. 실제 쿠팡 등록·발주·정산 반영은 이번 단계 범위 밖이다.

금액 필드는 전부 Numeric(Decimal)만 사용한다 — Float 신규 사용 금지.

2026-08-14 테넌트 격리 감사 — 회사 스코프 판단:
  - 회사 스코프(company_id 추가): CoupangMarketplaceProduct(+options/
    notices/profit_estimates 자식 전부 비정규화), CoupangDryRunAttempt,
    CoupangIntegrationDecision. 판매가·마진·재고·운영자 승인 이력은
    전부 이 회사만의 판단이다.
  - 전역 유지(company_id 없음): CoupangPolicySet/CoupangPolicyRule —
    "쿠팡이 공식적으로 정한 정책"(밴 카테고리·브랜드·필수 고시정보 등
    객관적 외부 사실)이라 모든 회사에 동일하게 적용된다. 회사가 채택한
    정책 "인스턴스"가 아니라 쿠팡 자체의 규정을 코드화한 것이므로
    MarketplaceChannel과 동일한 성격(전역 플랫폼 설정)으로 분류한다.
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
RATE = Numeric(9, 4, asdecimal=True)


class CoupangMarketplaceProduct(Base):
    """쿠팡 마켓플레이스/로켓그로스 상품 연동 계약(현재 상태 투영)."""

    __tablename__ = "coupang_marketplace_products"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_coupang_products_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 2026-08-14 테넌트 격리 감사.
    # 이 상품 초안의 판매가·마진·재고는 이 회사만의 판단이다.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    marketplace: Mapped[str] = mapped_column(
        String(30), nullable=False, default="COUPANG",
    )

    # MARKETPLACE / ROCKET_GROWTH — 두 판매방식의 재고·배송 Flow는
    # 혼합하지 않는다(서비스 레벨에서 강제).
    sales_method: Mapped[str] = mapped_column(String(30), nullable=False)

    # 논리 참조 (product_candidates.id) — 반드시 APPROVED 상태여야
    # 초안 생성이 허용된다(서비스 레벨 게이트).
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # --------------------------------------------------
    # 쿠팡 식별자 — 아직 쿠팡에서 발급되지 않았으면 전부 nullable
    # --------------------------------------------------

    seller_product_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    vendor_item_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )

    # 판매자 내부 식별자 — HOMEZ 상품과 안정적으로 연결하는 실제 키
    external_vendor_sku: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )

    display_category_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    seller_product_name: Mapped[str] = mapped_column(
        String(300), nullable=False,
    )

    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)

    gtin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mpn: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # GTIN/MPN이 모두 없을 때 반드시 채워야 하는 예외 근거. 없는 값을
    # 임의로 생성하지 않는다 — 근거가 없으면 정책 검증에서 차단된다.
    identifier_exemption_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    sale_price: Mapped[object | None] = mapped_column(MONEY, nullable=True)

    # --------------------------------------------------
    # 재고 — 공급처 재고를 그대로 노출 재고로 가정하지 않는다
    # --------------------------------------------------

    supplier_stock: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    safety_stock: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    # marketplace_exposure_stock = max(supplier_stock - safety_stock, 0).
    # 공급처 재고 확인이 오래되었으면(STOCK_FRESHNESS_THRESHOLD_HOURS 초과)
    # 0으로 강제하고 stock_review_required를 True로 표시한다.
    marketplace_exposure_stock: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    last_stock_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    stock_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    # 실제 쿠팡에 전송될 노출 재고(= marketplace_exposure_stock과 동기화).
    available_stock: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    maximum_buy_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # --------------------------------------------------
    # 배송/반품/고시 관련
    # --------------------------------------------------

    shipping_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    shipping_company_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    outbound_shipping_place_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    return_center_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    return_charge: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )

    # 해외구매대행 여부 — PCC(개인통관고유부호) 필요 여부와 함께 기록한다.
    overseas_purchase_agency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    pcc_needed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    # --------------------------------------------------
    # 상태
    # --------------------------------------------------

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True,
    )
    validation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NOT_VALIDATED",
    )
    # JSON 문자열 배열로 저장(검증 실패 사유 목록). 값을 임의로 생성하지
    # 않고, 실제 검증에서 발견된 사유만 append한다.
    validation_errors: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    risk_level: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )
    policy_version_applied: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(120), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CoupangProductOption(Base):
    """구조화된 구매옵션(문자열 한 칸 저장 금지)."""

    __tablename__ = "coupang_product_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모(coupang_marketplace_products)에서
    # 비정규화(denormalize)해 직접 WHERE 필터가 가능하게 한다
    # (marketplace_listing 하위 테이블과 동일한 컨벤션).
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (coupang_marketplace_products.id)
    coupang_product_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    option_name: Mapped[str] = mapped_column(String(100), nullable=False)
    option_value: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[object] = mapped_column(MONEY, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    barcode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CoupangPolicySet(Base):
    """
    정책 세트 — 정책 규칙 묶음의 버전·출처·인증 상태를 관리하는 헤더.

    법률/쿠팡 정책을 코드 영구 상수로 단정하지 않기 위해, 실제로
    정책 판단에 쓰일 수 있는지(usable) 여부를 이 헤더가 결정한다:
    status == VERIFIED이고 is_active/is_complete가 True이며
    effective_at을 지났고 expires_at 이전이며 policy_version/
    source_reference가 비어있지 않아야 "사용 가능"으로 간주된다. 이
    조건을 만족하는 세트가 하나도 없으면 정책 검증은
    RiskLevel.POLICY_DATA_UNAVAILABLE로 fail-closed한다(자동으로
    SELLABLE 취급하지 않는다).

    status를 VERIFIED로 바꾸는 것은 이 코드가 자동으로 수행하지 않는다
    — admin_guard가 걸린 API를 통해 실제 운영자가 명시적으로 수행하는
    별도 작업이다(AI가 임의로 정책을 완화·활성화할 수 없다).
    """

    __tablename__ = "coupang_policy_sets"
    __table_args__ = (
        UniqueConstraint(
            "policy_set_id",
            name="uq_coupang_policy_sets_policy_set_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 사람이 읽을 수 있는 업무 식별자 (예: "2026-07-29-draft-1")
    policy_set_id: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )

    # DRAFT / VERIFIED / EXPIRED / REVOKED — VERIFIED만 자동 판단에 사용
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", index=True,
    )
    is_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )

    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class CoupangPolicyRule(Base):
    """
    판매 금지·주의 상품 정책 규칙.

    match_field가 구조화된 필드(display_category_code/brand/
    overseas_purchase_agency/pcc_needed/gtin_mpn_missing)이면 상품의
    해당 필드값과 match_value를 정확히 비교한다. match_field가
    KEYWORD이면 상품명/브랜드/카테고리 코드를 합친 문자열에
    match_value가 포함되는지만 본다(자유문자, 보조 위험 탐지 전용 —
    이 방식의 매칭만으로는 SELLABLE을 확정할 수 없다, service.py에서
    강제).

    policy_set_id는 이 규칙이 속한 CoupangPolicySet에 대한 논리
    참조다 — 규칙 자체가 아니라 정책 세트의 상태(VERIFIED 등)가
    사용 가능 여부를 결정한다.
    """

    __tablename__ = "coupang_policy_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (coupang_policy_sets.id)
    policy_set_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    match_field: Mapped[str] = mapped_column(String(50), nullable=False)
    match_value: Mapped[str] = mapped_column(String(200), nullable=False)

    risk_level: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class CoupangProductNotice(Base):
    """
    상품 고시정보(전자상거래법 표시정보) — 구조화된 계약.

    Dry Run은 status == VERIFIED이고 content가 비어있지 않은 고시정보가
    최소 1건 이상 있어야 통과한다(app/domains/coupang/gateway.py의
    REQUIRED_PAYLOAD_FIELDS에 "notices" 포함). 빈 문자열·임시 문자열·
    자동 생성된 허위 정보를 허용하지 않기 위해 값을 임의로 채우지
    않는다 — 실제 고시정보가 없으면 Dry Run이 정직하게 실패한다.
    """

    __tablename__ = "coupang_product_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모에서 비정규화.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (coupang_marketplace_products.id)
    coupang_product_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    notice_category_name: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    notice_category_detail_name: Mapped[str] = mapped_column(
        String(200), nullable=False,
    )
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # DRAFT / VERIFIED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class CoupangProfitEstimate(Base):
    """
    Decimal 기반 수익성 계산 결과(append-only — 재계산 시 새 행 추가).

    net(= expected_net_settlement) == gross_revenue - marketplace_fee_total
    불변식을 만족하지 않으면 이 행은 생성되지 않는다(서비스 레벨에서
    생성 전 검증, 자동 보정 없음). 예상 정산이므로 Funding에는 절대
    반영하지 않는다(이 테이블 자체가 Funding/Settlement Model을 참조하지
    않는다).
    """

    __tablename__ = "coupang_profit_estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모에서 비정규화. 마진/수익성은
    # 정확히 이 회사만의 판단이다.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (coupang_marketplace_products.id)
    coupang_product_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # --------------------------------------------------
    # 입력
    # --------------------------------------------------

    consumer_sale_price: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    supplier_product_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    supplier_shipping_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )

    # 수수료(coupang_sales_fee)는 UNKNOWN(NULL)일 수 있다 — 0으로
    # 임의 가정하지 않는다. NULL이면 이 행 자체가 생성되지 않고 서비스가
    # BadRequestException으로 계산을 차단한다(컬럼은 nullable로 두어
    # 스키마상 "미상" 표현이 가능함을 명시하되, 실제로 NULL인 행은
    # 서비스가 만들지 않는다).
    coupang_sales_fee: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )
    coupang_shipping_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    advertising_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    coupon_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    expected_return_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    rocket_growth_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    other_deductions: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    vat_or_tax_estimate: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )

    # --------------------------------------------------
    # 출력
    # --------------------------------------------------

    gross_revenue: Mapped[object] = mapped_column(MONEY, nullable=False)
    marketplace_fee_total: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    supplier_payment_estimate: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    expected_net_settlement: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    expected_profit: Mapped[object] = mapped_column(MONEY, nullable=False)
    expected_margin_rate: Mapped[object] = mapped_column(
        RATE, nullable=False,
    )
    required_funding: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    break_even_price: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    meets_minimum_margin: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
    )

    calculation_version: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class CoupangDryRunAttempt(Base):
    """상품등록 Dry Run 시도 이력(append-only, 실제 네트워크 호출 없음)."""

    __tablename__ = "coupang_dry_run_attempts"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_coupang_dry_run_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모에서 비정규화.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    coupang_product_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(120), nullable=False,
    )

    # PASSED / FAILED
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    # JSON 문자열 배열
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_field_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    attempted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class CoupangIntegrationDecision(Base):
    """운영자 최종 승인(READY_FOR_SUBMISSION 전환) 이력(append-only)."""

    __tablename__ = "coupang_integration_decisions"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_coupang_decisions_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 부모에서 비정규화.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    coupang_product_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # APPROVE_FOR_SUBMISSION (이번 단계에는 이 액션만 존재)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    # 논리 참조 (users.id)
    operator_id: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(120), nullable=False,
    )
    memo: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


__all__ = [
    "CoupangMarketplaceProduct",
    "CoupangProductOption",
    "CoupangPolicySet",
    "CoupangPolicyRule",
    "CoupangProductNotice",
    "CoupangProfitEstimate",
    "CoupangDryRunAttempt",
    "CoupangIntegrationDecision",
]
