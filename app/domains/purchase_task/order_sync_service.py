"""
=========================================================
Homez OS

File : app/domains/purchase_task/order_sync_service.py

Gate PT-2(2026-08-22 16차 지시) — 채널(쿠팡 등) 주문 수집을 매입·발주
Workflow(purchase_task)에 연결한다. `app/domains/order/service.py`의
주문 수집·취소 로직을 다시 구현하지 않는다 — 이미 커밋된 Order/
OrderItem을 읽어 그 이후 단계만 담당한다.

설계 원칙:
  - Event Bus는 이 저장소에 실제로 배선된 것이 없다(조사 확인 —
    app/domains/event_bus/**, app/events/**는 전부 0바이트 스텁).
    그래서 OrderService가 이 서비스를 직접 호출하는 in-process 방식을
    쓴다(기존 저장소 전역 컨벤션과 동일).
  - `OrderService.collect_channel_order()`는 의도적으로 하나의
    Transaction이 아니다(Inventory reserve()의 독립 rollback이
    아직 commit 안 된 형제 데이터를 함께 지우는 결함이 있었기 때문 —
    order/service.py 주석 참고). 그래서 이 서비스는 항상 이미 commit된
    Order/OrderItem을 받아 그 이후에 "별도 경계"로 동작한다 — 이
    서비스 안에서 발생하는 어떤 실패도 이미 커밋된 주문 수집 결과를
    되돌리지 않는다(각 품목 처리마다 개별 try/except + 개별 commit).
  - PurchaseTask.idempotency_key(`order_item:{id}:purchase_task`)로
    같은 품목을 두 번 동기화해도 중복 생성되지 않는다
    (PurchaseTaskService.create_task()가 이미 이 idempotency를
    보장한다 — 재구현하지 않는다).
  - 상품 브랜드/제조사/모델/GTIN/용량/향·색상 중 이 저장소의 Order→
    InventorySku→ProductCandidate 체인이 실제로 제공하는 값은
    product_name(상품명)과 brand_hint(있으면)뿐이다 — 나머지는
    추정하지 않고 비워 둔다(정직 공개, 아래 클래스 docstring 참고).
  - 고객 개인정보(수취인 이름/주소/전화번호)는 이 도메인에 절대
    복사하지 않는다 — `shippable_region_note`는 자동 채우지 않는다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.domains.funding.service import FundingService
from app.domains.notification_center.model import NOTIFICATION_LEVEL_ERROR
from app.domains.notification_center.service import NotificationService
from app.domains.order.constants import OrderItemStatus
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.purchase_task.constants import BudgetReservationStatus
from app.domains.purchase_task.constants import PurchaseTaskCreationSource
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.purchase_task.service import PurchaseTaskService

# 이 서비스가 처리하는 OrderItem.status 값 — 이 두 값만 "재고 판정이
# 실제로 끝났다"는 뜻이다. PENDING(EStop 등으로 예약 시도 자체를
#건너뛴 상태)은 아직 판정 전이므로 건너뛴다 — reconcile_recent_orders()
# 가 나중에 다시 훑는다. CANCELLED/SHIPPED/RETURNED/EXCHANGED는 이미
# 종결됐거나 이 서비스 범위 밖이므로 건너뛴다.
_PROCESSABLE_ITEM_STATUSES = (
    OrderItemStatus.OUT_OF_STOCK, OrderItemStatus.RESERVED,
)


def _order_sync_audit(
    db: Session, *, company_id: int, user_id: int | None, action: str,
    entity: str, entity_id: int, description: str,
) -> None:

    write_audit_log(
        db, user_id=user_id, action=action, entity=entity,
        entity_id=str(entity_id), description=description,
        company_id=company_id,
    )


class PurchaseTaskOrderSyncService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = PurchaseTaskRepository(db)
        self.service = PurchaseTaskService(db)
        self.notification = NotificationService(db)

    # --------------------------------------------------
    # Gate PT-2B — 주문 수집 직후 자동 생성(품목 단위)
    # --------------------------------------------------

    def sync_order(
        self, order: Order, items: list[OrderItem],
        *, triggered_by: int | None = None,
    ) -> dict:
        """OrderService.collect_channel_order()가 각 품목을 확정 commit한
        "뒤"에 호출한다. 반환값은 요약일 뿐 — 이 메서드는 어떤 예외도
        밖으로 던지지 않는다(호출자의 주문 수집 결과를 오염시키지
        않기 위함, 지시문 4절 명시)."""

        created, skipped, review, failed = [], [], [], []

        for item in items:
            if item.status not in _PROCESSABLE_ITEM_STATUSES:
                continue
            try:
                task = self.sync_order_item(
                    item, order, triggered_by=triggered_by,
                )
            except Exception as exc:  # noqa: BLE001 — 품목 하나의 실패가
                # 나머지 품목·주문 수집 자체를 막지 않는다.
                self.db.rollback()
                self._record_failure(order, item, exc, triggered_by)
                failed.append(item.id)
                continue

            if task is None:
                continue
            if task.status == PurchaseTaskStatus.SKIPPED_INVENTORY_AVAILABLE:
                skipped.append(task.id)
            elif task.status == PurchaseTaskStatus.REVIEW_REQUIRED:
                review.append(task.id)
            else:
                created.append(task.id)

        try:
            _order_sync_audit(
                self.db, company_id=order.company_id, user_id=triggered_by,
                action=(
                    "COUPANG_ORDER_IMPORTED" if order.channel_code == "COUPANG"
                    else f"{order.channel_code}_ORDER_IMPORTED"
                ),
                entity="order", entity_id=order.id,
                description=(
                    f"주문 매입 연결: 생성 {len(created)}건, "
                    f"재고보유 생략 {len(skipped)}건, 확인필요 {len(review)}건, "
                    f"실패 {len(failed)}건"
                ),
            )
            self.db.commit()
        except Exception:  # noqa: BLE001 — 요약 감사로그 실패조차
            # 주문 수집 자체를 막지 않는다.
            self.db.rollback()

        return {
            "created_task_ids": created, "skipped_task_ids": skipped,
            "review_task_ids": review, "failed_item_ids": failed,
        }

    def sync_order_item(
        self, item: OrderItem, order: Order,
        *, triggered_by: int | None = None,
    ):

        idempotency_key = f"order_item:{item.id}:purchase_task"
        existing = self.repository.get_task_by_idempotency(
            order.company_id, idempotency_key,
        )
        if existing is not None:
            return existing

        if item.status == OrderItemStatus.RESERVED:
            task = self.service.create_task(
                order.company_id, source_order_id=order.id,
                source_order_item_id=item.id,
                product_title=item.product_name_snapshot or "(상품명 확인 필요)",
                brand=None, manufacturer=None, model_name=None, gtin=None,
                capacity=None, quantity=item.quantity, color_or_scent=None,
                idempotency_key=idempotency_key,
                correlation_id=f"order:{order.id}",
                created_by=triggered_by,
                creation_source=PurchaseTaskCreationSource.ORDER_AUTO,
                initial_status=PurchaseTaskStatus.SKIPPED_INVENTORY_AVAILABLE,
            )
            _order_sync_audit(
                self.db, company_id=order.company_id, user_id=triggered_by,
                action="PURCHASE_TASK_SKIPPED_INVENTORY_AVAILABLE",
                entity="purchase_task", entity_id=task.id,
                description="재고 보유로 매입 불필요 — 자동 생략 처리",
            )
            self.db.commit()
            return task

        # OUT_OF_STOCK — 매입이 필요하다. 이 저장소의 Order→
        # InventorySku→ProductCandidate 체인이 실제로 제공하는 값만
        # 채운다(brand_hint 외에는 추정하지 않는다).
        brand_hint = self._resolve_brand_hint(item, order.company_id)
        product_title = item.product_name_snapshot or ""
        needs_review = not product_title.strip()

        task = self.service.create_task(
            order.company_id, source_order_id=order.id,
            source_order_item_id=item.id,
            product_title=product_title or "(상품명 확인 필요)",
            brand=brand_hint, manufacturer=None, model_name=None,
            gtin=None, capacity=None, quantity=item.quantity,
            color_or_scent=None,
            coupang_sale_amount=(
                item.unit_price * item.quantity
                if item.unit_price is not None else None
            ),
            coupang_fee_amount=None,
            idempotency_key=idempotency_key,
            correlation_id=f"order:{order.id}",
            created_by=triggered_by,
            creation_source=PurchaseTaskCreationSource.ORDER_AUTO,
            initial_status=(
                PurchaseTaskStatus.REVIEW_REQUIRED if needs_review else None
            ),
            initial_caution_reason=(
                "PRODUCT_MAPPING_INSUFFICIENT" if needs_review else None
            ),
        )

        if needs_review:
            _order_sync_audit(
                self.db, company_id=order.company_id, user_id=triggered_by,
                action="PURCHASE_TASK_REVIEW_REQUIRED",
                entity="purchase_task", entity_id=task.id,
                description="상품 매핑 정보 부족 — 확인 필요",
            )
            self.db.commit()

        return task

    def _resolve_brand_hint(
        self, item: OrderItem, company_id: int,
    ) -> str | None:

        if item.inventory_sku_id is None:
            return None

        from app.domains.inventory.service import InventoryService

        try:
            sku = InventoryService(self.db).get_sku(
                item.inventory_sku_id, company_id,
            )
        except Exception:  # noqa: BLE001 — 힌트 조회 실패는 치명적이지
            # 않다, None으로 대체하고 계속 진행한다.
            return None

        if sku is None:
            return None

        from app.domains.product_candidate.model import ProductCandidate

        candidate = (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.id == sku.product_candidate_id)
            .first()
        )
        return candidate.brand_hint if candidate is not None else None

    def _record_failure(
        self, order: Order, item: OrderItem, exc: Exception,
        triggered_by: int | None,
    ) -> None:

        try:
            _order_sync_audit(
                self.db, company_id=order.company_id, user_id=triggered_by,
                action="PURCHASE_TASK_CREATION_FAILED",
                entity="order_item", entity_id=item.id,
                description=f"구매 작업 자동 생성 실패: {exc.__class__.__name__}",
            )
            self.notification.notify_company(
                company_id=order.company_id,
                category="purchase_task",
                level=NOTIFICATION_LEVEL_ERROR,
                title="구매 작업 자동 생성 실패",
                message=(
                    f"주문 품목(#{item.id}) 매입 작업 생성에 실패했습니다 — "
                    "매입·발주 관리 화면에서 수동으로 확인·생성하세요."
                ),
                link_path=f"purchase-task?source_order_id={order.id}",
            )
            self.db.commit()
        except Exception:  # noqa: BLE001 — 실패 기록 자체의 실패는
            # 삼킨다(무한 재귀·2차 오염 방지).
            self.db.rollback()

    # --------------------------------------------------
    # Gate PT-2C — 원 주문 취소 동기화
    # --------------------------------------------------

    def sync_order_cancelled(
        self, order: Order, items: list[OrderItem],
        *, triggered_by: int | None = None,
    ) -> dict:
        """OrderService.cancel_order()가 주문/품목을 CANCELLED로 확정
        commit한 "뒤"에 호출한다. 이미 결제 여부가 불확실하거나 이미
        구매·배송된 작업은 자동으로 재구매·환불을 실행하지 않는다 —
        상태만 사람이 처리할 수 있는 다음 단계로 옮긴다."""

        voided, uncertain, cancel_flagged, return_flagged, noop = (
            [], [], [], [], [],
        )

        for item in items:
            task = self.repository.get_task_by_source_order_item(
                order.company_id, item.id,
            )
            if task is None:
                continue

            try:
                outcome = self._apply_source_order_cancellation(
                    task, triggered_by,
                )
            except Exception:  # noqa: BLE001 — 품목 하나 실패가 나머지를
                # 막지 않는다.
                self.db.rollback()
                continue

            {
                "VOIDED": voided, "UNCERTAIN": uncertain,
                "CANCEL_REQUIRED": cancel_flagged,
                "RETURN_REQUIRED": return_flagged, "NOOP": noop,
            }[outcome].append(task.id)

        _order_sync_audit(
            self.db, company_id=order.company_id, user_id=triggered_by,
            action="SOURCE_ORDER_CANCELLED",
            entity="order", entity_id=order.id,
            description=(
                f"원 주문 취소 동기화: 취소됨 {len(voided)}건, "
                f"불명확 {len(uncertain)}건, 취소요청필요 {len(cancel_flagged)}건, "
                f"반품요청필요 {len(return_flagged)}건, 무관 {len(noop)}건"
            ),
        )
        self.db.commit()

        return {
            "voided_task_ids": voided, "uncertain_task_ids": uncertain,
            "cancel_required_task_ids": cancel_flagged,
            "return_required_task_ids": return_flagged,
            "noop_task_ids": noop,
        }

    def _apply_source_order_cancellation(
        self, task, triggered_by: int | None,
    ) -> str:

        pre_purchase_states = (
            PurchaseTaskStatus.SEARCH_REQUIRED,
            PurchaseTaskStatus.CANDIDATES_READY,
            PurchaseTaskStatus.REVIEW_REQUIRED,
        )

        if task.status in pre_purchase_states:
            self._void_task(task, triggered_by)
            return "VOIDED"

        if task.status == PurchaseTaskStatus.PURCHASE_READY:
            self._release_task_budget(task)
            self._void_task(task, triggered_by)
            return "VOIDED"

        if task.status == PurchaseTaskStatus.USER_PAYMENT_PENDING:
            # 결제 여부가 불확실하다 — 예산은 그대로 점유 유지, 자동
            # 재구매·자동 취소를 하지 않는다(기존 UNCERTAIN 철학 재사용).
            expected_version = task.version
            task.status = PurchaseTaskStatus.UNCERTAIN
            task.caution_reason = "SOURCE_ORDER_CANCELLED_WHILE_PAYMENT_PENDING"
            self.service._advance_version(task, expected_version)
            _order_sync_audit(
                self.db, company_id=task.company_id, user_id=triggered_by,
                action="PURCHASE_TASK_RESULT_UNCERTAIN",
                entity="purchase_task", entity_id=task.id,
                description="원 주문 취소 수신 — 결제 여부 불명확",
            )
            self.db.commit()
            return "UNCERTAIN"

        if task.status in (
            PurchaseTaskStatus.TRACKING_REQUIRED, PurchaseTaskStatus.SHIPPED,
        ):
            self.service.request_cancel(
                task.id, task.company_id,
                "원 쿠팡 주문이 취소되었습니다 — 구매처에 취소를 요청하세요.",
                requested_by=triggered_by,
            )
            return "CANCEL_REQUIRED"

        if task.status == PurchaseTaskStatus.DELIVERED:
            self.service.request_return(
                task.id, task.company_id,
                "원 쿠팡 주문이 취소되었습니다 — 이미 배송된 상품입니다, "
                "반품을 요청하세요.",
                requested_by=triggered_by,
            )
            return "RETURN_REQUIRED"

        return "NOOP"

    def _void_task(self, task, triggered_by: int | None) -> None:

        expected_version = task.version
        task.status = PurchaseTaskStatus.SOURCE_ORDER_CANCELLED
        task.block_reason = None
        task.caution_reason = None
        self.service._advance_version(task, expected_version)
        _order_sync_audit(
            self.db, company_id=task.company_id, user_id=triggered_by,
            action="PURCHASE_TASK_SOURCE_ORDER_CANCELLED",
            entity="purchase_task", entity_id=task.id,
            description="원 주문 취소로 구매 작업을 무효화함(구매 전 단계)",
        )
        self.db.commit()

    def _release_task_budget(self, task) -> None:

        if task.budget_reservation_id is None:
            return

        reservation = self.repository.get_reservation(
            task.budget_reservation_id, task.company_id,
        )
        if reservation is None or reservation.status not in (
            BudgetReservationStatus.ACTIVE
        ):
            return

        self.service._release_budget(reservation.account_id, reservation.amount)
        self.service._append_ledger(
            reservation.account_id, task.company_id, reservation.amount,
            FundingService.TYPE_HOLD_RELEASE, task.id,
            "원 주문 취소로 예산 예약 해제",
        )
        reservation.status = BudgetReservationStatus.RELEASED
        reservation.released_at = datetime.utcnow()

    # --------------------------------------------------
    # Gate PT-2C(수량 변경) — 정의만 하고 자동 트리거는 없음(정직 공개).
    # 이 저장소의 Order/OrderItem에는 생성 이후 수량을 바꾸는 경로가
    # 전혀 없다(조사 확인 — order/service.py 어디에도 quantity 재대입이
    # 없다). 채널이 실제로 수량 변경을 통보하는 수집 경로가 생기면
    # 그 경로가 이 메서드를 호출하면 된다 — 지금은 호출자가 없다.
    # --------------------------------------------------

    def sync_order_item_quantity_changed(
        self, item: OrderItem, new_quantity: int,
        *, triggered_by: int | None = None,
    ):

        task = self.repository.get_task_by_source_order_item(
            item.company_id, item.id,
        )
        if task is None:
            return None

        pre_payment_states = (
            PurchaseTaskStatus.SEARCH_REQUIRED,
            PurchaseTaskStatus.CANDIDATES_READY,
        )

        if task.status in pre_payment_states:
            expected_version = task.version
            task.quantity = new_quantity
            # 기존 후보 판정은 수량 변경으로 무효화된다 — 재판정을
            # 요구하기 위해 REVIEW_REQUIRED로 되돌린다.
            task.status = PurchaseTaskStatus.REVIEW_REQUIRED
            task.caution_reason = "SOURCE_ORDER_QUANTITY_CHANGED"
            self.service._advance_version(task, expected_version)
            _order_sync_audit(
                self.db, company_id=task.company_id, user_id=triggered_by,
                action="SOURCE_ORDER_QUANTITY_CHANGED",
                entity="purchase_task", entity_id=task.id,
                description=f"원 주문 수량 변경 반영: {new_quantity}",
            )
            self.db.commit()
            return task

        # 이미 예산이 점유됐거나(PURCHASE_READY 이상) 이미 구매된
        # 작업은 자동으로 수량을 바꾸지 않는다 — 사람이 직접 확인해야
        # 한다.
        expected_version = task.version
        task.caution_reason = "SOURCE_ORDER_QUANTITY_CHANGED_NEEDS_REVIEW"
        self.service._advance_version(task, expected_version)
        _order_sync_audit(
            self.db, company_id=task.company_id, user_id=triggered_by,
            action="SOURCE_ORDER_QUANTITY_CHANGED",
            entity="purchase_task", entity_id=task.id,
            description=(
                f"원 주문 수량 변경 수신({new_quantity}) — 이미 예산이 "
                "점유돼 있어 자동 반영하지 않음, 확인 필요"
            ),
        )
        self.db.commit()
        return task

    # --------------------------------------------------
    # Gate PT-2B — 수동 재조정(백필). 자동 생성 경로와 동일한
    # sync_order_item()을 그대로 재사용한다(Router가 로직을 복제하지
    # 않는다).
    # --------------------------------------------------

    def reconcile_recent_orders(
        self, company_id: int, *, limit: int = 200,
        triggered_by: int | None = None,
    ) -> dict:
        """기존 `OrderRepository.list_orders_for_company()`를 그대로
        재사용한다(최신순, 새 조회 로직을 만들지 않는다)."""

        from app.domains.order.repository import OrderRepository

        order_repo = OrderRepository(self.db)
        orders = order_repo.list_orders_for_company(
            company_id, limit=limit,
        )

        totals = {
            "orders_scanned": 0, "created_task_ids": [], "skipped_task_ids": [],
            "review_task_ids": [], "failed_item_ids": [],
        }

        for order in orders:
            items = order_repo.list_items_for_order(order.id, company_id)
            result = self.sync_order(items=items, order=order, triggered_by=triggered_by)
            totals["orders_scanned"] += 1
            totals["created_task_ids"].extend(result["created_task_ids"])
            totals["skipped_task_ids"].extend(result["skipped_task_ids"])
            totals["review_task_ids"].extend(result["review_task_ids"])
            totals["failed_item_ids"].extend(result["failed_item_ids"])

        return totals


__all__ = ["PurchaseTaskOrderSyncService"]
