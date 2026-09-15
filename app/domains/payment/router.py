"""
=========================================================
Homez OS

File : app/domains/payment/router.py

2026-09-10 Phase 7 — 결제수단·자동결제 한도 API. 결제수단 등록·
비활성화·한도 변경은 전부 `X-Recent-Auth-Token`(비밀번호 재확인,
app/core/recent_auth.py)을 요구한다 — HOMEZ_USER_OPERATION_
SETTINGS.md "자동결제 설정을 변경하기 전에는 비밀번호를 다시
확인한다"의 구현이다(app/domains/company/router.py의 회사명 변경과
동일한 패턴).

실제 결제 실행 엔드포인트는 없다 — 이 라우터는 결제수단 등록·조회·
비활성화와 자동결제 한도 설정·조회, "지금 자동결제가 허용되는가"
판정까지만 노출한다.
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
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.payment.gateway import FakePaymentProvider
from app.domains.payment.gateway import PaymentProvider
from app.domains.payment.schema import AutoPaymentAllowedResponse
from app.domains.payment.schema import PaymentAutoLimitResponse
from app.domains.payment.schema import PaymentAutoLimitSetRequest
from app.domains.payment.schema import PaymentMethodRegisterRequest
from app.domains.payment.schema import PaymentMethodResponse
from app.domains.payment.service import PaymentService
from app.domains.user.model import User

router = APIRouter(prefix="/payments", tags=["payments"])


def get_credential_store() -> CredentialStore:
    """운영 경로 기본값은 항상 실제 Windows Credential Manager다 —
    테스트는 이 의존성을 오버라이드한다."""

    return WindowsCredentialStore()


def get_payment_gateway() -> PaymentProvider:
    """
    2026-09-10 Phase 7 — 실제 Provider가 아직 없으므로 운영 경로도
    지금은 Fake뿐이다. 실제 Provider가 추가되면(별도 승인 하에)
    이 함수만 바꾸면 된다 — 나머지 코드는 `PaymentProvider` 계약에만
    의존한다.
    """

    return FakePaymentProvider()


def _require_recent_auth(token: str | None, user_id: int) -> None:

    if not consume_recent_auth_token(token, user_id):
        raise UnauthorizedException(
            "이 작업을 수행하려면 현재 비밀번호로 다시 확인해야 합니다.",
        )


def get_payment_service(
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_credential_store),
    gateway: PaymentProvider = Depends(get_payment_gateway),
) -> PaymentService:

    return PaymentService(db, credential_store, gateway)


@router.post(
    "/methods",
    response_model=PaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_payment_method(
    data: PaymentMethodRegisterRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):

    _require_recent_auth(recent_auth_token, current_user.id)

    return service.register_method(
        company_id=current_user.company_id,
        user_id=current_user.id,
        is_admin=True,
        method_type=data.method_type,
        raw_details=data.raw_details,
        display_name=data.display_name,
        make_default=data.make_default,
    )


@router.get(
    "/methods",
    response_model=list[PaymentMethodResponse],
)
def list_payment_methods(
    include_inactive: bool = Query(default=False),
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
):

    return service.list_methods(
        current_user.company_id, include_inactive=include_inactive,
    )


@router.post(
    "/methods/{method_id}/deactivate",
    response_model=PaymentMethodResponse,
)
def deactivate_payment_method(
    method_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):

    _require_recent_auth(recent_auth_token, current_user.id)

    return service.deactivate_method(
        company_id=current_user.company_id,
        method_id=method_id,
        is_admin=True,
    )


@router.post(
    "/methods/{method_id}/set-default",
    response_model=PaymentMethodResponse,
)
def set_default_payment_method(
    method_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):

    _require_recent_auth(recent_auth_token, current_user.id)

    return service.set_default_method(
        company_id=current_user.company_id,
        method_id=method_id,
        is_admin=True,
    )


@router.put(
    "/auto-limit",
    response_model=PaymentAutoLimitResponse,
)
def set_auto_payment_limit(
    data: PaymentAutoLimitSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):

    _require_recent_auth(recent_auth_token, current_user.id)

    return service.set_auto_limit(
        company_id=current_user.company_id,
        user_id=current_user.id,
        is_admin=True,
        per_transaction_limit_amount=data.per_transaction_limit_amount,
        daily_limit_amount=data.daily_limit_amount,
        currency=data.currency,
    )


@router.get(
    "/auto-limit",
    response_model=PaymentAutoLimitResponse | None,
)
def get_auto_payment_limit(
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
):

    return service.get_auto_limit(current_user.company_id)


@router.get(
    "/auto-payment-allowed",
    response_model=AutoPaymentAllowedResponse,
)
def check_auto_payment_allowed(
    amount: float = Query(...),
    current_user: User = Depends(SuperAdminGuard),
    service: PaymentService = Depends(get_payment_service),
):
    """
    "지금 이 금액을 자동으로 결제해도 되는가"만 판정한다 — 이
    엔드포인트는 실제 결제를 절대 실행하지 않는다.
    """

    allowed, reason = service.verify_auto_payment_allowed(
        current_user.company_id, amount,
    )

    return AutoPaymentAllowedResponse(allowed=allowed, reason=reason)


__all__ = ["router"]
