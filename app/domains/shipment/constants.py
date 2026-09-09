"""
=========================================================
Homez OS

File : app/domains/shipment/constants.py

Shipment 도메인 상수 — V7 Gate 4(2026-08-15) 처음부터 재설계.

CTO 지시 요구사항 5의 상태 목록(대기/출고준비/출고완료/배송중/
배송완료/취소/반품접수/반품완료/교환접수/교환완료)을 그대로 반영한다.
=========================================================
"""

from __future__ import annotations


class ShipmentStatus:

    PENDING = "PENDING"                        # 대기
    READY = "READY"                             # 출고준비
    SHIPPED = "SHIPPED"                         # 출고완료
    IN_TRANSIT = "IN_TRANSIT"                   # 배송중
    DELIVERED = "DELIVERED"                     # 배송완료
    CANCELLED = "CANCELLED"                     # 취소
    RETURN_REQUESTED = "RETURN_REQUESTED"       # 반품접수
    RETURNED = "RETURNED"                       # 반품완료
    EXCHANGE_REQUESTED = "EXCHANGE_REQUESTED"   # 교환접수
    EXCHANGED = "EXCHANGED"                     # 교환완료

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        PENDING: {READY, CANCELLED},
        READY: {SHIPPED, CANCELLED},
        SHIPPED: {IN_TRANSIT, DELIVERED, RETURN_REQUESTED},
        IN_TRANSIT: {DELIVERED, RETURN_REQUESTED},
        DELIVERED: {RETURN_REQUESTED, EXCHANGE_REQUESTED},
        CANCELLED: set(),
        RETURN_REQUESTED: {RETURNED},
        RETURNED: set(),
        EXCHANGE_REQUESTED: {EXCHANGED},
        EXCHANGED: set(),
    }


__all__ = [
    "ShipmentStatus",
]
