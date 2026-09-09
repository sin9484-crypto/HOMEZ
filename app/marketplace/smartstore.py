"""
=========================================================
Homez OS

File : app/marketplace/smartstore.py
Version : 2.0.0

Naver SmartStore Marketplace Adapter
=========================================================
"""

import os
from typing import Any
from typing import Dict
from typing import List

from app.marketplace.auth import MarketplaceAuth
from app.marketplace.client import MarketplaceClient
from app.marketplace.marketplace_base import MarketplaceBase


class SmartStoreMarketplace(MarketplaceBase):

    marketplace_name = "smartstore"

    def __init__(

        self,

        client: MarketplaceClient,

        auth: MarketplaceAuth,

        client_id: str | None = None,

        client_secret: str | None = None,

    ):

        self.client = client

        self.auth = auth

        self.client_id = (
            client_id
            or os.getenv("NAVER_CLIENT_ID")
        )

        self.client_secret = (
            client_secret
            or os.getenv("NAVER_CLIENT_SECRET")
        )

    # --------------------------------------------------
    # Token
    # --------------------------------------------------

    def _access_token(self):

        token = self.auth.get_token(
            "smartstore"
        )

        if not token:

            raise RuntimeError(
                "SmartStore Token Not Found"
            )

        return token

    # --------------------------------------------------
    # Headers
    # --------------------------------------------------

    def _headers(self):

        return {

            "Authorization":
                f"Bearer {self._access_token()}",

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

        product,

    ):

        path = "/external/v1/products"

        return self.client.post(

            path,

            headers=self._headers(),

            json=product,

        )

    def update_product(

        self,

        product,

    ):

        product_no = product["productNo"]

        path = (

            "/external/v1/products/"

            f"{product_no}"

        )

        return self.client.put(

            path,

            headers=self._headers(),

            json=product,

        )

    def delete_product(

        self,

        product_no,

    ):

        path = (

            "/external/v1/products/"

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

        product,

    ):

        product_no = product["productNo"]

        path = (

            "/external/v1/products/"

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

        path = "/external/v1/pay-order/seller/product-orders"

        params = {}

        if from_date:

            params["from"] = from_date

        if to_date:

            params["to"] = to_date

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

        product_order_id: str,

    ):

        path = (

            "/external/v1/pay-order/seller/product-orders/"

            f"{product_order_id}/dispatch"

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

        path = "/external/v1/pay-order/seller/dispatch"

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