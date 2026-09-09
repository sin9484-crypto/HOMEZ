"""
=========================================================
Homez OS

File : app/services/shipment_service.py
Version : 1.0.0

Shipment Service
=========================================================
"""

from typing import Any
from typing import Dict
from typing import List

from app.services.marketplace_service import MarketplaceService


class ShipmentService:

    def __init__(

        self,

        repository,

        marketplace_service: MarketplaceService,

    ):

        self.repository = repository

        self.marketplace_service = marketplace_service

    # --------------------------------------------------
    # Local Shipment
    # --------------------------------------------------

    def create(

        self,

        shipment: Dict[str, Any],

    ):

        return self.repository.create(

            shipment

        )

    def update(

        self,

        shipment_id,

        shipment: Dict[str, Any],

    ):

        return self.repository.update(

            shipment_id,

            shipment,

        )

    def delete(

        self,

        shipment_id,

    ):

        return self.repository.delete(

            shipment_id

        )

    def get(

        self,

        shipment_id,

    ):

        return self.repository.get(

            shipment_id

        )

    def list(

        self,

    ):

        return self.repository.list()

    # --------------------------------------------------
    # Marketplace Shipment
    # --------------------------------------------------

    def register_invoice(

        self,

        marketplace: str,

        shipment: Dict[str, Any],

    ):

        return self.marketplace_service.register_invoice(

            marketplace,

            shipment,

        )

    def marketplace_status(

        self,

    ):

        return self.marketplace_service.ping_all()
    # --------------------------------------------------
    # Register Invoice (All)
    # --------------------------------------------------

    def register_invoice_all(

        self,

        marketplaces: List[str],

        shipment: Dict[str, Any],

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                result[marketplace] = (

                    self.marketplace_service.register_invoice(

                        marketplace,

                        shipment,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Update Delivery
    # --------------------------------------------------

    def update_delivery(

        self,

        marketplace: str,

        shipment: Dict[str, Any],

    ):

        client = self.marketplace_service.marketplace(

            marketplace,

        )

        return client.update_delivery(

            shipment,

        )

    # --------------------------------------------------
    # Update Delivery (All)
    # --------------------------------------------------

    def update_delivery_all(

        self,

        marketplaces: List[str],

        shipment: Dict[str, Any],

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                client = self.marketplace_service.marketplace(

                    marketplace,

                )

                result[marketplace] = (

                    client.update_delivery(

                        shipment,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Marketplace Health
    # --------------------------------------------------

    def health(

        self,

        marketplace: str,

    ) -> bool:

        return self.marketplace_service.ping(

            marketplace,

        )

    # --------------------------------------------------
    # Marketplace Health (All)
    # --------------------------------------------------

    def health_all(

        self,

    ) -> Dict[str, bool]:

        return self.marketplace_service.ping_all()

    # --------------------------------------------------
    # Supported Marketplace
    # --------------------------------------------------

    def supported_marketplaces(

        self,

    ) -> List[str]:

        return self.marketplace_service.marketplaces()