"""
=========================================================
Homez OS

File : app/domains/inventory/__init__.py

Inventory Domain — V7 Gate 3(2026-08-15) 처음부터 재설계
(SKU/옵션/채널매핑 + 가용/예약/안전재고 + append-only 원장).
=========================================================
"""

from app.domains.inventory.model import (
    InventoryChannelMapping,
    InventoryLedgerEvent,
    InventoryReservation,
    InventorySku,
)

from app.domains.inventory.repository import (
    InventoryRepository,
)

from app.domains.inventory.service import (
    InventoryService,
)

from app.domains.inventory.schema import (
    InventoryAdjustRequest,
    InventoryChannelMappingCreate,
    InventoryChannelMappingResponse,
    InventoryLedgerEventResponse,
    InventoryMessage,
    InventoryReservationResponse,
    InventoryReserveRequest,
    InventoryRestockRequest,
    InventorySkuCreate,
    InventorySkuResponse,
)


__all__ = [
    "InventorySku",
    "InventoryReservation",
    "InventoryLedgerEvent",
    "InventoryChannelMapping",
    "InventoryRepository",
    "InventoryService",
    "InventorySkuCreate",
    "InventorySkuResponse",
    "InventoryReserveRequest",
    "InventoryReservationResponse",
    "InventoryRestockRequest",
    "InventoryAdjustRequest",
    "InventoryLedgerEventResponse",
    "InventoryChannelMappingCreate",
    "InventoryChannelMappingResponse",
    "InventoryMessage",
]
