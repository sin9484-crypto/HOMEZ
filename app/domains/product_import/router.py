"""
=========================================================
Homez OS

File : app/domains/product_import/router.py

URL 상품 가져오기(2026-10-14) API.

  POST /product-import/search           URL 1건을 읽어 상품 정보 제안을 돌려준다(저장 없음)
  POST /product-import/margin-proposal  원가·수수료·목표 마진율로 판매가 제안과 마진 계산(저장 없음)
  POST /product-import/create-draft     사용자가 확인한 값으로 상품 후보와 위저드 초안을 만든다
  POST /product-import/analyze          URL 1건을 읽고 AI가 상품을 해석한다(AI 호출 1회 — 월 한도·승인 센터 허용 필요)
  POST /product-import/calculate        단가·이익 계산과 판정(AI 없음, 결정적 계산, 저장 없음)
  GET/PUT /product-import/channel-fees  플랫폼 수수료율(사용자가 확인한 값) 조회·저장
  POST /product-import/competitors/read     경쟁 상품 주소(최대 5개)를 읽고 AI가 표시 가격·구성을 해석한다(주소마다 AI 1회 — 월 한도·승인 센터 허용 필요)
  POST /product-import/competitors/compare  확인된 경쟁 가격으로 결정적 통계(AI 없음, 저장 없음)
  POST /product-import/strategy             마진 하한가·시장 구간·권장 구간 가격 전략 제안(AI 없음, 저장 없음, 가격은 바꾸지 않음)
  POST /product-import/supplier-offers/rank 공급처 무관 공급 조건 비교(최대 50건, 순수 계산·AI 없음·저장 없음, 모르는 비용은 보류)

쿠팡 호출은 없다. 외부 접근은 /search의 페이지 읽기(+ 온채널이면 공식 API 상품 조회)와 /create-draft의 선택한 이미지
다운로드뿐이다. 권한은 위저드 생성 권한(listing_wizard.create)을 쓴다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.marketplace_listing.listing_wizard_permission_guard import ListingWizardPermissionGuard
from app.domains.marketplace_listing.listing_wizard_permissions import LISTING_WIZARD_CREATE
from app.domains.media_asset.url_import import RequestsTransport
from app.domains.media_asset.url_import import Transport
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.product_import import schema
from app.domains.product_import.channel_fee_source import UserConfirmedFeeStore
from app.domains.product_import.service import ProductImportError
from app.domains.product_import.service import ProductImportService
from app.domains.product_import.url_analysis_service import CalcInput
from app.domains.product_import.url_analysis_service import UrlAnalysisService
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
        response = service.search(data.url, current_user.company_id, current_user.id)
    except ProductImportError as exc:
        raise BadRequestException(str(exc)) from exc
    # 2026-10-18 S1 — 이미 있는 후보 중 같거나 비슷한 것을 알린다(읽기 전용, 실패해도 가져오기는 막지 않는다).
    try:
        from app.domains.product_candidate.candidate_similarity_service import CandidateSimilarityService

        response.similar_candidates = [schema.SimilarCandidate(**row) for row in CandidateSimilarityService(db).similar_to(
            current_user.company_id, response.suggested_product_name or response.title,
            CandidateSimilarityService.spec_text([(row.label, row.value) for row in response.notice_rows[:60]]), exclude_reference=response.source_reference,
        )]
    except Exception:  # noqa: BLE001 — 알림은 편의 기능이다
        db.rollback()
    return response


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


def get_llm_credential_store() -> CredentialStore:
    """AI 키 저장소 — 항상 실제 Windows 자격증명 저장소. 테스트는 이 의존성을 덮어쓴다."""

    return WindowsCredentialStore()


@router.post("/analyze")
def analyze_product_url(
    data: schema.AnalyzeRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
    transport: Transport = Depends(get_transport),
    connection_service=Depends(get_purchase_channel_connection_service),
    credential_store: CredentialStore = Depends(get_llm_credential_store),
):
    """페이지 읽기 1회(+온채널 API 1회) 뒤 AI 해석 1회. 우리 가격·원가는 AI에 보내지 않는다. 계산은 /calculate가 한다."""

    service = UrlAnalysisService(db, ProductImportService(db, transport, connection_service), AiDraftService(db, credential_store))
    try:
        return service.analyze(data.url, current_user.company_id, current_user.id)
    except ProductImportError as exc:
        raise BadRequestException(str(exc)) from exc


def _calc_input(data: schema.CalculateRequest) -> CalcInput:
    """계산 요청 → 계산 입력(계산·가격 전략이 같이 쓴다)."""

    return CalcInput(
        channel=data.channel.strip().upper(), category_code=data.category_code, supply_price=data.supply_price,
        vat_state=data.vat_state, shipping_cost=data.shipping_cost, sell_quantity=data.sell_quantity,
        packaging_cost=data.packaging_cost, return_reserve_rate=data.return_reserve_rate, payment_fee_rate=data.payment_fee_rate,
        sale_price=data.sale_price, pack_count=data.pack_count, unit_volume=data.unit_volume, unit=data.unit,
        risk_codes=list(data.risk_codes), acknowledged_risks=list(data.acknowledged_risks),
        ai_sourced=list(data.ai_sourced), ai_values_confirmed=data.ai_values_confirmed,
    )


@router.post("/calculate")
def calculate_product_economics(
    data: schema.CalculateRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_llm_credential_store),
):
    """결정적 계산과 판정(AI·바깥 호출 없음, 저장 없음). 모르는 값이 있으면 계산하지 않고 보류로 돌려준다."""

    inp = _calc_input(data)
    service = UrlAnalysisService(db, ProductImportService(db, RequestsTransport()), AiDraftService(db, credential_store))
    return service.calculate(current_user.company_id, current_user.id, inp)


@router.post("/competitors/read")
def read_competitor_prices(
    data: schema.CompetitorReadRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
    transport: Transport = Depends(get_transport),
    credential_store: CredentialStore = Depends(get_llm_credential_store),
):
    """2026-10-18 마케팅 AI M2 — 사용자가 넣은 경쟁 상품 주소(최대 5개)를 한 건씩 읽고 주소마다 AI 해석 1회. 저장 없음, 쓰기 없음.
    월 한도·승인 센터 허용이 있어야 나간다. 우리 가격·원가는 AI에 보내지 않는다. 통계는 /competitors/compare가 한다."""

    from app.domains.product_import.competitor_price_service import CompetitorPriceService

    service = CompetitorPriceService(db, ProductImportService(db, transport), AiDraftService(db, credential_store))
    return service.read(data.urls, current_user.company_id, current_user.id)


@router.post("/competitors/compare")
def compare_competitor_prices(
    data: schema.CompetitorCompareRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
):
    """결정적 통계(AI·바깥 호출·DB 없음). AI가 읽은 값은 사용자가 확인했다고 표시한 것만 센다."""

    from app.domains.product_import.competitor_price_service import CompetitorPriceService
    from app.domains.product_import.competitor_price_service import to_entry

    return CompetitorPriceService.compare([to_entry(row) for row in data.entries])


@router.post("/strategy")
def propose_price_strategy(
    data: schema.StrategyRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
):
    """2026-10-18 마케팅 AI M3 — 마진 하한가·시장 구간(M2)·권장 구간을 함께 보여 주는 가격 전략 제안(AI·바깥 호출·저장 없음).
    어떤 제안도 마진 하한가 아래로 내려가지 않고, 모르는 값이 있으면 가격을 만들지 않는다. 가격은 바꾸지 않는다."""

    from decimal import Decimal

    from app.domains.pricing.price_strategy import Market
    from app.domains.pricing.price_strategy import strategy_report

    market = None
    if data.market is not None:
        market = Market(n=data.market.n, basis=data.market.basis, min=Decimal(data.market.min),
                        median=None if data.market.median is None else Decimal(data.market.median),
                        max=None if data.market.max is None else Decimal(data.market.max))
    return strategy_report(db, current_user.company_id, current_user.id, _calc_input(data), market)


@router.post("/supplier-offers/rank")
def rank_supplier_offers(
    data: schema.SupplierOfferRankRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
):
    """2026-10-23 상품 서칭 AI S3 — 공급처가 어디든 같은 규칙으로 공급 조건을 비교한다(AI·바깥 호출·저장 없음, 순수 계산).
    모르는 비용은 후보 가능으로 올리지 않고, 위탁이 안 되거나 최소 주문이 1보다 크면 제외하며, 순서는 결정적이다. 권고일 뿐 — 공급처 선택은 사용자가 한다."""

    from app.domains.supplier import offer_rules as rules

    offers = [
        rules.SupplierOffer(
            supplier_code=o.supplier_code, external_id=o.external_id, title=o.title, unit_cost=o.unit_cost, shipping_cost=o.shipping_cost,
            return_shipping_cost=o.return_shipping_cost, min_order_qty=o.min_order_qty, price_visible=o.price_visible,
            dropship_supported=o.dropship_supported, api_available=o.api_available, domestic_stock=o.domestic_stock,
        )
        for o in data.offers
    ]
    assumptions = rules.MarginAssumptions(fee_rate=data.fee_rate, return_reserve_rate=data.return_reserve_rate, min_margin=data.min_margin)
    return {
        "results": rules.rank_offers(offers, data.target_price, assumptions),
        "assumptions": {"fee_rate": str(data.fee_rate), "return_reserve_rate": str(data.return_reserve_rate), "min_margin": str(data.min_margin)},
        "assumptions_note": "수수료율·반품 예비율은 가정값입니다(의류의 실제 쿠팡 수수료율은 확인하지 못했습니다). 실제 값을 넣어 다시 계산하세요.",
    }


@router.get("/channel-fees")
def list_channel_fees(
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
):
    return {"entries": UserConfirmedFeeStore(db).entries(current_user.id, current_user.company_id)}


@router.put("/channel-fees")
def save_channel_fee(
    data: schema.ChannelFeeSaveRequest,
    current_user: User = Depends(ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)),
    db: Session = Depends(get_db),
):
    """사용자가 플랫폼(예: 쿠팡 WING)에서 직접 확인한 수수료율을 저장한다. 출처는 USER_CONFIRMED로 남고 감사 기록이 생긴다."""

    entry = UserConfirmedFeeStore(db).save(
        current_user.id, current_user.company_id, channel=data.channel, category_code=data.category_code, rate=data.rate, note=data.note,
    )
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action="CHANNEL_FEE_CONFIRMED", entity="channel_fee",
        entity_id=f"{data.channel.strip().upper()}:{data.category_code}"[:100], description=f"플랫폼 수수료율 사용자 확인 저장({entry['rate']})",
    )
    db.commit()
    return entry
