"""
=========================================================
Homez OS

File : app/marketplace/aliexpress.py
Version : 2.0.0

AliExpress Marketplace Adapter
=========================================================
"""

import os
from typing import Any
from typing import Dict
from typing import List

from app.marketplace.auth import MarketplaceAuth
from app.marketplace.client import MarketplaceClient
from app.marketplace.marketplace_base import MarketplaceBase


class AliExpressMarketplace(MarketplaceBase):

    marketplace_name = "aliexpress"

    def __init__(

        self,

        client: MarketplaceClient,

        auth: MarketplaceAuth,

        app_key: str | None = None,

        app_secret: str | None = None,

        access_token: str | None = None,

    ):

        self.client = client

        self.auth = auth

        self.app_key = (

            app_key

            or os.getenv("ALIEXPRESS_APP_KEY")

        )

        self.app_secret = (

            app_secret

            or os.getenv("ALIEXPRESS_APP_SECRET")

        )

        self.access_token = (

            access_token

            or os.getenv("ALIEXPRESS_ACCESS_TOKEN")

        )

    # --------------------------------------------------
    # Headers
    # --------------------------------------------------

    def _headers(self):

        return {

            "Authorization":
                f"Bearer {self.access_token}",

            "Content-Type":
                "application/json",

            "Accept":
                "application/json",

        }

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    def create_product(

        self,

        product: Dict[str, Any],

    ):

        path = "/products"

        return self.client.post(

            path,

            headers=self._headers(),

            json=product,

        )

    def update_product(

        self,

        product: Dict[str, Any],

    ):

        product_id = product["productId"]

        path = f"/products/{product_id}"

        return self.client.put(

            path,

            headers=self._headers(),

            json=product,

        )

    def delete_product(

        self,

        product_id: str,

    ):

        path = f"/products/{product_id}"

        return self.client.delete(

            path,

            headers=self._headers(),

        )

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    def sync_inventory(

        self,

        product: Dict[str, Any],

    ):

        product_id = product["productId"]

        path = (

            f"/products/{product_id}/inventory"

        )

        return self.client.put(

            path,

            headers=self._headers(),

            json=product,

        )
        # --------------------------------------------------
    # Order
    # --------------------------------------------------

    def get_orders(

        self,

        from_date=None,

        to_date=None,

        status=None,

    ) -> List[Dict[str, Any]]:

        path = "/orders"

        params = {}

        if from_date:

            params["fromDate"] = from_date

        if to_date:

            params["toDate"] = to_date

        if status:

            params["status"] = status

        return self.client.get(

            path,

            headers=self._headers(),

            params=params,

        )

    # --------------------------------------------------
    # Confirm Order
    # --------------------------------------------------

    def confirm_order(

        self,

        order_id: str,

    ):

        path = f"/orders/{order_id}/confirm"

        return self.client.post(

            path,

            headers=self._headers(),

        )

    # --------------------------------------------------
    # Shipment
    # --------------------------------------------------

    def register_invoice(

        self,

        shipment: Dict[str, Any],

    ):

        path = "/shipments"

        return self.client.post(

            path,

            headers=self._headers(),

            json=shipment,

        )

    # --------------------------------------------------
    # Cancel
    # --------------------------------------------------

    def cancel_order(

        self,

        order,

    ):

        raise NotImplementedError

    # --------------------------------------------------
    # Return
    # --------------------------------------------------

    def return_order(

        self,

        order,

    ):

        raise NotImplementedError

    # --------------------------------------------------
    # Exchange
    # --------------------------------------------------

    def exchange_order(

        self,

        order,

    ):

        raise NotImplementedError

    # --------------------------------------------------
    # Health Check
    # --------------------------------------------------

    def ping(

        self,

    ) -> bool:

        try:

            result = self.get_orders()

            return result is not None

        except Exception:

            return False