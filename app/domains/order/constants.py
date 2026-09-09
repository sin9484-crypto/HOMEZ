"""
=========================================================
Homez OS

File : app/domains/order/constants.py

Order 도메인 상수 — V7 Gate 4(2026-08-15) 처음부터 재설계.

app/domains/inventory/constants.py(Gate 3)와 동일한 정신 — 상태값/
이벤트 타입을 코드 여러 곳에 하드코딩하지 않고 이 파일 하나로 모은다.
=========================================================
"""

from __future__ import annotations


class OrderStatus:
    """
    주문 정규화 상태(요구사항 1/5). 채널별 원본 상태값은 절대 이
    값으로 직접 대입하지 않는다 — 항상 `normalize_channel_status()`를
    거친다.
    """

    PENDING = "PENDING"
    RESERVED = "RESERVED"
    PARTIALLY_SHIPPED = "PARTIALLY_SHIPPED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    RETURNED = "RETURNED"
    EXCHANGE_REQUESTED = "EXCHANGE_REQUESTED"
    EXCHANGED = "EXCHANGED"

    # 취소가 더 이상 불가능한 상태 — 이 이후에는 반품/교환 경로만
    # 사용할 수 있다(요구사항 5).
    NOT_CANCELLABLE = (
        SHIPPED,
        PARTIALLY_SHIPPED,
        DELIVERED,
        CANCELLED,
        RETURNED,
        EXCHANGED,
    )


class OrderItemStatus:
    """
    주문 품목 상태 — Inventory 예약과 1:1로 대응한다(요구사항 3).
    """

    PENDING = "PENDING"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    RESERVED = "RESERVED"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"
    RETURNED = "RETURNED"
    EXCHANGED = "EXCHANGED"


class OrderIngestionStatus:
    """append-only OrderIngestionEvent.status(요구사항 2/6/7)."""

    COLLECTED = "COLLECTED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    FAILED = "FAILED"


class OrderChannelSyncStatus:
    """Order.channel_sync_status — Inventory의 InventoryChannelMapping과 동일 철학."""

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class OrderStatusEventSource:
    """append-only OrderStatusEvent.source."""

    INGESTION = "INGESTION"
    RESERVATION = "RESERVATION"
    CANCELLATION = "CANCELLATION"
    PURCHASE = "PURCHASE"
    SHIPMENT = "SHIPMENT"
    RETURN = "RETURN"
    EXCHANGE = "EXCHANGE"
    CHANNEL_SYNC = "CHANNEL_SYNC"


# --------------------------------------------------
# 채널별 원본 상태 → 내부 정규화 상태(요구사항 1) — 실제 쿠팡/네이버
# API가 붙기 전까지는 Fake Provider가 이 매핑을 그대로 반환한다.
# 알 수 없는 채널 코드는 FAKE 매핑으로 폴백한다(과설계 방지 — 실제
# Provider가 준비되면 이 dict에 채널을 추가하기만 하면 된다).
# --------------------------------------------------

CHANNEL_STATUS_NORMALIZATION_MAP: dict[str, dict[str, str]] = {
    "COUPANG": {
        "ACCEPT": OrderStatus.RESERVED,
        "INSTRUCT": OrderStatus.RESERVED,
        "DEPARTURE": OrderStatus.SHIPPED,
        "DELIVERING": OrderStatus.SHIPPED,
        "FINAL_DELIVERY": OrderStatus.DELIVERED,
        "CANCEL": OrderStatus.CANCELLED,
    },
    "NAVER": {
        "PAYED": OrderStatus.RESERVED,
        "DELIVERING": OrderStatus.SHIPPED,
        "DELIVERED": OrderStatus.DELIVERED,
        "CANCELED": OrderStatus.CANCELLED,
    },
    "FAKE": {
        "PAID": OrderStatus.RESERVED,
        "SHIPPING": OrderStatus.SHIPPED,
        "DELIVERED": OrderStatus.DELIVERED,
        "CANCELLED": OrderStatus.CANCELLED,
    },
}


def normalize_channel_status(
    channel_code: str,
    raw_status: str,
) -> str | None:
    """
    채널 원본 상태 문자열을 내부 공통 상태로 정규화한다. 매핑에 없는
    원본 상태는 None을 반환한다(추측하지 않는다 — 호출자가 "정규화
    실패"로 명시적으로 처리하게 한다, marketplace_listing.status_
    sync_service의 UNKNOWN 철학과 동일).
    """

    mapping = CHANNEL_STATUS_NORMALIZATION_MAP.get(
        channel_code, CHANNEL_STATUS_NORMALIZATION_MAP["FAKE"],
    )

    return mapping.get(raw_status)


__all__ = [
    "OrderStatus",
    "OrderItemStatus",
    "OrderIngestionStatus",
    "OrderChannelSyncStatus",
    "OrderStatusEventSource",
    "CHANNEL_STATUS_NORMALIZATION_MAP",
    "normalize_channel_status",
]
