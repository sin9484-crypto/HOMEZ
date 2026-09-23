"""
=========================================================
Homez OS

File : app/domains/purchase_task/supplier_option_link_router.py

2026-09-21 옵션 연결 — 쿠팡 판매 옵션(SKU) ↔ 온채널 상품코드·옵션ID 연결의
저장·조회·해제·준비 상태. 모든 API는 AdminGuard. 이 API는 어떤 실제
발주·판매신청·상품등록도 실행하지 않는다(저장 전 공급 상품 실조회 1회만).
=========================================================
"""

from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import AdminGuard
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierComponent,
)
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierOptionLinkService,
)
from app.domains.user.model import User

router = APIRouter(prefix="/supplier-option-links", tags=["Supplier Option Link"])


class SupplierComponentInput(BaseModel):
    product_code: str = Field(min_length=1, max_length=50)
    option_id: str = Field(min_length=1, max_length=50)
    units: int = Field(default=1, ge=1)


class SaveSupplierOptionLinkRequest(BaseModel):
    store_connection_id: int
    channel_sku: str = Field(min_length=1, max_length=150)
    purchase_connection_id: int
    components: list[SupplierComponentInput] = Field(min_length=1)
    replace: bool = False


class SupplierOptionLinkResponse(BaseModel):
    id: int
    store_connection_id: int
    channel_sku: str
    purchase_connection_id: int
    supplier_product_code: str
    supplier_option_id: str
    supplier_option_name_snapshot: Optional[str]
    units_per_sale: int
    status: str
    status_reason: Optional[str]
    coupang_seller_product_id: Optional[str]
    coupang_vendor_item_id: Optional[str]
    version: int

    model_config = {"from_attributes": True}


class ReadinessRequest(BaseModel):
    store_connection_id: int
    skus: list[str] = Field(min_length=1)


class ReadinessResponse(BaseModel):
    """클라이언트가 보낸 SKU 목록에 대한 "선택 범위 확인"이다. 서버가 옵션 목록을 확정한
    것이 아니므로 all_ready/ready는 항상 False다 — 전체 준비 완료는 등록 제출 단위의
    서버 확정 범위(상품 준비 화면의 옵션 연결 보기)에서만 판정한다."""

    scope: str
    total: int
    per_option: list[dict]
    selected_ready: bool
    all_ready: bool
    ready: bool
    reason: Optional[str]
    per_sku: dict[str, str]


class DisableRequest(BaseModel):
    reason: str = Field(default="사용자가 연결을 해제함", max_length=200)


@router.put("", response_model=SupplierOptionLinkResponse)
def save_supplier_option_link(
    data: SaveSupplierOptionLinkRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """사용자가 확인한 판매 옵션 ↔ 공급 옵션 대응을 저장한다(멱등). 이미 다른
    공급 옵션에 연결돼 있으면 replace=true로 명시해야 바꾼다. 저장 전에 그
    매입 계정으로 공급 상품을 실조회해 옵션ID가 그 상품에 속하는지 확인한다."""

    return SupplierOptionLinkService(db).save_link(
        current_user.company_id, store_connection_id=data.store_connection_id,
        channel_sku=data.channel_sku,
        purchase_connection_id=data.purchase_connection_id,
        components=[
            SupplierComponent(c.product_code, c.option_id, c.units)
            for c in data.components
        ],
        confirmed_by=current_user.id, replace=data.replace,
    )


@router.get("", response_model=list[SupplierOptionLinkResponse])
def list_supplier_option_links(
    store_connection_id: Optional[int] = None, channel_sku: Optional[str] = None,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = SupplierOptionLinkService(db)
    if not service.is_store_available(db):
        return []
    return service.list_links(
        current_user.company_id, store_connection_id=store_connection_id,
        channel_sku=channel_sku,
    )


@router.post("/readiness", response_model=ReadinessResponse)
def supplier_option_link_readiness(
    data: ReadinessRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """등록한 옵션 SKU 전부가 ACTIVE 연결을 가질 때만 ready=true —
    하나라도 없거나 재확인 필요면 자동 매입 준비 완료가 아니다."""

    return SupplierOptionLinkService(db).readiness(
        current_user.company_id, store_connection_id=data.store_connection_id,
        skus=data.skus,
    )


@router.post(
    "/{link_id}/clear-coupang-identifiers", response_model=SupplierOptionLinkResponse,
)
def clear_coupang_identifiers(
    link_id: int, data: DisableRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """식별자 충돌 복구용 명시 동작 — 저장된 쿠팡 상품번호·옵션번호를 비우고 연결을
    재확인 필요로 내린다. 다시 등록 결과를 조회해 부착한 뒤 연결을 재확인해야 한다."""

    return SupplierOptionLinkService(db).clear_coupang_identifiers(
        current_user.company_id, link_id, actor_user_id=current_user.id,
        reason=data.reason,
    )


@router.post("/{link_id}/disable", response_model=SupplierOptionLinkResponse)
def disable_supplier_option_link(
    link_id: int, data: DisableRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    return SupplierOptionLinkService(db).disable_link(
        current_user.company_id, link_id, actor_user_id=current_user.id,
        reason=data.reason,
    )
