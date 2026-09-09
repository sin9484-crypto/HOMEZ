"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/router.py

채널별 판매 방식 선택 — 운영자 API. 모든 쓰기 엔드포인트는
admin_guard로 보호되고, UI가 인코딩한 모든 제약(캡빌리티 VERIFIED,
자격 VERIFIED·미만료, 직매입 계약 상태)을 서버에서도 재검증한다 — UI를
우회해도 서버에서 동일한 이유로 거부한다.

2026-08-01 CTO 3차 지적 반영 — 회사 소유권 격리(신규 발견): 이전에는
Account/Draft/Listing/Selection/Eligibility/Approval/Submission
단건·목록 API 대부분이 current_user를 `_`로 버려 company_id를 전혀
Service에 전달하지 않았다. 이제 회사 데이터를 다루는 모든 엔드포인트가
current_user.company_id를 명시적으로 전달한다.

MarketplaceChannel/MarketplaceFulfillmentCapability 관련 엔드포인트
(list_channels/seed_capability_registry/list_capabilities/
verify_capability)는 예외다 — 전역 플랫폼 설정이므로 company_id가
필요 없다.
=========================================================
"""

from datetime import datetime

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.eligibility_service import (
    EligibilityService,
)
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceAccountCreateRequest,
)
from app.domains.marketplace_listing.schema import MarketplaceAccountResponse
from app.domains.marketplace_listing.schema import (
    MarketplaceCapabilityVerifyRequest,
)
from app.domains.marketplace_listing.schema import MarketplaceChannelResponse
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityCheckRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityManualReviewRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentCapabilityResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingDraftChannelSelectRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingDraftCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingDraftResponse,
)
from app.domains.marketplace_listing.schema import MarketplaceListingResponse
from app.domains.marketplace_listing.schema import (
    MarketplaceListingStatusEventResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingStatusSummaryResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalDecisionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRejectionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRequestRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalResponse,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionResponse,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.status_sync_service import (
    ListingStatusSyncService,
)
from app.domains.marketplace_listing.submission_service import (
    SubmissionService,
)
from app.domains.user.model import User

router = APIRouter(
    prefix="/marketplace-listings",
    tags=["Marketplace Listing"],
)


# --------------------------------------------------
# Channel / Account / Capability
# --------------------------------------------------

@router.get(
    "/channels",
    response_model=list[MarketplaceChannelResponse],
)
def list_channels(
    _: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)),
    db: Session = Depends(get_db),
):
    """
    전역 플랫폼 설정 — company_id 범위 없음(모든 회사가 같은 값을 본다).

    Gate R-2(2026-08-09) — admin_guard 대신 LISTING_WIZARD_VIEW로
    전환한다. 채널·자격 목록은 상품등록 통합 마법사가 화면을 그리는 데
    필요한 최소 읽기 전용 참조 데이터이지, 그 자체가 관리 대상은
    아니다(쓰기 엔드포인트인 seed_capability_registry/verify_capability
    는 그대로 admin_guard).
    """

    service = MarketplaceListingService(db)

    return service.list_channels()


@router.post(
    "/channels/seed-capability-registry",
    response_model=list[MarketplaceFulfillmentCapabilityResponse],
)
def seed_capability_registry(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    정적 레지스트리를 DRAFT 상태로 시딩한다(전역). 실제 노출 가능
    (VERIFIED) 전환은 이 엔드포인트가 수행하지 않는다 — verify_
    capability를 별도 호출해야 한다(AI가 임의로 판매 방식을 활성화할
    수 없다는 통제 지점).
    """

    service = MarketplaceListingService(db)

    return service.seed_capability_registry()


@router.get(
    "/channels/{channel_id}/capabilities",
    response_model=list[MarketplaceFulfillmentCapabilityResponse],
)
def list_capabilities(
    channel_id: int,
    _: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)),
    db: Session = Depends(get_db),
):
    """전역 플랫폼 설정 — company_id 범위 없음(Gate R-2, list_channels와 동일 이유)."""

    service = MarketplaceListingService(db)

    return service.list_capabilities(channel_id)


@router.post(
    "/capabilities/{capability_id}/verify",
    response_model=MarketplaceFulfillmentCapabilityResponse,
)
def verify_capability(
    capability_id: int,
    data: MarketplaceCapabilityVerifyRequest,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    캡빌리티를 VERIFIED로 전환한다(전역) — 실제 정책·공식 문서를
    대조 확인한 관리자의 명시적 판단이어야 한다.
    """

    service = MarketplaceListingService(db)

    return service.verify_capability(capability_id, data.idempotency_key)


@router.post(
    "/accounts",
    response_model=MarketplaceAccountResponse,
)
def create_account(
    data: MarketplaceAccountCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = MarketplaceListingService(db)

    return service.create_account(
        data.channel_id, data.account_code, data.account_name,
        current_user.company_id,
    )


@router.get(
    "/channels/{channel_id}/accounts",
    response_model=list[MarketplaceAccountResponse],
)
def list_accounts(
    channel_id: int,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)),
    db: Session = Depends(get_db),
):
    """Gate R-2(2026-08-09) — admin_guard 대신 LISTING_WIZARD_VIEW.
    회사 소유권 격리(company_id 스코프)는 그대로 유지된다."""

    service = MarketplaceListingService(db)

    return service.list_accounts_for_channel(
        channel_id, current_user.company_id,
    )


# --------------------------------------------------
# Listing Draft (위저드)
# --------------------------------------------------

@router.post(
    "/drafts",
    response_model=MarketplaceListingDraftResponse,
)
def create_draft(
    data: MarketplaceListingDraftCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = MarketplaceListingService(db)
    draft, _duplicate = service.create_draft(
        data, created_by=current_user.id,
        company_id=current_user.company_id,
    )

    return draft


@router.post(
    "/drafts/{draft_id}/select-channels",
    response_model=MarketplaceListingDraftResponse,
)
def select_channels(
    draft_id: int,
    data: MarketplaceListingDraftChannelSelectRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """선택되지 않은 채널은 등록 대상이 아니다(요청 원문 그대로)."""

    service = MarketplaceListingService(db)

    return service.select_channels(
        draft_id, data.marketplace_account_ids, current_user.company_id,
    )


@router.post(
    "/drafts/{draft_id}/finalize-fulfillment-selection",
    response_model=MarketplaceListingDraftResponse,
)
def finalize_fulfillment_selection(
    draft_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """선택된 모든 채널에 판매 방식이 있어야만 다음 단계로 진행된다."""

    service = MarketplaceListingService(db)

    return service.finalize_fulfillment_selection(
        draft_id, current_user.company_id,
    )


# --------------------------------------------------
# Listing
# --------------------------------------------------

@router.post(
    "",
    response_model=MarketplaceListingResponse,
)
def create_listing(
    data: MarketplaceListingCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = MarketplaceListingService(db)
    listing, _duplicate = service.create_listing(
        data, current_user.company_id,
    )

    return listing


@router.get(
    "/{listing_id}",
    response_model=MarketplaceListingResponse,
)
def get_listing(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = MarketplaceListingService(db)

    return service.get_listing(listing_id, current_user.company_id)


@router.post(
    "/{listing_id}/pause",
    response_model=MarketplaceListingResponse,
)
def pause_listing(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    2026-08-01 Gate 5(CTO 2차 지적) — READY/APPROVED만 일시정지할 수
    있다(순수 표시용, 실제 제출 인가는 별도 게이트). 재호출은
    idempotent 성공이다.
    """

    service = MarketplaceListingService(db)

    return service.pause_listing(listing_id, current_user.company_id)


@router.post(
    "/{listing_id}/resume",
    response_model=MarketplaceListingResponse,
)
def resume_listing(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """PAUSED만 재개할 수 있다 — 항상 READY로 돌아간다(재승인 필요)."""

    service = MarketplaceListingService(db)

    return service.resume_listing(listing_id, current_user.company_id)


# --------------------------------------------------
# 플랫폼 상태 동기화 (2026-08-05 최종 제품화 Phase 4)
#
# `/status-sync/...`는 두 세그먼트라 `/{listing_id}`(한 세그먼트)와
# 절대 충돌하지 않는다 — 등록 순서와 무관하게 안전하다.
# --------------------------------------------------

@router.post(
    "/{listing_id}/refresh-status",
    response_model=MarketplaceListingStatusEventResponse,
)
def refresh_listing_status(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    Fake Provider로 플랫폼 상태를 조회해 반영한다. 서버가 직접 rate
    limit(STATUS_REFRESH_MIN_INTERVAL_SECONDS)을 강제한다 — 너무 잦은
    요청은 429로 거부된다. UNKNOWN은 성공으로 추론되지 않는다.
    """

    service = ListingStatusSyncService(db)

    return service.refresh_status(
        listing_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/{listing_id}/retry-status-check",
    response_model=MarketplaceListingStatusEventResponse,
)
def retry_listing_status_check(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    실패한 새로고침 전용 재시도(2026-08-05 CTO 반려 반영 item 4) —
    마지막 이벤트가 성공(반영됨)이었다면 400으로 거부한다(재시도할
    대상이 없다). rate limit은 refresh-status와 동일한 조건부 UPDATE
    경로를 그대로 재사용해 중복 진행 중 재시도를 막는다.
    """

    service = ListingStatusSyncService(db)

    return service.retry_status_check(
        listing_id, current_user.company_id, current_user.id,
    )


@router.get(
    "/{listing_id}/status-history",
    response_model=list[MarketplaceListingStatusEventResponse],
)
def get_listing_status_history(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """append-only 상태 이력 — 수정·삭제 API는 존재하지 않는다."""

    service = ListingStatusSyncService(db)

    return service.get_status_history(listing_id, current_user.company_id)


@router.get(
    "/status-sync/listings",
    response_model=list[MarketplaceListingStatusSummaryResponse],
)
def list_listings_with_status(
    channel_code: str | None = Query(default=None),
    marketplace_account_id: int | None = Query(default=None),
    fulfillment_mode: str | None = Query(default=None),
    platform_sync_status: str | None = Query(default=None),
    updated_from: datetime | None = Query(default=None),
    updated_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, description="상품명 또는 외부 상품번호 검색"),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ListingStatusSyncService(db)
    rows = service.list_listings(
        current_user.company_id,
        channel_code=channel_code,
        marketplace_account_id=marketplace_account_id,
        fulfillment_mode=fulfillment_mode,
        platform_sync_status=platform_sync_status,
        updated_from=updated_from,
        updated_to=updated_to,
        search=q,
    )

    return [
        MarketplaceListingStatusSummaryResponse(
            listing=listing, channel_code=channel_code_value,
            product_name=product_name,
        )
        for listing, channel_code_value, product_name in rows
    ]


@router.get("/status-sync/export.csv")
def export_listings_status_csv(
    channel_code: str | None = Query(default=None),
    marketplace_account_id: int | None = Query(default=None),
    fulfillment_mode: str | None = Query(default=None),
    platform_sync_status: str | None = Query(default=None),
    updated_from: datetime | None = Query(default=None),
    updated_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None),
    # Gate H(2026-08-07) — 콘솔 UI가 현재 표시 언어를 그대로 실어
    # 보낸다. 알 수 없는 값은 서비스 쪽에서 ko-KR로 폴백한다.
    locale: str = Query(default="ko-KR"),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ListingStatusSyncService(db)
    csv_text = service.export_csv(
        current_user.company_id,
        locale=locale,
        channel_code=channel_code,
        marketplace_account_id=marketplace_account_id,
        fulfillment_mode=fulfillment_mode,
        platform_sync_status=platform_sync_status,
        updated_from=updated_from,
        updated_to=updated_to,
        search=q,
    )

    return PlainTextResponse(
        csv_text, media_type="text/csv; charset=utf-8",
        headers={
            # 파일명은 항상 이 상수 문자열이다(사용자/Provider 입력이
            # 전혀 섞이지 않는다 — header injection 여지 없음).
            "Content-Disposition": "attachment; filename=listing-status.csv",
        },
    )


@router.get(
    "/by-candidate/{product_candidate_id}/summary",
)
def get_candidate_summary(
    product_candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    최종 요약(채널/계정/방식/상태) — 행별 독립. 한 채널의 실패가 다른
    채널 행에 영향을 주지 않는다. 다른 회사의 Listing은 애초에 목록에
    포함되지 않는다.
    """

    service = MarketplaceListingService(db)

    return service.channel_selections_summary(
        product_candidate_id, current_user.company_id,
    )


# --------------------------------------------------
# Fulfillment Selection — "선택된 채널은 판매 방식이 반드시 필요함"
# --------------------------------------------------

@router.get(
    "/{listing_id}/current-selection",
    response_model=MarketplaceFulfillmentSelectionResponse | None,
)
def get_current_selection(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    2026-08-01 Gate 5(CTO 2차 지적) 신규 — Desktop UI가 승인/거절/
    취소·재시도 버튼을 그리려면 이 listing의 현재 selection_id가
    필요한데, 그 조회 엔드포인트 자체가 없었다.
    """

    service = MarketplaceListingService(db)

    return service.get_current_selection(listing_id, current_user.company_id)


@router.post(
    "/fulfillment-selections",
    response_model=MarketplaceFulfillmentSelectionResponse,
)
def select_fulfillment_mode(
    data: MarketplaceFulfillmentSelectionCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    UI가 비활성화한 방식을 우회해 요청해도, 여기서 캡빌리티
    VERIFIED·is_supported 여부를 다시 검증해 동일한 이유로 거부한다.
    """

    service = MarketplaceListingService(db)
    selection, _duplicate, _events = service.select_fulfillment_mode(
        data, selected_by=current_user.id,
        company_id=current_user.company_id,
    )

    return selection


# --------------------------------------------------
# Eligibility
# --------------------------------------------------

@router.post(
    "/eligibility/check",
    response_model=MarketplaceEligibilityResponse,
)
def check_eligibility(
    data: MarketplaceEligibilityCheckRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = EligibilityService(db)

    return service.check(data, current_user.company_id)


@router.post(
    "/eligibility/manual-review",
    response_model=MarketplaceEligibilityResponse,
)
def manual_review_eligibility(
    data: MarketplaceEligibilityManualReviewRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    자동 확인 경로가 없는 자격을 관리자가 직접 확정한다 — reviewer_id·
    사유·만료일이 모두 감사 기록된다.
    """

    service = EligibilityService(db)

    return service.manual_review(
        data, reviewer_id=current_user.id,
        company_id=current_user.company_id,
    )


@router.get(
    "/eligibility/{marketplace_account_id}/{fulfillment_mode}/history",
    response_model=list[MarketplaceEligibilityResponse],
)
def eligibility_history(
    marketplace_account_id: int,
    fulfillment_mode: str,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = EligibilityService(db)

    return service.history(
        marketplace_account_id, fulfillment_mode, current_user.company_id,
    )


# --------------------------------------------------
# Submission Approval — 제출과 분리된 별도 요청/승인 흐름.
# approved_by/rejected_by/revoked_by는 오직 admin_guard의
# current_user.id에서만 온다 — 요청 바디에는 그런 필드가 없다(Critical
# 재감사 수정: 클라이언트 Boolean으로 자기 승인을 자칭할 수 없다).
# --------------------------------------------------

@router.post(
    "/approvals",
    response_model=MarketplaceSubmissionApprovalResponse,
)
def request_approval(
    data: MarketplaceSubmissionApprovalRequestRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ApprovalService(db)
    approval, _duplicate = service.request_approval(
        data, requested_by=current_user.id,
        company_id=current_user.company_id,
    )

    return approval


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=MarketplaceSubmissionApprovalResponse,
)
def approve_submission(
    approval_id: int,
    data: MarketplaceSubmissionApprovalDecisionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    승인 요청과 승인은 항상 서로 다른 API 호출이다 — 이 엔드포인트는
    제출을 실행하지 않는다. approved_by는 이 호출의 admin_guard
    current_user에서만 채워진다.
    """

    service = ApprovalService(db)
    approval, _duplicate = service.approve(
        approval_id, data, approved_by=current_user.id,
        company_id=current_user.company_id,
    )

    return approval


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=MarketplaceSubmissionApprovalResponse,
)
def reject_submission(
    approval_id: int,
    data: MarketplaceSubmissionApprovalRejectionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ApprovalService(db)
    approval, _duplicate = service.reject(
        approval_id, data, rejected_by=current_user.id,
        company_id=current_user.company_id,
    )

    return approval


@router.post(
    "/approvals/{approval_id}/revoke",
    response_model=MarketplaceSubmissionApprovalResponse,
)
def revoke_submission_approval(
    approval_id: int,
    data: MarketplaceSubmissionApprovalRejectionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ApprovalService(db)
    approval, _duplicate = service.revoke(
        approval_id, data, revoked_by=current_user.id,
        company_id=current_user.company_id,
    )

    return approval


@router.get(
    "/approvals/{listing_id}/{selection_id}/history",
    response_model=list[MarketplaceSubmissionApprovalResponse],
)
def approval_history(
    listing_id: int,
    selection_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ApprovalService(db)

    return service.history(
        listing_id, selection_id, current_user.company_id,
    )


# --------------------------------------------------
# Submission
# --------------------------------------------------

@router.post(
    "/submissions",
    response_model=MarketplaceSubmissionResponse,
)
def submit(
    data: MarketplaceSubmissionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    실제 Marketplace API를 호출하지 않는다 — 구조 변환·안전성 판단
    결과만 기록한다. 승인 여부는 요청 바디가 아니라 서버에 이미
    저장된 유효한 APPROVED MarketplaceSubmissionApproval 레코드로만
    판단한다(POST /approvals/{id}/approve를 통해 별도로 승인받아야
    한다).
    """

    service = SubmissionService(db)

    return service.submit(data, current_user.company_id)


@router.get(
    "/{listing_id}/submissions",
    response_model=list[MarketplaceSubmissionResponse],
)
def list_submissions(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SubmissionService(db)

    return service.list_submissions(listing_id, current_user.company_id)


__all__ = [
    "router",
]
