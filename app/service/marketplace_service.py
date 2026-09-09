"""
=========================================================
Homez OS

File : app/services/marketplace_service.py
Version : 1.0.0

Marketplace Service
=========================================================
"""

from typing import Any
from typing import Dict
from typing import List

from app.marketplace.factory import MarketplaceFactory


class MarketplaceService:

    def __init__(

        self,

        factory: MarketplaceFactory,

    ):

        self.factory = factory

    # --------------------------------------------------
    # Marketplace
    # --------------------------------------------------

    def marketplace(

        self,

        name: str,

    ):

        return self.factory.get(name)

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    def create_product(

        self,

        marketplace: str,

        product: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.create_product(

            product

        )

    def update_product(

        self,

        marketplace: str,

        product: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.update_product(

            product

        )

    def delete_product(

        self,

        marketplace: str,

        product_id: str,

    ):

        client = self.marketplace(

            marketplace

        )

        return client.delete_product(

            product_id

        )

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    def sync_inventory(

        self,

        marketplace: str,

        product: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.sync_inventory(

            product

        )

    # --------------------------------------------------
    # Orders
    # --------------------------------------------------

    def get_orders(

        self,

        marketplace: str,

        **kwargs,

    ) -> List[Dict[str, Any]]:

        client = self.marketplace(

            marketplace

        )

        return client.get_orders(

            **kwargs

        )
    # --------------------------------------------------
    # Confirm Order
    # --------------------------------------------------

    def confirm_order(

        self,

        marketplace: str,

        order_id: str,

    ):

        client = self.marketplace(

            marketplace

        )

        return client.confirm_order(

            order_id

        )

    # --------------------------------------------------
    # Shipment
    # --------------------------------------------------

    def register_invoice(

        self,

        marketplace: str,

        shipment: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.register_invoice(

            shipment

        )

    # --------------------------------------------------
    # Cancel
    # --------------------------------------------------

    def cancel_order(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.cancel_order(

            order

        )

    # --------------------------------------------------
    # Return
    # --------------------------------------------------

    def return_order(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.return_order(

            order

        )

    # --------------------------------------------------
    # Exchange
    # --------------------------------------------------

    def exchange_order(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        client = self.marketplace(

            marketplace

        )

        return client.exchange_order(

            order

        )

    # --------------------------------------------------
    # Health Check
    # --------------------------------------------------

    def ping(

        self,

        marketplace: str,

    ) -> bool:

        client = self.marketplace(

            marketplace

        )

        return client.ping()

    # --------------------------------------------------
    # All Marketplace Health Check
    # --------------------------------------------------

    def ping_all(

        self,

    ) -> Dict[str, bool]:

        result = {}

        for name in self.factory.available():

            try:

                result[name] = self.marketplace(

                    name

                ).ping()

            except Exception:

                result[name] = False

        return result

    # --------------------------------------------------
    # Supported Marketplace
    # --------------------------------------------------

    def marketplaces(

        self,

    ) -> List[str]:

        return self.factory.available()