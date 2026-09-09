"""
=========================================================
Homez OS

File : app/domains/inventory/constants.py

Inventory 도메인 상수 — V7 Gate 3(2026-08-15) 재설계.

`docs/HOMEZ_V7_INVENTORY_PLAN.md` 2절이 정의한 이벤트 계약을 그대로
따른다. 값을 코드 여러 곳에 하드코딩하지 않고 이 파일 하나로 모은다
(app/domains/funding/service.py의 TYPE_*/STATUS_* 클래스 상수 관례와
동일한 정신 — 다만 여러 파일에서 재사용되므로 별도 constants.py로
분리했다, marketplace_listing/constants.py 관례).
=========================================================
"""

from __future__ import annotations


class InventoryLedgerEventType:
    """append-only 원장 이벤트 타입(계획 문서 2절)."""

    RESERVED = "RESERVED"
    RELEASED = "RELEASED"
    CONSUMED = "CONSUMED"
    RESTOCKED = "RESTOCKED"
    ADJUSTED = "ADJUSTED"
    CHANNEL_SYNC = "CHANNEL_SYNC"

    ALL = (
        RESERVED,
        RELEASED,
        CONSUMED,
        RESTOCKED,
        ADJUSTED,
        CHANNEL_SYNC,
    )

    # 사유(reason)가 서비스 레벨에서 필수인 이벤트 — 수동 조정은 금융
    # 조정과 동일한 민감도로 취급한다(CLAUDE.md/계획 문서 8절).
    REASON_REQUIRED = (ADJUSTED,)


class InventoryReservationReferenceType:
    """
    2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_LEDGER_
    AUDIT_20260907.md §8) — `InventoryReservation.reference_type`에
    쓰는 값 중, 실제 HOMEZ 공용 창고 재고(available_qty)를 전혀
    건드리지 않는 "개별 조달용 가짜 예약"을 명시적으로 구분하는
    값이다. `purchase_task`(사람이 직접 다른 쇼핑몰에서 그 주문
    한 건만을 위해 개별 구매하는 방식)로 산 상품은 HOMEZ의 공용
    재고 풀에 들어온 적이 없다 — 그런데 `app/domains/shipment/
    service.py`가 배송 생성·확정 조건으로 `OrderItem.status ==
    RESERVED` + 실제 `InventoryReservation`을 요구하므로, 이
    reference_type으로 표시된 "예약"을 만들어 그 계약만 충족시키고,
    실제 재고 수량 계산(available_qty/reserved_qty)에는 절대
    반영하지 않는다(`InventoryService.reserve_externally_procured()`
    /`consume()`/`release()`가 이 값을 보고 SKU 수량 갱신을
    건너뛴다 — 절대 진짜 `reserve()`와 혼동하면 안 된다).
    """

    PURCHASE_TASK_EXTERNAL_PROCUREMENT = "purchase_task_external_procurement"


class InventoryReservationStatus:
    """
    예약 상태 머신(계획 문서 4/5절) — FundingHold(HELD/COMMITTED/
    RELEASED)와 동일한 철학. RESERVED에서만 RELEASED 또는 CONSUMED로
    전이할 수 있고, 그 반대나 재전이는 전부 차단한다(이중 커밋/이중
    반환 방지).
    """

    RESERVED = "RESERVED"
    RELEASED = "RELEASED"
    CONSUMED = "CONSUMED"


class InventoryChannelSyncStatus:
    """InventoryChannelMapping.last_sync_status."""

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


__all__ = [
    "InventoryLedgerEventType",
    "InventoryReservationReferenceType",
    "InventoryReservationStatus",
    "InventoryChannelSyncStatus",
]
