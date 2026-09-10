"""
=========================================================
Homez OS

File : app/domains/refund/router.py

2026-09-10 Phase 8 — 환불 API. 승인/거부/실행확정 모두 SuperAdminGuard
필수. 승인만 추가로 `X-Recent-Auth-Token`(비밀번호 재확인)을
요구한다 — 환불 승인은 실제 돈이 나가는 결정이므로 결제수단 등록·
자동결제 한도 변경과 동일한 민감도로 취급한다(app/domains/payment/
router.py와 동일한 패턴).

실제 환불 실행 엔드포인트(`/refunds/{id}/mark-executed`)는 존재하지만
`FakeRefundExecutor`만 호출한다 — 실제 네트워크 호출이 없다
(app/domains/refund/executor.py 참고).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import UnauthorizedException
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.domains.refund.executor import FakeRefundExecutor
from app.domains.refund.executor import RefundExecutor
from app.domains.refund.schema import RefundCreateRequest
from app.domains.refund.schema import RefundRejectRequest
from app.domains.refund.schema import RefundResponse
from app.domains.refund.service import RefundService
from app.domains.user.model import User

router = APIRouter(prefix="/refunds", tags=["refunds"])


def get_refund_executor() -> RefundExecutor:
    """
    2026-09-10 Phase 8 — 실제 Executor가 아직 없으므로 운영 경로도
    지금은 Fake뿐이다(app/domains/payment/router.py::
    get_payment_gateway()와 동일한 이유).
    """

    return FakeRefundExecutor()


def _require_recent_auth(token: str | None, user_id: int) -> None:

    if not consume_recent_auth_token(token, user_id):
        raise UnauthorizedException(
            "이 작업을 수행하려면 현재 비밀번호로 다시 확인해야 합니다.",
        )


def get_refund_service(
    db: Session = Depends(get_db),
    executor: RefundExecutor = Depends(get_refund_executor),
) -> RefundService:

    return RefundService(db, executor)


@router.post(
    "",
    response_model=RefundResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_refund(
    data: RefundCreateRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: RefundService = Depends(get_refund_service),
):

    return service.create_refund_request(
        company_id=current_user.company_id,
        order_id=data.order_id,
        return_order_id=data.return_order_id,
        refund_type=data.refund_type,
        amount=data.amount,
        currency=data.currency,
        reason=data.reason,
        requested_by=current_user.id,
        idempotency_key=data.idempotency_key,
    )


@router.get(
    "",
    response_model=list[RefundResponse],
)
def list_refunds(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(SuperAdminGuard),
    service: RefundService = Depends(get_refund_service),
):

    return service.list_for_company(
        current_user.company_id, status=status_filter,
    )


@router.post(
    "/{refund_id}/approve",
    response_model=RefundResponse,
)
def approve_refund(
    refund_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: RefundService = Depends(get_refund_service),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """
    "returns proceed only after user notice + approval" — 이
    엔드포인트가 그 요구사항의 유일한 진입점이다. 자동으로 여기
    도달하는 코드 경로는 이 저장소 어디에도 없다(항상 사람이 직접
    호출).
    """

    _require_recent_auth(recent_auth_token, current_user.id)

    return service.approve_refund(
        refund_id=refund_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True,
    )


@router.post(
    "/{refund_id}/reject",
    response_model=RefundResponse,
)
def reject_refund(
    refund_id: int,
    data: RefundRejectRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: RefundService = Depends(get_refund_service),
):

    return service.reject_refund(
        refund_id=refund_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True, reason=data.reason,
    )


@router.post(
    "/{refund_id}/mark-executed",
    response_model=RefundResponse,
)
def mark_refund_executed(
    refund_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: RefundService = Depends(get_refund_service),
):
    """Fake Executor만 호출한다 — 실제 네트워크 호출 없음."""

    return service.mark_executed(
        refund_id=refund_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True,
    )


__all__ = ["router"]
