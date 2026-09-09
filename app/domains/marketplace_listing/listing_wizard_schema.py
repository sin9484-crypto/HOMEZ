"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_schema.py

Gate I(2026-08-08) — 상품등록 통합 마법사 Pydantic 계약. 기존
schema.py와 분리한다(fingerprint.py/required_fields_schemas.py와
동일한 이유 — 이 Domain의 기존 8개 테이블 계약과 섞이지 않게).

모든 PATCH 요청은 `expected_version`을 필수로 받는다 — Repository가
`WHERE version = expected_version`으로 조건부 UPDATE하고, 불일치 시
409(Gate J 자동 저장 충돌 감지가 이 위에 얹힌다).
=========================================================
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class WizardCreateRequest(BaseModel):

    source_type: str = Field(default="MANUAL")
    product_candidate_id: int | None = None
    cloned_from_wizard_id: int | None = None
    creation_idempotency_key: str = Field(min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class WizardListItem(BaseModel):

    id: int
    current_step: str
    status: str
    source_type: str
    product_candidate_id: int | None
    created_at: datetime
    updated_at: datetime
    version: int
    # 2026-08-28 "대기 상품 정리" — deletable/block_reason은 실제 컬럼이
    # 아니라 router.py::_to_list_item()이 매 행마다 계산해 채우는
    # 값이다(from_attributes 자동 매핑 대상 아님). UI가 별도 왕복 없이
    # 체크박스 활성화 여부와 비활성화 이유 툴팁을 바로 그릴 수 있게
    # 한다.
    deleted_at: datetime | None = None
    deletable: bool = False
    block_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class WizardDetailResponse(BaseModel):
    """
    Gate U-1(2026-08-10) — `economics_input`/`economics_result`는 이
    모델에 없다(의도적). `LISTING_ECONOMICS_VIEW` 권한이 없는 요청은
    이 필드들의 값을 가리는 게 아니라 응답 JSON에서 키 자체를
    제외해야 한다(Gate Q-2 계약보다 강화됨) — router.py::_to_detail()
    이 이 모델을 base로 dict를 만든 뒤, 권한이 있을 때만 두 키를
    수동으로 얹는다. response_model을 쓰지 않는 이유도 이것이다
    (response_model을 쓰면 여기 선언 안 된 키는 항상 잘려나가
    "권한 있음" 케이스에서도 절대 보이지 않게 된다).
    """

    id: int
    current_step: str
    status: str
    source_type: str
    product_candidate_id: int | None
    cloned_from_wizard_id: int | None
    draft: dict | None
    selected_media_asset_ids: list[int]
    channel_selections: list[dict]
    validation_result: dict | None
    approval_fingerprint: str | None
    approved_by_user_id: int | None
    approved_at: datetime | None
    approval_history: list[dict]
    # 컬럼명은 "ids"지만 실제로는 채널별 결과 dict 목록을 담는다
    # (marketplace_account_id/listing_id/status/error_reason) — 새
    # 컬럼을 추가하지 않기 위한 의도적 재사용(model.py 주석 참고).
    materialized_listing_ids: list[dict]
    autosave_client_token: str | None
    autosave_saved_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    # 2026-08-28 "대기 상품 정리" — WizardListItem과 동일한 계산 필드.
    deleted_at: datetime | None = None
    deleted_by_user_id: int | None = None
    delete_reason: str | None = None
    restored_at: datetime | None = None
    restored_by_user_id: int | None = None
    deletable: bool = False
    block_reason: str | None = None


class _ExpectedVersionRequest(BaseModel):
    """
    Gate J(2026-08-08) — `autosave_client_token`은 선택 필드다. 값이
    있으면 이 저장이 "이 브라우저 탭의 자동 저장 하트비트"라는 뜻이고,
    서버는 `autosave_client_token`/`autosave_saved_at`을 함께 갱신한다
    (수동 "다음" 클릭도 같은 필드를 보낼 수 있다 — 자동/수동을 구분하는
    게 목적이 아니라 "어느 클라이언트가 마지막으로 저장했는가"를
    추적하는 게 목적이다. 같은 위저드를 다른 탭/기기에서 동시에 열면
    서로 다른 토큰이 서로 다른 시점에 버전을 올리므로, 낙관적 동시성
    충돌(409)이 자연스럽게 "중복 편집" 신호가 된다).
    """

    expected_version: int
    autosave_client_token: str | None = None

    model_config = ConfigDict(extra="forbid")


class WizardSourceUpdateRequest(_ExpectedVersionRequest):

    product_candidate_id: int


class WizardOptionInput(BaseModel):

    name: str
    values: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class WizardSkuInput(BaseModel):

    sku_code: str
    option_values: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class WizardDraftUpdateRequest(_ExpectedVersionRequest):

    product_name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = None
    options: list[WizardOptionInput] = Field(default_factory=list)
    sku_list: list[WizardSkuInput] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class WizardMediaUpdateRequest(_ExpectedVersionRequest):

    selected_media_asset_ids: list[int] = Field(default_factory=list)


class CoupangContentsFromMediaRequest(BaseModel):
    """
    2026-08-31 — required_fields["contents"](상세설명) 자동 구성
    요청. wizard 자체를 변경하지 않는 순수 조회·변환 호출이라
    expected_version이 필요 없다 — 실제 반영은 이 응답을 담아 다시
    보내는 기존 /fulfillment PATCH(expected_version 필수)에서
    일어난다.
    """

    media_asset_ids: list[int] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class WizardChannelsUpdateRequest(_ExpectedVersionRequest):

    marketplace_account_ids: list[int] = Field(default_factory=list)


class FulfillmentSelectionInput(BaseModel):

    marketplace_account_id: int
    fulfillment_mode: str
    required_fields: dict = Field(default_factory=dict)
    outbound_shipping_place_code: str | None = None
    return_center_code: str | None = None

    # Audit(2026-08-21, CTO 후속 지시) — required_fields(쿠팡 Adapter
    # 제출 Schema용 가격·배송 데이터)와는 별개로, channel_policy 도메인
    # (원산지·브랜드·구매옵션·상품식별번호 등 정책 판정용 구조화 값)이
    # 요구하는 값을 위저드 호출자가 실제로 알고 있다면 여기로 전달할
    # 수 있다. 값이 없으면 빈 값 그대로 정직하게 평가된다(추정 채움
    # 없음) — listing_wizard_submission.py::_submit_one_channel()이
    # 이 값을 채널 정책 자동 평가에 그대로 사용한다.
    channel_policy_attributes: dict = Field(default_factory=dict)
    channel_policy_confirmed_evidence_rule_codes: list[str] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(extra="forbid")



class WizardFulfillmentUpdateRequest(_ExpectedVersionRequest):

    selections: list[FulfillmentSelectionInput] = Field(default_factory=list)


class CategoryRecommendationResponse(BaseModel):
    display_category_code: str
    display_category_name: str


class CategoryNoticeFieldResponse(BaseModel):
    key: str
    label: str
    required: bool
    allow_not_applicable: bool = False


class PurchaseOptionAttributeResponse(BaseModel):
    """2026-08-29 V7 종합 감사 Phase 4(V7-COUPANG-META-001 수정) —
    이전에는 이 화면이 `CategoryNoticeFieldResponse`(key/label/
    required만 있는 고시정보용 계약)를 구매옵션에도 그대로 재사용해
    `inputType`/`inputValues`/`exposed` 등이 API 응답에서 전부
    빠졌다. 전용 계약으로 분리한다."""

    attribute_type_name: str
    data_type: str = ""
    basic_unit: str = ""
    usable_units: list[str] = Field(default_factory=list)
    required: bool = False
    input_type: str = ""
    input_values: list[str] = Field(default_factory=list)
    group_number: str = "NONE"
    exposed: bool = False


class RequiredDocumentResponse(BaseModel):
    """2026-08-29 Phase 4(V7-COUPANG-META-002 수정) — 이전에는 이
    정보 자체가 API 응답에 없었다."""

    template_name: str
    required: str = "OPTIONAL"


class CertificationResponse(BaseModel):
    """2026-08-29 Phase 4(V7-COUPANG-META-002 수정) — 이전에는 이
    정보 자체가 API 응답에 없었다."""

    certification_type: str
    name: str = ""
    data_type: str = "NONE"
    required: str = "OPTIONAL"


class CategoryMetadataResponse(BaseModel):
    display_category_code: str
    display_category_name: str
    metadata_version: str
    metadata_fingerprint: str
    notice_fields: list[CategoryNoticeFieldResponse]
    purchase_option_fields: list[PurchaseOptionAttributeResponse]
    required_documents: list[RequiredDocumentResponse] = Field(default_factory=list)
    certifications: list[CertificationResponse] = Field(default_factory=list)


class OutboundShippingPlaceResponse(BaseModel):
    outbound_shipping_place_code: str
    shipping_place_name: str
    usable: bool


class ReturnShippingCenterResponse(BaseModel):
    return_center_code: str
    shipping_place_name: str
    usable: bool


class EconomicsInputItem(BaseModel):
    """
    입력은 전부 Decimal — 마진 계산기는 이 값을 그대로 소비한다.
    비율(fee/reserve/tax)은 0~1 사이 소수(예: 10% -> 0.10)로 받는다.
    """

    marketplace_account_id: int
    cost_of_goods: Decimal = Field(ge=0)
    sale_price: Decimal = Field(ge=0)
    channel_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    payment_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    packaging_cost: Decimal = Field(default=Decimal("0"), ge=0)
    ad_cost: Decimal = Field(default=Decimal("0"), ge=0)
    return_reserve_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    tax_basis_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    model_config = ConfigDict(extra="forbid")


class WizardEconomicsUpdateRequest(_ExpectedVersionRequest):

    items: list[EconomicsInputItem] = Field(default_factory=list)


class EconomicsResultItem(BaseModel):
    """
    `break_even_price`는 수수료·정산 비율(channel_fee_rate +
    payment_fee_rate + return_reserve_rate + tax_basis_rate) 합이 1
    이상이면 어떤 가격을 매겨도 손익분기 자체가 수학적으로 불가능하므로
    None이다(0으로 추측하지 않는다 — fail-closed) — 사전검사 엔진이
    이 경우를 BLOCKING 사유로 잡는다.
    """

    marketplace_account_id: int
    expected_revenue: Decimal
    total_cost: Decimal
    margin_amount: Decimal
    margin_rate: Decimal
    break_even_price: Decimal | None


class ValidationIssue(BaseModel):
    """
    완성된 한국어 문장을 저장하지 않는다 — `localized_message_key` +
    구조화 `params`만 저장하고, 실제 문구는 화면(i18n)이 조립한다.
    """

    code: str
    severity: str
    step: str
    field: str | None = None
    channel: str | None = None
    blocking: bool
    localized_message_key: str
    params: dict = Field(default_factory=dict)


class WizardValidateRequest(_ExpectedVersionRequest):
    pass


class WizardValidateResponse(BaseModel):

    status: str
    issues: list[ValidationIssue]


class ApprovalPreviewResponse(BaseModel):

    approval_package: dict
    fingerprint: str
    approval_nonce: str
    nonce_expires_at: datetime
    version: int


class WizardApproveRequest(BaseModel):

    expected_version: int
    approval_nonce: str
    expected_fingerprint: str
    # 2026-08-20 3차 지시 — 시스템이 용량·향·구성 정확성을 자동
    # 판별할 수 없으므로, 사용자가 최종 검토 화면(상품명·용량·향·
    # 구성 + 선택 이미지 썸네일)을 보고 "이미지가 등록 상품과
    # 일치함"을 명시적으로 확인해야만 승인이 통과한다.
    product_image_match_confirmed: bool

    model_config = ConfigDict(extra="forbid")


class WizardRejectRequest(BaseModel):

    expected_version: int
    reason: str = Field(min_length=1, max_length=1000)

    model_config = ConfigDict(extra="forbid")


class WizardRevokePreviewResponse(BaseModel):
    """
    Gate Q-1(2026-08-09) — 승인 취소 미리보기. 지금 취소하려는 그
    승인의 내용(현재 저장된 Package·fingerprint)을 그대로 보여줘,
    사용자가 "무엇을 취소하는지" 확인한 뒤에만 취소 nonce를 소비할 수
    있게 한다(승인 미리보기와 완전히 대칭인 설계).
    """

    approval_package: dict
    fingerprint: str
    approved_by_user_id: int | None
    approved_at: datetime | None
    revoke_nonce: str
    nonce_expires_at: datetime
    version: int


class WizardRevokeApprovalRequest(BaseModel):

    expected_version: int
    reason: str = Field(min_length=1, max_length=500)
    revoke_nonce: str

    model_config = ConfigDict(extra="forbid")


class WizardSubmitRequest(BaseModel):

    expected_version: int
    execution_mode: str  # DRAFT_SAVE / SUBMIT

    model_config = ConfigDict(extra="forbid")


class WizardCloneRequest(BaseModel):

    creation_idempotency_key: str = Field(min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class WizardResultChannelItem(BaseModel):

    marketplace_account_id: int
    listing_id: int | None
    status: str
    error_reason: str | None
    submission_id: int | None = None
    # 2026-08-30 후속 지시(성공 경고 보존) — SUBMITTED여도 확인이
    # 필요한 안내(예: Brand Enrollment 경고)가 있으면 담는다. 이미
    # 마스킹된 값만 들어온다(coupang_live_provider.py 참고).
    provider_warning_summary: str | None = None


class WizardResultsResponse(BaseModel):

    wizard_id: int
    status: str
    channels: list[WizardResultChannelItem]


class WizardLivePreflightResponse(BaseModel):

    ready: bool
    blockers: list[str]
    submission_id: int
    listing_id: int


class WizardLiveSendResponse(BaseModel):

    outcome: str
    external_reference: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    http_status: int | None = None
    correlation_id: str | None = None
    # 2026-08-30 후속 지시(성공 경고 보존) — outcome=SUBMITTED여도
    # 확인이 필요한 안내가 있으면 담는다(마스킹된 값).
    warning_summary: str | None = None


class WizardLiveStatusResponse(BaseModel):
    """2026-08-29 쿠팡 상품등록 핵심 차단 해결(V7-COUPANG-STATUS-001
    Service·Router 연결) — Provider 메서드는 Phase 4에서 이미
    추가됐으나 Service·Router·UI 어디에도 연결되지 않아 실제 업무
    흐름에서 쓸 수 없었다(코드로 확인된 결함). 이 응답 자체는 실제
    호출 없이도 구조만 정의한다 — 실제 호출은 Phase 10 사용자 승인
    후에만 실행한다."""

    outcome: str
    status_name: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_summary: str | None = None


class WizardDeleteRequest(BaseModel):
    """개별 삭제(soft delete=ARCHIVED). expected_version은 다른 모든
    PATCH 계열 요청과 동일한 낙관적 동시성 계약 — 다른 곳에서 이미
    바뀐 위저드를 모르고 삭제 시도하는 것을 막는다."""

    expected_version: int
    reason: str = Field(min_length=1, max_length=500)
    deletion_request_id: str = Field(min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class WizardRestoreRequest(BaseModel):

    expected_version: int

    model_config = ConfigDict(extra="forbid")


class WizardBulkArchiveRequest(BaseModel):
    """
    두 모드 중 정확히 하나만 선택한다:
      1) wizard_ids — "현재 페이지 전체 선택"/"선택 삭제"(사용자가 실제
         화면에서 명시적으로 고른 id만, 상한 없음 대신 서버가 사실상
         현재 페이지 크기 수준으로 제한된 것을 신뢰 — 화면에 없는
         항목은 애초에 이 목록에 담길 수 없다).
      2) select_all_matching_filter — "현재 필터의 삭제 가능한 대기
         상품 N건 삭제"(전체 검색결과). 이 모드만 confirm_text가
         정확히 "삭제"여야 하고, MAX_BULK_ARCHIVE_COUNT 상한이 걸리며,
         recent-auth 재확인도 router.py에서 별도로 요구한다.
    """

    wizard_ids: list[int] | None = Field(default=None, max_length=500)
    select_all_matching_filter: bool = False
    status_filter: str | None = None
    reason: str = Field(min_length=1, max_length=500)
    confirm_text: str | None = None
    deletion_request_id: str = Field(min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class WizardBulkArchiveResultItem(BaseModel):

    wizard_id: int
    outcome: str = Field(description="ARCHIVED / SKIPPED / FAILED")
    reason: str | None = None


class WizardBulkArchiveResponse(BaseModel):

    requested_count: int
    succeeded: list[int]
    skipped: list[WizardBulkArchiveResultItem]
    failed: list[WizardBulkArchiveResultItem]


class WizardBulkArchivePreviewResponse(BaseModel):
    """
    "현재 필터의 삭제 가능한 대기 상품 N건 삭제" 버튼 문구에 쓸 정확한
    건수를 클라이언트가 삭제를 실행하기 전에 미리 확인하기 위한
    읽기 전용 엔드포인트 — 이 값 자체는 아무것도 바꾸지 않는다.
    """

    deletable_count: int
    capped: bool = Field(
        description="deletable_count가 MAX_BULK_ARCHIVE_COUNT 상한에 "
        "걸려 잘렸는지 — True면 필터를 좁혀야 한 번에 전체 삭제 가능.",
    )


class WizardBulkMarginApplyRequest(BaseModel):
    """
    두 모드 중 정확히 하나만 선택한다(WizardBulkArchiveRequest와 동일
    원칙): wizard_ids(명시적 선택) 또는 select_all_matching_filter(현재
    필터 전체, confirm_text 필요). target_margin_rate 하나를 대상
    위저드 각각의 기존 원가·수수료율에 적용해 판매가를 새로 역산한다 —
    원가 데이터 자체는 각 위저드에 이미 저장된 것을 그대로 쓰고
    건드리지 않는다.
    """

    wizard_ids: list[int] | None = Field(default=None, max_length=500)
    select_all_matching_filter: bool = False
    status_filter: str | None = None
    target_margin_rate: Decimal = Field(ge=0, le=1)
    confirm_text: str | None = None

    model_config = ConfigDict(extra="forbid")


class WizardBulkMarginApplyResultItem(BaseModel):

    wizard_id: int
    outcome: str = Field(description="APPLIED / SKIPPED / FAILED")
    reason: str | None = None


class WizardBulkMarginApplyResponse(BaseModel):

    requested_count: int
    succeeded: list[int]
    skipped: list[WizardBulkMarginApplyResultItem]
    failed: list[WizardBulkMarginApplyResultItem]


class WizardBulkMarginApplyPreviewResponse(BaseModel):
    """대상 건수만 미리 보여준다 — 아무것도 바꾸지 않는다."""

    applicable_count: int
    capped: bool = Field(
        description="applicable_count가 MAX_BULK_MARGIN_APPLY_COUNT "
        "상한에 걸려 잘렸는지.",
    )


class WizardBulkSubmitRequest(BaseModel):
    """
    "선택 삭제"와 달리 전체 필터 제출은 지원하지 않는다 — 제출은
    실제 채널 등록(구조적 성공 기준)까지 이어지는 되돌리기 어려운
    작업이라, 사용자가 화면에서 명시적으로 고른 위저드만 대상으로
    한다(위험 반경을 의도적으로 좁힘).
    """

    wizard_ids: list[int] = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


class WizardBulkSubmitResultItem(BaseModel):

    wizard_id: int
    outcome: str = Field(description="SUBMITTED / SKIPPED / FAILED")
    reason: str | None = None


class WizardBulkSubmitResponse(BaseModel):

    requested_count: int
    succeeded: list[int]
    skipped: list[WizardBulkSubmitResultItem]
    failed: list[WizardBulkSubmitResultItem]


__all__ = [
    "WizardCreateRequest",
    "WizardListItem",
    "WizardDetailResponse",
    "WizardSourceUpdateRequest",
    "WizardOptionInput",
    "WizardSkuInput",
    "WizardDraftUpdateRequest",
    "WizardMediaUpdateRequest",
    "WizardChannelsUpdateRequest",
    "FulfillmentSelectionInput",
    "WizardFulfillmentUpdateRequest",
    "EconomicsInputItem",
    "WizardEconomicsUpdateRequest",
    "EconomicsResultItem",
    "ValidationIssue",
    "WizardValidateRequest",
    "WizardValidateResponse",
    "ApprovalPreviewResponse",
    "WizardApproveRequest",
    "WizardRejectRequest",
    "WizardRevokePreviewResponse",
    "WizardRevokeApprovalRequest",
    "WizardSubmitRequest",
    "WizardCloneRequest",
    "WizardResultChannelItem",
    "WizardResultsResponse",
    "WizardLivePreflightResponse",
    "WizardLiveSendResponse",
    "WizardDeleteRequest",
    "WizardRestoreRequest",
    "WizardBulkArchiveRequest",
    "WizardBulkArchiveResultItem",
    "WizardBulkArchiveResponse",
    "WizardBulkArchivePreviewResponse",
    "WizardBulkMarginApplyRequest",
    "WizardBulkMarginApplyResultItem",
    "WizardBulkMarginApplyResponse",
    "WizardBulkMarginApplyPreviewResponse",
    "WizardBulkSubmitRequest",
    "WizardBulkSubmitResultItem",
    "WizardBulkSubmitResponse",
]
