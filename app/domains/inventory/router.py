"""
=========================================================
Homez OS

File : app/domains/inventory/router.py

Inventory Router — V7 Gate 3(2026-08-15) 처음부터 재설계.

전 엔드포인트가 admin_guard(app/domains/funding/router.py와 동일
컨벤션 — 재고는 자금과 동일한 민감도로 취급) + current_user.company_id
만 사용한다(요청 바디로 company_id를 받지 않는다 — coupang/decision/
settlement/funding에서 반복 발견된 크로스테넌트 결함 클래스를
처음부터 차단).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.guard import admin_guard
from app.domains.automation_safety.service import SafetyService
from app.domains.inventory.replenishment_advisory_service import (
    ReplenishmentAdvisoryService,
)
from app.domains.inventory.schema import InventoryAdjustRequest
from app.domains.inventory.schema import InventoryChannelMappingCreate
from app.domains.inventory.schema import InventoryChannelMappingResponse
from app.domains.inventory.schema import InventoryLedgerEventResponse
from app.domains.inventory.schema import InventoryReservationResponse
from app.domains.inventory.schema import InventoryReserveRequest
from app.domains.inventory.schema import InventoryRestockRequest
from app.domains.inventory.schema import InventorySkuCreate
from app.domains.inventory.schema import InventorySkuResponse
from app.domains.inventory.schema import ReplenishmentProposalCreate
from app.domains.inventory.schema import ReplenishmentProposalResponse
from app.domains.inventory.schema import ReplenishmentSuggestionRequest
from app.domains.inventory.schema import ReplenishmentSuggestionResponse
from app.domains.inventory.service import InventoryService
from app.domains.user.model import User

router = APIRouter(
    prefix="/inventory",
    tags=["Inventory"],
)


# --------------------------------------------------
# SKU
# --------------------------------------------------

@router.post(
    "/skus",
    response_model=InventorySkuResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sku(
    data: InventorySkuCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.create_sku(data, current_user.company_id)


@router.get(
    "/skus",
    response_model=list[InventorySkuResponse],
)
def list_skus(
    product_candidate_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.list_skus(
        current_user.company_id, product_candidate_id, skip, limit,
    )


@router.get(
    "/skus/{sku_id}",
    response_model=InventorySkuResponse,
)
def get_sku(
    sku_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.get_sku(sku_id, current_user.company_id)


# --------------------------------------------------
# 예약 / 해제 / 확정소모
# --------------------------------------------------

@router.post(
    "/skus/{sku_id}/reserve",
    response_model=InventoryReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def reserve_stock(
    sku_id: int,
    data: InventoryReserveRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.reserve(
        sku_id, current_user.company_id, data, current_user.id,
    )


@router.post(
    "/reservations/{reservation_id}/release",
    response_model=InventoryReservationResponse,
)
def release_reservation(
    reservation_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.release(
        reservation_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/reservations/{reservation_id}/consume",
    response_model=InventoryReservationResponse,
)
def consume_reservation(
    reservation_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.consume(
        reservation_id, current_user.company_id, current_user.id,
    )


@router.get(
    "/skus/{sku_id}/reservations",
    response_model=list[InventoryReservationResponse],
)
def list_reservations(
    sku_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.list_reservations(sku_id, current_user.company_id)


# --------------------------------------------------
# 입고 / 수동 조정
# --------------------------------------------------

@router.post(
    "/skus/{sku_id}/restock",
    response_model=InventoryLedgerEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def restock(
    sku_id: int,
    data: InventoryRestockRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.restock(
        sku_id, current_user.company_id, data, current_user.id,
    )


@router.post(
    "/skus/{sku_id}/adjust",
    response_model=InventoryLedgerEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def adjust_stock(
    sku_id: int,
    data: InventoryAdjustRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """수동 재고 조정 — admin_guard(권한) + reason 필수 + audit_logs 기록."""

    service = InventoryService(db)

    return service.adjust_stock(
        sku_id, current_user.company_id, data, current_user.id,
    )


@router.get(
    "/skus/{sku_id}/ledger",
    response_model=list[InventoryLedgerEventResponse],
)
def list_ledger(
    sku_id: int,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.list_ledger(
        sku_id, current_user.company_id, skip, limit,
    )


# --------------------------------------------------
# 채널 매핑 / 동기화(Fake Provider 전용)
# --------------------------------------------------

@router.post(
    "/skus/{sku_id}/channel-mappings",
    response_model=InventoryChannelMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_channel_mapping(
    sku_id: int,
    data: InventoryChannelMappingCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.create_channel_mapping(
        sku_id, current_user.company_id, data,
    )


@router.get(
    "/skus/{sku_id}/channel-mappings",
    response_model=list[InventoryChannelMappingResponse],
)
def list_channel_mappings(
    sku_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = InventoryService(db)

    return service.list_channel_mappings(sku_id, current_user.company_id)


@router.post(
    "/channel-mappings/{mapping_id}/sync",
    response_model=InventoryChannelMappingResponse,
)
def sync_channel_stock(
    mapping_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    "재고가 바뀌면 채널에 동기화 요청을 보낸다" — Fake Provider 전용
    (계획 문서 9절). 실제 쿠팡/네이버 API를 호출하지 않는다.
    """

    service = InventoryService(db)

    return service.sync_channel_stock(
        mapping_id, current_user.company_id, current_user.id,
    )


# --------------------------------------------------
# Gate AI-F(2026-08-22) — 재고 보충 추천(AI Capability:
# PRICING_INVENTORY). 조회만으로는 실제 재고·발주 상태가 절대
# 바뀌지 않는다.
# --------------------------------------------------

@router.post(
    "/skus/{sku_id}/replenishment-suggestion",
    response_model=ReplenishmentSuggestionResponse,
)
def suggest_replenishment(
    sku_id: int,
    data: ReplenishmentSuggestionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReplenishmentAdvisoryService(db)
    suggestion, envelope = service.suggest(
        sku_id, current_user.company_id,
        lookback_days=data.lookback_days,
    )

    return ReplenishmentSuggestionResponse(
        **suggestion.__dict__, ai_result=envelope.model_dump(mode="json"),
    )


@router.post(
    "/skus/{sku_id}/replenishment-proposals",
    response_model=ReplenishmentProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def propose_replenishment(
    sku_id: int,
    data: ReplenishmentProposalCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """재고 보충안 생성 — 사용자가 명시적으로 이 엔드포인트를 호출해야
    ProposedAction이 만들어진다. EStop 활성화 시 새 제안을 만들지
    않는다."""

    if SafetyService(db).is_emergency_stop_active():
        raise BadRequestException(
            "Emergency Stop이 활성화되어 있어 새 재고 보충 제안을 "
            "만들 수 없습니다.",
        )

    service = ReplenishmentAdvisoryService(db)
    suggestion, envelope, action = service.propose_replenishment(
        sku_id, current_user.company_id,
        idempotency_key=data.idempotency_key,
        lookback_days=data.lookback_days,
    )

    return ReplenishmentProposalResponse(
        suggestion=ReplenishmentSuggestionResponse(
            **suggestion.__dict__, ai_result=envelope.model_dump(mode="json"),
        ),
        proposed_action=action,
    )


__all__ = [
    "router",
]
