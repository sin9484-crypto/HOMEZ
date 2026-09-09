"""
=========================================================
Homez OS

File : app/marketplace/elevenst.py
Version : 2.0.0

11st Marketplace Adapter
=========================================================
"""

import os
from typing import Any
from typing import Dict
from typing import List

from app.marketplace.auth import MarketplaceAuth
from app.marketplace.client import MarketplaceClient
from app.marketplace.marketplace_base import MarketplaceBase


class ElevenSTMarketplace(MarketplaceBase):

    marketplace_name = "11st"

    def __init__(

        self,

        client: MarketplaceClient,

        auth: MarketplaceAuth,

        api_key: str | None = None,

    ):

        self.client = client

        self.auth = auth

        self.api_key = (

            api_key

            or os.getenv("ELEVENST_API_KEY")

        )

    # --------------------------------------------------
    # Headers
    # --------------------------------------------------

    def _headers(self):

        return {

            "openapikey": self.api_key,

            "Content-Type": "application/json",

            "Accept": "application/json",

        }

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    def create_product(

        self,

        product: Dict[str, Any],

    ):

        path = "/prodservices/product"

        return self.client.post(

            path,

            headers=self._headers(),

            json=product,

        )

    def update_product(

        self,

        product: Dict[str, Any],

    ):

        product_no = product["productNo"]

        path = (

            "/prodservices/product/"

            f"{product_no}"

        )

        return self.client.put(

            path,

            headers=self._headers(),

            json=product,

        )

    def delete_product(

        self,

        product_no: str,

    ):

        path = (

            "/prodservices/product/"

            f"{product_no}"

        )

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

        product_no = product["productNo"]

        path = (

            "/prodservices/product/"

            f"{product_no}/stock"

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

        path = "/ordservices/orders"

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

        order_no: str,

    ):

        path = (

            "/ordservices/orders/"

            f"{order_no}/confirm"

        )

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

        path = "/ordservices/delivery"

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