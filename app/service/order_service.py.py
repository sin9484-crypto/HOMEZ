"""
=========================================================
Homez OS

File : app/services/order_service.py
Version : 1.0.0

Order Service
=========================================================
"""

from typing import Any
from typing import Dict
from typing import List

from app.services.marketplace_service import MarketplaceService


class OrderService:

    def __init__(

        self,

        repository,

        marketplace_service: MarketplaceService,

    ):

        self.repository = repository

        self.marketplace_service = marketplace_service

    # --------------------------------------------------
    # Local Order
    # --------------------------------------------------

    def create(

        self,

        order: Dict[str, Any],

    ):

        return self.repository.create(

            order

        )

    def update(

        self,

        order_id,

        order: Dict[str, Any],

    ):

        return self.repository.update(

            order_id,

            order,

        )

    def delete(

        self,

        order_id,

    ):

        return self.repository.delete(

            order_id

        )

    def get(

        self,

        order_id,

    ):

        return self.repository.get(

            order_id

        )

    def list(

        self,

    ):

        return self.repository.list()

    # --------------------------------------------------
    # Marketplace Orders
    # --------------------------------------------------

    def collect_orders(

        self,

        marketplace: str,

        **kwargs,

    ):

        return self.marketplace_service.get_orders(

            marketplace,

            **kwargs,

        )

    def confirm_order(

        self,

        marketplace: str,

        order_id: str,

    ):

        return self.marketplace_service.confirm_order(

            marketplace,

            order_id,

        )

    def register_invoice(

        self,

        marketplace: str,

        shipment: Dict[str, Any],

    ):

        return self.marketplace_service.register_invoice(

            marketplace,

            shipment,

        )
    # --------------------------------------------------
    # Collect All Orders
    # --------------------------------------------------

    def collect_all_orders(

        self,

        marketplaces: List[str],

        **kwargs,

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                result[marketplace] = (

                    self.marketplace_service.get_orders(

                        marketplace,

                        **kwargs,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Cancel Order
    # --------------------------------------------------

    def cancel_order(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        return self.marketplace_service.cancel_order(

            marketplace,

            order,

        )

    # --------------------------------------------------
    # Return Order
    # --------------------------------------------------

    def return_order(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        return self.marketplace_service.return_order(

            marketplace,

            order,

        )

    # --------------------------------------------------
    # Exchange Order
    # --------------------------------------------------

    def exchange_order(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        return self.marketplace_service.exchange_order(

            marketplace,

            order,

        )

    # --------------------------------------------------
    # Marketplace Health
    # --------------------------------------------------

    def marketplace_status(

        self,

    ) -> Dict[str, bool]:

        return self.marketplace_service.ping_all()

    # --------------------------------------------------
    # Supported Marketplaces
    # --------------------------------------------------

    def supported_marketplaces(

        self,

    ) -> List[str]:

        return self.marketplace_service.marketplaces()