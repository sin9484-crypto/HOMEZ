"""
=========================================================
Homez OS

File : app/services/sync_service.py
Version : 1.0.0

Sync Service
=========================================================
"""

from typing import Any
from typing import Dict
from typing import List

from app.services.marketplace_service import MarketplaceService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService
from app.services.shipment_service import ShipmentService


class SyncService:

    def __init__(

        self,

        marketplace_service: MarketplaceService,

        product_service: ProductService,

        order_service: OrderService,

        shipment_service: ShipmentService,

        purchase_service: PurchaseService,

    ):

        self.marketplace_service = marketplace_service

        self.product_service = product_service

        self.order_service = order_service

        self.shipment_service = shipment_service

        self.purchase_service = purchase_service

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    def sync_product(

        self,

        marketplaces: List[str],

        product: Dict[str, Any],

    ):

        return self.product_service.publish_all(

            marketplaces,

            product,

        )

    def update_product(

        self,

        marketplaces: List[str],

        product: Dict[str, Any],

    ):

        return self.product_service.update_all(

            marketplaces,

            product,

        )

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    def sync_inventory(

        self,

        marketplaces: List[str],

        product: Dict[str, Any],

    ):

        return self.product_service.sync_inventory(

            marketplaces,

            product,

        )

    # --------------------------------------------------
    # Orders
    # --------------------------------------------------

    def collect_orders(

        self,

        marketplaces: List[str],

        **kwargs,

    ):

        return self.order_service.collect_all_orders(

            marketplaces,

            **kwargs,

        )
    # --------------------------------------------------
    # Shipment
    # --------------------------------------------------

    def sync_shipment(

        self,

        marketplaces: List[str],

        shipment: Dict[str, Any],

    ):

        return self.shipment_service.register_invoice_all(

            marketplaces,

            shipment,

        )

    # --------------------------------------------------
    # Purchase
    # --------------------------------------------------

    def sync_purchase(

        self,

        marketplaces: List[str],

        order: Dict[str, Any],

    ):

        return self.purchase_service.purchase_all(

            marketplaces,

            order,

        )

    # --------------------------------------------------
    # Marketplace Health
    # --------------------------------------------------

    def health(

        self,

    ) -> Dict[str, bool]:

        return self.marketplace_service.ping_all()

    # --------------------------------------------------
    # Full Synchronization
    # --------------------------------------------------

    def synchronize(

        self,

        marketplaces: List[str],

        product: Dict[str, Any] | None = None,

        shipment: Dict[str, Any] | None = None,

        purchase: Dict[str, Any] | None = None,

        **order_filters,

    ) -> Dict[str, Any]:

        result = {}

        if product:

            result["product"] = self.sync_product(

                marketplaces,

                product,

            )

            result["inventory"] = self.sync_inventory(

                marketplaces,

                product,

            )

        result["orders"] = self.collect_orders(

            marketplaces,

            **order_filters,

        )

        if shipment:

            result["shipment"] = self.sync_shipment(

                marketplaces,

                shipment,

            )

        if purchase:

            result["purchase"] = self.sync_purchase(

                marketplaces,

                purchase,

            )

        result["health"] = self.health()

        return result

    # --------------------------------------------------
    # Supported Marketplace
    # --------------------------------------------------

    def supported_marketplaces(

        self,

    ) -> List[str]:

        return self.marketplace_service.marketplaces()