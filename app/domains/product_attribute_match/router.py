"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/router.py

2026-09-15 전면 감사 후속(Phase 9G, 10-4).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.domains.product_attribute_match.schema import (
    ProductAttributeComparisonRunResponse,
)
from app.domains.product_attribute_match.schema import ResolveComparisonRunRequest
from app.domains.product_attribute_match.schema import RunComparisonRequest
from app.domains.product_attribute_match.service import ProductAttributeMatchService
from app.domains.user.model import User

router = APIRouter(
    prefix="/product-attribute-comparisons", tags=["product-attribute-match"],
)


def get_product_attribute_match_service(
    db: Session = Depends(get_db),
) -> ProductAttributeMatchService:

    return ProductAttributeMatchService(db)


def _to_tuples(values):

    return {
        field: (item.value, item.source, item.confirmed_at)
        for field, item in values.items()
    }


@router.post("", response_model=ProductAttributeComparisonRunResponse)
def run_comparison(
    data: RunComparisonRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: ProductAttributeMatchService = Depends(
        get_product_attribute_match_service,
    ),
):

    return service.run_comparison(
        company_id=current_user.company_id,
        product_identifier=data.product_identifier,
        connection_id=data.connection_id,
        supplier_values=_to_tuples(data.supplier_values),
        sales_channel_values=_to_tuples(data.sales_channel_values),
        homez_current_values=_to_tuples(data.homez_current_values),
        triggered_by=current_user.id,
    )


@router.get("", response_model=list[ProductAttributeComparisonRunResponse])
def list_comparisons(
    status_filter: str | None = None,
    current_user: User = Depends(SuperAdminGuard),
    service: ProductAttributeMatchService = Depends(
        get_product_attribute_match_service,
    ),
):

    return service.list_runs(current_user.company_id, status=status_filter)


@router.get("/{run_id}", response_model=ProductAttributeComparisonRunResponse)
def get_comparison(
    run_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: ProductAttributeMatchService = Depends(
        get_product_attribute_match_service,
    ),
):

    return service.get_run(run_id, current_user.company_id)


@router.post(
    "/{run_id}/resolve", response_model=ProductAttributeComparisonRunResponse,
)
def resolve_comparison(
    run_id: int, data: ResolveComparisonRunRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: ProductAttributeMatchService = Depends(
        get_product_attribute_match_service,
    ),
):

    return service.resolve_run(
        run_id, current_user.company_id, is_admin=True,
        resolved_by=current_user.id,
        resolution_note=data.resolution_note,
        selected_values=data.selected_values,
    )


__all__ = ["router"]
