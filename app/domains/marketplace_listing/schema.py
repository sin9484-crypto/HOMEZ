"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/schema.py

채널별 판매 방식(Fulfillment Mode) 선택 — Pydantic 계약.
=========================================================
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import Field


# --------------------------------------------------
# Channel / Account / Capability (조회 중심 — 시딩은 관리자 액션)
# --------------------------------------------------

class MarketplaceChannelResponse(BaseModel):

    id: int
    code: str
    name: str
    doc_verification_status: str
    doc_source_reference: str | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MarketplaceAccountCreateRequest(BaseModel):

    channel_id: int
    account_code: str
    account_name: str


class MarketplaceAccountResponse(BaseModel):

    id: int
    channel_id: int
    account_code: str
    account_name: str
    direct_purchase_contract_status: str
    direct_purchase_contract_reference: str | None
    direct_purchase_contract_verified_at: datetime | None
    direct_purchase_contract_expires_at: datetime | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MarketplaceFulfillmentCapabilityResponse(BaseModel):

    id: int
    channel_id: int
    fulfillment_mode: str
    is_supported: bool
    requires_eligibility_check: bool
    requires_account_contract: bool
    external_display_name: str
    policy_version: str
    doc_source_reference: str
    status: str
    verified_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class MarketplaceCapabilityVerifyRequest(BaseModel):
    """
    캡빌리티를 VERIFIED로 전환한다 — 실제 정책/문서를 대조 확인한
    관리자의 명시적 판단이어야 한다(admin_guard가 그 통제 지점).
    """

    idempotency_key: str


# --------------------------------------------------
# Listing Draft (위저드)
# --------------------------------------------------

class MarketplaceListingDraftCreateRequest(BaseModel):

    product_candidate_id: int
    idempotency_key: str
    product_basics: dict = Field(default_factory=dict)


class MarketplaceListingDraftResponse(BaseModel):

    id: int
    product_candidate_id: int
    workflow_state: str
    selected_channel_ids_json: str
    product_basics_json: str | None
    validation_errors_json: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MarketplaceListingDraftChannelSelectRequest(BaseModel):

    marketplace_account_ids: list[int] = Field(default_factory=list)


# --------------------------------------------------
# Listing
# --------------------------------------------------

class MarketplaceListingCreateRequest(BaseModel):

    draft_id: int | None = None
    product_candidate_id: int
    marketplace_account_id: int


class MarketplaceListingResponse(BaseModel):

    id: int
    draft_id: int | None
    product_candidate_id: int
    marketplace_account_id: int
    external_listing_id: str | None
    status: str
    platform_sync_status: str
    platform_raw_status: str | None
    status_last_refreshed_at: datetime | None
    status_last_refresh_error_code: str | None
    # Gate H(2026-08-07) — Retry-After 정규화 결과. 원본(가장 최근
    # 관측 초 값)과 실제로 재시도를 막는 유효 시각(naive UTC)을 함께
    # 노출한다 — "지금 재시도 가능한지"는 클라이언트가 이 시각과
    # 현재 시각을 비교해 판단한다(서버는 매 렌더링마다 계산해 내려
    # 주지 않는다 — 카운트다운은 클라이언트가 매초 갱신한다).
    rate_limit_retry_after_seconds: int | None
    rate_limit_retry_available_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------
# 플랫폼 상태 동기화 (2026-08-05 최종 제품화 Phase 4)
# --------------------------------------------------

class MarketplaceListingStatusEventResponse(BaseModel):

    id: int
    listing_id: int
    submission_id: int | None
    source: str
    previous_status: str | None
    normalized_status: str
    platform_raw_status: str | None
    provider_observed_at: datetime | None
    error_code: str | None
    # 이 관측이 실제로 listing 캐시에 반영됐는지 — False면
    # stale/terminal-lock 등으로 event에만 기록되고 캐시는 그대로다.
    applied: bool
    # 연속 실패 재시도 횟수(REFRESH/RETRY 공통) — 재시도 UI가 "몇 번째
    # 시도인지" 표시할 수 있다.
    attempt_number: int
    triggered_by: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class MarketplaceListingStatusSummaryResponse(BaseModel):
    """목록/필터 화면용 요약 — 채널 코드·상품명을 함께 담아 클라이언트가
    다시 조회하지 않게 한다."""

    listing: MarketplaceListingResponse
    channel_code: str
    product_name: str


# --------------------------------------------------
# Fulfillment Selection
# --------------------------------------------------

class MarketplaceFulfillmentSelectionCreateRequest(BaseModel):

    listing_id: int
    fulfillment_mode: str
    required_fields: dict = Field(default_factory=dict)
    idempotency_key: str


class MarketplaceFulfillmentSelectionResponse(BaseModel):

    id: int
    listing_id: int
    capability_id: int
    fulfillment_mode: str
    required_fields_json: str
    status: str
    decision_evaluation_id: int | None
    selected_by: int
    created_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------
# Eligibility
# --------------------------------------------------

class MarketplaceEligibilityCheckRequest(BaseModel):

    marketplace_account_id: int
    fulfillment_mode: str
    idempotency_key: str


class MarketplaceEligibilityManualReviewRequest(BaseModel):
    """
    자동 확인 경로가 없는 자격을 수동으로 VERIFIED/REJECTED로 확정한다
    — reviewer/사유/만료일이 모두 필수다(감사 추적, fail-closed 유지).
    """

    marketplace_account_id: int
    fulfillment_mode: str
    new_state: str
    review_reason: str
    expires_at: datetime | None = None
    idempotency_key: str


class MarketplaceEligibilityResponse(BaseModel):

    id: int
    marketplace_account_id: int
    fulfillment_mode: str
    capability_id: int
    state: str
    check_source: str
    evidence_reference: str | None
    checked_at: datetime | None
    verified_at: datetime | None
    expires_at: datetime | None
    reviewer_id: int | None
    review_reason: str | None
    created_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------
# Submission Approval — 제출과 분리된 별도 요청/승인 흐름.
# client가 "승인됨"을 자칭할 수 있는 Boolean 필드는 어디에도 없다 —
# approved_by/approved_at은 오직 approve() 호출 자체의 admin_guard
# current_user에서만 채워진다(router.py 참고).
# --------------------------------------------------

class MarketplaceSubmissionApprovalRequestRequest(BaseModel):

    listing_id: int
    selection_id: int
    planned_quantity: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0)
    unit_cost_of_goods: Decimal = Field(ge=0)
    expected_logistics_cost: Decimal = Field(ge=0)
    idempotency_key: str


class MarketplaceSubmissionApprovalDecisionRequest(BaseModel):
    """approve() 전용 — expires_at 필수(무기한 승인 금지)."""

    expires_at: datetime
    reason: str | None = None
    idempotency_key: str


class MarketplaceSubmissionApprovalRejectionRequest(BaseModel):
    """reject()/revoke() 공용 — 사유 필수."""

    reason: str
    idempotency_key: str


class MarketplaceSubmissionApprovalResponse(BaseModel):

    id: int
    listing_id: int
    selection_id: int
    status: str
    listing_fingerprint: str
    selection_fingerprint: str
    capability_policy_version: str
    planned_quantity: int
    unit_price: Decimal
    unit_cost_of_goods: Decimal
    expected_logistics_cost: Decimal
    requested_by: int
    requested_at: datetime
    approved_by: int | None
    approved_at: datetime | None
    expires_at: datetime | None
    reason: str | None
    created_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------
# Submission
# --------------------------------------------------

class MarketplaceSubmissionRequest(BaseModel):
    """
    승인 여부를 나타내는 Boolean 필드가 없다 — 제출 서비스는 오직
    서버에 이미 저장된 유효한 APPROVED MarketplaceSubmissionApproval
    레코드만 인정한다(app/domains/marketplace_listing/
    approval_service.py::current_valid_approval).
    """

    listing_id: int
    selection_id: int
    idempotency_key: str


class MarketplaceSubmissionResponse(BaseModel):

    id: int
    listing_id: int
    selection_id: int
    marketplace_account_id: int
    external_submission_ref: str | None
    status: str
    safety_decision: str
    operator_approved_by: int | None
    operator_approved_at: datetime | None
    error_reason: str | None
    attempted_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------
# Submission Reconciliation — 2026-08-31 V7 필수 작업 2번
# --------------------------------------------------

class MarketplaceSubmissionReconciliationPreviewRequest(BaseModel):

    operator_confirmed_seller_product_id: str = Field(min_length=1, max_length=200)


class MarketplaceSubmissionReconciliationApplyRequest(BaseModel):

    operator_confirmed_seller_product_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class MarketplaceSubmissionReconciliationAssessmentResponse(BaseModel):

    outcome: str
    submission_id: int
    external_submission_ref: str | None = None
    observed_status_name: str | None = None
    match_tier: str | None = None
    matched_fields: list[str] = Field(default_factory=list)
    mismatched_fields: list[str] = Field(default_factory=list)
    unavailable_fields: list[str] = Field(default_factory=list)
    detail: str | None = None


class MarketplaceSubmissionReconciliationResultResponse(BaseModel):

    outcome: str
    submission_id: int
    external_submission_ref: str | None = None
    observed_status_name: str | None = None
    detail: str | None = None


class MarketplaceUnresolvedSubmissionResponse(BaseModel):

    id: int
    listing_id: int
    selection_id: int
    marketplace_account_id: int
    status: str
    error_reason: str | None
    attempted_at: datetime

    class Config:
        from_attributes = True


__all__ = [
    "MarketplaceChannelResponse",
    "MarketplaceAccountCreateRequest",
    "MarketplaceAccountResponse",
    "MarketplaceFulfillmentCapabilityResponse",
    "MarketplaceCapabilityVerifyRequest",
    "MarketplaceListingDraftCreateRequest",
    "MarketplaceListingDraftResponse",
    "MarketplaceListingDraftChannelSelectRequest",
    "MarketplaceListingCreateRequest",
    "MarketplaceListingResponse",
    "MarketplaceFulfillmentSelectionCreateRequest",
    "MarketplaceFulfillmentSelectionResponse",
    "MarketplaceEligibilityCheckRequest",
    "MarketplaceEligibilityManualReviewRequest",
    "MarketplaceEligibilityResponse",
    "MarketplaceSubmissionApprovalRequestRequest",
    "MarketplaceSubmissionApprovalDecisionRequest",
    "MarketplaceSubmissionApprovalRejectionRequest",
    "MarketplaceSubmissionApprovalResponse",
    "MarketplaceSubmissionRequest",
    "MarketplaceSubmissionResponse",
    "MarketplaceSubmissionReconciliationPreviewRequest",
    "MarketplaceSubmissionReconciliationApplyRequest",
    "MarketplaceSubmissionReconciliationAssessmentResponse",
    "MarketplaceSubmissionReconciliationResultResponse",
    "MarketplaceUnresolvedSubmissionResponse",
]
