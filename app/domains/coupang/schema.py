"""
=========================================================
Homez OS

File : app/domains/coupang/schema.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation — Pydantic 계약
=========================================================
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import Field


class CoupangProductOptionInput(BaseModel):

    option_name: str
    option_value: str
    vendor_sku: str
    price: Decimal
    stock: int = Field(ge=0)
    barcode: str | None = None
    status: str = "ACTIVE"


class CoupangProductNoticeInput(BaseModel):

    notice_category_name: str
    notice_category_detail_name: str
    content: str
    source: str
    verified_at: datetime | None = None
    status: str = "DRAFT"


class CoupangProductNoticeResponse(BaseModel):

    id: int
    coupang_product_id: int
    notice_category_name: str
    notice_category_detail_name: str
    content: str
    source: str
    verified_at: datetime | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class CoupangPolicySetCreateRequest(BaseModel):
    """
    정책 세트 생성 — admin_guard 필요. status를 VERIFIED로 두는 것은
    실제 정책 문서를 대조 확인한 운영자의 명시적 판단이어야 한다(AI가
    자동으로 VERIFIED를 부여하지 않는다 — 이 스키마 자체는 어떤 값도
    허용하지만, 호출 주체가 반드시 admin_guard로 인증된 사람이어야
    한다는 점이 그 통제 지점이다).
    """

    policy_set_id: str
    policy_version: str
    source_reference: str
    status: str = "DRAFT"
    is_complete: bool = False
    checked_at: datetime
    effective_at: datetime
    expires_at: datetime | None = None


class CoupangPolicySetResponse(BaseModel):

    id: int
    policy_set_id: str
    policy_version: str
    source_reference: str
    status: str
    is_complete: bool
    is_active: bool
    checked_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class CoupangPolicyRuleCreateRequest(BaseModel):

    match_field: str
    match_value: str
    risk_level: str
    reason: str


class CoupangPolicyRuleResponse(BaseModel):

    id: int
    policy_set_id: int
    match_field: str
    match_value: str
    risk_level: str
    reason: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CoupangDraftCreateRequest(BaseModel):

    product_candidate_id: int
    sales_method: str
    external_vendor_sku: str
    seller_product_name: str
    idempotency_key: str

    brand: str | None = None
    display_category_code: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    identifier_exemption_reason: str | None = None

    sale_price: Decimal | None = None
    maximum_buy_count: int | None = None

    shipping_method: str | None = None
    shipping_company_code: str | None = None
    outbound_shipping_place_code: str | None = None
    return_center_code: str | None = None
    return_charge: Decimal | None = None

    overseas_purchase_agency: bool = False
    pcc_needed: bool = False

    supplier_stock: int | None = None
    safety_stock: int = 0
    last_stock_checked_at: datetime | None = None

    options: list[CoupangProductOptionInput] = Field(default_factory=list)
    notices: list[CoupangProductNoticeInput] = Field(default_factory=list)


class CoupangProductOptionResponse(BaseModel):

    id: int
    option_name: str
    option_value: str
    vendor_sku: str
    price: Decimal
    stock: int
    barcode: str | None
    status: str

    class Config:
        from_attributes = True


class CoupangProductResponse(BaseModel):

    id: int
    company_id: int
    marketplace: str
    sales_method: str
    product_candidate_id: int
    seller_product_id: str | None
    vendor_item_id: str | None
    external_vendor_sku: str
    display_category_code: str | None
    seller_product_name: str
    brand: str | None
    gtin: str | None
    mpn: str | None
    identifier_exemption_reason: str | None
    sale_price: Decimal | None
    supplier_stock: int | None
    safety_stock: int
    marketplace_exposure_stock: int
    last_stock_checked_at: datetime | None
    stock_review_required: bool
    available_stock: int
    maximum_buy_count: int | None
    shipping_method: str | None
    shipping_company_code: str | None
    outbound_shipping_place_code: str | None
    return_center_code: str | None
    return_charge: Decimal | None
    overseas_purchase_agency: bool
    pcc_needed: bool
    status: str
    validation_status: str
    validation_errors: str | None
    risk_level: str | None
    policy_version_applied: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CoupangProfitEstimateRequest(BaseModel):
    """
    coupang_sales_fee는 필수 입력이다(수수료를 모르면 0으로 가정하지
    않고 계산 자체를 차단하기 위해 Optional로 두되 None이면 서비스가
    명시적으로 예외를 던진다). 나머지 공제 항목은 실제로 0인 경우가
    흔하므로 기본값 0을 허용한다(호출자가 명시적으로 선택한 0이지,
    서비스가 임의로 가정한 0이 아니다).
    """

    consumer_sale_price: Decimal
    supplier_product_cost: Decimal
    supplier_shipping_cost: Decimal = Decimal("0")

    coupang_sales_fee: Decimal | None = None
    coupang_shipping_cost: Decimal = Decimal("0")
    advertising_cost: Decimal = Decimal("0")
    coupon_cost: Decimal = Decimal("0")
    expected_return_cost: Decimal = Decimal("0")
    rocket_growth_cost: Decimal = Decimal("0")
    other_deductions: Decimal = Decimal("0")
    vat_or_tax_estimate: Decimal = Decimal("0")


class CoupangProfitEstimateResponse(BaseModel):

    id: int
    coupang_product_id: int
    consumer_sale_price: Decimal
    supplier_product_cost: Decimal
    supplier_shipping_cost: Decimal
    coupang_sales_fee: Decimal | None
    coupang_shipping_cost: Decimal
    advertising_cost: Decimal
    coupon_cost: Decimal
    expected_return_cost: Decimal
    rocket_growth_cost: Decimal
    other_deductions: Decimal
    vat_or_tax_estimate: Decimal
    gross_revenue: Decimal
    marketplace_fee_total: Decimal
    supplier_payment_estimate: Decimal
    expected_net_settlement: Decimal
    expected_profit: Decimal
    expected_margin_rate: Decimal
    required_funding: Decimal
    break_even_price: Decimal
    meets_minimum_margin: bool
    calculation_version: str
    calculated_at: datetime

    class Config:
        from_attributes = True


class CoupangDryRunRequest(BaseModel):

    idempotency_key: str


class CoupangDryRunResponse(BaseModel):

    outcome: str
    errors: list[str]
    payload_field_count: int
    attempted_at: datetime
    duplicate: bool = False


class CoupangApprovalRequest(BaseModel):

    idempotency_key: str
    memo: str | None = None


__all__ = [
    "CoupangProductOptionInput",
    "CoupangProductNoticeInput",
    "CoupangProductNoticeResponse",
    "CoupangPolicySetCreateRequest",
    "CoupangPolicySetResponse",
    "CoupangPolicyRuleCreateRequest",
    "CoupangPolicyRuleResponse",
    "CoupangDraftCreateRequest",
    "CoupangProductOptionResponse",
    "CoupangProductResponse",
    "CoupangProfitEstimateRequest",
    "CoupangProfitEstimateResponse",
    "CoupangDryRunRequest",
    "CoupangDryRunResponse",
    "CoupangApprovalRequest",
]
