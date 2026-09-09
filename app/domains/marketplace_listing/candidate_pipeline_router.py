"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/candidate_pipeline_router.py

V7 Gate 6 — 후보 선택→초안 생성→이미지 생성 통합 파이프라인 API. 전부
admin_guard로 보호되고 current_user.company_id를 Service에 전달한다
(media_asset/decision 라우터와 동일한 권한 패턴). 이 라우터는 실제
외부 AI/이미지 Provider를 호출하지 않는다(FAKE만 실제로 동작).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.marketplace_listing.candidate_pipeline_schema import (
    CandidatePipelineImageSelectionRequest,
    CandidatePipelineResult,
    CandidatePipelineRunRequest,
)
from app.domains.marketplace_listing.candidate_pipeline_service import (
    CandidatePipelineService,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardListItem,
)
from app.domains.user.model import User

router = APIRouter(prefix="/candidate-pipeline", tags=["Candidate Pipeline"])


@router.post("/run", response_model=CandidatePipelineResult)
def run_candidate_pipeline(
    data: CandidatePipelineRunRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CandidatePipelineService(db)

    return service.start_pipeline(data, current_user.company_id, current_user.id)


@router.post("/select-images", response_model=WizardListItem)
def select_pipeline_image_results(
    data: CandidatePipelineImageSelectionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CandidatePipelineService(db)

    return service.select_image_results(data, current_user.company_id)


__all__ = [
    "router",
]
