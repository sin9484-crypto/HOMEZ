"""
=========================================================
Homez OS

File : app/domains/media_asset/schema.py
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from typing import Literal


class ImageGenerationItemRequest(BaseModel):

    purpose: str = Field(..., description="MAIN / DETAIL / THUMBNAIL")


class ImageGenerationJobSubmitRequest(BaseModel):

    product_candidate_id: int
    listing_package_id: int | None = None
    provider_code: str = Field(default="FAKE")
    model_name: str | None = None
    items: list[ImageGenerationItemRequest] = Field(..., min_length=1)
    style_params: dict = Field(default_factory=dict)
    idempotency_key: str = Field(..., min_length=1, max_length=160)


class MediaAssetUploadRequest(BaseModel):
    """사용자가 직접 촬영/보유한 실제 제품 이미지를 업로드한다.

    multipart/python-multipart 신규 의존성을 피하기 위해 base64 JSON
    본문을 쓴다 — 이미지 자체는 10MB 한도(constants.py)라 base64
    팽창(약 33%)을 감안해도 JSON 본문으로 무리 없다."""

    owner_type: str = Field(..., description="PRODUCT_CANDIDATE / LISTING_PACKAGE")
    owner_id: int
    purpose: str = Field(default="MAIN", description="MAIN / DETAIL / THUMBNAIL")
    display_order: int = Field(default=0)
    image_base64: str = Field(..., min_length=1)
    original_filename: str | None = Field(default=None, max_length=255)


class BackgroundRemovalRequestSchema(BaseModel):

    source_asset_id: int
    provider_code: str = Field(default="REMBG", description="FAKE / REMBG")


class ManualImageEditSaveRequest(BaseModel):
    """
    2026-08-29 Phase 5(Fabric.js 수동 편집기 MVP) — 브라우저 캔버스에서
    자르기/회전/밝기·대비 조정/도형·화살표 그리기/되돌리기까지 전부
    끝난 최종 결과 이미지 1장만 받는다. 편집 단계별 상태(레이어,
    undo 이력)는 서버에 보관하지 않는다 — 새로고침 시 편집 중이던
    내용은 사라진다(이번 MVP 범위, 자동 임시저장은 없음).
    """

    source_asset_id: int
    image_base64: str = Field(..., min_length=1)


class RightsVerificationConfirmRequest(BaseModel):

    basis: str = Field(
        ..., description="SELF_CAPTURED / SUPPLIER_BRAND_PERMISSION / COMMERCIAL_LICENSE",
    )


class RightsVerificationRevokeRequest(BaseModel):

    reason: str = Field(..., min_length=1, max_length=500)


class LongDetailImageUploadRequest(BaseModel):
    """"상세페이지용 긴 이미지"임을 사용자가 명시적으로 선택했을 때만
    쓴다 — 일반 업로드(MediaAssetUploadRequest)와 별개 경로."""

    owner_type: str
    owner_id: int
    image_base64: str = Field(..., min_length=1)
    original_filename: str | None = Field(default=None, max_length=255)
    segment_height: int = Field(default=1200, ge=600, le=1500)
    # FIXED: 고정 높이 분할(기존), SECTION: 섹션 경계 분할(글자·표가 중간에서 잘리지 않음)
    mode: Literal["FIXED", "SECTION"] = "FIXED"


class LongDetailImageUploadResponse(BaseModel):

    original: MediaAssetResponse
    segments: list[MediaAssetResponse]


class ImageSplitRequestSchema(BaseModel):

    source_asset_id: int
    segment_height: int = Field(default=1200, ge=600, le=1500)
    mode: Literal["FIXED", "SECTION"] = "FIXED"


class BackgroundCompositionRequestSchema(BaseModel):

    cutout_asset_id: int
    background_kind: str = Field(
        default="WHITE", description="WHITE / SOLID_COLOR / GRADIENT",
    )
    color_hex: str | None = None
    color_hex_end: str | None = None
    # 컨셉을 고르면 위 배경 값 대신 컨셉의 배경을 쓴다(대표 이미지는 흰 배경 컨셉만 허용).
    concept_code: str | None = None
    purpose: Literal["MAIN", "ADDITIONAL"] = "ADDITIONAL"


class BackgroundSuggestionRequestSchema(BaseModel):
    """제품 색에 맞춘 배경 제안 요청(읽기 전용). variation을 바꾸면 같은 제품의 다른 색조 묶음."""

    cutout_asset_id: int
    purpose: Literal["MAIN", "ADDITIONAL", "DETAIL"] = "ADDITIONAL"
    count: int = Field(default=6, ge=1, le=8)
    variation: int = Field(default=0, ge=0, le=11)


class AdaptiveCompositionRequestSchema(BaseModel):
    cutout_asset_id: int
    suggestion_code: str
    purpose: Literal["MAIN", "ADDITIONAL", "DETAIL"] = "ADDITIONAL"
    variation: int = Field(default=0, ge=0, le=11)
    shadow: bool = False
    fill_ratio: float = Field(default=0.86, ge=0.3, le=1.0)
    # 크롭이 작아 제품이 작게 나올 때만 확대를 허용한다(1.0 = 확대 없음, 최대 2.5배).
    max_upscale: float = Field(default=1.0, ge=1.0, le=2.5)


class ReferenceGenerationRequestSchema(BaseModel):
    """참고 사진으로 만들기(2026-10-22 R3a) — 배경·포즈는 서버의 프리셋 코드만 받는다(글을 그대로 받지 않는다)."""

    reference_asset_id: int
    purpose: Literal["MAIN", "ADDITIONAL", "DETAIL"] = "ADDITIONAL"
    backgrounds: list[str] = Field(..., min_length=1, max_length=3)
    poses: list[str] = Field(..., min_length=1, max_length=3)
    count: int = Field(default=1, ge=1, le=6)
    # 대표(MAIN)에 AI 생성 사진을 쓰는 데 대한 사용자 최종 확인. 서버가 MAIN이면 이 값이 True여야만 실행한다.
    confirmed_main: bool = False
    # 2026-10-25 — FAKE(시연용) 또는 GEMINI(실제 AI, 설정·키·승인 센터 허용 필요). 대화로 준 요청 글(선택, 500자).
    provider_code: Literal["FAKE", "GEMINI"] = "FAKE"
    instruction: str | None = Field(default=None, max_length=500)


class ImageAiSettingsSchema(BaseModel):
    """2026-10-25 AI 이미지(Gemini) 설정. API 키는 저장할 때만 보내고 응답에는 실리지 않는다(빈 값이면 기존 키 유지)."""

    model: str = Field(min_length=3, max_length=80)
    image_size: Literal["1K", "2K", "4K"] = "2K"
    price_per_image_krw: float = Field(default=0, ge=0, le=100_000)
    api_key: str | None = Field(default=None, max_length=300)


class ReferenceGenerationResponseSchema(BaseModel):
    plan: dict
    assets: list["MediaAssetResponse"]
    job_id: int | None
    demo: bool
    generated_label: str
    label_placement: str
    provider_cost: float | None = None


class BatchAdjustRequestSchema(BaseModel):
    """선택한 사진에 같은 밝기·색온도 보정을 한 번에 적용한다(2026-10-23 R2). 둘 다 정수 −50~+50, 둘 다 0이면 보정할 것이 없다(서버가 거절)."""

    asset_ids: list[int] = Field(..., min_length=1, max_length=20)
    brightness: int = Field(default=0, ge=-50, le=50, strict=True)
    warmth: int = Field(default=0, ge=-50, le=50, strict=True)

    @field_validator("asset_ids")
    @classmethod
    def _positive_ids(cls, value):
        if any(isinstance(v, bool) or v <= 0 for v in value):
            raise ValueError("사진 번호는 양의 정수여야 합니다.")
        return value


class BatchAdjustItemSchema(BaseModel):
    asset_id: int
    status: str
    reason: str | None = None
    asset: "MediaAssetResponse | None" = None


class BatchAdjustResponseSchema(BaseModel):
    results: list[BatchAdjustItemSchema]
    created: int
    reused: int
    skipped: int
    failed: int


class ConceptRunRequestSchema(BaseModel):
    """누끼 한 장으로 고른 컨셉 여러 개를 각각 따로 만든다(2026-10-23 I2). 컨셉 코드는 서버의 컨셉 목록에 있는 것만 처리하고 나머지는 건너뛴다."""

    cutout_asset_id: int
    concept_codes: list[str] = Field(..., min_length=1, max_length=6)
    purpose: Literal["MAIN", "ADDITIONAL", "DETAIL"] = "ADDITIONAL"

    @field_validator("concept_codes")
    @classmethod
    def _codes(cls, value):
        import re

        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,39}", v) for v in value):
            raise ValueError("컨셉 코드는 영문 대문자·숫자·밑줄 3~40자여야 합니다.")
        return value


class ConceptCheckSchema(BaseModel):
    code: str
    status: str
    detail: dict


class ConceptRunItemSchema(BaseModel):
    concept_code: str
    status: str
    reason: str | None = None
    usable: bool | None = None
    checks: list[ConceptCheckSchema] = []
    asset: "MediaAssetResponse | None" = None


class ConceptRunResponseSchema(BaseModel):
    results: list[ConceptRunItemSchema]
    created: int
    reused: int
    check_failed: int
    skipped: int
    failed: int


class FeatureHighlightInput(BaseModel):

    title: str = Field(..., min_length=1, max_length=60)
    description: str = Field(..., min_length=1, max_length=200)


class SpecRowInput(BaseModel):

    label: str = Field(..., min_length=1, max_length=30)
    value: str = Field(..., min_length=1, max_length=200)


class DetailPageGenerationRequestSchema(BaseModel):
    """쿠팡 상세이미지 자동 생성 요청 — 실제 상품 사진(hero_asset_id,
    권리 확인된 자산이어야 함)과 사용자가 입력한 텍스트를 조합한다.
    새 각도·장면을 AI로 만들어내지 않는다(app/domains/media_asset/
    detail_page_generator.py 원칙 참고) — hero_asset_id가 가리키는
    실제 사진 그대로가 그대로 들어간다."""

    hero_asset_id: int
    brand_name: str = Field(..., min_length=1, max_length=60)
    tagline: str | None = Field(default=None, max_length=100)
    feature_highlights: list[FeatureHighlightInput] = Field(default_factory=list)
    spec_rows: list[SpecRowInput] = Field(default_factory=list)
    usage_text: str | None = Field(default=None, max_length=1000)
    accent_color_hex: str = "#2D6CDF"
    # 페이지 전체 컨셉과 섹션별 컨셉(BRAND·HERO·FEATURES·SPECS·USAGE). 없으면 기존과 같은 흰 배경.
    concept_code: str | None = None
    section_concepts: dict[str, str] = Field(default_factory=dict)


class ImageSearchQueryRequest(BaseModel):

    product_name: str = Field(..., min_length=1)
    brand: str | None = None
    scent: str | None = None
    volume: str | None = None
    composition: str | None = None
    barcode: str | None = None
    supplier_sku: str | None = None
    provider_code: str = Field(default="FAKE")


class ImageSearchResultItemResponse(BaseModel):

    title: str
    source_page_url: str
    preview_image_url: str
    provider_code: str
    source_site: str
    width: int | None
    height: int | None
    file_format: str | None
    searched_at: datetime
    match_status: str
    permission_status: str
    selectable: bool


class MediaAssetResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    owner_type: str
    owner_id: int
    asset_role: str
    source_asset_id: int | None
    channel_code: str | None
    purpose: str
    display_order: int
    storage_path: str
    mime_type: str
    file_size_bytes: int
    sha256_hex: str
    width: int | None
    height: int | None
    status: str
    rights_status: str
    source_url: str | None = None
    source_domain: str | None = None
    source_classification: str = "UNKNOWN"
    created_at: datetime


class ImageGenerationResultResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    media_asset_id: int | None
    sequence_index: int
    purpose: str
    status: str
    safety_check_status: str
    error_reason: str | None
    created_at: datetime


class ImageGenerationJobResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    listing_package_id: int | None
    product_candidate_id: int
    provider_code: str
    model_name: str | None
    status: str
    progress_percent: int
    retry_count: int
    max_retries: int
    estimated_cost: float | None
    actual_cost: float | None
    error_reason: str | None
    idempotency_key: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class UrlImageImportRequest(BaseModel):
    """URL 하나를 실제로 가져와 MediaAsset으로 저장한다(SSRF 안전
    다운로드는 url_import_service.py가 담당)."""

    owner_type: str = Field(..., description="PRODUCT_CANDIDATE / LISTING_PACKAGE")
    owner_id: int
    purpose: str = Field(default="MAIN", description="MAIN / DETAIL / THUMBNAIL")
    display_order: int = Field(default=0)
    source_url: str = Field(..., min_length=1, max_length=2000)
    source_classification: str = Field(
        default="UNKNOWN",
        description="SUPPLIER / MANUFACTURER / USER_CAPTURED / UNKNOWN",
    )


class UrlImageImportBatchItemRequest(BaseModel):

    source_url: str = Field(..., min_length=1, max_length=2000)


class UrlImageImportBatchRequest(BaseModel):
    """여러 URL을 한 번에 가져온다 — 항목 하나가 실패해도 나머지는
    각자 독립적으로 계속 시도한다(부분 실패 허용)."""

    owner_type: str = Field(..., description="PRODUCT_CANDIDATE / LISTING_PACKAGE")
    owner_id: int
    purpose: str = Field(default="MAIN", description="MAIN / DETAIL / THUMBNAIL")
    items: list[UrlImageImportBatchItemRequest] = Field(..., min_length=1, max_length=40)
    source_classification: str = Field(
        default="UNKNOWN",
        description="SUPPLIER / MANUFACTURER / USER_CAPTURED / UNKNOWN",
    )


class UrlImageImportBatchResultItemResponse(BaseModel):

    source_url: str
    success: bool
    asset: MediaAssetResponse | None = None
    error_message: str | None = None


class UrlImageImportBatchResponse(BaseModel):

    results: list[UrlImageImportBatchResultItemResponse]


class ProductPageImageCandidatesRequest(BaseModel):

    product_url: str = Field(..., min_length=1, max_length=2000)


class ImageCandidateResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    url: str
    source_domain: str
    is_declared_representative: bool


class ImageGenerationJobCancelRequest(BaseModel):

    idempotency_key: str = Field(..., min_length=1, max_length=160)


class ImageGenerationJobRetryRequest(BaseModel):

    idempotency_key: str = Field(..., min_length=1, max_length=160)


__all__ = [
    "ImageGenerationItemRequest",
    "ImageGenerationJobSubmitRequest",
    "MediaAssetUploadRequest",
    "LongDetailImageUploadRequest",
    "LongDetailImageUploadResponse",
    "RightsVerificationConfirmRequest",
    "RightsVerificationRevokeRequest",
    "BackgroundRemovalRequestSchema",
    "BackgroundCompositionRequestSchema",
    "ImageSearchQueryRequest",
    "ImageSearchResultItemResponse",
    "ImageSplitRequestSchema",
    "MediaAssetResponse",
    "ImageGenerationResultResponse",
    "ImageGenerationJobResponse",
    "ImageGenerationJobCancelRequest",
    "ImageGenerationJobRetryRequest",
    "ImageRightsEvidenceCreateRequest",
    "ImageRightsEvidenceResponse",
    "ImageRightsAcknowledgeRequest",
    "ImageRightsAcknowledgeResponse",
    "UrlImageImportRequest",
    "UrlImageImportBatchItemRequest",
    "UrlImageImportBatchRequest",
    "UrlImageImportBatchResultItemResponse",
    "UrlImageImportBatchResponse",
    "ProductPageImageCandidatesRequest",
    "ImageCandidateResponse",
]


class ImageRightsEvidenceCreateRequest(BaseModel):
    """증빙 등록 — 등록해도 HOMEZ가 진위나 법적 효력을 보증하지
    않는다. supplier_id 또는 source_domain 중 하나는 필수."""

    supplier_id: int | None = None
    source_domain: str | None = Field(default=None, max_length=255)
    evidence_type: str = Field(..., min_length=1, max_length=50)
    allowed_channels: list[str] = Field(default_factory=list)
    commercial_use_status: str = Field(default="UNKNOWN")
    editing_allowed: bool | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    evidence_reference: str | None = Field(default=None, max_length=500)
    memo: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class ImageRightsEvidenceResponse(BaseModel):

    id: int
    supplier_id: int | None
    source_domain: str | None
    evidence_type: str
    allowed_channels: list[str]
    commercial_use_status: str
    editing_allowed: bool | None
    valid_from: datetime | None
    valid_until: datetime | None
    evidence_reference: str | None
    memo: str | None
    verified_by_user_id: int
    verified_at: datetime
    created_at: datetime


class ImageRightsAcknowledgeRequest(BaseModel):
    """권리 경고 화면에서의 사용자 선택 — 증빙 미제출은 이 선택과
    무관하게 어떤 기능도 차단하지 않는다. 이 기록은 "경고를 봤다"는
    사실만 남긴다."""

    asset_id: int
    workflow_stage: str = Field(
        ..., description="GENERATE_OR_EDIT_OR_EXPORT / CHANNEL_SUBMISSION_FINAL",
    )
    warning_code: str = Field(..., min_length=1, max_length=50)
    user_action: str = Field(
        ..., description="CONTINUE / REGISTER_EVIDENCE_LATER / CANCELLED",
    )

    model_config = ConfigDict(extra="forbid")


class ImageRightsAcknowledgeResponse(BaseModel):

    id: int
    asset_id: int
    workflow_stage: str
    warning_code: str
    user_action: str
    image_fingerprint: str
    acknowledged_at: datetime


class R2CredentialsSaveRequest(BaseModel):
    """2026-08-29 Pre-Live 검증 — R2 공개 이미지 호스팅 Credential 저장.
    Secret 원문은 Windows Credential Manager에만 저장되고 DB·로그에
    남지 않는다(app/domains/media_asset/public_hosting.py와 동일 계약).
    """

    account_id: str = Field(min_length=1, max_length=100)
    access_key_id: str = Field(min_length=1, max_length=200)
    secret_access_key: str = Field(min_length=1, max_length=200)
    bucket_name: str = Field(min_length=1, max_length=100)
    public_base_url: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")


class R2CredentialsStatusResponse(BaseModel):
    """Secret 값은 절대 포함하지 않는다 — 설정 여부만 반환한다."""

    configured: bool


class WizardImageAutoUploadResponse(BaseModel):
    """위저드가 이미 선택한 media_asset들을 R2에 업로드해 얻은 공개
    URL 목록 — coupang_image_autofill.autofill_coupang_images()와
    동일한 REPRESENTATION+DETAIL 배정 규칙을 그대로 반환한다."""

    images: list[dict]


class ImageConceptResponse(BaseModel):
    """화면에서 컨셉을 고를 때 쓰는 목록 항목."""

    code: str
    label_ko: str
    description: str
    usable_for: list[str]
    preview: dict


class DetailSectionResponse(BaseModel):
    key: str
    label_ko: str
