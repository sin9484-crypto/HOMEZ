"""
=========================================================
Homez OS

File : app/domains/media_asset/router.py

이미지 생성 Job 제출/취소/재시도/조회 API — 전부 admin_guard로
보호되고 current_user.company_id를 Service에 전달한다. 이 라우터는
실제 외부 Provider를 호출하지 않는다(FAKE만 실제로 동작).
=========================================================
"""

import base64
import binascii
import json

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Response
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.guard import admin_guard
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.schema import ImageGenerationJobCancelRequest
from app.domains.media_asset.schema import ImageGenerationJobResponse
from app.domains.media_asset.schema import ImageGenerationJobRetryRequest
from app.domains.media_asset.schema import ImageGenerationJobSubmitRequest
from app.domains.media_asset.schema import ImageGenerationResultResponse
from app.domains.media_asset.schema import MediaAssetResponse
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.image_search_providers import ImageSearchQuery
from app.domains.media_asset.image_search_providers import (
    ImageSearchProviderError,
)
from app.domains.media_asset.image_search_providers import (
    ImagePermissionStatus,
)
from app.domains.media_asset.image_search_providers import (
    get_image_search_provider,
)
from app.domains.media_asset.schema import BackgroundCompositionRequestSchema
from app.domains.media_asset.schema import BackgroundRemovalRequestSchema
from app.domains.media_asset.schema import DetailPageGenerationRequestSchema
from app.domains.media_asset.schema import ImageSearchQueryRequest
from app.domains.media_asset.schema import ImageSearchResultItemResponse
from app.domains.media_asset.schema import ImageSplitRequestSchema
from app.domains.media_asset.schema import LongDetailImageUploadRequest
from app.domains.media_asset.schema import LongDetailImageUploadResponse
from app.domains.media_asset.schema import ManualImageEditSaveRequest
from app.domains.media_asset.schema import MediaAssetUploadRequest
from app.domains.media_asset.schema import RightsVerificationConfirmRequest
from app.domains.media_asset.schema import RightsVerificationRevokeRequest
from app.domains.media_asset import schema
from app.domains.media_asset.rights_evidence_service import (
    ImageRightsEvidenceService,
)
from app.domains.media_asset.url_import import RequestsTransport
from app.domains.media_asset.url_import_service import UrlImageImportService
from app.domains.media_asset.public_hosting import MediaHostingNotConfiguredError
from app.domains.media_asset.public_hosting import R2Credentials
from app.domains.media_asset.public_hosting import load_r2_credentials
from app.domains.media_asset.public_hosting import save_r2_credentials
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.user.model import User

router = APIRouter(prefix="/media-assets", tags=["Media Asset"])


@router.post("/image-jobs", response_model=ImageGenerationJobResponse)
def submit_image_job(
    data: ImageGenerationJobSubmitRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)
    job, _duplicate = service.submit_job(
        data, current_user.id, current_user.company_id,
    )

    return job


@router.post(
    "/image-jobs/{job_id}/cancel", response_model=ImageGenerationJobResponse,
)
def cancel_image_job(
    job_id: int,
    data: ImageGenerationJobCancelRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)

    return service.cancel_job(
        job_id, data.idempotency_key, current_user.company_id,
    )


@router.post(
    "/image-jobs/{job_id}/retry", response_model=ImageGenerationJobResponse,
)
def retry_image_job(
    job_id: int,
    data: ImageGenerationJobRetryRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)
    job, _duplicate = service.retry_job(
        job_id, data.idempotency_key, current_user.id,
        current_user.company_id,
    )

    return job


@router.get(
    "/image-jobs/{job_id}/results",
    response_model=list[ImageGenerationResultResponse],
)
def list_image_job_results(
    job_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)

    return service.list_results(job_id, current_user.company_id)


@router.post("/upload", response_model=MediaAssetResponse)
def upload_media_asset(
    data: MediaAssetUploadRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """사용자가 직접 촬영/보유한 실제 제품 이미지를 업로드한다(AI
    생성이 아님 — asset_role=ORIGINAL). 실제 브랜드 제품 등록에서
    유일하게 정식으로 허용되는 이미지 확보 경로다."""

    try:
        image_bytes = base64.b64decode(data.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadRequestException("image_base64 디코딩에 실패했습니다.") from exc

    service = ImageGenerationJobQueueService(db)

    return service.upload_original_asset(
        owner_type=data.owner_type,
        owner_id=data.owner_id,
        purpose=data.purpose,
        display_order=data.display_order,
        image_bytes=image_bytes,
        original_filename=data.original_filename,
        company_id=current_user.company_id,
    )


@router.post("/manual-edit", response_model=MediaAssetResponse)
def save_manual_edit(
    data: ManualImageEditSaveRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """Fabric.js 수동 편집기(자르기/회전/밝기·대비/도형·화살표/
    되돌리기)에서 편집이 끝난 최종 결과 이미지를 저장한다. 원본은
    수정하지 않고 새 파생 자산을 만든다."""

    try:
        image_bytes = base64.b64decode(data.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadRequestException("image_base64 디코딩에 실패했습니다.") from exc

    service = ImageGenerationJobQueueService(db)

    return service.save_manual_edit(
        source_asset_id=data.source_asset_id,
        image_bytes=image_bytes,
        requested_by=current_user.id,
        company_id=current_user.company_id,
    )


@router.post(
    "/{asset_id}/confirm-rights-verified", response_model=MediaAssetResponse,
)
def confirm_rights_verified(
    asset_id: int,
    data: RightsVerificationConfirmRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)

    return service.confirm_rights_verified(
        asset_id, current_user.company_id, data.basis, current_user.id,
    )


@router.post(
    "/{asset_id}/revoke-rights-verification", response_model=MediaAssetResponse,
)
def revoke_rights_verification(
    asset_id: int,
    data: RightsVerificationRevokeRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)

    return service.revoke_rights_verification(
        asset_id, current_user.company_id, current_user.id, data.reason,
    )


@router.get("/{asset_id}/file")
def get_media_asset_file(
    asset_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """UI 미리보기(썸네일·긴 이미지 세그먼트 미리보기·최종 검토 화면
    상품 이미지 일치 확인)에서만 쓰는 원본 바이트 조회 — company_id
    경계는 Service가 Repository 조회로 강제한다."""

    service = ImageGenerationJobQueueService(db)
    image_bytes, mime_type = service.get_media_asset_file_bytes(
        asset_id, current_user.company_id,
    )

    return Response(content=image_bytes, media_type=mime_type)


@router.post(
    "/upload-long-detail-image", response_model=LongDetailImageUploadResponse,
)
def upload_and_split_long_detail_image(
    data: LongDetailImageUploadRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """"상세페이지용 긴 이미지"임을 사용자가 명시적으로 선택했을 때만
    쓰는 별도 경로 — 일반 이미지 업로드(/upload)의 한도는 그대로
    유지되고 절대 전역으로 완화되지 않는다."""

    try:
        image_bytes = base64.b64decode(data.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadRequestException("image_base64 디코딩에 실패했습니다.") from exc

    service = ImageGenerationJobQueueService(db)
    original, segments = service.upload_and_split_long_detail_image(
        owner_type=data.owner_type,
        owner_id=data.owner_id,
        image_bytes=image_bytes,
        original_filename=data.original_filename,
        segment_height=data.segment_height,
        requested_by=current_user.id,
        company_id=current_user.company_id,
    )

    return LongDetailImageUploadResponse(original=original, segments=segments)


@router.post("/split", response_model=list[MediaAssetResponse])
def split_long_image(
    data: ImageSplitRequestSchema,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)

    return service.split_long_image(
        source_asset_id=data.source_asset_id,
        segment_height=data.segment_height,
        requested_by=current_user.id,
        company_id=current_user.company_id,
    )


@router.post("/background-removal", response_model=MediaAssetResponse)
def remove_background(
    data: BackgroundRemovalRequestSchema,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)

    return service.remove_background(
        source_asset_id=data.source_asset_id,
        provider_code=data.provider_code,
        requested_by=current_user.id,
        company_id=current_user.company_id,
    )


@router.post("/background-composition", response_model=MediaAssetResponse)
def compose_background(
    data: BackgroundCompositionRequestSchema,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageGenerationJobQueueService(db)
    spec = BackgroundSpec(
        kind=data.background_kind,
        color_hex=data.color_hex,
        color_hex_end=data.color_hex_end,
    )

    return service.compose_background(
        cutout_asset_id=data.cutout_asset_id,
        background_spec=spec,
        requested_by=current_user.id,
        company_id=current_user.company_id,
    )


@router.post("/generate-detail-page", response_model=MediaAssetResponse)
def generate_detail_page(
    data: DetailPageGenerationRequestSchema,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """실제 상품 사진(hero_asset_id) + 브랜드명·특징·스펙·사용법
    텍스트로 쿠팡 상세이미지를 자동 생성한다. 새 각도·장면을 AI로
    만들지 않는다 — hero 사진 그대로에 텍스트·표만 배치한다."""

    service = ImageGenerationJobQueueService(db)

    return service.generate_detail_page(
        hero_asset_id=data.hero_asset_id,
        brand_name=data.brand_name,
        tagline=data.tagline,
        feature_highlights=[
            (f.title, f.description) for f in data.feature_highlights
        ],
        spec_rows=[(r.label, r.value) for r in data.spec_rows],
        usage_text=data.usage_text,
        accent_color_hex=data.accent_color_hex,
        requested_by=current_user.id,
        company_id=current_user.company_id,
    )


@router.post("/search", response_model=list[ImageSearchResultItemResponse])
def search_images(
    data: ImageSearchQueryRequest,
    current_user: User = Depends(admin_guard),
):
    """이미지 검색 — FAKE(네트워크 없음, 시연용)와 NAVER(2026-09-07
    연결, NAVER API HUB 실 이미지 검색)를 지원한다. NAVER 결과도
    permission_status는 항상 UNKNOWN이므로(상업적 재사용 허가 여부를
    이 Provider가 판단하지 않음) selectable=false로 내려간다 —
    결과는 미리보기·후보 목록일 뿐 자동 저장하지 않는다.
    permission_status가 VERIFIED_ALLOWED인 항목만 selectable=true다."""

    try:
        provider = get_image_search_provider(data.provider_code)
    except ImageSearchProviderError as exc:
        raise BadRequestException(str(exc)) from exc

    results = provider.search(ImageSearchQuery(
        product_name=data.product_name, brand=data.brand,
        scent=data.scent, volume=data.volume, composition=data.composition,
        barcode=data.barcode, supplier_sku=data.supplier_sku,
    ))

    return [
        ImageSearchResultItemResponse(
            title=item.title,
            source_page_url=item.source_page_url,
            preview_image_url=item.preview_image_url,
            provider_code=item.provider_code,
            source_site=item.source_site,
            width=item.width,
            height=item.height,
            file_format=item.file_format,
            searched_at=item.searched_at,
            match_status=item.match_status,
            permission_status=item.permission_status,
            selectable=item.permission_status in ImagePermissionStatus.SELECTABLE,
        )
        for item in results
    ]


@router.get(
    "/owners/{owner_type}/{owner_id}/assets",
    response_model=list[MediaAssetResponse],
)
def list_assets_for_owner(
    owner_type: str,
    owner_id: int,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)),
    db: Session = Depends(get_db),
):
    """Gate R-2(2026-08-09) — 상품등록 통합 마법사의 이미지 단계가
    읽기 전용으로 호출한다. admin_guard 대신 LISTING_WIZARD_VIEW로
    전환하되, 회사 소유권 격리(company_id 스코프)는 그대로 유지된다.
    이미지 생성 Job 제출/취소/재시도(쓰기)는 그대로 admin_guard."""

    service = ImageGenerationJobQueueService(db)

    return service.list_active_assets(
        current_user.company_id, owner_type, owner_id,
    )


@router.post(
    "/rights-evidence",
    response_model=schema.ImageRightsEvidenceResponse,
)
def register_rights_evidence(
    data: schema.ImageRightsEvidenceCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """증빙 등록 — 제안일 뿐 강제하지 않는다(2026-08-28 사용자 결정
    4번). 등록해도 HOMEZ가 진위나 법적 효력을 보증하지 않는다."""

    service = ImageRightsEvidenceService(db)
    evidence = service.register_evidence(
        company_id=current_user.company_id,
        verified_by_user_id=current_user.id,
        evidence_type=data.evidence_type,
        supplier_id=data.supplier_id,
        source_domain=data.source_domain,
        allowed_channels=data.allowed_channels,
        commercial_use_status=data.commercial_use_status,
        editing_allowed=data.editing_allowed,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        evidence_reference=data.evidence_reference,
        memo=data.memo,
    )
    return _rights_evidence_to_response(evidence)


@router.get(
    "/rights-evidence",
    response_model=list[schema.ImageRightsEvidenceResponse],
)
def list_rights_evidence(
    supplier_id: int | None = None,
    source_domain: str | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ImageRightsEvidenceService(db)
    rows = service.list_evidence(
        current_user.company_id, supplier_id=supplier_id,
        source_domain=source_domain,
    )
    return [_rights_evidence_to_response(row) for row in rows]


@router.post(
    "/rights-acknowledgements",
    response_model=schema.ImageRightsAcknowledgeResponse,
)
def acknowledge_rights_warning(
    data: schema.ImageRightsAcknowledgeRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """권리 경고 화면에서의 사용자 선택 기록(2026-08-28 사용자 결정
    5·6번) — 증빙 미제출은 이 호출과 무관하게 어떤 기능도 차단하지
    않는다. 이 엔드포인트는 "경고를 봤다"는 사실만 append-only로
    남긴다."""

    service = ImageRightsEvidenceService(db)
    record = service.acknowledge(
        company_id=current_user.company_id,
        user_id=current_user.id,
        asset_id=data.asset_id,
        workflow_stage=data.workflow_stage,
        warning_code=data.warning_code,
        user_action=data.user_action,
    )
    return schema.ImageRightsAcknowledgeResponse(
        id=record.id,
        asset_id=record.asset_id,
        workflow_stage=record.workflow_stage,
        warning_code=record.warning_code,
        user_action=record.user_action,
        image_fingerprint=record.image_fingerprint,
        acknowledged_at=record.acknowledged_at,
    )


def _rights_evidence_to_response(
    evidence,
) -> schema.ImageRightsEvidenceResponse:

    return schema.ImageRightsEvidenceResponse(
        id=evidence.id,
        supplier_id=evidence.supplier_id,
        source_domain=evidence.source_domain,
        evidence_type=evidence.evidence_type,
        allowed_channels=json.loads(evidence.allowed_channels_json or "[]"),
        commercial_use_status=evidence.commercial_use_status,
        editing_allowed=evidence.editing_allowed,
        valid_from=evidence.valid_from,
        valid_until=evidence.valid_until,
        evidence_reference=evidence.evidence_reference,
        memo=evidence.memo,
        verified_by_user_id=evidence.verified_by_user_id,
        verified_at=evidence.verified_at,
        created_at=evidence.created_at,
    )


@router.post("/import-from-url", response_model=MediaAssetResponse)
def import_media_asset_from_url(
    data: schema.UrlImageImportRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """URL 하나를 SSRF 안전 검증을 거쳐 실제로 가져와 MediaAsset으로
    저장한다(app/domains/media_asset/url_import.py의 방어를 그대로
    통과해야 한다). 운영에서는 RequestsTransport(실제 네트워크)를
    쓴다 — 테스트는 이 서비스에 FakeTransport를 직접 주입해 검증한다."""

    service = UrlImageImportService(db, RequestsTransport())

    return service.import_image_from_url(
        owner_type=data.owner_type,
        owner_id=data.owner_id,
        purpose=data.purpose,
        display_order=data.display_order,
        source_url=data.source_url,
        source_classification=data.source_classification,
        company_id=current_user.company_id,
    )


@router.post("/import-from-url/batch", response_model=schema.UrlImageImportBatchResponse)
def import_media_assets_from_urls_batch(
    data: schema.UrlImageImportBatchRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """여러 URL을 한 번에 가져온다 — 항목 하나가 실패해도(SSRF 차단,
    위장 시도, 크기 초과 등) 나머지는 각자 독립적으로 계속 시도한다."""

    service = UrlImageImportService(db, RequestsTransport())

    results = service.import_images_from_urls_batch(
        owner_type=data.owner_type,
        owner_id=data.owner_id,
        purpose=data.purpose,
        source_urls=[item.source_url for item in data.items],
        source_classification=data.source_classification,
        company_id=current_user.company_id,
    )

    return schema.UrlImageImportBatchResponse(
        results=[
            schema.UrlImageImportBatchResultItemResponse(
                source_url=r.source_url,
                success=r.success,
                asset=r.asset,
                error_message=r.error_message,
            )
            for r in results
        ],
    )


@router.post(
    "/product-page-candidates", response_model=list[schema.ImageCandidateResponse],
)
def list_product_page_image_candidates(
    data: schema.ProductPageImageCandidatesRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """상품 페이지 URL에서 이미지 후보 URL만 추출한다 — 이 호출
    자체는 이미지를 하나도 저장하지 않는다("모든 이미지를 무조건
    저장하지 않는다"는 요구사항). 사용자가 이 중 일부를 선택한
    뒤에만 /import-from-url(/batch)로 실제 저장을 요청한다."""

    service = UrlImageImportService(db, RequestsTransport())

    return service.list_product_page_candidates(data.product_url)


# --------------------------------------------------
# 2026-08-29 Pre-Live 검증 — R2 공개 이미지 호스팅. 백엔드
# (app/domains/media_asset/public_hosting.py)는 이미 있었지만 이
# Credential을 저장할 화면·API가 전혀 연결돼 있지 않아, 위저드 5단계
# 이미지 URL 자동채움이 실제로는 항상 실패하던 실사용 결함을 고친다.
# Secret 원문은 요청 처리 중에만 메모리에 존재하고 Windows Credential
# Manager에만 저장된다 — DB·로그·예외 메시지 어디에도 남지 않는다
# (기존 쿠팡 StoreConnection Credential 저장과 동일 원칙).
# --------------------------------------------------

def get_r2_credential_store() -> CredentialStore:
    """
    운영 경로의 기본 Credential 저장소 — 항상 실제 Windows Credential
    Manager다. 테스트는 이 의존성을 오버라이드해 in-memory 구현을
    주입한다(운영 코드 자신은 절대 자동으로 in-memory를 선택하지
    않는다). app/domains/store_connection/router.py::get_credential_store
    와 동일한 관례.
    """

    return WindowsCredentialStore()


@router.post("/r2-hosting/credentials", response_model=schema.R2CredentialsStatusResponse)
def save_r2_hosting_credentials(
    data: schema.R2CredentialsSaveRequest,
    current_user: User = Depends(admin_guard),
    credential_store: CredentialStore = Depends(get_r2_credential_store),
):
    save_r2_credentials(
        credential_store,
        R2Credentials(
            account_id=data.account_id,
            access_key_id=data.access_key_id,
            secret_access_key=data.secret_access_key,
            bucket_name=data.bucket_name,
            public_base_url=data.public_base_url,
        ),
    )
    return schema.R2CredentialsStatusResponse(configured=True)


@router.get("/r2-hosting/status", response_model=schema.R2CredentialsStatusResponse)
def get_r2_hosting_status(
    current_user: User = Depends(admin_guard),
    credential_store: CredentialStore = Depends(get_r2_credential_store),
):
    try:
        load_r2_credentials(credential_store)
        return schema.R2CredentialsStatusResponse(configured=True)
    except MediaHostingNotConfiguredError:
        return schema.R2CredentialsStatusResponse(configured=False)


__all__ = [
    "router",
]
