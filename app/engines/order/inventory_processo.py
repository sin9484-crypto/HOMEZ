"""
=========================================================
Homez OS

File : app/engines/order/inventory_processor.py
Version : 1.0.0

Order Inventory Processor
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.inventory.model import Inventory
from app.domains.inventory.service import InventoryService
from app.domains.order.model import Order


class InventoryProcessor:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

        self.inventory_service = InventoryService(db)

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    def get_inventory(
        self,
        order: Order,
    ) -> Inventory:

        return self.inventory_service.get_product_inventory(
            order.product_id,
        )

    # --------------------------------------------------
    # Reserve
    # --------------------------------------------------

    def reserve(
        self,
        order: Order,
    ) -> Inventory:

        inventory = self.get_inventory(
            order,
        )

        return self.inventory_service.reserve_stock(
            inventory.id,
            order.quantity,
        )

    # --------------------------------------------------
    # Release
    # --------------------------------------------------

    def release(
        self,
        order: Order,
    ) -> Inventory:

        inventory = self.get_inventory(
            order,
        )

        return self.inventory_service.release_stock(
            inventory.id,
            order.quantity,
        )

    # --------------------------------------------------
    # Auto Purchase
    # --------------------------------------------------

    def needs_auto_purchase(
        self,
        order: Order,
    ) -> bool:

        inventory = self.get_inventory(
            order,
        )

        return self.inventory_service.needs_auto_purchase(
            inventory.id,
        )

    # --------------------------------------------------
    # Available Stock
    # --------------------------------------------------

    def available_stock(
        self,
        order: Order,
    ) -> int:

        inventory = self.get_inventory(
            order,
        )

        return inventory.available_stock