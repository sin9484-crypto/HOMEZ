"""
=========================================================
Homez OS

File : app/domains/recall_notice/router.py

2026-09-15 전면 감사 후속(Phase 9I/9J, 10-17/10-18).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.domains.recall_notice.repository import RecallNoticeRepository
from app.domains.recall_notice.schema import RecallCheckJobModeResponse
from app.domains.recall_notice.schema import RecallCheckJobModeSetRequest
from app.domains.recall_notice.schema import RecallCheckRunResponse
from app.domains.recall_notice.schema import RecallProductBlockResponse
from app.domains.recall_notice.schema import UnblockRecallProductRequest
from app.domains.recall_notice.service import RecallNoticeService
from app.domains.user.model import User

router = APIRouter(prefix="/recall-notices", tags=["recall-notice"])


def get_recall_notice_service(
    db: Session = Depends(get_db),
) -> RecallNoticeService:

    return RecallNoticeService(db)


@router.get("/job-mode", response_model=RecallCheckJobModeResponse)
def get_job_mode(
    current_user: User = Depends(SuperAdminGuard),
    service: RecallNoticeService = Depends(get_recall_notice_service),
):

    return RecallCheckJobModeResponse(mode=service.get_job_mode())


@router.put("/job-mode", response_model=RecallCheckJobModeResponse)
def set_job_mode(
    data: RecallCheckJobModeSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: RecallNoticeService = Depends(get_recall_notice_service),
):

    mode = service.set_job_mode(
        data.mode, set_by=current_user.id, is_admin=True,
    )
    return RecallCheckJobModeResponse(mode=mode)


@router.get("/check-runs", response_model=list[RecallCheckRunResponse])
def list_check_runs(
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    return RecallNoticeRepository(db).list_check_runs()


@router.get("/product-blocks", response_model=list[RecallProductBlockResponse])
def list_product_blocks(
    status_filter: str | None = None,
    current_user: User = Depends(SuperAdminGuard),
    service: RecallNoticeService = Depends(get_recall_notice_service),
):

    return service.list_blocks(current_user.company_id, status=status_filter)


@router.get(
    "/product-blocks/{block_id}", response_model=RecallProductBlockResponse,
)
def get_product_block(
    block_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: RecallNoticeService = Depends(get_recall_notice_service),
):

    return service.get_block(block_id, current_user.company_id)


@router.post(
    "/product-blocks/{block_id}/unblock",
    response_model=RecallProductBlockResponse,
)
def unblock_product(
    block_id: int, data: UnblockRecallProductRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: RecallNoticeService = Depends(get_recall_notice_service),
):

    return service.unblock_product(
        block_id, current_user.company_id, is_admin=True,
        approved_by=current_user.id, justification=data.justification,
    )


__all__ = ["router"]
