"""
=========================================================
Homez OS

File : app/domains/listing_package/router.py

전부 admin_guard로 보호되고 current_user.company_id를 Service에
전달한다. /submit 엔드포인트는 이번 Phase에서 항상 차단된다(dry-run
전용 — 실제 채널 제출은 별도 승인 후 연결).
=========================================================
"""

import json

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.listing_package.schema import CreateListingPackageRequest
from app.domains.listing_package.schema import ListingPackageApprovalRequest
from app.domains.listing_package.schema import ListingPackageApprovalResponse
from app.domains.listing_package.schema import ListingPackageRejectionRequest
from app.domains.listing_package.schema import ListingPackageResponse
from app.domains.listing_package.service import ListingPackageService
from app.domains.user.model import User

router = APIRouter(prefix="/listing-packages", tags=["Listing Package"])


def _to_response(package, service: ListingPackageService, company_id: int) -> dict:

    approval = service.current_valid_approval(package.id, company_id)

    jobs = service.image_queue_service.repository.list_jobs_for_package(
        package.id, company_id,
    )
    latest_job = jobs[-1] if jobs else None
    image_job_id = latest_job.id if latest_job else None
    image_job_status = latest_job.status if latest_job else None

    return {
        "id": package.id,
        "company_id": package.company_id,
        "product_candidate_id": package.product_candidate_id,
        "mode": package.mode,
        "status": package.status,
        "draft_payload": json.loads(package.draft_payload_json),
        "channel_selection": json.loads(package.channel_selection_json),
        "image_options": json.loads(package.image_options_json),
        "channel_readiness": service.compute_channel_readiness_for_package(
            package,
        ),
        "estimated_revenue": (
            float(package.estimated_revenue)
            if package.estimated_revenue is not None else None
        ),
        "risk_summary": package.risk_summary,
        "recommendation_reason": package.recommendation_reason,
        "package_fingerprint": package.package_fingerprint,
        "submit_ready": approval is not None,
        "image_job_id": image_job_id,
        "image_job_status": image_job_status,
        "created_at": package.created_at,
        "updated_at": package.updated_at,
    }


@router.post("", response_model=ListingPackageResponse)
def create_listing_package(
    data: CreateListingPackageRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ListingPackageService(db)
    package, _duplicate = service.create_listing_package(
        data, current_user.id, current_user.company_id,
    )

    return _to_response(package, service, current_user.company_id)


@router.get("/{package_id}", response_model=ListingPackageResponse)
def get_listing_package(
    package_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ListingPackageService(db)
    package = service.get_package(package_id, current_user.company_id)

    return _to_response(package, service, current_user.company_id)


@router.post(
    "/{package_id}/approve", response_model=ListingPackageApprovalResponse,
)
def approve_listing_package(
    package_id: int,
    data: ListingPackageApprovalRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ListingPackageService(db)
    approval, _duplicate = service.approve_package(
        package_id, data, current_user.id, current_user.company_id,
    )

    return approval


@router.post(
    "/{package_id}/reject", response_model=ListingPackageApprovalResponse,
)
def reject_listing_package(
    package_id: int,
    data: ListingPackageRejectionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ListingPackageService(db)
    approval, _duplicate = service.reject_package(
        package_id, data, current_user.id, current_user.company_id,
    )

    return approval


@router.post("/{package_id}/submit")
def submit_listing_package(
    package_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    실제 채널 제출 — 이번 Phase는 항상 차단한다(dry-run 전용).
    승인이 없으면 403, 있으면 400(범위 밖)으로 명확히 구분한다.
    """

    service = ListingPackageService(db)
    service.submit_to_channels(package_id, current_user.company_id)


__all__ = [
    "router",
]
