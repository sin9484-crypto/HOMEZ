"""
=========================================================
Homez OS

File : app/domains/funding/router.py

Funding Router — 사업 운영자금 API

2026-08-15 V7 Gate 2 — Account 관련 전 엔드포인트가 이제
current_user.company_id를 Service로 실제 전달한다(요구사항 3). 이전에는
company_id가 요청 바디(FundingAccountCreate.company_id)로만 전달되거나
아예 전달되지 않아, 인증된 어떤 admin이든 타사 명의로 계정을 만들거나
account_id만으로 다른 회사의 계정을 조회/증액/감액할 수 있었다 —
coupang/decision/settlement/audit_logs와 동일 클래스의 실제 크로스테넌트
결함이었다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.schema import FundingAccountResponse
from app.domains.funding.schema import FundingAmountUpdate
from app.domains.funding.schema import FundingHoldResponse
from app.domains.funding.schema import FundingLedgerResponse
from app.domains.funding.schema import SupplierPaymentConfirm
from app.domains.funding.schema import SupplierPaymentResponse
from app.domains.funding.service import FundingService
from app.domains.user.model import User


router = APIRouter(
    prefix="/funding",
    tags=["Funding"],
)


@router.post(
    "/accounts",
    response_model=FundingAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_funding_account(
    data: FundingAccountCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """사업 운영자금 최초 등록. company_id는 항상 current_user에서 온다."""

    service = FundingService(db)

    return service.create_account(data, current_user.company_id)


@router.get(
    "/accounts",
    response_model=list[FundingAccountResponse],
)
def list_funding_accounts(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """이 회사 소유 계정만 반환한다(보통 0개 또는 1개)."""

    service = FundingService(db)

    return service.list_accounts(current_user.company_id)


@router.get(
    "/accounts/{account_id}",
    response_model=FundingAccountResponse,
)
def get_funding_account(
    account_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = FundingService(db)

    return service.get_account(account_id, current_user.company_id)


@router.post(
    "/accounts/{account_id}/add",
    response_model=FundingAccountResponse,
)
def add_funding(
    account_id: int,
    data: FundingAmountUpdate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """사업 운영자금 추가."""

    service = FundingService(db)

    return service.add_funding(account_id, data, current_user.company_id)


@router.post(
    "/accounts/{account_id}/remove",
    response_model=FundingAccountResponse,
)
def remove_funding(
    account_id: int,
    data: FundingAmountUpdate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """사업 운영자금 회수."""

    service = FundingService(db)

    return service.remove_funding(account_id, data, current_user.company_id)


@router.get(
    "/accounts/{account_id}/ledgers",
    response_model=list[FundingLedgerResponse],
)
def list_funding_ledgers(
    account_id: int,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = FundingService(db)

    return service.list_ledgers(
        account_id, current_user.company_id, skip, limit,
    )


@router.get(
    "/holds",
    response_model=list[FundingHoldResponse],
)
def list_holds_by_order(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    2026-08-15 V7 Gate 2 — 이 회사 소유 Hold만 반환한다. Order 자체는
    아직 company_id가 없지만(V7 Gate 3/4 범위), FundingHold는 생성
    시점에 계정에서 company_id를 비정규화해 갖고 있어 이 필터가
    가능하다 — 다른 회사가 만든 Hold가 이 응답에 섞여 나오지 않는다.
    """

    service = FundingService(db)

    return service.repository.list_holds_by_order(
        order_id, current_user.company_id,
    )


@router.post(
    "/purchases/{purchase_id}/confirm-payment",
    response_model=SupplierPaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_supplier_payment(
    purchase_id: int,
    data: SupplierPaymentConfirm | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    공급처 지급 수동 확정.

    Hold HELD → COMMITTED, 사업 운영자금 차감.
    complete_purchase와 연결하지 않는다.

    2026-08-15 V7 Gate 2 — current_user.company_id를 함께 전달해 이
    Purchase에 연결된 Hold/계정이 실제로 호출자 회사 소유인지
    검증한다(Purchase 자체에는 company_id가 없지만, Hold는 생성
    시점에 계정에서 company_id를 비정규화해 갖고 있어 검증 가능하다
    — 다른 회사의 Hold를 purchase_id 추측만으로 확정할 수 없다).
    """

    service = FundingService(db)

    return service.confirm_supplier_payment(
        purchase_id,
        data,
        company_id=current_user.company_id,
    )
