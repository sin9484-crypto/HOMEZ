"""
=========================================================
Homez OS

File : app/marketplace/coupang.py
Version : 2.0.0

Coupang Marketplace Adapter
=========================================================
"""

import hashlib
import hmac
import os
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from urllib.parse import urlencode

from app.marketplace.auth import MarketplaceAuth
from app.marketplace.client import MarketplaceClient
from app.marketplace.marketplace_base import MarketplaceBase


class CoupangMarketplace(MarketplaceBase):

    marketplace_name = "coupang"

    def __init__(

        self,

        client: MarketplaceClient,

        auth: MarketplaceAuth,

        access_key: str | None = None,

        secret_key: str | None = None,

        vendor_id: str | None = None,

    ):

        self.client = client

        self.auth = auth

        self.access_key = (
            access_key
            or os.getenv("COUPANG_ACCESS_KEY")
        )

        self.secret_key = (
            secret_key
            or os.getenv("COUPANG_SECRET_KEY")
        )

        self.vendor_id = (
            vendor_id
            or os.getenv("COUPANG_VENDOR_ID")
        )

    # --------------------------------------------------
    # Timestamp
    # --------------------------------------------------

    def _timestamp(self):

        return datetime.utcnow().strftime(
            "%y%m%dT%H%M%SZ"
        )

    # --------------------------------------------------
    # Signature
    # --------------------------------------------------

    def _signature(

        self,

        method: str,

        path: str,

        query: Dict | None = None,

    ):

        timestamp = self._timestamp()

        query_string = ""

        if query:

            query_string = urlencode(query)

            path = f"{path}?{query_string}"

        message = (

            timestamp

            + method.upper()

            + path

        )

        signature = hmac.new(

            self.secret_key.encode(),

            message.encode(),

            hashlib.sha256,

        ).hexdigest()

        authorization = (

            f"CEA algorithm=HmacSHA256, "

            f"access-key={self.access_key}, "

            f"signed-date={timestamp}, "

            f"signature={signature}"

        )

        return {

            "Authorization": authorization,

            "Content-Type": "application/json",

        }

    # --------------------------------------------------
    # Headers
    # --------------------------------------------------

    def _headers(

        self,

        method,

        path,

        query=None,

    ):

        headers = self._signature(

            method,

            path,

            query,

        )

        return headers

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    def create_product(

        self,

        product,

    ):

        raise NotImplementedError

    def update_product(

        self,

        product,

    ):

        raise NotImplementedError

    def delete_product(

        self,

        product_id,

    ):

        raise NotImplementedError

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    def sync_inventory(

        self,

        product,

    ):

        raise NotImplementedError
        # --------------------------------------------------
    # Order
    # --------------------------------------------------

    def get_orders(

        self,

        created_at_from=None,

        created_at_to=None,

        status=None,

    ) -> List[Dict[str, Any]]:

        path = "/v2/providers/openapi/apis/api/v4/vendors/ordersheets"

        query = {}

        if created_at_from:

            query["createdAtFrom"] = created_at_from

        if created_at_to:

            query["createdAtTo"] = created_at_to

        if status:

            query["status"] = status

        headers = self._headers(

            "GET",

            path,

            query,

        )

        return self.client.get(

            path,

            headers=headers,

            params=query,

        )

    # --------------------------------------------------
    # Confirm Order
    # --------------------------------------------------

    def confirm_order(

        self,

        order_sheet_id: str,

    ):

        path = (

            "/v2/providers/openapi/apis/api/v4/vendors/ordersheets/"

            f"{order_sheet_id}/acknowledgement"

        )

        headers = self._headers(

            "PUT",

            path,

        )

        return self.client.put(

            path,

            headers=headers,

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
    # Shipment
    # --------------------------------------------------

    def register_invoice(

        self,

        shipment,

    ):

        path = (

            "/v2/providers/openapi/apis/api/v4/vendors/"
            "invoice/uploadInvoice"

        )

        headers = self._headers(

            "POST",

            path,

        )

        return self.client.post(

            path,

            headers=headers,

            json=shipment,

        )

    def update_delivery(

        self,

        shipment,

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