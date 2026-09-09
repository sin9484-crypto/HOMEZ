"""
=========================================================
Homez OS

File : app/engines/order/shipment_processor.py
Version : 1.0.0

Order Shipment Processor
=========================================================
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.order.model import Order
from app.domains.shipment.model import Shipment
from app.domains.shipment.schema import (
    ShipmentCreate,
    ShipmentStatusUpdate,
)
from app.domains.shipment.service import ShipmentService


class ShipmentProcessor:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

        self.shipment_service = ShipmentService(db)

    # --------------------------------------------------
    # Create Shipment
    # --------------------------------------------------

    def create(
        self,
        order: Order,
        purchase_id: int,
        supplier_id: int,
        shipment_number: str,
    ) -> Shipment:

        shipment = ShipmentCreate(

            shipment_number=shipment_number,

            order_id=order.id,

            purchase_id=purchase_id,

            supplier_id=supplier_id,

            courier="",

            invoice_number="",

            tracking_url=None,
        )

        return self.shipment_service.create_shipment(
            shipment,
        )

    # --------------------------------------------------
    # Register Invoice
    # --------------------------------------------------

    def register_invoice(
        self,
        shipment_id: int,
        courier: str,
        invoice_number: str,
    ) -> Shipment:

        return self.shipment_service.update_invoice(
            shipment_id,
            courier,
            invoice_number,
        )

    # --------------------------------------------------
    # Update Status
    # --------------------------------------------------

    def update_status(
        self,
        shipment_id: int,
        status: str,
    ) -> Shipment:

        return self.shipment_service.update_status(
            shipment_id,
            ShipmentStatusUpdate(
                status=status,
            ),
        )

    # --------------------------------------------------
    # Complete Shipment
    # --------------------------------------------------

    def complete(
        self,
        shipment_id: int,
    ) -> Shipment:

        shipment = self.shipment_service.complete_shipment(
            shipment_id,
        )

        shipment.delivered_at = datetime.utcnow()

        return shipment

    # --------------------------------------------------
    # Marketplace Sync
    # --------------------------------------------------

    def ready_for_marketplace(
        self,
        shipment: Shipment,
    ) -> bool:

        return (
            shipment.invoice_number != ""
            and shipment.courier != ""
        )