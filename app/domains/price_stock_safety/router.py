"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/router.py

2026-09-10 Phase 10 — 가상재고 임계값·가격 검토주기 API.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.domains.price_stock_safety.schema import PriceCacheTtlResponse
from app.domains.price_stock_safety.schema import PriceCacheTtlSetRequest
from app.domains.price_stock_safety.schema import ResolveVirtualStockZeroProposalRequest
from app.domains.price_stock_safety.schema import ReviewCycleResponse
from app.domains.price_stock_safety.schema import ReviewCycleSetRequest
from app.domains.price_stock_safety.schema import StockCacheTtlResponse
from app.domains.price_stock_safety.schema import StockCacheTtlSetRequest
from app.domains.price_stock_safety.schema import VirtualStockCheckRequest
from app.domains.price_stock_safety.schema import VirtualStockCheckResponse
from app.domains.price_stock_safety.schema import VirtualStockThresholdResponse
from app.domains.price_stock_safety.schema import VirtualStockThresholdSetRequest
from app.domains.price_stock_safety.schema import VirtualStockZeroProposalResponse
from app.domains.price_stock_safety.service import PriceStockSafetyService
from app.domains.user.model import User

router = APIRouter(prefix="/price-stock-safety", tags=["price-stock-safety"])


def get_price_stock_safety_service(
    db: Session = Depends(get_db),
) -> PriceStockSafetyService:

    return PriceStockSafetyService(db)


@router.get(
    "/virtual-stock-threshold",
    response_model=VirtualStockThresholdResponse,
)
def get_virtual_stock_threshold(
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    return VirtualStockThresholdResponse(
        threshold_quantity=service.get_virtual_stock_threshold(
            current_user.company_id,
        ),
    )


@router.put(
    "/virtual-stock-threshold",
    response_model=VirtualStockThresholdResponse,
)
def set_virtual_stock_threshold(
    data: VirtualStockThresholdSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    service.set_virtual_stock_threshold(
        company_id=current_user.company_id, user_id=current_user.id,
        is_admin=True, threshold_quantity=data.threshold_quantity,
    )

    return VirtualStockThresholdResponse(
        threshold_quantity=service.get_virtual_stock_threshold(
            current_user.company_id,
        ),
    )


@router.post(
    "/virtual-stock-check",
    response_model=VirtualStockCheckResponse,
)
def check_virtual_stock(
    data: VirtualStockCheckRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    allowed, reason = service.check_virtual_stock_allows_auto_order(
        current_user.company_id, data.displayed_stock,
    )

    return VirtualStockCheckResponse(allowed=allowed, reason=reason)


@router.get(
    "/review-cycle",
    response_model=ReviewCycleResponse,
)
def get_review_cycle(
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    return ReviewCycleResponse(
        review_cycle_days=service.get_review_cycle_days(
            current_user.company_id,
        ),
    )


@router.put(
    "/review-cycle",
    response_model=ReviewCycleResponse,
)
def set_review_cycle(
    data: ReviewCycleSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    service.set_review_cycle_days(
        company_id=current_user.company_id, user_id=current_user.id,
        is_admin=True, review_cycle_days=data.review_cycle_days,
    )

    return ReviewCycleResponse(
        review_cycle_days=service.get_review_cycle_days(
            current_user.company_id,
        ),
    )


@router.get(
    "/price-cache-ttl",
    response_model=PriceCacheTtlResponse,
)
def get_price_cache_ttl(
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    return PriceCacheTtlResponse(
        ttl_minutes=service.get_price_cache_ttl_minutes(
            current_user.company_id,
        ),
    )


@router.put(
    "/price-cache-ttl",
    response_model=PriceCacheTtlResponse,
)
def set_price_cache_ttl(
    data: PriceCacheTtlSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    service.set_price_cache_ttl_minutes(
        company_id=current_user.company_id, user_id=current_user.id,
        is_admin=True, ttl_minutes=data.ttl_minutes,
    )

    return PriceCacheTtlResponse(
        ttl_minutes=service.get_price_cache_ttl_minutes(
            current_user.company_id,
        ),
    )


@router.get(
    "/stock-cache-ttl",
    response_model=StockCacheTtlResponse,
)
def get_stock_cache_ttl(
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    return StockCacheTtlResponse(
        ttl_minutes=service.get_stock_cache_ttl_minutes(
            current_user.company_id,
        ),
    )


@router.put(
    "/stock-cache-ttl",
    response_model=StockCacheTtlResponse,
)
def set_stock_cache_ttl(
    data: StockCacheTtlSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    service.set_stock_cache_ttl_minutes(
        company_id=current_user.company_id, user_id=current_user.id,
        is_admin=True, ttl_minutes=data.ttl_minutes,
    )

    return StockCacheTtlResponse(
        ttl_minutes=service.get_stock_cache_ttl_minutes(
            current_user.company_id,
        ),
    )


@router.get(
    "/virtual-stock-zero-proposals",
    response_model=list[VirtualStockZeroProposalResponse],
)
def list_virtual_stock_zero_proposals(
    status_filter: str | None = None,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    return service.list_zero_stock_proposals(
        current_user.company_id, status=status_filter,
    )


@router.post(
    "/virtual-stock-zero-proposals/{proposal_id}/resolve",
    response_model=VirtualStockZeroProposalResponse,
)
def resolve_virtual_stock_zero_proposal(
    proposal_id: int, data: ResolveVirtualStockZeroProposalRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: PriceStockSafetyService = Depends(
        get_price_stock_safety_service,
    ),
):

    return service.resolve_zero_stock_proposal(
        proposal_id, current_user.company_id, is_admin=True,
        resolved_by=current_user.id, approve=data.approve,
        resolution_note=data.resolution_note,
    )


__all__ = ["router"]
