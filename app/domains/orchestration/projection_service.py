"""
=========================================================
Homez OS

File : app/domains/orchestration/projection_service.py

자동 운영 상태 조회(L, 2026-08-20) — 순수 읽기 전용 projection.
어떤 도메인의 상태도 대체하거나 새로 쓰지 않는다. 각 OrderItem
1건에 대해 Order/Purchase/Shipment/ReturnOrder/Settlement/EStop을
읽어 OrchestrationStage 값 하나로 요약한다.
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundException
from app.domains.order.constants import OrderItemStatus
from app.domains.order.repository import OrderRepository
from app.domains.orchestration.constants import OrchestrationStage
from app.domains.purchase.constants import PurchaseStatus
from app.domains.purchase.repository import PurchaseRepository
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.model import ReturnOrder
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.shipment.constants import ShipmentStatus
from app.domains.shipment.model import ShipmentItem


class OrchestrationProjectionService:

    def __init__(self, db: Session):

        self.db = db
        self._order_repository = OrderRepository(db)
        self._purchase_repository = PurchaseRepository(db)

    def _is_estop_active(self) -> bool:

        from app.domains.automation_safety.model import EmergencyStop

        latest = (
            self.db.query(EmergencyStop)
            .order_by(EmergencyStop.id.desc())
            .first()
        )

        return latest is not None and latest.is_active

    def project_order_item_stage(
        self, order_item_id: int, company_id: int,
    ) -> str:

        order_item = self._order_repository.get_item_for_company(
            order_item_id, company_id,
        )
        if order_item is None:
            raise NotFoundException("주문 품목을 찾을 수 없습니다.")

        if self._is_estop_active():
            return OrchestrationStage.ESTOP_BLOCKED

        active_return = (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.company_id == company_id)
            .filter(ReturnOrder.order_item_id == order_item_id)
            .filter(ReturnOrder.status.notin_(
                (ReturnOrderStatus.COMPLETED, ReturnOrderStatus.REJECTED),
            ))
            .first()
        )
        if active_return is not None:
            return OrchestrationStage.RETURN_REQUESTED

        if order_item.status == OrderItemStatus.CANCELLED:
            return OrchestrationStage.USER_ACTION_REQUIRED

        if order_item.purchase_id is not None:
            purchase = self._purchase_repository.get_purchase_for_company(
                order_item.purchase_id, company_id,
            )
            if purchase is not None:
                if purchase.status == PurchaseStatus.CANCELLED:
                    return OrchestrationStage.PURCHASE_FAILED
                if purchase.status == PurchaseStatus.REQUESTED:
                    return OrchestrationStage.PURCHASE_APPROVAL_REQUIRED
                if purchase.status == PurchaseStatus.CONFIRMED:
                    return OrchestrationStage.SUPPLIER_CONFIRMED
                # RECEIVED — 재입고 완료, order_item.status가
                # receive_purchase()에서 이미 재예약을 시도했을 것이므로
                # 아래 일반 상태 흐름으로 계속 판정한다.

        if order_item.status == OrderItemStatus.OUT_OF_STOCK:
            return OrchestrationStage.OUT_OF_STOCK

        if order_item.status == OrderItemStatus.RESERVED:
            return OrchestrationStage.FULFILLMENT_READY

        if order_item.status in (
            OrderItemStatus.SHIPPED, OrderItemStatus.EXCHANGED,
        ):
            shipment_item = (
                self.db.query(ShipmentItem)
                .filter(ShipmentItem.company_id == company_id)
                .filter(ShipmentItem.order_item_id == order_item_id)
                .order_by(ShipmentItem.id.desc())
                .first()
            )
            if shipment_item is None:
                return OrchestrationStage.SHIPPED

            from app.domains.shipment.model import Shipment

            shipment = self.db.get(Shipment, shipment_item.shipment_id)
            if shipment is None or shipment.company_id != company_id:
                return OrchestrationStage.SHIPPED

            if shipment.status == ShipmentStatus.DELIVERED:
                settlement = (
                    self.db.query(MarketplaceSettlement)
                    .filter(MarketplaceSettlement.company_id == company_id)
                    .filter(MarketplaceSettlement.order_id == order_item.order_id)
                    .first()
                )
                if settlement is None:
                    return OrchestrationStage.SETTLEMENT_PENDING

                return OrchestrationStage.SETTLED

            return OrchestrationStage.SHIPPED

        if order_item.status == OrderItemStatus.RETURNED:
            return OrchestrationStage.REFUND_REQUIRED

        return OrchestrationStage.USER_ACTION_REQUIRED


__all__ = [
    "OrchestrationProjectionService",
]
