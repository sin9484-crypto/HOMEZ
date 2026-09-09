"""Materialize collected Coupang records into HOMEZ orders and order items."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.service import SafetyService
from app.domains.inventory.model import InventorySku
from app.domains.inventory.schema import InventoryReserveRequest
from app.domains.inventory.service import InventoryService
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
from app.domains.order.coupang_normalizer import NormalizedCoupangOrder
from app.domains.order.model import Order
from app.domains.order.model import OrderItem


MONEY_SCALE = Decimal("0.0001")


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
    ) -> dict[str, Order]:
        grouped: dict[str, list[NormalizedCoupangOrder]] = defaultdict(list)
        for record in normalized_orders:
            grouped[record.channel_order_id].append(record)

        result: dict[str, Order] = {}
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
            total = sum(
                (item.order_price.amount for record in records for item in record.items),
                Decimal("0"),
            )
            if order is None:
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
        return result

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
]
