"""Materialize collected Coupang records into HOMEZ orders and order items."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.logger import logger
from app.domains.automation_safety.service import SafetyService
from app.domains.inventory.model import InventorySku
from app.domains.inventory.schema import InventoryReserveRequest
from app.domains.inventory.service import InventoryService
from app.domains.order.adapters.coupang_collection import ALLOWED_STATUSES
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
from app.domains.order.coupang_normalizer import NormalizedCoupangOrder
from app.domains.order.model import Order
from app.domains.order.model import OrderItem


MONEY_SCALE = Decimal("0.0001")

# 2026-09-17 Phase 7A 사후 감사 2차(결함 2, High) — 처음 보는
# channel_order_id를 신규 Order로 만들어도 되는지는 호출자가 어떤
# status로 조회했는지가 아니라, 응답 레코드 자체의 raw_status로
# 판단한다(요구사항 11 — 호출 파라미터를 신뢰하지 않는다). ACCEPT만
# "신규 주문"이라는 의미가 있고, 나머지 5개(ALLOWED_STATUSES에서
# ACCEPT를 뺀 값)는 "이미 HOMEZ에 있어야 정상인 주문"이므로 그
# 상태로 처음 발견되면 자동 생성하지 않고 복구 검토 대상으로만
# 남긴다. 두 집합은 ALLOWED_STATUSES 하나에서 파생되므로 새 상태가
# 추가돼도 둘 중 하나에 명시적으로 배정하기 전까지는 아래
# REJECTED_UNKNOWN_STATUS 분기로 fail-closed된다.
NEW_ORDER_ELIGIBLE_STATUSES = frozenset({"ACCEPT"})
EXISTING_ORDER_ONLY_STATUSES = ALLOWED_STATUSES - NEW_ORDER_ELIGIBLE_STATUSES


class OrderMaterializationOutcome:
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    RECOVERY_REVIEW_REQUIRED = "RECOVERY_REVIEW_REQUIRED"
    REJECTED_UNKNOWN_STATUS = "REJECTED_UNKNOWN_STATUS"


def _masked_channel_order_id(channel_order_id: str) -> str:
    """감사로그·복구후보 표시용 — 뒤 4자리만 남기고 마스킹한다."""
    if len(channel_order_id) <= 4:
        return "*" * len(channel_order_id)
    return "*" * (len(channel_order_id) - 4) + channel_order_id[-4:]


RECOVERY_REVIEW_REASON = "EXISTING_ORDER_NOT_FOUND_FOR_NON_ACCEPT_STATUS"


@dataclass(frozen=True)
class RecoveryReviewCandidate:
    """2026-09-17 Phase 7A 사후 감사 2차(Phase 4) — HOMEZ에 Order가
    없는데 ACCEPT 이후 상태로 처음 발견된 주문. 이번 라운드에서는
    이 계약(분류·보고)까지만 구현한다 — 자동으로 Order/PurchaseTask를
    만들지 않으며, 실제 복구 실행(선택적 저장) 기능은 별도 후속
    작업이다.

    2026-09-18 Phase 7B 진입 전 잔여 4항목 확인(항목 3) — 회사·판매
    계정·외부 주문·배송 묶음까지 전부 다시 식별할 수 있어야 한다는
    요구사항에 맞춰 company_id/store_connection_id/마스킹된
    shipment_box_id를 추가했다. 이 값들은 새 테이블이 아니라 이미
    커밋되어 있는 `OrderChannelFulfillment` 행(company_id/
    store_connection_id/channel_order_id/shipment_box_id/raw_status,
    order_id=NULL)에서 그대로 다시 계산할 수 있다 —
    `list_recovery_review_candidates()` 참고."""

    company_id: int
    store_connection_id: int
    masked_channel_order_id: str
    masked_shipment_box_id: str
    observed_status: str
    observed_at: datetime
    reason: str
    requires_user_approval: bool = True


@dataclass(frozen=True)
class OrderMaterializationResult:
    orders: dict[str, Order] = field(default_factory=dict)
    recovery_candidates: tuple[RecoveryReviewCandidate, ...] = ()
    rejected_unknown_status_count: int = 0


def list_recovery_review_candidates(
    db: Session, company_id: int, *, store_connection_id: int | None = None,
) -> list[RecoveryReviewCandidate]:
    """2026-09-18 Phase 7B 진입 전 잔여 4항목 확인(항목 3) — 복구 후보
    목록을 별도 테이블 없이, 이미 커밋된 `OrderChannelFulfillment`
    행에서 그대로 다시 계산한다("응답에만 존재"하지 않고 DB에서
    다시 조회 가능함을 증명하는 함수). `order_id IS NULL`이면서
    `raw_status`가 `EXISTING_ORDER_ONLY_STATUSES`에 속하는 행만
    복구 후보다 — `order_id IS NULL`이지만 상태 자체가 알 수 없는
    값(`REJECTED_UNKNOWN_STATUS`, fail-closed로 애초에 저장 거부된
    데이터 품질 문제)은 복구 후보가 아니므로 제외한다. 프로세스
    재시작 여부와 무관하게(새 DB 세션으로도) 항상 같은 결과를
    반환한다 — 조회만 하고 아무 것도 쓰지 않는다."""

    query = (
        db.query(OrderChannelFulfillment)
        .filter(OrderChannelFulfillment.company_id == company_id)
        .filter(OrderChannelFulfillment.order_id.is_(None))
        .filter(OrderChannelFulfillment.raw_status.in_(EXISTING_ORDER_ONLY_STATUSES))
    )
    if store_connection_id is not None:
        query = query.filter(
            OrderChannelFulfillment.store_connection_id == store_connection_id,
        )
    return [
        RecoveryReviewCandidate(
            company_id=row.company_id,
            store_connection_id=row.store_connection_id,
            masked_channel_order_id=_masked_channel_order_id(row.channel_order_id),
            masked_shipment_box_id=_masked_channel_order_id(row.shipment_box_id),
            observed_status=row.raw_status,
            observed_at=row.ordered_at,
            reason=RECOVERY_REVIEW_REASON,
        )
        for row in query.order_by(OrderChannelFulfillment.id.asc()).all()
    ]


def decimal_to_legacy_float(value: Decimal) -> float:
    """Numeric(18,4) source to legacy Float boundary, half-up at four places."""
    if not value.is_finite() or value < 0:
        raise ValueError("INVALID_LEGACY_MONEY_VALUE")
    quantized = value.quantize(MONEY_SCALE, rounding=ROUND_HALF_UP)
    if quantized >= Decimal("100000000000000"):
        raise ValueError("LEGACY_MONEY_VALUE_TOO_LARGE")
    return float(quantized)


class CoupangOrderMaterializationService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _channel_code(store_connection_id: int) -> str:
        return f"COUPANG#{store_connection_id}"

    def ensure_orders(
        self,
        company_id: int,
        store_connection_id: int,
        normalized_orders: list[NormalizedCoupangOrder],
    ) -> OrderMaterializationResult:
        grouped: dict[str, list[NormalizedCoupangOrder]] = defaultdict(list)
        for record in normalized_orders:
            grouped[record.channel_order_id].append(record)

        result: dict[str, Order] = {}
        recovery_candidates: list[RecoveryReviewCandidate] = []
        rejected_unknown_status_count = 0
        channel_code = self._channel_code(store_connection_id)
        for channel_order_id, records in grouped.items():
            order = (
                self.db.query(Order)
                .filter(Order.company_id == company_id)
                .filter(Order.channel_code == channel_code)
                .filter(Order.channel_order_id == channel_order_id)
                .first()
            )
            first = records[0]
            # 2026-09-17 Phase 7A 사후 감사 2차(요구사항 6/7/10/11) —
            # 호출자가 어떤 status로 조회를 요청했는지가 아니라, 응답
            # 레코드 자체의 raw_status로 저장 가능 여부를 판단한다.
            # 그룹 내 레코드들의 raw_status가 서로 다르면(정상적으로는
            # 발생하지 않아야 하지만) 가장 보수적으로 판단한다 — 하나라도
            # 신규생성 비대상이면 전체를 신규생성 비대상으로 취급한다.
            observed_statuses = {r.raw_status for r in records}
            if not observed_statuses <= ALLOWED_STATUSES:
                rejected_unknown_status_count += 1
                logger.warning(
                    "ORDER_MATERIALIZATION_UNKNOWN_STATUS company_id=%s "
                    "store_connection_id=%s masked_channel_order_id=%s "
                    "statuses=%s",
                    company_id, store_connection_id,
                    _masked_channel_order_id(channel_order_id),
                    sorted(observed_statuses - ALLOWED_STATUSES),
                )
                continue

            new_order_eligible = observed_statuses <= NEW_ORDER_ELIGIBLE_STATUSES
            total = sum(
                (item.order_price.amount for record in records for item in record.items),
                Decimal("0"),
            )
            if order is None:
                if not new_order_eligible:
                    recovery_candidates.append(RecoveryReviewCandidate(
                        company_id=company_id,
                        store_connection_id=store_connection_id,
                        masked_channel_order_id=_masked_channel_order_id(channel_order_id),
                        masked_shipment_box_id=_masked_channel_order_id(
                            first.channel_fulfillment_id,
                        ),
                        observed_status=first.raw_status,
                        observed_at=first.ordered_at.replace(tzinfo=None),
                        reason=RECOVERY_REVIEW_REASON,
                    ))
                    continue
                order = Order(
                    company_id=company_id, channel_code=channel_code,
                    channel_order_id=channel_order_id, status=OrderStatus.PENDING,
                    buyer_name=first.buyer_name,
                    receiver_name=first.receiver_name,
                    receiver_phone=first.receiver_phone,
                    receiver_address=first.receiver_address,
                    receiver_zipcode=first.receiver_zipcode,
                    total_amount=decimal_to_legacy_float(total),
                    ordered_at=first.ordered_at.replace(tzinfo=None),
                )
                self.db.add(order)
                try:
                    self.db.flush()
                except IntegrityError:
                    self.db.rollback()
                    order = (
                        self.db.query(Order)
                        .filter(Order.company_id == company_id)
                        .filter(Order.channel_code == channel_code)
                        .filter(Order.channel_order_id == channel_order_id)
                        .one()
                    )
                else:
                    order.order_number = f"O-{order.id}"
            else:
                order.total_amount = decimal_to_legacy_float(total)
                order.buyer_name = first.buyer_name
                order.receiver_name = first.receiver_name
                order.receiver_phone = first.receiver_phone
                order.receiver_address = first.receiver_address
                order.receiver_zipcode = first.receiver_zipcode
                order.ordered_at = first.ordered_at.replace(tzinfo=None)

            fulfillment_ids = [r.channel_fulfillment_id for r in records]
            (
                self.db.query(OrderChannelFulfillment)
                .filter(OrderChannelFulfillment.company_id == company_id)
                .filter(OrderChannelFulfillment.store_connection_id == store_connection_id)
                .filter(OrderChannelFulfillment.channel_order_id == channel_order_id)
                .filter(OrderChannelFulfillment.shipment_box_id.in_(fulfillment_ids))
                .update({OrderChannelFulfillment.order_id: order.id}, synchronize_session=False)
            )
            self.db.commit()
            self.db.refresh(order)
            result[channel_order_id] = order
        return OrderMaterializationResult(
            orders=result,
            recovery_candidates=tuple(recovery_candidates),
            rejected_unknown_status_count=rejected_unknown_status_count,
        )

    def _inventory_sku(self, company_id: int, inventory_sku_id: int) -> InventorySku:
        sku = (
            self.db.query(InventorySku)
            .filter(InventorySku.id == inventory_sku_id)
            .filter(InventorySku.company_id == company_id)
            .filter(InventorySku.is_active.is_(True))
            .first()
        )
        if sku is None:
            raise NotFoundException("연결할 재고 SKU를 찾을 수 없습니다.")
        return sku

    def resolve_item(
        self,
        company_id: int,
        unresolved_item_id: int,
        inventory_sku_id: int,
        actor_user_id: int,
        *,
        remember_mapping: bool = True,
    ) -> OrderItem:
        unresolved = (
            self.db.query(UnresolvedOrderItem)
            .filter(UnresolvedOrderItem.id == unresolved_item_id)
            .filter(UnresolvedOrderItem.company_id == company_id)
            .first()
        )
        if unresolved is None:
            raise NotFoundException("상품 연결 대기 품목을 찾을 수 없습니다.")
        if unresolved.quantity <= 0:
            raise BadRequestException("발주 가능한 수량이 0인 품목은 연결할 수 없습니다.")
        sku = self._inventory_sku(company_id, inventory_sku_id)
        fulfillment = (
            self.db.query(OrderChannelFulfillment)
            .filter(OrderChannelFulfillment.id == unresolved.fulfillment_id)
            .filter(OrderChannelFulfillment.company_id == company_id)
            .first()
        )
        if fulfillment is None or fulfillment.order_id is None:
            raise ConflictException("연결할 HOMEZ 주문이 아직 준비되지 않았습니다.")

        claim = self.db.execute(
            update(UnresolvedOrderItem)
            .where(UnresolvedOrderItem.id == unresolved.id)
            .where(UnresolvedOrderItem.company_id == company_id)
            .where(UnresolvedOrderItem.status == "MAPPING_REQUIRED")
            .values(status="RESOLVING"),
        )
        if claim.rowcount != 1:
            self.db.rollback()
            raise ConflictException("이미 연결됐거나 다른 요청이 처리 중인 품목입니다.")

        item = OrderItem(
            company_id=company_id, order_id=fulfillment.order_id,
            inventory_sku_id=sku.id, channel_sku=unresolved.channel_sku,
            sku_code_snapshot=sku.sku_code,
            product_name_snapshot=unresolved.product_name_snapshot,
            quantity=unresolved.quantity,
            unit_price=decimal_to_legacy_float(Decimal(unresolved.unit_price)),
            status=OrderItemStatus.PENDING,
        )
        self.db.add(item)
        if remember_mapping:
            mapping = (
                self.db.query(OrderSkuResolution)
                .filter(OrderSkuResolution.company_id == company_id)
                .filter(OrderSkuResolution.store_connection_id == fulfillment.store_connection_id)
                .filter(OrderSkuResolution.channel_sku == unresolved.channel_sku)
                .first()
            )
            if mapping is None:
                self.db.add(OrderSkuResolution(
                    company_id=company_id,
                    store_connection_id=fulfillment.store_connection_id,
                    channel_sku=unresolved.channel_sku,
                    inventory_sku_id=sku.id, created_by=actor_user_id,
                ))
            elif mapping.inventory_sku_id != sku.id:
                self.db.rollback()
                raise ConflictException("이 채널 SKU는 다른 재고 SKU에 연결되어 있습니다.")
        self.db.flush()
        unresolved.status = "RESOLVED"
        unresolved.resolved_order_item_id = item.id
        unresolved.resolved_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(item)

        estop_active = SafetyService(self.db).is_emergency_stop_active()
        if not estop_active:
            try:
                reservation = InventoryService(self.db).reserve(
                    sku.id, company_id,
                    InventoryReserveRequest(
                        quantity=item.quantity,
                        idempotency_key=f"order_item:{item.id}:reserve",
                        reference_type="order_item", reference_id=item.id,
                    ),
                    actor_user_id,
                )
                item.status = OrderItemStatus.RESERVED
                item.reservation_id = reservation.id
            except BadRequestException:
                item.status = OrderItemStatus.OUT_OF_STOCK
            self.db.add(item)
            self.db.commit()
            self.db.refresh(item)

        order = self.db.query(Order).filter(Order.id == item.order_id).one()
        order_items = self.db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        order.status = (
            OrderStatus.RESERVED
            if order_items and all(x.status == OrderItemStatus.RESERVED for x in order_items)
            else OrderStatus.PENDING
        )
        self.db.add(order)
        self.db.commit()

        if not estop_active:
            from app.domains.purchase_task.order_sync_service import PurchaseTaskOrderSyncService
            try:
                PurchaseTaskOrderSyncService(self.db).sync_order(
                    order, [item], triggered_by=actor_user_id,
                )
            except Exception:
                self.db.rollback()
        return item

    def auto_resolve(
        self,
        company_id: int,
        store_connection_id: int,
        *,
        actor_user_id: int,
    ) -> int:
        mappings = {
            row.channel_sku: row.inventory_sku_id
            for row in self.db.query(OrderSkuResolution)
            .filter(OrderSkuResolution.company_id == company_id)
            .filter(OrderSkuResolution.store_connection_id == store_connection_id)
            .all()
        }
        if not mappings:
            return 0
        unresolved = (
            self.db.query(UnresolvedOrderItem)
            .join(
                OrderChannelFulfillment,
                OrderChannelFulfillment.id == UnresolvedOrderItem.fulfillment_id,
            )
            .filter(UnresolvedOrderItem.company_id == company_id)
            .filter(UnresolvedOrderItem.status == "MAPPING_REQUIRED")
            .filter(OrderChannelFulfillment.store_connection_id == store_connection_id)
            # 2026-09-17 Phase 7A 사후 감사 2차 — ensure_orders()가 복구
            # 검토 대상(RECOVERY_REVIEW_REQUIRED)으로 분류해 order_id를
            # 연결하지 않은 fulfillment는 여기서도 건너뛴다. 그런 항목을
            # resolve_item()에 넘기면 "연결할 HOMEZ 주문이 아직 준비되지
            # 않았습니다" ConflictException이 발생해 이 배치 전체의
            # 자동연결이 중단된다 — 복구 승인 전까지는 자동으로 손대지
            # 않는 것이 정책이므로 애초에 대상에서 제외한다.
            .filter(OrderChannelFulfillment.order_id.isnot(None))
            .all()
        )
        resolved = 0
        for item in unresolved:
            sku_id = mappings.get(item.channel_sku)
            if sku_id is None or item.quantity <= 0:
                continue
            self.resolve_item(
                company_id, item.id, sku_id, actor_user_id=actor_user_id,
                remember_mapping=False,
            )
            resolved += 1
        return resolved


__all__ = [
    "CoupangOrderMaterializationService", "MONEY_SCALE",
    "decimal_to_legacy_float",
    "NEW_ORDER_ELIGIBLE_STATUSES", "EXISTING_ORDER_ONLY_STATUSES",
    "OrderMaterializationOutcome", "OrderMaterializationResult",
    "RecoveryReviewCandidate", "RECOVERY_REVIEW_REASON",
    "list_recovery_review_candidates",
]
