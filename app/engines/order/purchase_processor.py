"""
=========================================================
Homez OS

File : app/engines/order/purchase_processor.py
Version : 1.0.0

Order Purchase Processor
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.service import PurchaseService
from app.domains.order.model import Order


class PurchaseProcessor:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.purchase_service = PurchaseService(db)

    # --------------------------------------------------
    # Auto Purchase
    # --------------------------------------------------

    def execute(
        self,
        order: Order,
        supplier_id: int,
        purchase_number: str,
        purchase_price: float,
        shipping_price: float = 0,
    ):

        total_price = purchase_price + shipping_price

        purchase = PurchaseCreate(

            purchase_number=purchase_number,

            order_id=order.id,

            supplier_id=supplier_id,

            purchase_price=purchase_price,

            shipping_price=shipping_price,

            total_price=total_price,

            memo="Auto Purchase",
        )

        return self.purchase_service.create_purchase(
            purchase,
        )