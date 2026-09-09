"""
=========================================================
Homez OS

File : app/domains/product_selection/router.py

상품 선별 통합 화면 — API 경계.
=========================================================
"""

from decimal import Decimal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.product_selection.schema import ProductSelectionOverviewResponse
from app.domains.product_selection.schema import ProfitabilityQuery
from app.domains.product_selection.service import ProductSelectionService
from app.domains.user.model import User

router = APIRouter(prefix="/product-selection", tags=["Product Selection"])

ProductSelectionViewGuard = ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)


@router.get(
    "/{product_candidate_id}/overview",
    response_model=ProductSelectionOverviewResponse,
)
def get_product_selection_overview(
    product_candidate_id: int,
    channel: str | None = None,
    sale_price: Decimal | None = Query(default=None),
    cost_of_goods: Decimal | None = Query(default=None),
    current_user: User = Depends(ProductSelectionViewGuard),
    db: Session = Depends(get_db),
):

    service = ProductSelectionService(db)
    profitability_query = (
        ProfitabilityQuery(sale_price=sale_price, cost_of_goods=cost_of_goods)
        if sale_price is not None else None
    )

    return service.get_overview(
        company_id=current_user.company_id,
        product_candidate_id=product_candidate_id,
        channel=channel,
        profitability_query=profitability_query,
    )


__all__ = ["router"]
