"""
=========================================================
Homez OS

File : app/services/purchase_service.py
Version : 1.0.0

Purchase Service
=========================================================
"""

from typing import Any
from typing import Dict
from typing import List

from app.services.marketplace_service import MarketplaceService


class PurchaseService:

    def __init__(

        self,

        repository,

        marketplace_service: MarketplaceService,

    ):

        self.repository = repository

        self.marketplace_service = marketplace_service

    # --------------------------------------------------
    # Purchase CRUD
    # --------------------------------------------------

    def create(

        self,

        purchase: Dict[str, Any],

    ):

        return self.repository.create(

            purchase

        )

    def update(

        self,

        purchase_id,

        purchase: Dict[str, Any],

    ):

        return self.repository.update(

            purchase_id,

            purchase,

        )

    def delete(

        self,

        purchase_id,

    ):

        return self.repository.delete(

            purchase_id

        )

    def get(

        self,

        purchase_id,

    ):

        return self.repository.get(

            purchase_id

        )

    def list(

        self,

    ):

        return self.repository.list()

    # --------------------------------------------------
    # Supplier Purchase
    # --------------------------------------------------

    def purchase(

        self,

        marketplace: str,

        order: Dict[str, Any],

    ):

        client = self.marketplace_service.marketplace(

            marketplace,

        )

        return client.create_order(

            order,

        )

    # --------------------------------------------------
    # Purchase Status
    # --------------------------------------------------

    def status(

        self,

        marketplace: str,

        purchase_id: str,

    ):

        client = self.marketplace_service.marketplace(

            marketplace,

        )

        return client.get_purchase_status(

            purchase_id,

        )
    # --------------------------------------------------
    # Purchase All
    # --------------------------------------------------

    def purchase_all(

        self,

        marketplaces: List[str],

        order: Dict[str, Any],

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                client = self.marketplace_service.marketplace(

                    marketplace,

                )

                result[marketplace] = (

                    client.create_order(

                        order,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Purchase Status (All)
    # --------------------------------------------------

    def status_all(

        self,

        marketplaces: List[str],

        purchase_id: str,

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                client = self.marketplace_service.marketplace(

                    marketplace,

                )

                result[marketplace] = (

                    client.get_purchase_status(

                        purchase_id,

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