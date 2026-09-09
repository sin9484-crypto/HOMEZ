"""
=========================================================
Homez OS

File : app/domains/inventory/service.py

Inventory Service — V7 Gate 3(2026-08-15) 처음부터 재설계.

동시성/원자성 설계 원칙(요구사항 4/5/6):
  - 모든 재고 증감은 "atomic-write-first, audit-insert-second, single
    commit" 순서로 수행한다. SQLite는 파일 레벨 쓰기 잠금(단일
    writer)이라 두 트랜잭션의 쓰기 구간은 항상 직렬화되고, 어느
    단계에서든 실패하면 db.rollback()이 그 트랜잭션의 모든 쓰기(재고
    UPDATE 포함)를 함께 되돌린다 — 따라서 "먼저 재고를 바꾸고 나중에
    idempotency 충돌이 나면 rollback으로 통째로 취소"하는 순서가
    "먼저 idempotency를 확인/삽입하고 나중에 재고를 바꾸는" 순서보다
    더 단순하면서도 동일하게 안전하다(둘 다 단일 트랜잭션·단일
    commit이므로). 재고 수량 자체의 무결성은 조건부 UPDATE의 WHERE
    절(예: `available_qty >= quantity`)이 언제나 최종 방어선이다 —
    Python이 미리 읽은 값으로 계산해 blind write하지 않는다.
  - idempotency_key 중복은 UNIQUE(company_id, idempotency_key)
    제약(INSERT 시 IntegrityError)으로 검출한다. 빠른 경로(사전 조회)는
    최적화일 뿐 race-free 보장이 아니다 — 최종 판정은 항상 제약
    위반 예외다(marketplace_listing/status_sync_service.py와 동일 철학).
  - 예약(RESERVE)의 상태 머신(RESERVED→RELEASED|CONSUMED)은 조건부
    UPDATE(WHERE status = 이전상태)로 강제한다 — 이미 처리된 예약을
    다시 처리하는 경쟁은 rowcount != 1로 즉시 검출된다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.inventory.adapters.fake_provider import (
    get_channel_inventory_sync_adapter,
)
from app.domains.inventory.constants import InventoryChannelSyncStatus
from app.domains.inventory.constants import InventoryLedgerEventType
from app.domains.inventory.constants import (
    InventoryReservationReferenceType,
)
from app.domains.inventory.constants import InventoryReservationStatus
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.schema import InventoryAdjustRequest
from app.domains.inventory.schema import InventoryChannelMappingCreate
from app.domains.inventory.schema import InventoryReserveRequest
from app.domains.inventory.schema import InventoryRestockRequest
from app.domains.inventory.schema import InventorySkuCreate


class InventoryService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = InventoryRepository(db)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _get_sku_required(
        self,
        sku_id: int,
        company_id: int,
    ) -> InventorySku:

        sku = self.repository.get_sku_for_company(sku_id, company_id)

        if sku is None:
            raise NotFoundException(
                "Inventory SKU를 찾을 수 없습니다.",
            )

        return sku

    def _get_reservation_required(
        self,
        reservation_id: int,
        company_id: int,
    ) -> InventoryReservation:

        reservation = self.repository.get_reservation_for_company(
            reservation_id, company_id,
        )

        if reservation is None:
            raise NotFoundException(
                "Inventory Reservation을 찾을 수 없습니다.",
            )

        return reservation

    @staticmethod
    def _is_externally_procured(reservation: InventoryReservation) -> bool:
        """
        2026-09-07 — `reserve_externally_procured()`가 만든 "가짜"
        예약인지 판별한다. 이 값이 True인 예약은 `release()`/
        `consume()`에서 SKU의 `available_qty`/`reserved_qty`를 절대
        건드리면 안 된다(그 값들은 애초에 이 예약 때문에 바뀐 적이
        없다 — reserve_externally_procured()의 docstring 참고).
        """

        return reservation.reference_type == (
            InventoryReservationReferenceType
            .PURCHASE_TASK_EXTERNAL_PROCUREMENT
        )

    @staticmethod
    def _require_idempotency_key(
        idempotency_key: str | None,
    ) -> str:

        if idempotency_key is None or not idempotency_key.strip():
            raise BadRequestException(
                "idempotency_key는 필수입니다.",
            )

        return idempotency_key.strip()

    # --------------------------------------------------
    # SKU / 옵션 관리
    # --------------------------------------------------

    def create_sku(
        self,
        data: InventorySkuCreate,
        company_id: int,
    ) -> InventorySku:
        """
        2026-08-15 V7 Gate 3 — 이 회사가 이미 승인(APPROVED)한
        ProductCandidate에 대해서만 SKU(옵션)를 등록할 수 있다.
        중복 구현을 피하기 위해 marketplace_listing/coupang/
        listing_package가 이미 쓰는 공유 게이트
        `ProductCandidateService.require_approved_for_company()`를
        그대로 재사용한다(순환 import 방지를 위해 함수 내부 import —
        app/domains/funding/service.py::confirm_supplier_payment의
        기존 컨벤션과 동일).
        """

        from app.domains.product_candidate.service import (
            ProductCandidateService,
        )

        ProductCandidateService(self.db).require_approved_for_company(
            data.product_candidate_id, company_id,
        )

        existing = self.repository.get_sku_by_code(
            company_id, data.sku_code,
        )
        if existing is not None:
            raise BadRequestException(
                "이미 사용 중인 sku_code입니다.",
            )

        initial_qty = int(data.initial_qty or 0)

        sku = InventorySku(
            company_id=company_id,
            product_candidate_id=data.product_candidate_id,
            sku_code=data.sku_code,
            option_label=data.option_label or "기본",
            available_qty=initial_qty,
            reserved_qty=0,
            safety_stock=int(data.safety_stock or 0),
            is_active=True,
        )

        try:
            sku = self.repository.add_sku_no_commit(sku)

            if initial_qty > 0:
                self._append_ledger_no_commit(
                    sku,
                    event_type=InventoryLedgerEventType.RESTOCKED,
                    quantity_delta=initial_qty,
                    triggered_by=None,
                    idempotency_key=f"sku:{sku.id}:initial_stock",
                )

            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise BadRequestException(
                "이미 동일한 옵션(product_candidate_id + option_label) "
                "또는 sku_code가 존재합니다.",
            ) from exc

        self.db.refresh(sku)

        return sku

    def get_sku(
        self,
        sku_id: int,
        company_id: int,
    ) -> InventorySku:

        return self._get_sku_required(sku_id, company_id)

    def list_skus(
        self,
        company_id: int,
        product_candidate_id: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InventorySku]:

        return self.repository.list_skus_for_company(
            company_id, product_candidate_id, skip, limit,
        )

    def _append_ledger_no_commit(
        self,
        sku: InventorySku,
        *,
        event_type: str,
        quantity_delta: int,
        triggered_by: int | None,
        reservation_id: int | None = None,
        idempotency_key: str | None = None,
        reason: str | None = None,
        channel_code: str | None = None,
    ) -> InventoryLedgerEvent:
        """
        호출 시점에 sku가 이미 최신(재조회된) 상태라고 가정하고 그
        available_qty/reserved_qty를 그대로 스냅샷한다 — 이 메서드
        자체는 재고 값을 바꾸지 않는다(순수 append-only 기록만).
        """

        ledger = InventoryLedgerEvent(
            company_id=sku.company_id,
            inventory_sku_id=sku.id,
            event_type=event_type,
            quantity_delta=quantity_delta,
            available_after=sku.available_qty,
            reserved_after=sku.reserved_qty,
            safety_stock_threshold=sku.safety_stock,
            channel_code=channel_code,
            reservation_id=reservation_id,
            idempotency_key=idempotency_key,
            reason=reason,
            triggered_by=triggered_by,
        )

        return self.repository.add_ledger_no_commit(ledger)

    # --------------------------------------------------
    # 예약(RESERVE) / 해제(RELEASE) / 확정소모(CONSUME)
    # --------------------------------------------------

    def reserve(
        self,
        sku_id: int,
        company_id: int,
        data: InventoryReserveRequest,
        triggered_by: int | None = None,
    ) -> InventoryReservation:
        """
        가용재고 확인 + 예약 생성을 단일 트랜잭션 안에서 수행한다
        (계획 문서 5절). 부족하면 자동 보정 없이 fail-closed로
        차단한다(CLAUDE.md "금융 오류 자동 보정 금지"와 동일 철학을
        재고에도 적용).

        Audit(2026-08-21, AG-0) — 이 메서드는 사람이 직접 수행하는
        핵심 CRUD 트랜잭션이다(주문 처리 등 일반 업무의 일부) — AI
        Capability Registry로 게이트하지 않는다(이전 라운드에서 잘못
        연결했던 것을 되돌림). PRICING_INVENTORY는 "가격 제안·재고
        부족 예측" 같은 AI 추천 기능에만 적용해야 한다.
        """

        quantity = int(data.quantity)

        if quantity <= 0:
            raise BadRequestException(
                "예약 수량은 1 이상이어야 합니다.",
            )

        idempotency_key = self._require_idempotency_key(
            data.idempotency_key,
        )

        existing = self.repository.get_reservation_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing

        self._get_sku_required(sku_id, company_id)

        rowcount = self.repository.decrement_available_conditional(
            sku_id, company_id, quantity,
        )

        if rowcount != 1:
            self.db.rollback()
            current = self.repository.get_sku_for_company(
                sku_id, company_id,
            )
            if current is None:
                raise NotFoundException(
                    "Inventory SKU를 찾을 수 없습니다.",
                )
            raise BadRequestException(
                "가용재고(available_qty)가 부족하여 예약할 수 "
                f"없습니다. 요청={quantity}, 가용={current.available_qty}",
            )

        updated_sku = self.repository.get_sku_for_company(
            sku_id, company_id,
        )

        reservation = InventoryReservation(
            company_id=company_id,
            inventory_sku_id=sku_id,
            quantity=quantity,
            status=InventoryReservationStatus.RESERVED,
            reference_type=data.reference_type,
            reference_id=data.reference_id,
            idempotency_key=idempotency_key,
            triggered_by=triggered_by,
        )

        try:
            reservation = self.repository.add_reservation_no_commit(
                reservation,
            )
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_reservation_by_idempotency(
                company_id, idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "예약 생성 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        self._append_ledger_no_commit(
            updated_sku,
            event_type=InventoryLedgerEventType.RESERVED,
            quantity_delta=-quantity,
            triggered_by=triggered_by,
            reservation_id=reservation.id,
        )

        self.db.commit()
        self.db.refresh(reservation)

        return reservation

    def reserve_externally_procured(
        self,
        sku_id: int,
        company_id: int,
        data: InventoryReserveRequest,
        triggered_by: int | None = None,
    ) -> InventoryReservation:
        """
        2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_
        LEDGER_AUDIT_20260907.md §8, 사용자 확정) — "개별 조달용
        가짜 예약". `reserve()`와 절대 혼동하지 말 것: 이 메서드는
        `available_qty`/`reserved_qty`를 **전혀 건드리지 않는다.**

        `purchase_task`(사람이 직접 다른 쇼핑몰에서 이 주문 한 건만을
        위해 개별 구매하는 방식)로 조달한 상품은 HOMEZ 공용 창고
        재고 풀에 들어온 적이 없다 — 그런데 `app/domains/shipment/
        service.py`는 배송 생성·확정 조건으로 `OrderItem.status ==
        RESERVED` + 실제 `InventoryReservation` 존재를 요구한다. 이
        메서드는 그 계약만 형식적으로 충족시키는 `InventoryReservation`
        행을 만들되, `reference_type`을
        `InventoryReservationReferenceType.
        PURCHASE_TASK_EXTERNAL_PROCUREMENT`로 고정해 실제 창고
        예약과 항상 구분 가능하게 한다 — `consume()`/`release()`가
        이 값을 보고 SKU 수량 갱신을 건너뛴다.

        `reference_id`는 호출자가 `data.reference_id`로 넘긴다(보통
        `PurchaseTask.id`) — `reference_type`은 호출자가 무엇을
        넘기든 이 메서드가 항상 위 고정값으로 덮어써 오용을 막는다.
        """

        quantity = int(data.quantity)

        if quantity <= 0:
            raise BadRequestException(
                "예약 수량은 1 이상이어야 합니다.",
            )

        idempotency_key = self._require_idempotency_key(
            data.idempotency_key,
        )

        existing = self.repository.get_reservation_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing

        sku = self._get_sku_required(sku_id, company_id)

        reservation = InventoryReservation(
            company_id=company_id,
            inventory_sku_id=sku_id,
            quantity=quantity,
            status=InventoryReservationStatus.RESERVED,
            reference_type=(
                InventoryReservationReferenceType
                .PURCHASE_TASK_EXTERNAL_PROCUREMENT
            ),
            reference_id=data.reference_id,
            idempotency_key=idempotency_key,
            triggered_by=triggered_by,
        )

        try:
            reservation = self.repository.add_reservation_no_commit(
                reservation,
            )
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_reservation_by_idempotency(
                company_id, idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "예약 생성 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        # 공용 재고(available_qty/reserved_qty)는 절대 건드리지 않는다
        # — quantity_delta=0으로 그 사실을 원장에도 명시적으로 남긴다.
        self._append_ledger_no_commit(
            sku,
            event_type=InventoryLedgerEventType.RESERVED,
            quantity_delta=0,
            triggered_by=triggered_by,
            reservation_id=reservation.id,
            reason=(
                "purchase_task 개별 조달 — 공용 재고(available_qty/"
                "reserved_qty) 미영향(가짜 예약)"
            ),
        )

        self.db.commit()
        self.db.refresh(reservation)

        return reservation

    def release(
        self,
        reservation_id: int,
        company_id: int,
        triggered_by: int | None = None,
    ) -> InventoryReservation:
        """
        RESERVED → RELEASED. 이미 RELEASED/CONSUMED인 예약을 다시
        해제하면 차단한다(상태 머신, 요구사항 5).

        Audit(2026-08-21, AG-0) — 사람이 직접 수행하는 CRUD, AI
        Capability Registry로 게이트하지 않는다.
        """

        reservation = self._get_reservation_required(
            reservation_id, company_id,
        )

        rowcount = self.repository.transition_reservation_conditional(
            reservation_id, company_id,
            InventoryReservationStatus.RESERVED,
            InventoryReservationStatus.RELEASED,
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "RESERVED 상태가 아닌 예약은 해제할 수 없습니다 — "
                f"현재 상태: {reservation.status}",
            )

        updated_sku = self.repository.get_sku_for_company(
            reservation.inventory_sku_id, company_id,
        )

        if self._is_externally_procured(reservation):
            # 2026-09-07 — reserve_externally_procured()가 애초에
            # available_qty/reserved_qty를 건드리지 않았으므로,
            # 여기서도 release_conditional()을 호출하면 안 된다
            # (never-incremented reserved_qty를 잘못 차감하게 됨).
            self._append_ledger_no_commit(
                updated_sku,
                event_type=InventoryLedgerEventType.RELEASED,
                quantity_delta=0,
                triggered_by=triggered_by,
                reservation_id=reservation.id,
                reason=(
                    "purchase_task 개별 조달 예약 해제 — 공용 재고 "
                    "미영향(가짜 예약)"
                ),
            )
            self.db.commit()
            self.db.refresh(reservation)
            return reservation

        sku_rowcount = self.repository.release_conditional(
            reservation.inventory_sku_id, company_id, reservation.quantity,
        )

        if sku_rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "재고 상태 불일치로 예약을 해제하지 못했습니다.",
            )

        updated_sku = self.repository.get_sku_for_company(
            reservation.inventory_sku_id, company_id,
        )

        self._append_ledger_no_commit(
            updated_sku,
            event_type=InventoryLedgerEventType.RELEASED,
            quantity_delta=reservation.quantity,
            triggered_by=triggered_by,
            reservation_id=reservation.id,
        )

        self.db.commit()
        self.db.refresh(reservation)

        return reservation

    def consume(
        self,
        reservation_id: int,
        company_id: int,
        triggered_by: int | None = None,
    ) -> InventoryReservation:
        """
        RESERVED → CONSUMED(주문 확정 등으로 실제 소모). available_qty는
        RESERVE 시점에 이미 차감돼 있으므로 여기서는 reserved_qty만
        추가로 감소한다(재고가 실제로 창고를 떠난다).

        Audit(2026-08-21, AG-0) — 사람이 직접 수행하는 CRUD, AI
        Capability Registry로 게이트하지 않는다.
        """

        reservation = self._get_reservation_required(
            reservation_id, company_id,
        )

        rowcount = self.repository.transition_reservation_conditional(
            reservation_id, company_id,
            InventoryReservationStatus.RESERVED,
            InventoryReservationStatus.CONSUMED,
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "RESERVED 상태가 아닌 예약은 확정소모(CONSUME)할 수 "
                f"없습니다 — 현재 상태: {reservation.status}",
            )

        updated_sku = self.repository.get_sku_for_company(
            reservation.inventory_sku_id, company_id,
        )

        if self._is_externally_procured(reservation):
            # 2026-09-07 — reserve_externally_procured()가 애초에
            # reserved_qty를 늘리지 않았으므로, consume_conditional()의
            # WHERE reserved_qty >= quantity 가드가 이 예약과 무관한
            # 다른 진짜 예약분의 reserved_qty를 잘못 갉아먹을 위험이
            # 있다 — 절대 호출하지 않는다.
            self._append_ledger_no_commit(
                updated_sku,
                event_type=InventoryLedgerEventType.CONSUMED,
                quantity_delta=0,
                triggered_by=triggered_by,
                reservation_id=reservation.id,
                reason=(
                    "purchase_task 개별 조달 예약 확정소모 — 공용 "
                    "재고 미영향(가짜 예약, 배송은 실제로 발생)"
                ),
            )
            self.db.commit()
            self.db.refresh(reservation)
            return reservation

        sku_rowcount = self.repository.consume_conditional(
            reservation.inventory_sku_id, company_id, reservation.quantity,
        )

        if sku_rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "재고 상태 불일치로 예약을 확정소모하지 못했습니다.",
            )

        updated_sku = self.repository.get_sku_for_company(
            reservation.inventory_sku_id, company_id,
        )

        self._append_ledger_no_commit(
            updated_sku,
            event_type=InventoryLedgerEventType.CONSUMED,
            quantity_delta=-reservation.quantity,
            triggered_by=triggered_by,
            reservation_id=reservation.id,
        )

        self.db.commit()
        self.db.refresh(reservation)

        return reservation

    # --------------------------------------------------
    # 입고(RESTOCK) / 수동 조정(ADJUST)
    # --------------------------------------------------

    def restock(
        self,
        sku_id: int,
        company_id: int,
        data: InventoryRestockRequest,
        triggered_by: int | None = None,
    ) -> InventoryLedgerEvent:
        # Audit(2026-08-21, AG-0) — 사람이 직접 수행하는 CRUD, AI
        # Capability Registry로 게이트하지 않는다.

        quantity = int(data.quantity)

        if quantity <= 0:
            raise BadRequestException(
                "입고 수량은 1 이상이어야 합니다.",
            )

        idempotency_key = self._require_idempotency_key(
            data.idempotency_key,
        )

        existing = self.repository.get_ledger_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing

        self._get_sku_required(sku_id, company_id)

        rowcount = self.repository.apply_available_delta_conditional(
            sku_id, company_id, quantity,
        )

        if rowcount != 1:
            self.db.rollback()
            raise NotFoundException(
                "Inventory SKU를 찾을 수 없습니다.",
            )

        updated_sku = self.repository.get_sku_for_company(
            sku_id, company_id,
        )

        # 2026-08-15 실측 동시성 테스트로 발견한 결함 수정: idempotency_
        # key UNIQUE 위반은 이 뒤 `_append_ledger_no_commit()`가 내부적
        # 으로 호출하는 flush() 시점에 발생할 수 있다(commit() 시점이
        # 아니다) — try 블록이 commit()만 감싸면 그 IntegrityError가
        # 잡히지 않고 그대로 전파돼, 동시에 같은 idempotency_key로
        # 들어온 두 번째 요청이 "재생(replay)"이 아니라 예외로 실패한다
        # (barrier 기반 2-스레드 경쟁 테스트로 재현·확인).
        try:
            ledger = self._append_ledger_no_commit(
                updated_sku,
                event_type=InventoryLedgerEventType.RESTOCKED,
                quantity_delta=quantity,
                triggered_by=triggered_by,
                idempotency_key=idempotency_key,
                reason=data.reason,
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_ledger_by_idempotency(
                company_id, idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "입고 처리 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        self.db.refresh(ledger)

        return ledger

    def adjust_stock(
        self,
        sku_id: int,
        company_id: int,
        data: InventoryAdjustRequest,
        triggered_by: int | None,
    ) -> InventoryLedgerEvent:
        """
        수동 재고 조정(요구사항 7) — 권한(Router의 admin_guard) +
        사유(reason) 필수 + audit_logs 기록이 함께 있어야 한다. 금융
        조정과 동일한 민감도로 취급한다(계획 문서 8절).
        """

        delta = int(data.quantity_delta)

        if delta == 0:
            raise BadRequestException(
                "조정 수량(quantity_delta)은 0이 될 수 없습니다.",
            )

        if data.reason is None or not data.reason.strip():
            raise BadRequestException(
                "수동 재고 조정은 사유(reason)가 필수입니다.",
            )

        idempotency_key = self._require_idempotency_key(
            data.idempotency_key,
        )

        existing = self.repository.get_ledger_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing

        sku = self._get_sku_required(sku_id, company_id)

        rowcount = self.repository.apply_available_delta_conditional(
            sku_id, company_id, delta,
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "조정 후 가용재고(available_qty)가 음수가 될 수 "
                f"없습니다. 현재={sku.available_qty}, 조정={delta:+d}",
            )

        updated_sku = self.repository.get_sku_for_company(
            sku_id, company_id,
        )

        # 2026-08-15 실측 동시성 테스트로 발견한 결함 수정: restock()과
        # 동일한 이유로 idempotency_key UNIQUE 위반이 flush() 시점에
        # 발생할 수 있어, 원장 삽입부터 commit()까지 하나의 try 블록으로
        # 감싼다(write_audit_log()도 같은 트랜잭션 안에서 함께 rollback
        # 되어야 "감사 로그만 남고 재고는 안 바뀐" 불일치를 막는다).
        try:
            ledger = self._append_ledger_no_commit(
                updated_sku,
                event_type=InventoryLedgerEventType.ADJUSTED,
                quantity_delta=delta,
                triggered_by=triggered_by,
                idempotency_key=idempotency_key,
                reason=data.reason.strip(),
            )

            write_audit_log(
                self.db,
                user_id=triggered_by,
                action="INVENTORY_ADJUSTED",
                entity="inventory_sku",
                entity_id=str(sku_id),
                description=(
                    f"재고 수동 조정 {delta:+d} (sku_code={sku.sku_code}, "
                    f"사유: {data.reason.strip()})"
                ),
                company_id=company_id,
            )

            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_ledger_by_idempotency(
                company_id, idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "재고 조정 처리 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        self.db.refresh(ledger)

        return ledger

    # --------------------------------------------------
    # 원장 조회
    # --------------------------------------------------

    def list_ledger(
        self,
        sku_id: int,
        company_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InventoryLedgerEvent]:

        self._get_sku_required(sku_id, company_id)

        return self.repository.list_ledger_for_sku(
            sku_id, company_id, skip, limit,
        )

    def list_reservations(
        self,
        sku_id: int,
        company_id: int,
    ) -> list[InventoryReservation]:

        self._get_sku_required(sku_id, company_id)

        return self.repository.list_reservations_for_sku(
            sku_id, company_id,
        )

    # --------------------------------------------------
    # 채널 매핑 / 재고 동기화(Fake Provider 전용)
    # --------------------------------------------------

    def create_channel_mapping(
        self,
        sku_id: int,
        company_id: int,
        data: InventoryChannelMappingCreate,
    ) -> InventoryChannelMapping:
        """
        2026-08-15 V7 Gate 3 — 새 채널/계정 개념을 만들지 않고 기존
        marketplace_listing.MarketplaceListing(이미 채널별 등록/승인
        흐름·company_id 스코프 완비)에 논리 참조만 추가한다(중복 구현
        금지 지시 준수). listing이 이 회사 소유가 아니면 404(존재
        노출 금지 — 이 저장소 전역 관례).
        """

        from app.domains.marketplace_listing.repository import (
            MarketplaceListingRepository,
        )

        sku = self._get_sku_required(sku_id, company_id)

        listing = MarketplaceListingRepository(self.db).get_listing_for_company(
            data.marketplace_listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(
                "MarketplaceListing을 찾을 수 없습니다.",
            )

        existing = self.repository.get_mapping_by_sku_listing(
            company_id, sku_id, data.marketplace_listing_id,
        )
        if existing is not None:
            raise BadRequestException(
                "이미 이 SKU와 채널 Listing이 매핑되어 있습니다.",
            )

        mapping = InventoryChannelMapping(
            company_id=company_id,
            inventory_sku_id=sku.id,
            marketplace_listing_id=listing.id,
            channel_code=data.channel_code,
            channel_sku=data.channel_sku,
            is_active=True,
            last_sync_status=InventoryChannelSyncStatus.PENDING,
        )

        try:
            return self.repository.add_mapping(mapping)
        except IntegrityError as exc:
            self.db.rollback()
            raise BadRequestException(
                "이미 동일한 channel_code + channel_sku가 이 회사에 "
                "등록되어 있습니다.",
            ) from exc

    def list_channel_mappings(
        self,
        sku_id: int,
        company_id: int,
    ) -> list[InventoryChannelMapping]:

        self._get_sku_required(sku_id, company_id)

        return self.repository.list_mappings_for_sku(sku_id, company_id)

    def sync_channel_stock(
        self,
        mapping_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> InventoryChannelMapping:
        """
        "재고가 바뀌면 채널에 동기화 요청을 보낸다" 흐름(계획 문서
        9절) — Fake Provider만 사용, 실제 네트워크 호출 없음. 자동
        재고 조정/동기화는 EStop 게이트를 거치도록 설계한다(계획 문서
        7절 — marketplace_listing.status_sync_service.
        _raise_if_emergency_stop_active()와 동일 재사용).
        """

        from app.domains.automation_safety.service import SafetyService

        if SafetyService(self.db).is_emergency_stop_active():
            raise BadRequestException(
                "Emergency Stop이 활성화되어 있어 채널 재고 동기화를 "
                "실행할 수 없습니다.",
            )

        now = now or datetime.utcnow()

        mapping = self.repository.get_mapping_for_company(
            mapping_id, company_id,
        )
        if mapping is None:
            raise NotFoundException(
                "InventoryChannelMapping을 찾을 수 없습니다.",
            )

        sku = self._get_sku_required(mapping.inventory_sku_id, company_id)

        adapter = get_channel_inventory_sync_adapter(mapping.channel_code)
        result = adapter.sync_stock(
            mapping.channel_sku, sku.available_qty, now,
        )

        mapping.last_synced_at = result.synced_at
        mapping.last_sync_status = (
            InventoryChannelSyncStatus.SYNCED
            if result.success
            else InventoryChannelSyncStatus.FAILED
        )
        mapping.last_sync_error = result.error_summary

        self.repository.save_mapping_no_commit(mapping)

        self._append_ledger_no_commit(
            sku,
            event_type=InventoryLedgerEventType.CHANNEL_SYNC,
            quantity_delta=0,
            triggered_by=triggered_by,
            channel_code=mapping.channel_code,
        )
        self.db.commit()
        self.db.refresh(mapping)

        return mapping


__all__ = [
    "InventoryService",
]
