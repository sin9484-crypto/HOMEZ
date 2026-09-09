"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_router.py

Gate I(2026-08-08) — 상품등록 통합 마법사 운영자 API. 승인(approve)/
승인 취소(revoke-approval)만 Gate G와 동일한 3중 게이트
(SuperAdminGuard + recent-auth + 단발성 nonce)를 요구한다.

Gate Q-2(2026-08-09) — 나머지 엔드포인트는 admin_guard 대신 세부
Permission 기반 `ListingWizardPermissionGuard`로 보호된다
(listing_wizard_permission_guard.py 참고) — ADMIN/SUPER_ADMIN은
기존과 동일하게 항상 통과하고, 그 외 역할은 실제로 해당 Permission
코드가 부여된 경우에만 통과한다(오늘은 아직 아무 역할에도 부여되지
않았으므로 사실상 이전과 동일한 접근성이 유지된다 — 향후 실제
Permission 시딩+부여가 이뤄져야 VIEWER 등이 실제로 접근 가능해진다).
Gate U-1(2026-08-10) — 금액 정보(economics_input/economics_result)는
별도로 LISTING_ECONOMICS_VIEW 권한이 있어야만 응답에 채워진다 —
없으면 값을 빈 배열로 가리는 게 아니라 두 필드 키 자체가 응답
JSON에서 빠진다(Gate Q-2 당시의 "빈 배열로 가림" 계약보다 강화됨).
같은 이유로 승인 미리보기(approval-preview)/승인 취소 미리보기
(revoke-approval-preview)가 반환하는 approval_package도 Economics
권한이 없으면 두 키가 제외된다(listing_wizard_approval.py의
strip_economics_from_package() 참고) — fingerprint 자체는 항상 전체
내용 기준으로 계산되므로 이 redaction과 무관하다.

회사 소유권 격리: 모든 엔드포인트가 current_user.company_id를
Service에 전달하며, 다른 회사의 위저드는 항상 404(존재하지 않는
것과 동일하게 취급 — 403이 아니다).
=========================================================
"""

import json

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.domains.marketplace_listing.listing_wizard_approval import (
    redact_approval_history,
)
from app.domains.marketplace_listing.listing_wizard_approval import (
    strip_economics_from_package,
)
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    user_can,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_ECONOMICS_VIEW,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_APPROVE,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_CREATE,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_DELETE,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_EDIT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_EXPORT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_RECONCILE,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_RETRY,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_SUBMIT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    ApprovalPreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardApproveRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchivePreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyPreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkSubmitRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkSubmitResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    CoupangContentsFromMediaRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDeleteRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRestoreRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardChannelsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCloneRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCreateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDetailResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDraftUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardEconomicsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardFulfillmentUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardListItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardLivePreflightResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardLiveSendResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardLiveStatusResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardMediaUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardResultsResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRevokeApprovalRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRevokePreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSourceUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSubmitRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardValidateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardValidateResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    CategoryMetadataResponse, CategoryRecommendationResponse,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    deletion_eligibility,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionReconciliationApplyRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionReconciliationAssessmentResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionReconciliationPreviewRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionReconciliationResultResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceUnresolvedSubmissionResponse,
)
from app.domains.marketplace_listing.submission_reconciliation_service import (
    MarketplaceSubmissionReconciliationRepository,
    apply_reconciliation,
    preview_reconciliation,
)
from app.domains.user.model import User

router = APIRouter(
    prefix="/listing-wizards",
    tags=["Listing Wizard"],
)


def _coupang_metadata_provider(db: Session, company_id: int):
    # 2026-08-29 Pre-Live 감사 Phase C — 격리 패키징 검증 전용 테스트
    # 훅. installer/homez.iss의 HOMEZ_TEST_FORCE_* 패턴과 동일한 원칙:
    # 이 정확한 이름의 환경변수가 명시적으로 "1"일 때만 개입하고,
    # 설정되지 않은 실제 배포에서는 이 import조차 실행되지 않는다
    # (아래 os.environ 체크가 지연 import보다 먼저 평가됨).
    import os
    if os.environ.get("HOMEZ_TEST_FAKE_COUPANG_PROVIDER") == "1":
        from app.domains.marketplace_listing.coupang_test_fakes import (
            FakeCoupangCategoryMetadataProvider,
        )
        return FakeCoupangCategoryMetadataProvider()

    from app.core.exceptions import ServiceUnavailableException
    from app.core.windows_credential_store import WindowsCredentialStore
    from app.domains.marketplace_listing.coupang_category_metadata_provider import (
        CoupangCategoryMetadataProvider, CategoryMetadataProviderError,
    )
    from app.domains.store_connection.model import StoreConnection

    connection = (
        db.query(StoreConnection)
        .filter(StoreConnection.company_id == company_id)
        .filter(StoreConnection.marketplace_code == "COUPANG")
        .filter(StoreConnection.connection_status == "CONNECTED")
        .first()
    )
    if connection is None or not connection.credential_reference:
        raise ServiceUnavailableException("연결된 쿠팡 판매계정이 필요합니다.")
    try:
        credential = WindowsCredentialStore().read(connection.credential_reference)
        return CoupangCategoryMetadataProvider(credential)
    except Exception as exc:
        if isinstance(exc, ServiceUnavailableException):
            raise
        raise ServiceUnavailableException("쿠팡 카테고리 조회를 준비할 수 없습니다.") from exc


def _coupang_logistics_provider(db: Session, company_id: int):
    # 2026-08-29 Pre-Live 감사 Phase C 테스트 훅 — 위 _coupang_metadata_
    # provider()와 동일 원칙.
    import os
    if os.environ.get("HOMEZ_TEST_FAKE_COUPANG_PROVIDER") == "1":
        from app.domains.marketplace_listing.coupang_test_fakes import (
            FakeCoupangLogisticsProvider,
        )
        return FakeCoupangLogisticsProvider()

    from app.core.exceptions import ServiceUnavailableException
    from app.core.windows_credential_store import WindowsCredentialStore
    from app.domains.marketplace_listing.coupang_logistics_provider import (
        CoupangLogisticsProvider,
    )
    from app.domains.store_connection.model import StoreConnection

    connection = (
        db.query(StoreConnection)
        .filter(StoreConnection.company_id == company_id)
        .filter(StoreConnection.marketplace_code == "COUPANG")
        .filter(StoreConnection.connection_status == "CONNECTED")
        .first()
    )
    if connection is None or not connection.credential_reference:
        raise ServiceUnavailableException("연결된 쿠팡 판매계정이 필요합니다.")
    try:
        credential = WindowsCredentialStore().read(connection.credential_reference)
        return CoupangLogisticsProvider(credential)
    except Exception as exc:
        raise ServiceUnavailableException("쿠팡 출고지·반품지 조회를 준비할 수 없습니다.") from exc


def _coupang_live_product_provider(db: Session, company_id: int):
    # 2026-08-29 Pre-Live 감사 Phase C 테스트 훅 — 위 두 팩토리 함수와
    # 동일 원칙. 실제 쿠팡 상품 생성/조회 API를 대체하는 지점이므로
    # 특히 이 정확한 이름의 환경변수 없이는 절대 개입하지 않는다.
    import os
    if os.environ.get("HOMEZ_TEST_FAKE_COUPANG_PROVIDER") == "1":
        from app.domains.marketplace_listing.coupang_test_fakes import (
            FakeCoupangLiveProductProvider,
        )
        return FakeCoupangLiveProductProvider()

    from app.core.exceptions import ServiceUnavailableException
    from app.core.windows_credential_store import WindowsCredentialStore
    from app.domains.marketplace_listing.coupang_live_provider import (
        CoupangLiveProductProvider,
    )
    from app.domains.store_connection.model import StoreConnection

    connection = (
        db.query(StoreConnection)
        .filter(StoreConnection.company_id == company_id)
        .filter(StoreConnection.marketplace_code == "COUPANG")
        .filter(StoreConnection.connection_status == "CONNECTED")
        .filter(StoreConnection.credential_reference.is_not(None))
        .order_by(StoreConnection.id.desc())
        .first()
    )
    if connection is None:
        raise ServiceUnavailableException("연결된 쿠팡 판매계정이 필요합니다.")
    try:
        credential = WindowsCredentialStore().read(
            connection.credential_reference,
        )
        return CoupangLiveProductProvider(credential)
    except Exception as exc:
        raise ServiceUnavailableException(
            "쿠팡 상품등록 전송을 준비할 수 없습니다.",
        ) from exc


@router.get("/{wizard_id}/coupang/outbound-shipping-places")
def list_coupang_outbound_shipping_places(
    wizard_id: int,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
):
    from app.core.exceptions import ServiceUnavailableException
    from app.domains.marketplace_listing.coupang_logistics_provider import (
        CoupangLogisticsProviderError, cache_locations,
    )
    ListingWizardService(db).get(wizard_id, current_user.company_id)
    try:
        items = _coupang_logistics_provider(db, current_user.company_id).list_outbound_shipping_places()
    except CoupangLogisticsProviderError as exc:
        raise ServiceUnavailableException(str(exc)) from exc
    cache_locations(current_user.company_id, wizard_id, "outbound", items)
    return [item.public_dict("outbound_shipping_place_code") for item in items]


@router.get("/{wizard_id}/coupang/return-shipping-centers")
def list_coupang_return_shipping_centers(
    wizard_id: int,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
):
    from app.core.exceptions import ServiceUnavailableException
    from app.domains.marketplace_listing.coupang_logistics_provider import (
        CoupangLogisticsProviderError, cache_locations,
    )
    ListingWizardService(db).get(wizard_id, current_user.company_id)
    try:
        items = _coupang_logistics_provider(db, current_user.company_id).list_return_shipping_centers()
    except CoupangLogisticsProviderError as exc:
        raise ServiceUnavailableException(str(exc)) from exc
    cache_locations(current_user.company_id, wizard_id, "return", items)
    return [item.public_dict("return_center_code") for item in items]


# 2026-08-30 V7 후속 안정화 Phase 3 — 브랜드 3상태 계약(OFFICIAL_BRAND)
# 검색 UI. 실제 쿠팡 브랜드 검색 API는 이번 세션 범위 밖(별도 승인
# 없이 호출하지 않는다) — Fake Provider로 화면 왕복(검색→선택→
# brandId/공식명/Enrollment 상태 채움)을 먼저 증명한다. 다른 곳의
# 정직한 공개 배너(예: "media_search_not_connected")와 동일한 원칙 —
# 실제 데이터가 아님을 응답에 명시한다(화면이 이 플래그로 안내 문구를
# 보여준다).
_FAKE_BRAND_SEARCH_SEED = {
    "홈즈": [{"brand_id": "BR-DEMO-0001", "official_brand_name": "HOMEZ", "enrollment_status": "ENROLLED"}],
    "homez": [{"brand_id": "BR-DEMO-0001", "official_brand_name": "HOMEZ", "enrollment_status": "ENROLLED"}],
    "에브리홈즈": [{"brand_id": "BR-DEMO-0002", "official_brand_name": "에브리홈즈", "enrollment_status": "NOT_ENROLLED"}],
}


@router.get("/{wizard_id}/coupang/brand-search")
def search_coupang_brand(
    wizard_id: int,
    query: str = Query(..., min_length=1, max_length=100),
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
):
    from app.domains.marketplace_listing.coupang_brand_provider import (
        BrandSearchResult, FakeCoupangBrandProvider, brand_lookup_fingerprint,
    )

    ListingWizardService(db).get(wizard_id, current_user.company_id)

    seed = {
        key: [BrandSearchResult(**row) for row in rows]
        for key, rows in _FAKE_BRAND_SEARCH_SEED.items()
    }
    provider = FakeCoupangBrandProvider(seed)
    results = provider.search_brand(query)

    return {
        "connected": False,
        "results": [
            {
                "brand_id": r.brand_id,
                "official_brand_name": r.official_brand_name,
                "enrollment_status": r.enrollment_status,
                "lookup_fingerprint": brand_lookup_fingerprint(query, r),
            }
            for r in results
        ],
    }


# 2026-08-29 Pre-Live 검증 — 5단계 "쿠팡 전송용 대표 이미지 URL"
# 수동 입력 필드 이전에, 3단계에서 이미 선택한 이미지를 R2에
# 업로드해 공개 URL을 자동으로 만드는 명시적 버튼용 엔드포인트.
# 기존에는 이 자동 채움이 9단계 제출 준비 시점(listing_wizard_live_
# service.py)에만 best-effort로 조용히 시도되어, 실패해도 사용자가
# 5단계에서는 원인을 알 방법이 없었다 — 이제 5단계에서 명시적으로
# 호출해 즉시 URL을 보거나 즉시 실패 사유(R2 미설정 등)를 볼 수 있다.
def get_r2_credential_store() -> CredentialStore:
    """
    운영 경로의 기본 Credential 저장소 — 항상 실제 Windows Credential
    Manager다. 테스트는 이 의존성을 오버라이드해 in-memory 구현을
    주입한다(운영 코드 자신은 절대 자동으로 in-memory를 선택하지
    않는다). app/domains/store_connection/router.py::get_credential_store
    와 동일한 관례.
    """

    return WindowsCredentialStore()


@router.post("/{wizard_id}/coupang/auto-upload-images")
def auto_upload_coupang_images(
    wizard_id: int,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_r2_credential_store),
):
    import json
    from app.core.exceptions import BadRequestException
    from app.domains.marketplace_listing.coupang_image_autofill import (
        autofill_coupang_images,
    )

    wizard = ListingWizardService(db).get(wizard_id, current_user.company_id)
    selected_ids = json.loads(wizard.selected_media_asset_ids_json or "[]")
    if not selected_ids:
        raise BadRequestException("먼저 3단계에서 이미지를 선택해야 합니다.")

    images = autofill_coupang_images(
        db, current_user.company_id, selected_ids, credential_store,
    )
    db.commit()
    return {"images": images}


@router.post("/{wizard_id}/coupang/contents-from-media")
def build_coupang_contents_from_media(
    wizard_id: int,
    data: CoupangContentsFromMediaRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_r2_credential_store),
):
    """
    2026-08-31 — 선택한 상세 이미지 media_asset들로
    required_fields["contents"](상세설명)를 구성한다. 원본 JSON
    편집기 없이도 완주할 수 있게 하는 3단계 대응 UI의 백엔드.
    wizard 자체는 변경하지 않는다 — 프론트엔드가 결과를 받아 기존
    /fulfillment PATCH에 담아 보내야 실제로 저장된다.
    """
    from app.core.exceptions import BadRequestException
    from app.domains.marketplace_listing.coupang_contents_builder import (
        build_contents_from_media_assets,
    )

    wizard = ListingWizardService(db).get(wizard_id, current_user.company_id)
    if not data.media_asset_ids:
        raise BadRequestException("상세설명에 사용할 이미지를 최소 1개 선택하세요.")

    contents = build_contents_from_media_assets(
        db, current_user.company_id, wizard.product_candidate_id,
        data.media_asset_ids, credential_store,
    )
    db.commit()
    return {"contents": contents}


@router.post(
    "/{wizard_id}/category-recommendation",
    response_model=CategoryRecommendationResponse,
)
def recommend_category(
    wizard_id: int,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
):
    service = ListingWizardService(db)
    wizard = service.get(wizard_id, current_user.company_id)
    from app.core.exceptions import ServiceUnavailableException
    from app.domains.marketplace_listing.coupang_category_metadata_provider import (
        CategoryMetadataProviderError,
    )
    from app.domains.product_candidate.repository import ProductCandidateRepository

    candidate = ProductCandidateRepository(db).get_visible_for_company(
        wizard.product_candidate_id, current_user.company_id,
    )
    if candidate is None:
        raise ServiceUnavailableException("카테고리를 추천할 상품 후보를 찾을 수 없습니다.")
    provider = _coupang_metadata_provider(db, current_user.company_id)
    try:
        return provider.recommend(
            candidate.product_name,
            str(candidate.category_hint or ""),
            str(candidate.brand_hint or ""),
        )
    except CategoryMetadataProviderError as exc:
        raise ServiceUnavailableException(str(exc)) from exc


@router.get(
    "/{wizard_id}/category-metadata/{display_category_code}",
    response_model=CategoryMetadataResponse,
)
def get_category_metadata(
    wizard_id: int, display_category_code: str,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
):
    from app.domains.marketplace_listing.category_metadata import metadata_fingerprint
    service = ListingWizardService(db)
    service.get(wizard_id, current_user.company_id)
    from app.core.exceptions import ServiceUnavailableException
    from app.domains.marketplace_listing.coupang_category_metadata_provider import (
        CategoryMetadataProviderError,
    )
    try:
        metadata = _coupang_metadata_provider(db, current_user.company_id).get(display_category_code)
    except CategoryMetadataProviderError as exc:
        raise ServiceUnavailableException(str(exc)) from exc
    return {
        "display_category_code": metadata.display_category_code,
        "display_category_name": metadata.display_category_name,
        "metadata_version": metadata.version,
        "metadata_fingerprint": metadata_fingerprint(metadata),
        "notice_fields": [field.__dict__ for field in metadata.notice_fields],
        "purchase_option_fields": [field.__dict__ for field in metadata.purchase_option_fields],
        # 2026-08-29 Phase 4(V7-COUPANG-META-002 수정) — 이전에는 이
        # 두 값 자체가 응답에 없었다.
        "required_documents": [doc.__dict__ for doc in metadata.required_documents],
        "certifications": [cert.__dict__ for cert in metadata.certifications],
    }


@router.get("/{wizard_id}/category-metadata/{display_category_code}/raw-attributes")
def get_category_metadata_raw_attributes(
    wizard_id: int, display_category_code: str,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)),
    db: Session = Depends(get_db),
):
    """2026-08-28 진단 도구 — 구매옵션 속성이 자유 텍스트인지 고정
    선택지(값 목록)를 요구하는지 원본 그대로 확인한다. HOMEZ가 이미
    호출하는 공식 조회 API를 그대로 다시 쓸 뿐, 새 외부 연동이나 쓰기
    동작이 아니다."""

    service = ListingWizardService(db)
    service.get(wizard_id, current_user.company_id)
    from app.core.exceptions import ServiceUnavailableException
    from app.domains.marketplace_listing.coupang_category_metadata_provider import (
        CategoryMetadataProviderError,
    )
    provider = _coupang_metadata_provider(db, current_user.company_id)
    try:
        response = provider._request(
            "GET", provider.METADATA_PATH.format(code=display_category_code),
        )
    except CategoryMetadataProviderError as exc:
        raise ServiceUnavailableException(str(exc)) from exc
    data = response.get("data") or {}
    return {"attributes": data.get("attributes") or []}


def _to_detail(
    wizard: ListingWizard, *, include_economics: bool,
) -> dict:
    """
    Gate U-1(2026-08-10) — `include_economics=False`면 `economics_input`/
    `economics_result` 키 자체를 응답에서 뺀다(Gate Q-2의 "빈 배열로
    가림"보다 강한 계약 — 값이 아니라 키가 없다). `WizardDetailResponse`
    자체에는 이 두 필드가 더 이상 선언돼 있지 않으므로, 우선 그 모델로
    나머지 필드를 검증한 뒤 dict로 변환하고, 권한이 있을 때만 두 키를
    수동으로 추가한다. `approval_history`에 남아있는 과거 승인 취소
    스냅샷도 동일 기준으로 정리한다. ADMIN/SUPER_ADMIN 또는
    LISTING_ECONOMICS_VIEW 권한을 가진 사용자만 True로 호출된다
    (호출부 참고).
    """

    raw_history = json.loads(wizard.approval_history_json or "[]")

    base = WizardDetailResponse(
        id=wizard.id,
        current_step=wizard.current_step,
        status=wizard.status,
        source_type=wizard.source_type,
        product_candidate_id=wizard.product_candidate_id,
        cloned_from_wizard_id=wizard.cloned_from_wizard_id,
        draft=(
            json.loads(wizard.draft_json) if wizard.draft_json else None
        ),
        selected_media_asset_ids=json.loads(
            wizard.selected_media_asset_ids_json or "[]",
        ),
        channel_selections=json.loads(
            wizard.channel_selections_json or "[]",
        ),
        validation_result=(
            json.loads(wizard.validation_result_json)
            if wizard.validation_result_json else None
        ),
        approval_fingerprint=wizard.approval_fingerprint,
        approved_by_user_id=wizard.approved_by_user_id,
        approved_at=wizard.approved_at,
        approval_history=(
            raw_history if include_economics
            else redact_approval_history(raw_history)
        ),
        materialized_listing_ids=json.loads(
            wizard.materialized_listing_ids_json or "[]",
        ),
        autosave_client_token=wizard.autosave_client_token,
        autosave_saved_at=wizard.autosave_saved_at,
        version=wizard.version,
        created_at=wizard.created_at,
        updated_at=wizard.updated_at,
        deleted_at=wizard.deleted_at,
        deleted_by_user_id=wizard.deleted_by_user_id,
        delete_reason=wizard.delete_reason,
        restored_at=wizard.restored_at,
        restored_by_user_id=wizard.restored_by_user_id,
        deletable=deletion_eligibility(wizard)[0],
        block_reason=deletion_eligibility(wizard)[1],
    )

    data = base.model_dump(mode="json")

    if include_economics:
        data["economics_input"] = json.loads(wizard.economics_input_json or "[]")
        data["economics_result"] = json.loads(wizard.economics_result_json or "[]")

    return data


def _to_list_item(wizard: ListingWizard) -> WizardListItem:

    deletable, block_reason = deletion_eligibility(wizard)

    return WizardListItem(
        id=wizard.id,
        current_step=wizard.current_step,
        status=wizard.status,
        source_type=wizard.source_type,
        product_candidate_id=wizard.product_candidate_id,
        created_at=wizard.created_at,
        updated_at=wizard.updated_at,
        version=wizard.version,
        deleted_at=wizard.deleted_at,
        deletable=deletable,
        block_reason=block_reason,
    )


@router.post("")
def create_wizard(
    data: WizardCreateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_CREATE),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard, _duplicate = service.create(
        data, created_by=current_user.id, company_id=current_user.company_id,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.get("", response_model=list[WizardListItem])
def list_wizards(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, le=200, gt=0),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(
        default=False,
        description=(
            "True면 삭제(ARCHIVED)된 항목도 포함한다(status를 명시하면 "
            "이 값과 무관하게 그 status만 조회). 사용자 결정: "
            "\"목록에서는 기본적으로 숨기되 '삭제된 대기 상품' 필터에서 "
            "조회할 수 있게 한다\"."
        ),
    ),
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_VIEW),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)

    wizards = service.list_wizards(
        current_user.company_id, status=status, limit=limit, offset=offset,
        include_archived=include_archived,
    )

    return [_to_list_item(w) for w in wizards]


@router.get("/export.csv")
def export_wizards_csv(
    status: str | None = Query(default=None),
    locale: str = Query(default="ko-KR"),
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EXPORT),
    ),
    db: Session = Depends(get_db),
):
    """
    Gate U-3(2026-08-10) — `LISTING_WIZARD_VIEW`만으로는 내보낼 수
    없다(별도 `LISTING_WIZARD_EXPORT` Permission 필요). 금액 컬럼은
    `LISTING_ECONOMICS_VIEW`가 없으면 CSV에서 완전히 빠진다(값을
    가리는 게 아니다) — Gate U-1의 `strip_economics_from_package()`와
    동일한 원칙을 CSV 컬럼 단위로 적용한다. 회사 범위는 항상
    `current_user.company_id`로만 결정되므로(요청 바디/쿼리로 다른
    회사를 지정할 방법 자체가 없다) 다른 회사 데이터는 애초에 조회
    대상에 포함되지 않는다.
    """

    service = ListingWizardService(db)
    csv_text = service.export_csv(
        current_user.company_id,
        include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
        locale=locale, status=status,
    )

    return PlainTextResponse(
        csv_text, media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=listing-wizards.csv",
        },
    )


@router.get(
    "/bulk-archive-preview", response_model=WizardBulkArchivePreviewResponse,
)
def bulk_archive_preview(
    status: str | None = Query(default=None),
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_VIEW),
    ),
    db: Session = Depends(get_db),
):
    """
    "현재 필터의 삭제 가능한 대기 상품 N건 삭제" 버튼 문구에 쓸 정확한
    건수를 미리 보여준다 — 읽기 전용, 아무것도 바꾸지 않는다. 정적
    경로이므로 `/{wizard_id}`보다 먼저 등록한다(라우팅 충돌 방지 —
    `export.csv`와 동일한 기존 관례).
    """

    service = ListingWizardService(db)

    return service.preview_bulk_archive_count(current_user.company_id, status)


@router.post("/bulk-archive", response_model=WizardBulkArchiveResponse)
def bulk_archive_wizards(
    data: WizardBulkArchiveRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_DELETE),
    ),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
    db: Session = Depends(get_db),
):
    """
    2026-08-28 "대기 상품 정리" — 두 모드(명시적 wizard_ids 선택 삭제 /
    select_all_matching_filter 전체 필터 삭제) 공통 엔드포인트.
    select_all_matching_filter 모드는 LISTING_WIZARD_DELETE 권한에 더해
    recent-auth 재확인도 요구한다(사용자 결정 "대량 삭제는 관리자 권한
    또는 recent-auth를 검토" — 두 조건을 모두 반영: 항상 권한 필요 +
    가장 넓은 변형에서만 추가로 recent-auth). 건별 결과는 서비스가
    성공/제외/실패로 나눠 반환한다(부분 실패를 통째로 막지 않는다).
    """

    from app.core.exceptions import ForbiddenException

    if data.select_all_matching_filter:
        if not consume_recent_auth_token(recent_auth_token, current_user.id):
            raise ForbiddenException("현재 비밀번호 재확인이 필요합니다.")

    service = ListingWizardService(db)

    return service.bulk_archive(data, current_user.company_id, current_user.id)


@router.get(
    "/bulk-margin-apply-preview",
    response_model=WizardBulkMarginApplyPreviewResponse,
)
def bulk_margin_apply_preview(
    status: str | None = Query(default=None),
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_VIEW),
    ),
    db: Session = Depends(get_db),
):
    """읽기 전용 — 아무것도 바꾸지 않는다. 정적 경로이므로
    `/{wizard_id}`보다 먼저 등록한다."""

    service = ListingWizardService(db)

    return service.preview_bulk_margin_apply_count(
        current_user.company_id, status,
    )


@router.post(
    "/bulk-margin-apply", response_model=WizardBulkMarginApplyResponse,
)
def bulk_apply_margin_rate(
    data: WizardBulkMarginApplyRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
    db: Session = Depends(get_db),
):
    """
    2026-09-06 "일괄 마진 설정" — bulk-archive와 동일한 두 모드
    (명시적 wizard_ids / select_all_matching_filter) 공통 엔드포인트.
    select_all_matching_filter 모드는 recent-auth 재확인도 요구한다
    (넓은 변형에서만 추가 게이트 — bulk-archive와 동일 원칙).
    """

    from app.core.exceptions import ForbiddenException

    if data.select_all_matching_filter:
        if not consume_recent_auth_token(recent_auth_token, current_user.id):
            raise ForbiddenException("현재 비밀번호 재확인이 필요합니다.")

    service = ListingWizardService(db)

    return service.bulk_apply_margin_rate(data, current_user.company_id)


@router.post("/bulk-submit", response_model=WizardBulkSubmitResponse)
def bulk_submit_wizards(
    data: WizardBulkSubmitRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_SUBMIT),
    ),
    db: Session = Depends(get_db),
):
    """
    2026-09-06 "멀티마켓 동시 등록" — 사용자가 명시적으로 고른 위저드만
    대상으로 한다(전체 필터 제출 없음). 위저드 하나당 이미 선택된 모든
    채널에 대해 기존 submit()이 그대로 실행된다 — 채널별 실제 등록은
    기존 submit_wizard_channels()가 담당하며 여기서 새로 만들지 않는다.
    """

    service = ListingWizardService(db)

    return service.bulk_submit(
        data, current_user.company_id, current_user.id,
    )


@router.get("/{wizard_id}")
def get_wizard(
    wizard_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_VIEW),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)

    return _to_detail(
        service.get(wizard_id, current_user.company_id),
        include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.delete("/{wizard_id}")
def delete_wizard(
    wizard_id: int,
    data: WizardDeleteRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_DELETE),
    ),
    db: Session = Depends(get_db),
):
    """개별 삭제(soft delete=ARCHIVED) — 쿠팡 등 실제 판매채널의 상품을
    삭제/비활성화하지 않는다. HOMEZ 내부 대기 항목(ListingWizard)만
    ARCHIVED로 전이한다."""

    service = ListingWizardService(db)
    wizard = service.archive(
        wizard_id, current_user.company_id, data.expected_version,
        current_user.id, data.reason, data.deletion_request_id,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.post("/{wizard_id}/restore")
def restore_wizard(
    wizard_id: int,
    data: WizardRestoreRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_DELETE),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.restore(
        wizard_id, current_user.company_id, data.expected_version,
        current_user.id,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.patch("/{wizard_id}/source")
def update_source(
    wizard_id: int,
    data: WizardSourceUpdateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.update_source(
        wizard_id, current_user.company_id, data,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.patch("/{wizard_id}/draft")
def update_draft(
    wizard_id: int,
    data: WizardDraftUpdateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.update_draft(wizard_id, current_user.company_id, data)

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.patch("/{wizard_id}/media")
def update_media(
    wizard_id: int,
    data: WizardMediaUpdateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.update_media(wizard_id, current_user.company_id, data)

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.patch("/{wizard_id}/channels")
def update_channels(
    wizard_id: int,
    data: WizardChannelsUpdateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.update_channels(
        wizard_id, current_user.company_id, data,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.patch("/{wizard_id}/fulfillment")
def update_fulfillment(
    wizard_id: int,
    data: WizardFulfillmentUpdateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.update_fulfillment(
        wizard_id, current_user.company_id, data,
        actor_user_id=current_user.id,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.patch("/{wizard_id}/economics")
def update_economics(
    wizard_id: int,
    data: WizardEconomicsUpdateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.update_economics(
        wizard_id, current_user.company_id, data,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.post("/{wizard_id}/validate", response_model=WizardValidateResponse)
def validate_wizard(
    wizard_id: int,
    data: WizardValidateRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_EDIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)

    return service.validate(
        wizard_id, current_user.company_id, data.expected_version,
        current_user.id,
    )


@router.get(
    "/{wizard_id}/approval-preview", response_model=ApprovalPreviewResponse,
)
def approval_preview(
    wizard_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_APPROVE),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    preview = service.approval_preview(wizard_id, current_user.company_id)

    if not user_can(db, current_user, LISTING_ECONOMICS_VIEW):
        preview.approval_package = strip_economics_from_package(
            preview.approval_package,
        )

    return preview


@router.post("/{wizard_id}/approve")
def approve_wizard(
    wizard_id: int,
    data: WizardApproveRequest,
    current_user: User = Depends(SuperAdminGuard),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.approve(
        wizard_id, current_user.company_id, current_user.id,
        recent_auth_token, data,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.get(
    "/{wizard_id}/revoke-approval-preview",
    response_model=WizardRevokePreviewResponse,
)
def revoke_approval_preview(
    wizard_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_APPROVE),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    preview = service.revoke_approval_preview(wizard_id, current_user.company_id)

    if not user_can(db, current_user, LISTING_ECONOMICS_VIEW):
        preview.approval_package = strip_economics_from_package(
            preview.approval_package,
        )

    return preview


@router.post(
    "/{wizard_id}/revoke-approval",
)
def revoke_approval(
    wizard_id: int,
    data: WizardRevokeApprovalRequest,
    current_user: User = Depends(SuperAdminGuard),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
    db: Session = Depends(get_db),
):
    """승인만큼 민감한 조작이므로 승인과 동일한 3중 게이트
    (SuperAdminGuard + recent-auth + 단발성 nonce)를 요구한다."""

    service = ListingWizardService(db)
    wizard = service.revoke_approval(
        wizard_id, current_user.company_id, current_user.id,
        recent_auth_token, data,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.post("/{wizard_id}/submit")
def submit_wizard(
    wizard_id: int,
    data: WizardSubmitRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_SUBMIT),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.submit(
        wizard_id, current_user.company_id, current_user.id, data,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.post("/{wizard_id}/retry-failed")
def retry_failed_channels(
    wizard_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_RETRY),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    wizard = service.retry_failed_channels(
        wizard_id, current_user.company_id, current_user.id,
    )

    return _to_detail(
        wizard, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.post("/{wizard_id}/clone")
def clone_wizard(
    wizard_id: int,
    data: WizardCloneRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_CREATE),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)
    clone, _duplicate = service.clone(
        wizard_id, current_user.company_id, current_user.id, data,
    )

    return _to_detail(
        clone, include_economics=user_can(db, current_user, LISTING_ECONOMICS_VIEW),
    )


@router.get("/{wizard_id}/results", response_model=WizardResultsResponse)
def get_results(
    wizard_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_VIEW),
    ),
    db: Session = Depends(get_db),
):

    service = ListingWizardService(db)

    return service.results(wizard_id, current_user.company_id)


@router.get(
    "/{wizard_id}/submissions/{submission_id}/live-preflight",
    response_model=WizardLivePreflightResponse,
)
def live_submission_preflight(
    wizard_id: int,
    submission_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_SUBMIT),
    ),
    db: Session = Depends(get_db),
):
    """Read-only and secret-free check before a real Coupang side effect."""

    from app.domains.marketplace_listing.listing_wizard_live_service import (
        ListingWizardLiveService,
    )

    return ListingWizardLiveService(db).preflight(
        wizard_id, submission_id, current_user.company_id,
    )


@router.post(
    "/{wizard_id}/submissions/{submission_id}/send-live",
    response_model=WizardLiveSendResponse,
)
def send_live_submission(
    wizard_id: int,
    submission_id: int,
    current_user: User = Depends(SuperAdminGuard),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
    db: Session = Depends(get_db),
):
    """Execute exactly one Coupang product-create call after all gates pass."""

    from app.core.exceptions import ForbiddenException
    from app.domains.marketplace_listing.listing_wizard_live_service import (
        ListingWizardLiveService,
    )

    service = ListingWizardLiveService(db)
    preflight = service.preflight(
        wizard_id, submission_id, current_user.company_id,
    )
    if not preflight.ready:
        raise ForbiddenException(
            "쿠팡 전송 조건이 충족되지 않았습니다: "
            + ", ".join(preflight.blockers),
        )
    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise ForbiddenException("현재 비밀번호 재확인이 필요합니다.")

    provider = _coupang_live_product_provider(db, current_user.company_id)
    return service.send(
        wizard_id, submission_id, current_user.company_id, provider,
    )


@router.get(
    "/{wizard_id}/submissions/{submission_id}/live-status",
    response_model=WizardLiveStatusResponse,
)
def live_submission_status(
    wizard_id: int,
    submission_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_SUBMIT),
    ),
    db: Session = Depends(get_db),
):
    """
    2026-08-29 쿠팡 상품등록 핵심 차단 해결(V7-COUPANG-STATUS-001
    Router 연결) — 실제로 쿠팡에 제출되어 sellerProductId를 받은
    건에 한해 현재 승인·노출 상태를 조회한다. 이 엔드포인트는 이번
    Phase에서 실제로 호출되지 않는다(사용자 최종 승인 후 Phase 10
    Live 검증 단계에서만 실사용 — 코드 연결만 완료한다).
    """

    from app.domains.marketplace_listing.listing_wizard_live_service import (
        ListingWizardLiveService,
    )

    service = ListingWizardLiveService(db)
    provider = _coupang_live_product_provider(db, current_user.company_id)
    return service.check_status(
        wizard_id, submission_id, current_user.company_id, provider,
    )


# --------------------------------------------------
# Submission Reconciliation — 2026-08-31 V7 필수 작업 2번
#
# 위저드 하나에 갇히지 않는다(과거 wizard가 삭제·보관됐어도 그
# wizard가 만들어낸 submission의 정합화는 계속 가능해야 한다) —
# submission_id + company_id로만 대상을 정한다. 미리보기(preview)는
# LISTING_WIZARD_RECONCILE 권한(오늘은 사실상 ADMIN/SUPER_ADMIN)이면
# 충분하지만, 실제 적용(apply)은 이 권한과 무관하게 SuperAdminGuard로
# 별도 통제한다 — "실제 적용은 별도 운영 승인 없이는 실행되지 않게
# 함" 요구사항을 이 두 단계로 나눠 반영한다(preview는 아무것도 쓰지
# 않으므로 낮은 문턱, apply만 가장 높은 문턱).
# --------------------------------------------------

@router.get(
    "/reconciliation/unresolved-submissions",
    response_model=list[MarketplaceUnresolvedSubmissionResponse],
)
def list_unresolved_submissions(
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_RECONCILE),
    ),
    db: Session = Depends(get_db),
):
    """
    external_submission_ref가 비어 있는 PENDING/SUBMITTING/UNKNOWN
    제출을 회사 전체 범위로 나열한다 — 정합화 검토 화면의 진입점.
    """

    repo = MarketplaceListingRepository(db)

    return repo.list_unresolved_submissions_for_company(current_user.company_id)


@router.get(
    "/reconciliation/submissions/{submission_id}",
    response_model=MarketplaceSubmissionReconciliationResultResponse | None,
)
def get_submission_reconciliation(
    submission_id: int,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_RECONCILE),
    ),
    db: Session = Depends(get_db),
):
    """이미 정합화된 기록이 있으면 그대로 보여준다(없으면 null)."""

    recon = MarketplaceSubmissionReconciliationRepository(db).get_by_submission_id(
        submission_id,
    )
    if recon is None:
        return None

    return MarketplaceSubmissionReconciliationResultResponse(
        outcome="RECONCILED", submission_id=recon.submission_id,
        external_submission_ref=recon.external_submission_ref,
        observed_status_name=recon.observed_status_name,
    )


@router.post(
    "/reconciliation/submissions/{submission_id}/preview",
    response_model=MarketplaceSubmissionReconciliationAssessmentResponse,
)
def preview_submission_reconciliation(
    submission_id: int,
    data: MarketplaceSubmissionReconciliationPreviewRequest,
    current_user: User = Depends(
        ListingWizardPermissionGuard(LISTING_WIZARD_RECONCILE),
    ),
    db: Session = Depends(get_db),
):
    """
    dry-run — 아무것도 쓰지 않는다. 운영자가 sellerProductId를 입력해
    실제 적용 전에 무엇이 일치·불일치·확인불가인지 먼저 확인한다.
    """

    provider = _coupang_live_product_provider(db, current_user.company_id)
    assessment = preview_reconciliation(
        db, submission_id=submission_id, company_id=current_user.company_id,
        operator_confirmed_seller_product_id=(
            data.operator_confirmed_seller_product_id
        ),
        provider=provider,
    )

    return MarketplaceSubmissionReconciliationAssessmentResponse(
        outcome=assessment.outcome, submission_id=assessment.submission_id,
        external_submission_ref=assessment.external_submission_ref,
        observed_status_name=assessment.observed_status_name,
        match_tier=assessment.match_tier,
        matched_fields=list(assessment.matched_fields),
        mismatched_fields=list(assessment.mismatched_fields),
        unavailable_fields=list(assessment.unavailable_fields),
        detail=assessment.detail,
    )


@router.post(
    "/reconciliation/submissions/{submission_id}/apply",
    response_model=MarketplaceSubmissionReconciliationResultResponse,
)
def apply_submission_reconciliation(
    submission_id: int,
    data: MarketplaceSubmissionReconciliationApplyRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    """
    실제로 MarketplaceSubmissionReconciliation 행을 append한다 —
    SuperAdminGuard 통과 자체가 이 지시가 요구한 "별도 운영 승인"이다.
    preview 이후 다른 요청이 상태를 바꿨을 가능성을 신뢰하지 않고,
    이 호출 내부에서 모든 검증을 처음부터 다시 수행한다.
    """

    provider = _coupang_live_product_provider(db, current_user.company_id)
    result = apply_reconciliation(
        db, submission_id=submission_id, company_id=current_user.company_id,
        operator_confirmed_seller_product_id=(
            data.operator_confirmed_seller_product_id
        ),
        provider=provider, actor_user_id=current_user.id, reason=data.reason,
    )

    return MarketplaceSubmissionReconciliationResultResponse(
        outcome=result.outcome, submission_id=result.submission_id,
        external_submission_ref=result.external_submission_ref,
        observed_status_name=result.observed_status_name,
        detail=result.detail,
    )


__all__ = ["router"]
