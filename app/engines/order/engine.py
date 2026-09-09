"""
=========================================================
Homez OS

File : app/engines/order/engine.py
Version : 1.0.0

Order Engine
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.order.service import OrderService
from app.domains.inventory.service import InventoryService
from app.domains.purchase.service import PurchaseService


class OrderEngine:
    """
    Homez 주문 자동처리 엔진

    Flow

    Order
        ↓
    Inventory Check
        ↓
    Reserve Stock
        ↓
    Auto Purchase
        ↓
    Order Update
    """

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

        self.order_service = OrderService(db)

        self.inventory_service = InventoryService(db)

        self.purchase_service = PurchaseService(db)

    # --------------------------------------------------
    # Execute
    # --------------------------------------------------

    def process(
        self,
        order_id: int,
    ):

        order = self.order_service.get_order(
            order_id,
        )

        inventory = (
            self.inventory_service
            .get_product_inventory(
                order.product_id,
            )
        )

        # 재고 예약
        self.inventory_service.reserve_stock(
            inventory.id,
            order.quantity,
        )

        # 자동 발주 필요 여부
        auto_purchase = (
            self.inventory_service
            .needs_auto_purchase(
                inventory.id,
            )
        )

        return {
            "order_id": order.id,
            "reserved": True,
            "auto_purchase": auto_purchase,
        }