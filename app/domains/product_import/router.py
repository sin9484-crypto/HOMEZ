"""
=========================================================
Homez OS

File : app/domains/product_import/router.py

URL 상품 가져오기(2026-10-14) API.

  POST /product-import/search           URL 1건을 읽어 상품 정보 제안을 돌려준다(저장 없음)
  POST /product-import/margin-proposal  원가·수수료·목표 마진율로 판매가 제안과 마진 계산(저장 없음)
  POST /product-import/create-draft     사용자가 확인한 값으로 상품 후보와 위저드 초안을 만든다

쿠팡 호출은 없다. 외부 접근은 /search의 페이지 읽기(+ 온채널이면 공식 API 상품 조회)와 /create-draft의 선택한 이미지
다운로드뿐이다. 권한은 위저드 생성 권한(listing_wizard.create)을 쓴다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.domains.marketplace_listing.listing_wizard_permission_guard import ListingWizardPermissionGuard
from app.domains.marketplace_listing.listing_wizard_permissions import LISTING_WIZARD_CREATE
from app.domains.media_asset.url_import import RequestsTransport
from app.domains.media_asset.url_import import Transport
from app.domains.product_import import schema
from app.domains.product_import.service import ProductImportError
from app.domains.product_import.service import ProductImportService
from app.domains.purchase_task.router import get_purchase_channel_connection_service
from app.domains.user.model import User

router = APIRouter(prefix="/product-import", tags=["product-import"])


def get_transport() -> Transport:
    """테스트는 이 의존성을 FakeTransport로 덮어쓴다(실제 네트워크 사용 금지)."""

    return RequestsTransport()


@router.post("/search", response_model=schema.UrlSearchResponse)
def search_product_url(
    data: schema.UrlSearchRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
    transport: Transport = Depends(get_transport),
    connection_service=Depends(get_purchase_channel_connection_service),
):
    service = ProductImportService(db, transport, connection_service)
    try:
        return service.search(data.url, current_user.company_id, current_user.id)
    except ProductImportError as exc:
        raise BadRequestException(str(exc)) from exc


@router.post("/margin-proposal", response_model=schema.MarginProposalResponse)
def propose_margin(
    data: schema.MarginProposalRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
):
    return ProductImportService.propose_margin(data)


@router.post("/create-draft", response_model=schema.DraftCreateResponse)
def create_draft_from_import(
    data: schema.DraftCreateRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
    transport: Transport = Depends(get_transport),
):
    service = ProductImportService(db, transport)
    try:
        return service.create_draft(data, current_user.company_id, current_user.id)
    except ProductImportError as exc:
        raise BadRequestException(str(exc)) from exc


@router.post("/notice-suggestions", response_model=schema.NoticeSuggestionResponse)
def suggest_notice_information(
    data: schema.NoticeSuggestionRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
):
    """URL 가져오기 때 보관한 공급처 고시로 5단계 정보고시 칸의 제안값을 돌려준다(읽기 전용, 외부 호출 없음)."""

    service = ProductImportService(db, transport=None)
    return service.notice_suggestions(data.candidate_id, data.labels, current_user.company_id)
