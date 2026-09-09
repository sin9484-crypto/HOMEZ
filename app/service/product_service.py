"""
=========================================================
Homez OS

File : app/services/product_service.py
Version : 1.0.0

Product Service
=========================================================
"""

from typing import Any
from typing import Dict
from typing import List

from app.marketplace.marketplace_base import MarketplaceBase
from app.services.marketplace_service import MarketplaceService


class ProductService:

    def __init__(

        self,

        repository,

        marketplace_service: MarketplaceService,

    ):

        self.repository = repository

        self.marketplace_service = marketplace_service

    # --------------------------------------------------
    # CRUD
    # --------------------------------------------------

    def create(

        self,

        product: Dict[str, Any],

    ):

        return self.repository.create(

            product

        )

    def update(

        self,

        product_id,

        product: Dict[str, Any],

    ):

        return self.repository.update(

            product_id,

            product,

        )

    def delete(

        self,

        product_id,

    ):

        return self.repository.delete(

            product_id

        )

    def get(

        self,

        product_id,

    ):

        return self.repository.get(

            product_id

        )

    def list(

        self,

    ) -> List:

        return self.repository.list()

    # --------------------------------------------------
    # Marketplace Sync
    # --------------------------------------------------

    def publish(

        self,

        marketplace: str,

        product: Dict[str, Any],

    ):

        return self.marketplace_service.create_product(

            marketplace,

            product,

        )

    def update_marketplace(

        self,

        marketplace: str,

        product: Dict[str, Any],

    ):

        return self.marketplace_service.update_product(

            marketplace,

            product,

        )

    def delete_marketplace(

        self,

        marketplace: str,

        product_id: str,

    ):

        return self.marketplace_service.delete_product(

            marketplace,

            product_id,

        )
    # --------------------------------------------------
    # Publish All
    # --------------------------------------------------

    def publish_all(

        self,

        marketplaces: List[str],

        product: Dict[str, Any],

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                result[marketplace] = (

                    self.marketplace_service.create_product(

                        marketplace,

                        product,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Update All
    # --------------------------------------------------

    def update_all(

        self,

        marketplaces: List[str],

        product: Dict[str, Any],

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                result[marketplace] = (

                    self.marketplace_service.update_product(

                        marketplace,

                        product,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Delete All
    # --------------------------------------------------

    def delete_all(

        self,

        marketplaces: List[str],

        product_id: str,

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                result[marketplace] = (

                    self.marketplace_service.delete_product(

                        marketplace,

                        product_id,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Sync Inventory
    # --------------------------------------------------

    def sync_inventory(

        self,

        marketplaces: List[str],

        product: Dict[str, Any],

    ) -> Dict[str, Any]:

        result = {}

        for marketplace in marketplaces:

            try:

                result[marketplace] = (

                    self.marketplace_service.sync_inventory(

                        marketplace,

                        product,

                    )

                )

            except Exception as e:

                result[marketplace] = {

                    "success": False,

                    "error": str(e),

                }

        return result

    # --------------------------------------------------
    # Marketplace Status
    # --------------------------------------------------

    def marketplace_status(

        self,

    ):

        return self.marketplace_service.ping_all()