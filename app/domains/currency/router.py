"""
=========================================================
Homez OS

File : app/domains/currency/router.py

2026-09-10 Phase 9 — 환율 기록·조회, 허용률 조회/변경 API. 환율
기록·허용률 변경은 SuperAdminGuard 필수(company/payment/refund
라우터와 동일한 민감도 처리). 실제 외부 환율 API 호출은 없다 —
`record_rate()`는 호출자가 이미 확인한 숫자를 그대로 저장한다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.domains.currency.schema import ExchangeRateResponse
from app.domains.currency.schema import ExchangeRateRecordRequest
from app.domains.currency.schema import ToleranceResponse
from app.domains.currency.schema import ToleranceSetRequest
from app.domains.currency.service import ExchangeRateService
from app.domains.user.model import User

router = APIRouter(prefix="/currency", tags=["currency"])


def get_exchange_rate_service(
    db: Session = Depends(get_db),
) -> ExchangeRateService:

    return ExchangeRateService(db)


@router.post(
    "/rates",
    response_model=ExchangeRateResponse,
)
def record_exchange_rate(
    data: ExchangeRateRecordRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: ExchangeRateService = Depends(get_exchange_rate_service),
):

    return service.record_rate(
        base_currency=data.base_currency,
        quote_currency=data.quote_currency,
        rate=data.rate,
        recorded_by=current_user.id,
    )


@router.get(
    "/rates/latest",
    response_model=ExchangeRateResponse | None,
)
def get_latest_exchange_rate(
    base_currency: str = Query(...),
    quote_currency: str = Query(...),
    current_user: User = Depends(SuperAdminGuard),
    service: ExchangeRateService = Depends(get_exchange_rate_service),
):

    return service.get_latest_rate(base_currency, quote_currency)


@router.get(
    "/tolerance",
    response_model=ToleranceResponse,
)
def get_exchange_rate_tolerance(
    current_user: User = Depends(SuperAdminGuard),
    service: ExchangeRateService = Depends(get_exchange_rate_service),
):

    return ToleranceResponse(
        tolerance_percent=service.get_tolerance_percent(
            current_user.company_id,
        ),
    )


@router.put(
    "/tolerance",
    response_model=ToleranceResponse,
)
def set_exchange_rate_tolerance(
    data: ToleranceSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: ExchangeRateService = Depends(get_exchange_rate_service),
):

    service.set_tolerance_percent(
        company_id=current_user.company_id, user_id=current_user.id,
        is_admin=True, tolerance_percent=data.tolerance_percent,
    )

    return ToleranceResponse(
        tolerance_percent=service.get_tolerance_percent(
            current_user.company_id,
        ),
    )


__all__ = ["router"]
