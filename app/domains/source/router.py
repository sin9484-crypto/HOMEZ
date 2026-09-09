"""
=========================================================
Homez OS

File : app/domains/source/router.py

공급처 검색·상품 연결 API — 쓰기(연결/거래정보 생성, 승인, 비활성화)는
admin_guard(ADMIN/SUPER_ADMIN)로만 보호된다. 조회(검색·목록·비교)는
2026-08-21 작업 4에서 SourcingViewGuard로 넓혔다 — 기존 permission_
catalog.py에 이미 존재하지만 이 코드베이스 어떤 라우터도 실제로
소비하지 않던 "SUPPLIER_VIEW" 코드를 재사용한다(신규 Permission 코드를
만들지 않는다는 지시에 따름). ADMIN/SUPER_ADMIN은 무조건 통과하고,
그 외 역할(MANAGER/STAFF, 그리고 role_permission 도메인에서
SUPPLIER_VIEW를 grant받은 임의의 역할 — 이 저장소에 UserRole enum
자체에는 없는 "VIEWER"도 이 grant 경로로만 접근 가능)은 SUPPLIER_VIEW
grant가 있어야 통과한다. 전부 current_user.company_id로 격리된다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.core.authorization import UserRole
from app.core.authorization import has_role
from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.purchase.schema import PurchaseResponse
from app.domains.source.discovery_providers import CsvSupplierDiscoveryProvider
from app.domains.source.discovery_providers import SupplierDiscoveryProviderError
from app.domains.source.discovery_providers import SupplierDiscoveryQuery
from app.domains.source.discovery_providers import get_discovery_provider
from app.domains.source.recommendation_service import SourcingRecommendationService
from app.domains.source.schema import CompanySupplierRelationApprove
from app.domains.source.schema import CompanySupplierRelationCreate
from app.domains.source.schema import CompanySupplierRelationResponse
from app.domains.source.schema import SourcingProductCandidateResponse
from app.domains.source.schema import SupplierDiscoverySearchRequest
from app.domains.source.schema import SupplierDiscoveryResultResponse
from app.domains.source.schema import SupplierProductLinkCreate
from app.domains.source.schema import SupplierProductLinkResponse
from app.domains.source.schema import SupplierPublicDirectoryResponse
from app.domains.source.service import SourceService
from app.domains.supplier.model import Supplier
from app.domains.user.model import User
from app.core.exceptions import BadRequestException
from app.domains.order.constants import OrderItemStatus
from app.domains.order.model import OrderItem
from app.domains.order.schema import OrderItemResponse

router = APIRouter(prefix="/sourcing", tags=["Sourcing"])

# "SUPPLIER_VIEW"는 app/domains/role_permission/permission_catalog.py에
# 이미 존재하는 기존 코드다(작업 4 — 신규 Permission 임의 생성 금지).
SourcingViewGuard = ListingWizardPermissionGuard("SUPPLIER_VIEW")


@router.post("/search", response_model=list[SupplierDiscoveryResultResponse])
def search_suppliers(
    data: SupplierDiscoverySearchRequest,
    current_user: User = Depends(SourcingViewGuard),
):
    """공급처 검색 — Manual/CSV/Fake Provider 선택(작업 1). 어떤
    Provider도 네트워크 호출을 하지 않는다(discovery_providers.py
    자체 계약) — 결과는 아무것도 저장하지 않고, 사용자가 이 결과를
    바탕으로 /sourcing/links로 명시적으로 확정해야 실제 연결이
    생긴다."""

    # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
    # 강제(SUPPLIER_RECOMMENDATION). 이 엔드포인트가 Provider를
    # 실제로 호출하는 유일한 진입점이다.
    require_active_capability(CapabilityCode.SUPPLIER_RECOMMENDATION)

    query = SupplierDiscoveryQuery(
        product_name=data.product_name,
        brand=data.brand,
        barcode=data.barcode,
        manufacturer_sku=data.manufacturer_sku,
        category=data.category,
        min_order_qty=data.min_order_qty,
        ship_regions=tuple(data.ship_regions),
    )

    try:
        if data.provider_code == "CSV":
            provider = CsvSupplierDiscoveryProvider(data.csv_rows)
        else:
            provider = get_discovery_provider(data.provider_code)
    except SupplierDiscoveryProviderError as exc:
        raise BadRequestException(str(exc)) from exc

    return provider.search_suppliers(query)


@router.get(
    "/product-candidates", response_model=list[SourcingProductCandidateResponse],
)
def list_sourcing_product_candidates(
    approved_only: bool = False,
    current_user: User = Depends(SourcingViewGuard),
    db: Session = Depends(get_db),
):
    """공급처 상품 연결 화면 전용 최소 조회(2026-08-21 4차 지시,
    작업 1) — 기존 admin 전용 GET /product-candidates의 권한은
    낮추지 않는다. 이 sourcing 경계에서 연결에 필요한 최소 필드
    (id/product_name/brand/category/status/is_approved)만 새로
    노출한다 — 원가·마진·내부 점수·타 회사 정보는 절대 포함하지
    않는다. 회사가 볼 수 있는 후보(GLOBAL 전체 + 자사 PRIVATE)만
    반환하고, 다른 회사의 PRIVATE 후보는 목록에서 자연히 빠진다
    (ProductCandidateService.list_candidates_for_company()가 이미
    보장) — 그 후보의 candidate_id로 직접 링크 생성을 시도해도
    SourceService.create_link()가 require_approved_for_company()로
    404를 던진다. 승인 여부는 ProductCandidateService의 회사별 단일
    진입점(require_approved_for_company()와 동일한 판단 로직)을
    그대로 재사용한다."""

    from app.domains.product_candidate.constants import CandidateStatus
    from app.domains.product_candidate.service import ProductCandidateService

    service = ProductCandidateService(db)
    candidates = service.list_candidates_for_company(
        current_user.company_id, limit=500,
    )

    results = []
    for candidate in candidates:
        effective_status = service.get_effective_status_for_company(
            candidate.id, current_user.company_id,
        )
        is_approved = effective_status == CandidateStatus.APPROVED
        if approved_only and not is_approved:
            continue
        results.append(SourcingProductCandidateResponse(
            id=candidate.id,
            product_name=candidate.product_name,
            brand=candidate.brand_hint,
            category=candidate.category_hint,
            status=effective_status,
            is_approved=is_approved,
        ))

    return results


@router.get(
    "/suppliers/public", response_model=list[SupplierPublicDirectoryResponse],
)
def list_public_suppliers(
    current_user: User = Depends(SourcingViewGuard),
    db: Session = Depends(get_db),
):
    """공개 공급처 정보 조회(작업 1) — 전역 공유 카탈로그의 공개
    식별정보만 반환한다. api_key/api_secret 등 비공개 필드는 이
    response_model 자체가 절대 포함하지 않는다."""

    return (
        db.query(Supplier)
        .filter(Supplier.is_active.is_(True))
        .order_by(Supplier.name)
        .all()
    )


@router.post("/links", response_model=SupplierProductLinkResponse)
def create_supplier_product_link(
    data: SupplierProductLinkCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)

    return service.create_link(data, current_user.company_id, current_user.id)


@router.get(
    "/candidates/{product_candidate_id}/links",
    response_model=list[SupplierProductLinkResponse],
)
def list_supplier_product_links(
    product_candidate_id: int,
    current_user: User = Depends(SourcingViewGuard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)

    return service.list_links_for_candidate(
        product_candidate_id, current_user.company_id,
    )


@router.get(
    "/candidates/{product_candidate_id}/best-link",
    response_model=SupplierProductLinkResponse | None,
)
def get_best_supplier_product_link(
    product_candidate_id: int,
    current_user: User = Depends(SourcingViewGuard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)

    return service.get_best_link(product_candidate_id, current_user.company_id)


@router.patch(
    "/links/{link_id}/deactivate", response_model=SupplierProductLinkResponse,
)
def deactivate_supplier_product_link(
    link_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)

    return service.deactivate_link(link_id, current_user.company_id)


@router.post("/relations", response_model=CompanySupplierRelationResponse)
def create_company_supplier_relation(
    data: CompanySupplierRelationCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)

    return service.create_relation(data, current_user.company_id, current_user.id)


_RELATION_RESTRICTED_FIELDS = (
    "contact_name", "contact_phone", "contact_email",
    "payment_terms", "credential_reference", "notes",
)


def _redact_relation_for_viewer(relation, is_privileged: bool) -> dict:
    """작업 2 — 계약 연락처·결제조건·Credential reference는 권한
    없는 사용자(ADMIN/SUPER_ADMIN이 아닌 SUPPLIER_VIEW grant만 있는
    조회자)에게 숨긴다. Credential 원문 자체는 애초에 이 테이블에
    저장되지 않는다(credential_reference는 불투명 참조 문자열일
    뿐 — model.py 주석 참고) — 그 참조 문자열조차 노출하지 않는다."""

    data = {
        "id": relation.id, "company_id": relation.company_id,
        "supplier_id": relation.supplier_id,
        "approval_status": relation.approval_status,
        "created_by": relation.created_by,
        "created_at": relation.created_at, "updated_at": relation.updated_at,
        "contact_name": relation.contact_name,
        "contact_phone": relation.contact_phone,
        "contact_email": relation.contact_email,
        "payment_terms": relation.payment_terms,
        "credential_reference": relation.credential_reference,
        "notes": relation.notes,
    }
    if not is_privileged:
        for field in _RELATION_RESTRICTED_FIELDS:
            data[field] = None

    return data


@router.get("/relations", response_model=list[CompanySupplierRelationResponse])
def list_company_supplier_relations(
    current_user: User = Depends(SourcingViewGuard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)
    relations = service.list_relations(current_user.company_id)
    is_privileged = has_role(current_user, UserRole.ADMIN, UserRole.SUPER_ADMIN)

    return [
        _redact_relation_for_viewer(relation, is_privileged)
        for relation in relations
    ]


@router.patch(
    "/relations/{relation_id}/approval",
    response_model=CompanySupplierRelationResponse,
)
def set_company_supplier_relation_approval(
    relation_id: int,
    data: CompanySupplierRelationApprove,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SourceService(db)

    return service.set_relation_approval(
        relation_id, current_user.company_id, data.approve,
    )


@router.get(
    "/order-items/out-of-stock", response_model=list[OrderItemResponse],
)
def list_out_of_stock_order_items(
    current_user: User = Depends(SourcingViewGuard),
    db: Session = Depends(get_db),
):
    """재고 부족(OUT_OF_STOCK) 주문 품목을 회사 전체 범위로 나열한다
    (작업 1 — "재고 부족 주문에서 발주안 생성" 진입점). 기존
    app/domains/order/router.py는 주문 하나 단위(GET /orders/{id}/items)
    로만 품목을 조회하고, OrderRepository에도 회사 전체·상태 필터
    조회 메서드가 없어(직접 확인함) 이 라우터에서 직접 조회한다 —
    같은 파일의 list_public_suppliers()가 이미 쓰는 것과 동일한
    패턴(레포지토리/서비스 계층을 거치지 않고 db.query 직접 사용)."""

    return (
        db.query(OrderItem)
        .filter(OrderItem.company_id == current_user.company_id)
        .filter(OrderItem.status == OrderItemStatus.OUT_OF_STOCK)
        .order_by(OrderItem.id.desc())
        .all()
    )


class PurchaseProposalRequest(BaseModel):

    idempotency_key: str


@router.post(
    "/order-items/{order_item_id}/purchase-proposal",
    response_model=PurchaseResponse,
)
def create_purchase_proposal_for_order_item(
    order_item_id: int,
    data: PurchaseProposalRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """OUT_OF_STOCK 주문 품목에 대해 가장 저렴한 활성 공급처 연결로
    발주안(Purchase, REQUESTED)을 만든다 — 사용자 승인은 기존
    PurchaseService.confirm_purchase()(/purchases/{id}/confirm)가
    담당한다."""

    service = SourcingRecommendationService(db)

    return service.create_purchase_proposal_for_order_item(
        order_item_id, current_user.company_id, current_user.id,
        data.idempotency_key,
    )


__all__ = [
    "router",
]
