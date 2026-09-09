"""
=========================================================
Homez OS

File : app/domains/purchase/router.py

Purchase Router — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.purchase.schema import OnchannelCredentialSaveRequest
from app.domains.purchase.schema import OnchannelCredentialStatusResponse
from app.domains.purchase.schema import PurchaseCancelRequest
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.schema import PurchaseItemResponse
from app.domains.purchase.schema import PurchaseResponse
from app.domains.purchase.schema import PurchaseRetrySubmitRequest
from app.domains.purchase.schema import PurchaseSubmitRequest
from app.domains.purchase.service import PurchaseService
from app.domains.user.model import User


def get_onchannel_credential_store() -> CredentialStore:
    return WindowsCredentialStore()


router = APIRouter(
    prefix="/purchases",
    tags=["Purchase"],
)

# 2026-08-21 작업 4 — app/domains/source/router.py::SourcingViewGuard와
# 동일한 이유·동일한 재사용(신규 Permission 코드를 만들지 않는다).
PurchaseViewGuard = ListingWizardPermissionGuard("SUPPLIER_VIEW")


@router.post(
    "",
    response_model=PurchaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_purchase(
    data: PurchaseCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = PurchaseService(db)

    return service.create_purchase(
        current_user.company_id, data, current_user.id,
    )


@router.get(
    "",
    response_model=list[PurchaseResponse],
)
def list_purchases(
    purchase_status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(PurchaseViewGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseService(db)

    return service.list_purchases(
        current_user.company_id, purchase_status, skip, limit,
    )


@router.get(
    "/system/onchannel-credential",
    response_model=OnchannelCredentialStatusResponse,
)
def get_onchannel_credential_status(
    current_user: User = Depends(admin_guard),
    credential_store: CredentialStore = Depends(get_onchannel_credential_store),
):
    """
    등록 여부만 알려준다 — 인증키 원문은 어떤 응답에도 담지 않는다.
    정적 경로이므로 `/{purchase_id}`보다 먼저 등록한다(NAVER API HUB
    credential 엔드포인트와 동일한 기존 관례 — 그렇지 않으면
    "system"이 purchase_id로 오인돼 422가 난다).
    """

    from app.core.windows_credential_store import CredentialNotFoundError
    from app.core.windows_credential_store import CredentialStoreError
    from app.domains.purchase.supplier_order_providers import (
        OnchannelSupplierOrderProvider,
    )

    try:
        credential = credential_store.read(
            OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE,
        )
        return OnchannelCredentialStatusResponse(
            registered=True, allowed_ip=credential.get("allowed_ip") or None,
        )
    except (CredentialNotFoundError, CredentialStoreError):
        return OnchannelCredentialStatusResponse(registered=False)


@router.post(
    "/system/onchannel-credential",
    response_model=OnchannelCredentialStatusResponse,
)
def save_onchannel_credential(
    data: OnchannelCredentialSaveRequest,
    current_user: User = Depends(admin_guard),
    credential_store: CredentialStore = Depends(get_onchannel_credential_store),
):
    """
    2026-09-08 — 온채널 API 인증키를 Windows Credential Manager에
    저장한다(평문 DB 저장 금지 원칙, NAVER API HUB credential
    저장과 동일 패턴).
    """

    from app.domains.purchase.supplier_order_providers import (
        OnchannelSupplierOrderProvider,
    )

    credential_store.save(
        OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE,
        {"auth_key": data.auth_key, "allowed_ip": data.allowed_ip},
    )

    return OnchannelCredentialStatusResponse(
        registered=True, allowed_ip=data.allowed_ip or None,
    )


@router.get(
    "/{purchase_id}",
    response_model=PurchaseResponse,
)
def get_purchase(
    purchase_id: int,
    current_user: User = Depends(PurchaseViewGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseService(db)

    return service.get_purchase(purchase_id, current_user.company_id)


@router.get(
    "/{purchase_id}/items",
    response_model=list[PurchaseItemResponse],
)
def list_purchase_items(
    purchase_id: int,
    current_user: User = Depends(PurchaseViewGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseService(db)

    return service.list_items(purchase_id, current_user.company_id)


@router.post(
    "/{purchase_id}/confirm",
    response_model=PurchaseResponse,
)
def confirm_purchase(
    purchase_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = PurchaseService(db)

    return service.confirm_purchase(
        purchase_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/{purchase_id}/receive",
    response_model=PurchaseResponse,
)
def receive_purchase(
    purchase_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    입고 확정 — Inventory.restock() + Funding.confirm_supplier_
    payment(company_id=...) + OUT_OF_STOCK 주문 품목 자동 재예약.
    """

    service = PurchaseService(db)

    return service.receive_purchase(
        purchase_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/{purchase_id}/submit",
    response_model=PurchaseResponse,
)
def submit_purchase_to_supplier(
    purchase_id: int,
    data: PurchaseSubmitRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """CONFIRMED(사용자 승인 완료) 발주를 실제 SupplierOrderProvider로
    전송한다 — 승인과 전송은 분리된 별도 단계다."""

    service = PurchaseService(db)

    return service.submit_to_supplier(
        purchase_id, current_user.company_id, data.provider_code,
        current_user.id, test_scenario=data.test_scenario,
    )


@router.post(
    "/{purchase_id}/submit/retry",
    response_model=PurchaseResponse,
)
def retry_purchase_submission(
    purchase_id: int,
    data: PurchaseRetrySubmitRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """실패(FAILED)한 공급처 발주 전송만 재시도한다(작업 1 항목 19) —
    이미 공급처가 응답한 상태(SUBMITTED/PARTIALLY_ACCEPTED/REJECTED)는
    재시도 대상이 아니다(중복 발주 방지)."""

    service = PurchaseService(db)

    return service.retry_submission_to_supplier(
        purchase_id, current_user.company_id, data.provider_code,
        current_user.id, test_scenario=data.test_scenario,
    )


@router.post(
    "/{purchase_id}/cancel",
    response_model=PurchaseResponse,
)
def cancel_purchase(
    purchase_id: int,
    data: PurchaseCancelRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = PurchaseService(db)

    return service.cancel_purchase(
        purchase_id, current_user.company_id, data.reason, current_user.id,
    )


__all__ = [
    "router",
]
