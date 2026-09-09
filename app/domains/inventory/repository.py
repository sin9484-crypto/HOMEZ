"""
=========================================================
Homez OS

File : app/domains/inventory/repository.py

Inventory Repository — V7 Gate 3(2026-08-15) 처음부터 재설계.

원자적 재고 증감은 전부 "조건부 UPDATE"(단일 SQL UPDATE 문 안에서
산술 연산 + WHERE 조건)로 구현한다 — Python에서 값을 읽어 계산한 뒤
다시 쓰는 방식(compute-in-app, read-modify-write)은 두 트랜잭션이
동시에 같은 값을 읽으면 조건 없는 blind write로 한쪽 갱신이 사라지는
lost-update가 가능하다. 이 저장소는
app/domains/marketplace_listing/repository.py::
update_listing_platform_status_conditional()과 동일한 철학을 재고
수량에 적용한다 — 다만 여기서는 "이전 값과 정확히 같은지"가 아니라
"연산 후에도 불변식(available_qty ≥ 0)을 만족하는지"를 WHERE 절 자체가
검사한다(더 강한 보장 — 두 요청이 동시에 들어와도 하나만 통과).
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku


class InventoryRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # SKU
    # --------------------------------------------------

    def get_sku(
        self,
        sku_id: int,
    ) -> InventorySku | None:

        return (
            self.db.query(InventorySku)
            .filter(InventorySku.id == sku_id)
            .first()
        )

    def get_sku_for_company(
        self,
        sku_id: int,
        company_id: int,
    ) -> InventorySku | None:

        return (
            self.db.query(InventorySku)
            .filter(InventorySku.id == sku_id)
            .filter(InventorySku.company_id == company_id)
            .first()
        )

    def get_sku_by_code(
        self,
        company_id: int,
        sku_code: str,
    ) -> InventorySku | None:

        return (
            self.db.query(InventorySku)
            .filter(InventorySku.company_id == company_id)
            .filter(InventorySku.sku_code == sku_code)
            .first()
        )

    def list_skus_for_company(
        self,
        company_id: int,
        product_candidate_id: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InventorySku]:

        query = self.db.query(InventorySku).filter(
            InventorySku.company_id == company_id,
        )

        if product_candidate_id is not None:
            query = query.filter(
                InventorySku.product_candidate_id == product_candidate_id,
            )

        return (
            query
            .order_by(InventorySku.id.asc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_sku(
        self,
        sku: InventorySku,
    ) -> InventorySku:

        self.db.add(sku)
        self.db.commit()
        self.db.refresh(sku)

        return sku

    def add_sku_no_commit(
        self,
        sku: InventorySku,
    ) -> InventorySku:

        self.db.add(sku)
        self.db.flush()

        return sku

    def decrement_available_conditional(
        self,
        sku_id: int,
        company_id: int,
        quantity: int,
    ) -> int:
        """
        예약(RESERVE) — available_qty -= quantity, reserved_qty +=
        quantity를 단일 원자적 UPDATE로 수행한다. WHERE available_qty
        >= quantity가 "이 요청을 반영해도 available_qty가 음수가 되지
        않는 경우에만" 통과시키는 원자적 가드다(요구사항 4/5 — 음수
        재고 방지를 애플리케이션 read-then-write가 아닌 DB 레벨
        조건부 UPDATE로 강제).
        """

        stmt = (
            update(InventorySku)
            .where(InventorySku.id == sku_id)
            .where(InventorySku.company_id == company_id)
            .where(InventorySku.available_qty >= quantity)
            .values(
                available_qty=InventorySku.available_qty - quantity,
                reserved_qty=InventorySku.reserved_qty + quantity,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def release_conditional(
        self,
        sku_id: int,
        company_id: int,
        quantity: int,
    ) -> int:
        """예약 해제 — reserved_qty -= quantity, available_qty += quantity."""

        stmt = (
            update(InventorySku)
            .where(InventorySku.id == sku_id)
            .where(InventorySku.company_id == company_id)
            .where(InventorySku.reserved_qty >= quantity)
            .values(
                available_qty=InventorySku.available_qty + quantity,
                reserved_qty=InventorySku.reserved_qty - quantity,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def consume_conditional(
        self,
        sku_id: int,
        company_id: int,
        quantity: int,
    ) -> int:
        """
        예약 확정 소모(CONSUMED) — 이미 RESERVE 시점에 available_qty에서
        차감됐으므로 reserved_qty만 -= quantity(재고가 실제로 창고를
        떠난다 — available/reserved 합계가 순감소).
        """

        stmt = (
            update(InventorySku)
            .where(InventorySku.id == sku_id)
            .where(InventorySku.company_id == company_id)
            .where(InventorySku.reserved_qty >= quantity)
            .values(
                reserved_qty=InventorySku.reserved_qty - quantity,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def apply_available_delta_conditional(
        self,
        sku_id: int,
        company_id: int,
        delta: int,
    ) -> int:
        """
        RESTOCKED(delta>0)/ADJUSTED(delta 양/음 모두 가능) 공용 — 연산
        후에도 available_qty가 음수가 되지 않는 경우에만 통과한다
        (요구사항 5 — 음수 재고 방지, 자동 보정 없이 fail-closed).
        """

        stmt = (
            update(InventorySku)
            .where(InventorySku.id == sku_id)
            .where(InventorySku.company_id == company_id)
            .where(InventorySku.available_qty + delta >= 0)
            .values(
                available_qty=InventorySku.available_qty + delta,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # Reservation
    # --------------------------------------------------

    def get_reservation(
        self,
        reservation_id: int,
    ) -> InventoryReservation | None:

        return (
            self.db.query(InventoryReservation)
            .filter(InventoryReservation.id == reservation_id)
            .first()
        )

    def get_reservation_for_company(
        self,
        reservation_id: int,
        company_id: int,
    ) -> InventoryReservation | None:

        return (
            self.db.query(InventoryReservation)
            .filter(InventoryReservation.id == reservation_id)
            .filter(InventoryReservation.company_id == company_id)
            .first()
        )

    def get_reservation_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> InventoryReservation | None:

        return (
            self.db.query(InventoryReservation)
            .filter(InventoryReservation.company_id == company_id)
            .filter(
                InventoryReservation.idempotency_key == idempotency_key,
            )
            .first()
        )

    def add_reservation_no_commit(
        self,
        reservation: InventoryReservation,
    ) -> InventoryReservation:
        """
        flush만 수행(commit은 호출자가 담당) — UNIQUE(company_id,
        idempotency_key) 위반 시 여기서 IntegrityError가 즉시
        발생하며, 아직 재고 수량은 전혀 변경되지 않은 상태다(Service가
        이 예외를 잡아 기존 예약을 반환하는 idempotent 재생 경로로
        처리한다 — FundingService.apply_settlement_deposit과 동일 기법).
        """

        self.db.add(reservation)
        self.db.flush()

        return reservation

    def transition_reservation_conditional(
        self,
        reservation_id: int,
        company_id: int,
        from_status: str,
        to_status: str,
    ) -> int:
        """
        상태 머신 전이를 원자적 조건부 UPDATE로 강제한다 — WHERE
        status == from_status가 "이미 처리된 예약을 다시 처리"하는
        경쟁(이중 반환/이중 커밋)을 DB 레벨에서 차단한다(요구사항 5).
        """

        stmt = (
            update(InventoryReservation)
            .where(InventoryReservation.id == reservation_id)
            .where(InventoryReservation.company_id == company_id)
            .where(InventoryReservation.status == from_status)
            .values(status=to_status)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def list_reservations_for_sku(
        self,
        sku_id: int,
        company_id: int,
    ) -> list[InventoryReservation]:

        return (
            self.db.query(InventoryReservation)
            .filter(InventoryReservation.inventory_sku_id == sku_id)
            .filter(InventoryReservation.company_id == company_id)
            .order_by(InventoryReservation.id.desc())
            .all()
        )

    # --------------------------------------------------
    # Ledger (append-only)
    # --------------------------------------------------

    def add_ledger_no_commit(
        self,
        ledger: InventoryLedgerEvent,
    ) -> InventoryLedgerEvent:

        self.db.add(ledger)
        self.db.flush()

        return ledger

    def get_ledger_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> InventoryLedgerEvent | None:

        return (
            self.db.query(InventoryLedgerEvent)
            .filter(InventoryLedgerEvent.company_id == company_id)
            .filter(
                InventoryLedgerEvent.idempotency_key == idempotency_key,
            )
            .first()
        )

    def list_ledger_for_sku(
        self,
        sku_id: int,
        company_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InventoryLedgerEvent]:

        return (
            self.db.query(InventoryLedgerEvent)
            .filter(InventoryLedgerEvent.inventory_sku_id == sku_id)
            .filter(InventoryLedgerEvent.company_id == company_id)
            .order_by(InventoryLedgerEvent.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    # --------------------------------------------------
    # Channel Mapping
    # --------------------------------------------------

    def get_mapping(
        self,
        mapping_id: int,
    ) -> InventoryChannelMapping | None:

        return (
            self.db.query(InventoryChannelMapping)
            .filter(InventoryChannelMapping.id == mapping_id)
            .first()
        )

    def get_mapping_for_company(
        self,
        mapping_id: int,
        company_id: int,
    ) -> InventoryChannelMapping | None:

        return (
            self.db.query(InventoryChannelMapping)
            .filter(InventoryChannelMapping.id == mapping_id)
            .filter(InventoryChannelMapping.company_id == company_id)
            .first()
        )

    def get_mapping_by_sku_listing(
        self,
        company_id: int,
        inventory_sku_id: int,
        marketplace_listing_id: int,
    ) -> InventoryChannelMapping | None:

        return (
            self.db.query(InventoryChannelMapping)
            .filter(InventoryChannelMapping.company_id == company_id)
            .filter(
                InventoryChannelMapping.inventory_sku_id
                == inventory_sku_id,
            )
            .filter(
                InventoryChannelMapping.marketplace_listing_id
                == marketplace_listing_id,
            )
            .first()
        )

    def list_mappings_for_sku(
        self,
        sku_id: int,
        company_id: int,
    ) -> list[InventoryChannelMapping]:

        return (
            self.db.query(InventoryChannelMapping)
            .filter(InventoryChannelMapping.inventory_sku_id == sku_id)
            .filter(InventoryChannelMapping.company_id == company_id)
            .order_by(InventoryChannelMapping.id.asc())
            .all()
        )

    def add_mapping(
        self,
        mapping: InventoryChannelMapping,
    ) -> InventoryChannelMapping:

        self.db.add(mapping)
        self.db.commit()
        self.db.refresh(mapping)

        return mapping

    def save_mapping(
        self,
        mapping: InventoryChannelMapping,
    ) -> InventoryChannelMapping:

        self.db.add(mapping)
        self.db.commit()
        self.db.refresh(mapping)

        return mapping

    def save_mapping_no_commit(
        self,
        mapping: InventoryChannelMapping,
    ) -> InventoryChannelMapping:

        self.db.add(mapping)
        self.db.flush()

        return mapping


__all__ = [
    "InventoryRepository",
]
