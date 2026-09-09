"""
=========================================================
Homez OS

File : app/domains/order/service.py

Order Service — V7 Gate 4(2026-08-15) 처음부터 재설계.

전체 흐름(CTO 지시 요구사항 1~8):
  1) collect_channel_order() — 채널 원본 주문(웹훅형 payload)을
     회사 스코프로 수집·정규화. raw_payload/normalized_snapshot을
     분리 저장(요구사항 7).
  2) Order.UNIQUE(company_id, channel_code, channel_order_id)로 중복
     수집을 DB 레벨에서 차단(요구사항 2) — idempotency_key 사전 조회는
     최적화일 뿐, 최종 판정은 항상 IntegrityError(Inventory Gate 3와
     동일 철학).
  3) 수집 즉시 각 품목에 대해 InventoryService.reserve()를 시도한다
     (요구사항 3) — 재고 부족이면 그 품목만 OUT_OF_STOCK으로 표시하고
     나머지 품목은 계속 진행한다(요구사항 6의 "부분 실패" 정신을 수집
     단계에도 동일하게 적용).
  4) cancel_order() — 예약된 품목은 InventoryService.release() +
     Funding Hold 해제(생성돼 있었다면)까지 연쇄 처리.
  5) sync_channel_status() — Fake Provider로 채널 측 상태를 폴링,
     EStop/Retry-After 게이트(요구사항 6) 적용. 채널이 취소를
     관측하면 내부 cancel_order() 파이프라인을 그대로 태운다.
  6) retry_reservation() — Purchase.receive_purchase()가 재입고 후
     자동 호출해 OUT_OF_STOCK 품목을 재예약한다(Inventory↔Purchase
     연결 고리를 닫는다, 요구사항 3/4).

EStop 적용 범위(marketplace_listing.status_sync_service와 동일 원칙):
  - **수집 자체**(주문 기록)는 EStop 중에도 항상 성공한다 — 실제 고객
    주문 데이터를 비상 정지를 이유로 유실시키지 않는다. 다만 EStop이
    활성화되어 있으면 "자동 예약 시도"만 건너뛰고 품목을 PENDING으로
    남긴다(재고를 확정적으로 묶는 자동화 행위만 멈춘다).
  - **채널 상태 동기화**(외부에 영향/의존하는 능동 폴링)는 EStop 중
    완전히 차단한다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import TooManyRequestsException
from app.domains.order.adapters.fake_provider import (
    get_order_channel_status_adapter,
)
from app.domains.order.constants import OrderChannelSyncStatus
from app.domains.order.constants import OrderIngestionStatus
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
from app.domains.order.constants import OrderStatusEventSource
from app.domains.order.constants import normalize_channel_status
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent
from app.domains.order.repository import OrderRepository
from app.domains.order.schema import OrderChannelCollectRequest


class OrderService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = OrderRepository(db)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _get_order_required(
        self,
        order_id: int,
        company_id: int,
    ) -> Order:

        order = self.repository.get_order_for_company(order_id, company_id)

        if order is None:
            raise NotFoundException("주문을 찾을 수 없습니다.")

        return order

    def _get_item_required(
        self,
        item_id: int,
        company_id: int,
    ) -> OrderItem:

        item = self.repository.get_item_for_company(item_id, company_id)

        if item is None:
            raise NotFoundException("주문 품목을 찾을 수 없습니다.")

        return item

    def _append_status_event_no_commit(
        self,
        order: Order,
        *,
        previous_status: str | None,
        new_status: str,
        source: str,
        reason: str | None = None,
        triggered_by: int | None = None,
    ) -> OrderStatusEvent:

        return self.repository.add_status_event_no_commit(
            OrderStatusEvent(
                company_id=order.company_id,
                order_id=order.id,
                previous_status=previous_status,
                new_status=new_status,
                source=source,
                reason=reason,
                triggered_by=triggered_by,
            ),
        )

    def _resolve_inventory_sku_id(
        self,
        company_id: int,
        channel_code: str,
        channel_sku: str,
    ) -> tuple[int, str] | None:
        """
        InventoryChannelMapping(company_id, channel_code, channel_sku)
        →  inventory_sku_id 해석. 매핑이 없으면 None을 반환한다(그
        품목만 건너뛰는 부분 실패 처리 — 요구사항 6).
        """

        from app.domains.inventory.model import InventoryChannelMapping
        from app.domains.inventory.model import InventorySku

        mapping = (
            self.db.query(InventoryChannelMapping)
            .filter(InventoryChannelMapping.company_id == company_id)
            .filter(InventoryChannelMapping.channel_code == channel_code)
            .filter(InventoryChannelMapping.channel_sku == channel_sku)
            .filter(InventoryChannelMapping.is_active.is_(True))
            .first()
        )

        if mapping is None:
            return None

        sku = (
            self.db.query(InventorySku)
            .filter(InventorySku.id == mapping.inventory_sku_id)
            .filter(InventorySku.company_id == company_id)
            .first()
        )

        if sku is None:
            return None

        return sku.id, sku.sku_code

    # --------------------------------------------------
    # 채널 주문 수집(요구사항 1/2/3/6/7)
    # --------------------------------------------------

    def collect_channel_order(
        self,
        company_id: int,
        data: OrderChannelCollectRequest,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> dict:
        # Audit(2026-08-21, AG-0) — 되돌림: 채널 주문 수집은 시스템/
        # 운영자가 직접 수행하는 핵심 업무 CRUD다 — AI Capability
        # Registry로 게이트하지 않는다. ORDER_SHIPMENT_RETURN은 향후
        # "예외·우선순위 분석·처리안 제시" 같은 AI 추천 기능(아직
        # 미구현)에만 적용한다.

        now = now or datetime.utcnow()

        raw_payload_json = json.dumps(
            data.model_dump(mode="json"), ensure_ascii=False,
        )

        # 1) idempotency 사전 조회(최적화 — 최종 판정은 아래 UNIQUE
        #    제약 위반 예외).
        existing = self.repository.get_order_by_channel(
            company_id, data.channel_code, data.channel_order_id,
        )

        if existing is not None:
            self.repository.add_ingestion_event_no_commit(
                OrderIngestionEvent(
                    company_id=company_id,
                    channel_code=data.channel_code,
                    channel_order_id=data.channel_order_id,
                    status=OrderIngestionStatus.DUPLICATE_IGNORED,
                    order_id=existing.id,
                    raw_payload=raw_payload_json,
                    normalized_snapshot=None,
                    triggered_by=triggered_by,
                ),
            )
            self.db.commit()

            return {
                "ingestion_status": OrderIngestionStatus.DUPLICATE_IGNORED,
                "order": existing,
                "items": self.repository.list_items_for_order(
                    existing.id, company_id,
                ),
                "unresolved_channel_skus": [],
            }

        from app.domains.automation_safety.service import SafetyService

        estop_active = SafetyService(self.db).is_emergency_stop_active()

        total_amount = sum(
            item.quantity * item.unit_price for item in data.items
        )

        order = Order(
            company_id=company_id,
            channel_code=data.channel_code,
            channel_order_id=data.channel_order_id,
            status=OrderStatus.PENDING,
            buyer_name=data.buyer_name,
            receiver_name=data.receiver_name,
            receiver_phone=data.receiver_phone,
            receiver_address=data.receiver_address,
            receiver_zipcode=data.receiver_zipcode,
            total_amount=total_amount,
            ordered_at=data.ordered_at,
        )

        try:
            order = self.repository.add_order_no_commit(order)
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_order_by_channel(
                company_id, data.channel_code, data.channel_order_id,
            )
            if existing is not None:
                self.repository.add_ingestion_event_no_commit(
                    OrderIngestionEvent(
                        company_id=company_id,
                        channel_code=data.channel_code,
                        channel_order_id=data.channel_order_id,
                        status=OrderIngestionStatus.DUPLICATE_IGNORED,
                        order_id=existing.id,
                        raw_payload=raw_payload_json,
                        normalized_snapshot=None,
                        triggered_by=triggered_by,
                    ),
                )
                self.db.commit()
                return {
                    "ingestion_status": (
                        OrderIngestionStatus.DUPLICATE_IGNORED
                    ),
                    "order": existing,
                    "items": self.repository.list_items_for_order(
                        existing.id, company_id,
                    ),
                    "unresolved_channel_skus": [],
                }
            raise ConflictException(
                "채널 주문 수집 중 충돌이 발생했습니다 — 다시 "
                "시도하세요.",
            )

        order.order_number = f"O-{order.id}"
        self.repository.save_order_no_commit(order)

        # 2026-08-15 실측 발견 Critical 결함 수정: InventoryService.
        # reserve()는 이 서비스와 같은 self.db 세션을 공유하면서 자신만의
        # 독립적인 commit()/rollback()을 수행한다(Inventory Gate 3 설계,
        # 재고 부족 시 실패 경로에서 즉시 rollback() 호출). 이 rollback()
        # 은 "그 시점까지 아직 commit되지 않고 세션에 남아있던 모든
        # 변경"을 함께 되돌린다 — order 자체나 아직 commit 전인 다른
        # 품목의 생성까지 함께 사라지는 결함을 실제로 재현·확인했다
        # (한 번은 "재고는 정상 차감됐는데 OrderItem.status만 PENDING
        # 으로 되돌아가는" 형태로, 한 번은 "order/item이 세션에서 아예
        # 통째로 사라져 refresh()가 InvalidRequestError를 던지는" 형태로
        # 나타났다). 해결: reserve()를 호출하기 "전에" order와 이번
        # 품목(PENDING 상태)을 먼저 확정 commit해 둔다 — reserve()의
        # rollback 반경 안에 내 자신의 미확정 변경이 전혀 없도록
        # 만들어, 실패해도 되돌릴 대상 자체가 없게 한다.
        self.db.commit()
        self.db.refresh(order)

        created_items: list[OrderItem] = []
        unresolved: list[str] = []

        from app.domains.inventory.schema import InventoryReserveRequest
        from app.domains.inventory.service import InventoryService

        inventory_service = InventoryService(self.db)

        for raw_item in data.items:
            resolved = self._resolve_inventory_sku_id(
                company_id, data.channel_code, raw_item.channel_sku,
            )

            if resolved is None:
                unresolved.append(raw_item.channel_sku)
                continue

            inventory_sku_id, sku_code = resolved

            item = OrderItem(
                company_id=company_id,
                order_id=order.id,
                inventory_sku_id=inventory_sku_id,
                channel_sku=raw_item.channel_sku,
                sku_code_snapshot=sku_code,
                product_name_snapshot=raw_item.product_name,
                quantity=raw_item.quantity,
                unit_price=raw_item.unit_price,
                status=OrderItemStatus.PENDING,
            )
            item = self.repository.add_item_no_commit(item)

            # 아래 reserve() 호출이 실패해 내부적으로 rollback()하기
            # 전에, 이 품목의 PENDING 생성 자체를 먼저 확정 commit한다.
            self.db.commit()
            self.db.refresh(item)

            if not estop_active:
                try:
                    reservation = inventory_service.reserve(
                        inventory_sku_id,
                        company_id,
                        InventoryReserveRequest(
                            quantity=raw_item.quantity,
                            idempotency_key=(
                                f"order_item:{item.id}:reserve"
                            ),
                            reference_type="order_item",
                            reference_id=item.id,
                        ),
                        triggered_by,
                    )
                    item.status = OrderItemStatus.RESERVED
                    item.reservation_id = reservation.id
                except BadRequestException:
                    item.status = OrderItemStatus.OUT_OF_STOCK

                self.repository.save_item_no_commit(item)
                self.db.commit()
                self.db.refresh(item)

            created_items.append(item)

        all_reserved = bool(created_items) and all(
            item.status == OrderItemStatus.RESERVED
            for item in created_items
        )
        order.status = (
            OrderStatus.RESERVED if all_reserved else OrderStatus.PENDING
        )
        self.repository.save_order_no_commit(order)

        self._append_status_event_no_commit(
            order,
            previous_status=None,
            new_status=order.status,
            source=OrderStatusEventSource.INGESTION,
            reason=(
                None if not unresolved else
                f"매핑되지 않은 channel_sku {len(unresolved)}건 건너뜀"
            ),
            triggered_by=triggered_by,
        )

        normalized_snapshot_json = json.dumps(
            {
                "order_id": order.id,
                "status": order.status,
                "items": [
                    {
                        "order_item_id": item.id,
                        "inventory_sku_id": item.inventory_sku_id,
                        "sku_code": item.sku_code_snapshot,
                        "quantity": item.quantity,
                        "status": item.status,
                    }
                    for item in created_items
                ],
                "unresolved_channel_skus": unresolved,
            },
            ensure_ascii=False,
        )

        ingestion_status = OrderIngestionStatus.COLLECTED

        self.repository.add_ingestion_event_no_commit(
            OrderIngestionEvent(
                company_id=company_id,
                channel_code=data.channel_code,
                channel_order_id=data.channel_order_id,
                status=ingestion_status,
                order_id=order.id,
                raw_payload=raw_payload_json,
                normalized_snapshot=normalized_snapshot_json,
                error_summary=(
                    None if not unresolved else
                    "일부 품목 SKU 매핑 실패: " + ", ".join(unresolved)
                ),
                triggered_by=triggered_by,
            ),
        )

        self.db.commit()
        self.db.refresh(order)

        # Gate PT-2B(2026-08-22 16차 지시) — 이미 확정 commit된 주문/
        # 품목을 매입·발주 Workflow(purchase_task)에 연결한다. 이
        # 호출은 위 주문 수집 결과를 절대 되돌리지 않는다(자체
        # 예외처리·자체 commit 경계, order_sync_service.py 참고).
        from app.domains.purchase_task.order_sync_service import (
            PurchaseTaskOrderSyncService,
        )
        try:
            PurchaseTaskOrderSyncService(self.db).sync_order(
                order, created_items, triggered_by=triggered_by,
            )
        except Exception:  # noqa: BLE001 — 매입 작업 연결 실패가 주문
            # 수집 자체의 성공 응답을 막지 않는다.
            self.db.rollback()

        return {
            "ingestion_status": ingestion_status,
            "order": order,
            "items": created_items,
            "unresolved_channel_skus": unresolved,
        }

    # --------------------------------------------------
    # 취소(요구사항 3)
    # --------------------------------------------------

    def cancel_order(
        self,
        order_id: int,
        company_id: int,
        reason: str,
        triggered_by: int | None = None,
    ) -> Order:

        order = self._get_order_required(order_id, company_id)

        if order.status in OrderStatus.NOT_CANCELLABLE:
            raise BadRequestException(
                "이 주문은 이미 출고/취소/반품/교환 처리되어 더 이상 "
                f"취소할 수 없습니다 — 현재 상태: {order.status}",
            )

        from app.domains.inventory.service import InventoryService

        inventory_service = InventoryService(self.db)

        items = self.repository.list_items_for_order(order_id, company_id)

        for item in items:
            if (
                item.status == OrderItemStatus.RESERVED
                and item.reservation_id is not None
            ):
                inventory_service.release(
                    item.reservation_id, company_id, triggered_by,
                )

            if item.status in (
                OrderItemStatus.PENDING,
                OrderItemStatus.OUT_OF_STOCK,
                OrderItemStatus.RESERVED,
            ):
                item.status = OrderItemStatus.CANCELLED
                self.repository.save_item_no_commit(item)

        from app.domains.funding.service import FundingService

        FundingService(self.db).release_hold_for_order(order_id)

        previous_status = order.status
        order.status = OrderStatus.CANCELLED
        self.repository.save_order_no_commit(order)

        self._append_status_event_no_commit(
            order,
            previous_status=previous_status,
            new_status=OrderStatus.CANCELLED,
            source=OrderStatusEventSource.CANCELLATION,
            reason=reason,
            triggered_by=triggered_by,
        )

        # 2026-08-15 V7 Gate 8 — 주문 취소는 이 도메인 자체의
        # append-only 이력(OrderStatusEvent, 위에서 이미 기록)에
        # 남지만, 회사를 넘나드는 SUPER_ADMIN 감사
        # 화면(`GET /admin/audit-logs`)에서는 전역 `audit_logs`가
        # 없으면 보이지 않는다 — 재고 예약 해제·Funding Hold 해제까지
        # 연쇄되는 되돌릴 수 있는 액션이라 재고 수동조정과 동일한
        # 민감도로 취급해 같은 트랜잭션에 함께 기록한다.
        write_audit_log(
            self.db,
            user_id=triggered_by,
            action="ORDER_CANCELLED",
            entity="order",
            entity_id=str(order.id),
            description=f"주문 취소 (사유: {reason})",
            company_id=company_id,
        )

        self.db.commit()
        self.db.refresh(order)

        # Gate PT-2C — 원 주문 취소를 이미 자동 생성된 구매 작업에
        # 반영한다(결제 전이면 무효화, 결제 대기 중이면 UNCERTAIN,
        # 이미 구매됐으면 취소/반품 요청 상태로 — 자동 재구매·자동
        # 환불은 절대 하지 않는다). 실패해도 주문 취소 자체는 이미
        # 확정됐으므로 되돌리지 않는다.
        from app.domains.purchase_task.order_sync_service import (
            PurchaseTaskOrderSyncService,
        )
        try:
            PurchaseTaskOrderSyncService(self.db).sync_order_cancelled(
                order, items, triggered_by=triggered_by,
            )
        except Exception:  # noqa: BLE001
            self.db.rollback()

        return order

    # --------------------------------------------------
    # 재예약(Purchase 입고 후 자동 호출 — 요구사항 3/4)
    # --------------------------------------------------

    def retry_reservation(
        self,
        item_id: int,
        company_id: int,
        triggered_by: int | None = None,
    ) -> OrderItem:

        item = self._get_item_required(item_id, company_id)

        if item.status != OrderItemStatus.OUT_OF_STOCK:
            return item

        from app.domains.inventory.schema import InventoryReserveRequest
        from app.domains.inventory.service import InventoryService

        inventory_service = InventoryService(self.db)

        try:
            reservation = inventory_service.reserve(
                item.inventory_sku_id,
                company_id,
                InventoryReserveRequest(
                    quantity=item.quantity,
                    idempotency_key=f"order_item:{item.id}:reserve",
                    reference_type="order_item",
                    reference_id=item.id,
                ),
                triggered_by,
            )
        except BadRequestException:
            return item

        item.status = OrderItemStatus.RESERVED
        item.reservation_id = reservation.id
        self.repository.save_item_no_commit(item)

        order = self._get_order_required(item.order_id, company_id)
        all_items = self.repository.list_items_for_order(
            order.id, company_id,
        )
        active_items = [
            i for i in all_items if i.status != OrderItemStatus.CANCELLED
        ]

        if active_items and all(
            i.status == OrderItemStatus.RESERVED for i in active_items
        ):
            previous_status = order.status
            order.status = OrderStatus.RESERVED
            self.repository.save_order_no_commit(order)
            self._append_status_event_no_commit(
                order,
                previous_status=previous_status,
                new_status=OrderStatus.RESERVED,
                source=OrderStatusEventSource.RESERVATION,
                reason="재입고 후 재예약 성공",
                triggered_by=triggered_by,
            )

        self.db.commit()
        self.db.refresh(item)

        return item

    # --------------------------------------------------
    # 채널 상태 동기화(요구사항 1/6, Fake Provider 전용)
    # --------------------------------------------------

    def sync_channel_status(
        self,
        order_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> dict:

        now = now or datetime.utcnow()

        from app.domains.automation_safety.service import SafetyService

        if SafetyService(self.db).is_emergency_stop_active():
            raise BadRequestException(
                "Emergency Stop이 활성화되어 있어 채널 상태 동기화를 "
                "실행할 수 없습니다.",
            )

        order = self._get_order_required(order_id, company_id)

        available_at = order.rate_limit_retry_available_at
        if available_at is not None and now < available_at:
            remaining = max(1, int((available_at - now).total_seconds()))
            raise TooManyRequestsException(
                detail={
                    "error_code": "RATE_LIMITED",
                    "retry_after_seconds": remaining,
                    "retry_available_at": available_at.isoformat(),
                },
                headers={"Retry-After": str(remaining)},
            )

        adapter = get_order_channel_status_adapter(order.channel_code)
        result = adapter.check_order_status(order.channel_order_id, now)

        if not result.success:
            order.channel_sync_status = OrderChannelSyncStatus.FAILED
            order.channel_last_synced_at = now
            order.channel_last_sync_error = result.error_summary

            if result.retry_after_seconds is not None:
                from app.domains.marketplace_listing.retry_after import (
                    combine_retry_available_at,
                    compute_retry_available_at,
                )

                candidate = compute_retry_available_at(
                    result.retry_after_seconds, now=now,
                )
                order.rate_limit_retry_after_seconds = (
                    result.retry_after_seconds
                )
                order.rate_limit_retry_available_at = (
                    combine_retry_available_at(
                        order.rate_limit_retry_available_at, candidate,
                    )
                )

            self.repository.save_order_no_commit(order)
            self.db.commit()
            self.db.refresh(order)

            return {
                "order": order,
                "success": False,
                "raw_status": None,
                "error_code": result.error_code,
                "cancelled": False,
            }

        cancelled = False
        normalized = normalize_channel_status(
            order.channel_code, result.raw_status,
        )

        if (
            normalized == OrderStatus.CANCELLED
            and order.status not in OrderStatus.NOT_CANCELLABLE
        ):
            self.cancel_order(
                order_id, company_id,
                reason="채널에서 주문 취소가 감지되었습니다.",
                triggered_by=triggered_by,
            )
            cancelled = True
            order = self._get_order_required(order_id, company_id)

        order.channel_sync_status = OrderChannelSyncStatus.SYNCED
        order.channel_last_synced_at = now
        order.channel_last_sync_error = None
        self.repository.save_order_no_commit(order)

        self.db.commit()
        self.db.refresh(order)

        return {
            "order": order,
            "success": True,
            "raw_status": result.raw_status,
            "error_code": None,
            "cancelled": cancelled,
        }

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_order(
        self,
        order_id: int,
        company_id: int,
    ) -> Order:

        return self._get_order_required(order_id, company_id)

    def list_orders(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:

        return self.repository.list_orders_for_company(
            company_id, status, skip, limit,
        )

    def list_items(
        self,
        order_id: int,
        company_id: int,
    ) -> list[OrderItem]:

        self._get_order_required(order_id, company_id)

        return self.repository.list_items_for_order(order_id, company_id)

    def list_status_events(
        self,
        order_id: int,
        company_id: int,
    ) -> list[OrderStatusEvent]:

        self._get_order_required(order_id, company_id)

        return self.repository.list_status_events_for_order(
            order_id, company_id,
        )

    def list_ingestion_events(
        self,
        order_id: int,
        company_id: int,
    ) -> list[OrderIngestionEvent]:

        self._get_order_required(order_id, company_id)

        return self.repository.list_ingestion_events_for_order(
            order_id, company_id,
        )


__all__ = [
    "OrderService",
]
