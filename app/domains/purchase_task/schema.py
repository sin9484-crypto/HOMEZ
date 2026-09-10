"""
=========================================================
Homez OS

File : app/domains/purchase_task/schema.py

Gate PT-1(2026-08-22) — API 요청/응답 스키마. company_id는 어떤
요청 바디에도 없다(항상 current_user.company_id만 사용).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PurchaseTaskCreate(BaseModel):

    source_order_id: int = Field(gt=0)
    source_order_item_id: Optional[int] = Field(default=None, gt=0)
    product_title: str = Field(min_length=1, max_length=500)
    brand: Optional[str] = Field(default=None, max_length=200)
    manufacturer: Optional[str] = Field(default=None, max_length=200)
    model_name: Optional[str] = Field(default=None, max_length=200)
    gtin: Optional[str] = Field(default=None, max_length=50)
    capacity: Optional[str] = Field(default=None, max_length=100)
    quantity: int = Field(gt=0)
    color_or_scent: Optional[str] = Field(default=None, max_length=100)
    options: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    shippable_region_note: Optional[str] = Field(default=None, max_length=500)
    coupang_sale_amount: Optional[float] = Field(default=None, gt=0)
    coupang_fee_amount: Optional[float] = Field(default=None, ge=0)
    purchase_deadline: Optional[datetime] = None
    idempotency_key: str = Field(min_length=1, max_length=150)
    correlation_id: Optional[str] = Field(default=None, max_length=100)


class PurchaseTaskResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    source_order_id: int
    source_order_item_id: Optional[int]
    creation_source: str
    product_title: str
    brand: Optional[str]
    manufacturer: Optional[str]
    model_name: Optional[str]
    gtin: Optional[str]
    capacity: Optional[str]
    quantity: int
    color_or_scent: Optional[str]
    shippable_region_note: Optional[str]
    status: str
    version: int
    purchase_deadline: Optional[datetime]
    selected_candidate_id: Optional[int]
    coupang_sale_amount: Optional[float]
    coupang_fee_amount: Optional[float]
    expected_net_profit: Optional[float]
    expected_margin_rate: Optional[float]
    block_reason: Optional[str]
    caution_reason: Optional[str]
    failure_code: Optional[str]
    retryable: bool
    budget_reservation_id: Optional[int]
    channel_connection_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class SearchLinksResponse(BaseModel):

    keyword: str
    urls: dict[str, str]


class CandidateCreate(BaseModel):

    shopping_mall_code: str = Field(min_length=1, max_length=30)
    product_url: str = Field(min_length=1, max_length=1000)
    candidate_title: Optional[str] = Field(default=None, max_length=500)
    brand: Optional[str] = Field(default=None, max_length=200)
    manufacturer: Optional[str] = Field(default=None, max_length=200)
    model_name: Optional[str] = Field(default=None, max_length=200)
    gtin: Optional[str] = Field(default=None, max_length=50)
    capacity: Optional[str] = Field(default=None, max_length=100)
    color_or_scent: Optional[str] = Field(default=None, max_length=100)
    options: list[str] = Field(default_factory=list)
    estimated_price: Optional[float] = Field(default=None, ge=0)
    estimated_shipping_fee: Optional[float] = Field(default=None, ge=0)
    estimated_delivery_days: Optional[int] = Field(default=None, gt=0)
    seller_trust_score: Optional[float] = Field(default=None, ge=0, le=1)
    return_allowed: Optional[bool] = None


class CandidateResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_task_id: int
    shopping_mall_code: str
    product_url: str
    candidate_title: Optional[str]
    brand: Optional[str]
    manufacturer: Optional[str]
    model_name: Optional[str]
    gtin: Optional[str]
    capacity: Optional[str]
    color_or_scent: Optional[str]
    estimated_price: Optional[float]
    estimated_shipping_fee: Optional[float]
    confirmed_additional_cost: float
    confirmed_discount: float
    estimated_delivery_days: Optional[int]
    seller_trust_score: Optional[float]
    return_allowed: Optional[bool]
    match_confidence: Optional[float]
    match_tier: Optional[str]
    match_confirmed_by: Optional[int]
    match_confirmed_at: Optional[datetime]
    is_selected: bool
    created_at: datetime


class SourceAttributesInput(BaseModel):
    """원 주문(쿠팡 등) 상품의 실제 속성 — 운영자가 직접 입력한다."""

    brand: Optional[str] = None
    manufacturer: Optional[str] = None
    model_name: Optional[str] = None
    gtin: Optional[str] = None
    capacity: Optional[str] = None
    color_or_scent: Optional[str] = None
    options: list[str] = Field(default_factory=list)


class MatchCheckRequest(BaseModel):

    source: SourceAttributesInput


class EvaluateRequest(BaseModel):

    source: SourceAttributesInput
    additional_shipping_fee: Optional[float] = Field(default=None, ge=0)
    return_risk_reserve: Optional[float] = Field(default=None, ge=0)


class EvaluateResponse(BaseModel):

    task: PurchaseTaskResponse
    decision: str
    reasons: list[str]


class ExtendReservationRequest(BaseModel):

    extra_hours: int = Field(gt=0, le=168)


class RecordPurchaseRequest(BaseModel):

    shopping_mall_code: str = Field(min_length=1, max_length=30)
    external_order_number: str = Field(min_length=1, max_length=200)
    actual_amount: float = Field(gt=0)
    actual_shipping_fee: Optional[float] = Field(default=None, ge=0)
    purchased_at: datetime
    selected_option_note: Optional[str] = Field(default=None, max_length=500)
    memo: Optional[str] = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=150)


class RecordTrackingRequest(BaseModel):

    courier: Optional[str] = Field(default=None, max_length=100)
    courier_confirmed: bool = False
    tracking_number: Optional[str] = Field(default=None, max_length=200)
    shipped_at: Optional[datetime] = None
    expected_arrival_at: Optional[datetime] = None
    is_partial_shipment: bool = False


class TrackingResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    purchase_task_id: int
    courier: Optional[str]
    courier_confirmed: bool
    tracking_number: Optional[str]
    shipped_at: Optional[datetime]
    expected_arrival_at: Optional[datetime]
    delivery_status: Optional[str]
    is_partial_shipment: bool
    cancel_status: Optional[str]
    return_status: Optional[str]
    refund_status: Optional[str]
    refund_amount: Optional[float]


class CancelRequest(BaseModel):

    reason: str = Field(min_length=1, max_length=500)


class RefundRequest(BaseModel):

    refund_amount: float = Field(ge=0)
    memo: Optional[str] = Field(default=None, max_length=500)


class PolicySettingResponse(BaseModel):

    id: int
    company_id: int
    per_order_max_amount: Optional[float]
    daily_purchase_limit_amount: Optional[float]
    monthly_purchase_budget_amount: Optional[float]
    max_quantity_per_product: Optional[int]
    min_net_profit: float
    min_margin_rate: float
    max_price_increase_rate: float
    max_delivery_days: Optional[int]
    require_return_allowed: bool
    min_match_confidence: float
    max_concurrent_tasks: Optional[int]
    default_additional_shipping_fee: float
    default_return_risk_reserve: float
    budget_reservation_hours: int
    updated_at: datetime


class PolicySettingUpdate(BaseModel):

    per_order_max_amount: Optional[float] = Field(default=None, gt=0)
    daily_purchase_limit_amount: Optional[float] = Field(default=None, gt=0)
    monthly_purchase_budget_amount: Optional[float] = Field(default=None, gt=0)
    max_quantity_per_product: Optional[int] = Field(default=None, gt=0)
    min_net_profit: Optional[float] = Field(default=None, ge=0)
    min_margin_rate: Optional[float] = Field(default=None, ge=0, le=1)
    max_price_increase_rate: Optional[float] = Field(default=None, ge=0, le=1)
    max_delivery_days: Optional[int] = Field(default=None, gt=0)
    require_return_allowed: Optional[bool] = None
    min_match_confidence: Optional[float] = Field(default=None, ge=0.90, le=1)
    max_concurrent_tasks: Optional[int] = Field(default=None, gt=0)
    default_additional_shipping_fee: Optional[float] = Field(default=None, ge=0)
    default_return_risk_reserve: Optional[float] = Field(default=None, ge=0)
    budget_reservation_hours: Optional[int] = Field(default=None, gt=0, le=720)


class EmailPreferenceResponse(BaseModel):

    enabled: bool
    event_toggles: dict[str, bool]


class EmailPreferenceUpdate(BaseModel):

    enabled: bool
    event_toggles: dict[str, bool] = Field(default_factory=dict)


class TestEmailRequest(BaseModel):

    to_email: str = Field(min_length=3, max_length=200)


class EmailProviderSettingResponse(BaseModel):

    provider_type: str
    smtp_host: Optional[str]
    smtp_port: Optional[int]
    smtp_use_tls: bool
    from_address: Optional[str]
    from_name: Optional[str]
    has_credential_reference: bool
    is_active: bool
    updated_at: datetime


class EmailProviderSettingUpdate(BaseModel):
    """실제 SMTP/Transactional Adapter의 설정 계약만 받는다 —
    이 값을 채워도 실제 발송 경로는 여전히 Null Provider다(정직
    공개, router.py 참고). Secret 원문(비밀번호·API Key 원문)은
    이 스키마 어디에도 없다 — credential_reference는 반드시 Windows
    Credential Manager의 target name이어야 한다는 계약이다(실제
    값 검증은 이번 라운드 범위 밖 — 저장만 한다)."""

    provider_type: str = Field(default="NONE", max_length=30)
    smtp_host: Optional[str] = Field(default=None, max_length=200)
    smtp_port: Optional[int] = Field(default=None, gt=0, le=65535)
    smtp_use_tls: bool = True
    from_address: Optional[str] = Field(default=None, max_length=200)
    from_name: Optional[str] = Field(default=None, max_length=200)
    credential_reference: Optional[str] = Field(default=None, max_length=200)
    is_active: bool = False


class CsvPreviewResponse(BaseModel):

    header: list[str]
    row_count: int
    sample_rows: list[dict]
    column_mapping_ok: bool
    missing_columns: list[str]


class CsvRowResultResponse(BaseModel):

    row_number: int
    success: bool
    error: Optional[str]
    purchase_task_id: Optional[int] = None


class CsvImportResultResponse(BaseModel):

    total_rows: int
    success_rows: int
    failure_rows: int
    results: list[CsvRowResultResponse]


# --------------------------------------------------
# 매입처 연결(Gate PT-3, 2026-09-08, item 7) — 비밀번호·쿠키·카드정보
# 원문은 어떤 요청/응답 바디에도 없다. account_label은 표시값일 뿐
# 실제 계정 식별자가 아니다.
# --------------------------------------------------

class ChannelConnectionCreate(BaseModel):
    """connection_method는 요청 바디로 받지 않는다 — 서버가 mall_code
    로 항상 다시 계산한다(PurchaseChannelMallCode.resolve_connection_
    method) — 클라이언트가 실제 연결 방식을 임의로 지정하지 못하게
    막는다."""

    mall_code: str = Field(min_length=1, max_length=30)
    account_label: str = Field(min_length=1, max_length=200)
    memo: Optional[str] = Field(default=None, max_length=500)
    idempotency_key: Optional[str] = Field(default=None, max_length=150)


class ChannelConnectionRename(BaseModel):

    account_label: str = Field(min_length=1, max_length=200)


class ChannelConnectionResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    mall_code: str
    connection_method: str
    account_label: str
    status: str
    verified_at: Optional[datetime]
    last_checked_at: Optional[datetime]
    is_active: bool
    disconnected_at: Optional[datetime]
    memo: Optional[str]
    created_at: datetime
    updated_at: datetime


class AssignChannelConnectionRequest(BaseModel):

    connection_id: int = Field(gt=0)


class ChannelConnectionCredentialSave(BaseModel):
    """CREDENTIAL 방식 연결(현재 온채널) 전용. 값은 응답 어디에도
    되돌려주지 않는다 — 저장 성공 여부만 ChannelConnectionResponse로
    돌려준다."""

    auth_key: str = Field(min_length=1, max_length=2000)
    allowed_ip: str = Field(default="", max_length=100)


class ChannelProductOptionResponse(BaseModel):

    option_id: str
    label: str
    price: Optional[float]
    in_stock: Optional[bool]


class ProductLookupResponse(BaseModel):
    """channel_adapter.ProductLookupResult를 그대로 옮긴다."""

    support: str
    external_product_id: Optional[str]
    title: Optional[str]
    options: list[ChannelProductOptionResponse]
    detail: str


class OrderLookupResponse(BaseModel):
    """channel_adapter.OrderLookupResult를 그대로 옮긴다."""

    support: str
    external_order_number: Optional[str]
    status_text: Optional[str]
    amount: Optional[float]
    detail: str


class TrackingLookupResponse(BaseModel):
    """channel_adapter.TrackingLookupResult를 그대로 옮긴다.
    `multiple_deliveries_detected=True`일 때는 courier·tracking_number
    가 항상 null이다(단일 송장 정책 — 복수 관측 시 자동 선택 금지,
    2026-09-10 후속)."""

    support: str
    courier: Optional[str]
    tracking_number: Optional[str]
    delivery_status: Optional[str]
    detail: str
    multiple_deliveries_detected: bool = False


class MemberPointCheckResponse(BaseModel):
    """channel_adapter.MemberPointCheckResult를 그대로 옮긴다.
    member_id는 원문이 아니라 마스킹된 값만 담는다(서버가 이미
    마스킹 처리 — 이 응답에는 원문이 존재한 적조차 없다)."""

    support: str
    member_id_masked: Optional[str]
    point: Optional[int]
    point_interpretable: bool
    observed_fields: list[str]
    detail: str


class OrderSubmissionReviewOptionInput(BaseModel):

    id: str
    qty: int


class OrderSubmissionReviewRequest(BaseModel):
    """2026-09-09 후속("발주 전 최종 검토 화면") — 사람이 온채널
    상품코드·옵션·수량을 직접 골라서 넘긴다(자동 매칭 없음)."""

    external_product_id: str
    options: list[OrderSubmissionReviewOptionInput]


class OrderSubmissionReviewProductResponse(BaseModel):

    support: str
    external_product_id: Optional[str]
    title: Optional[str]
    options: list[ChannelProductOptionResponse]
    selected_options: list[OrderSubmissionReviewOptionInput]
    estimated_item_amount: Optional[float]
    any_selected_option_out_of_stock: bool
    any_selected_option_price_unknown: bool
    detail: str


class OrderSubmissionReviewRecipientResponse(BaseModel):
    """unmasked=False가 기본이다 — X-Recent-Auth-Token으로 재인증한
    요청에서만 원문이 담긴다(app/domains/order/router.py의
    /sensitive-detail과 동일한 재인증 계약)."""

    unmasked: bool
    name: Optional[str]
    phone: Optional[str]
    zipcode: Optional[str]
    address: Optional[str]


class OrderSubmissionReviewSalesApplicationResponse(BaseModel):
    """2026-09-10 신규(Phase 3) — confirmed=True는 "온채널이 접수를
    확인했다"는 뜻일 뿐 "승인됐다"는 뜻이 아니다(승인 상태 조회
    API 자체가 없다고 공식 답변으로 확정됨)."""

    confirmed: bool
    detail: str


class OrderSubmissionReviewPointBalanceResponse(BaseModel):
    """2026-09-10 신규(Phase 4) — point는 point_interpretable이
    True일 때만 신뢰할 수 있는 값이다."""

    support: str
    point: Optional[int]
    point_interpretable: bool
    detail: str


class OrderSubmissionReviewResponse(BaseModel):
    """읽기 전용 검토 결과 — 이 응답을 만드는 과정에서 실제 발주
    API는 절대 호출되지 않는다(온채널 상품 조회만 실제 호출). 실제
    "전송" 여부는 이 응답과 완전히 별개의, 아직 어디에도 배선되지
    않은 PurchaseOrderSubmissionService.submit_order()가 담당한다."""

    task_id: int
    source_order_id: int
    source_product_title: str
    source_order_quantity: Optional[int]
    connection_id: int
    connection_mall_code: str
    connection_account_label: str
    product: OrderSubmissionReviewProductResponse
    recipient: OrderSubmissionReviewRecipientResponse
    sales_application: OrderSubmissionReviewSalesApplicationResponse
    point_balance: OrderSubmissionReviewPointBalanceResponse
    shipping_fee_known: bool
    shipping_fee_detail: str
    product_title_mismatch_warning: bool
    quantity_mismatch_warning: bool
    send_blocked: bool
    blocked_reasons: list[str]
    checked_at: datetime


__all__ = [
    "PurchaseTaskCreate", "PurchaseTaskResponse", "SearchLinksResponse",
    "CandidateCreate", "CandidateResponse", "SourceAttributesInput",
    "MatchCheckRequest", "EvaluateRequest", "EvaluateResponse",
    "ExtendReservationRequest", "RecordPurchaseRequest",
    "RecordTrackingRequest", "TrackingResponse", "CancelRequest",
    "RefundRequest", "PolicySettingResponse", "PolicySettingUpdate",
    "EmailPreferenceResponse", "EmailPreferenceUpdate", "TestEmailRequest",
    "EmailProviderSettingResponse", "EmailProviderSettingUpdate",
    "CsvPreviewResponse", "CsvRowResultResponse", "CsvImportResultResponse",
    "ChannelConnectionCreate", "ChannelConnectionRename",
    "ChannelConnectionResponse", "AssignChannelConnectionRequest",
    "ChannelConnectionCredentialSave", "ChannelProductOptionResponse",
    "ProductLookupResponse", "OrderLookupResponse", "TrackingLookupResponse",
    "MemberPointCheckResponse",
    "OrderSubmissionReviewOptionInput", "OrderSubmissionReviewRequest",
    "OrderSubmissionReviewProductResponse", "OrderSubmissionReviewRecipientResponse",
    "OrderSubmissionReviewSalesApplicationResponse",
    "OrderSubmissionReviewPointBalanceResponse",
    "OrderSubmissionReviewResponse",
]
