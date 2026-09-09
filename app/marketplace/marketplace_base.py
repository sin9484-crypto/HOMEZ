"""
=========================================================
Homez OS

File : app/marketplace/marketplace_base.py
Version : 1.0.0

Marketplace Base Interface
=========================================================
"""

from abc import ABC
from abc import abstractmethod


class MarketplaceBase(ABC):

    @property
    @abstractmethod
    def marketplace_name(self) -> str:
        """Marketplace Name"""
        raise NotImplementedError

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    @abstractmethod
    def create_product(
        self,
        product,
    ):
        raise NotImplementedError

    @abstractmethod
    def update_product(
        self,
        product,
    ):
        raise NotImplementedError

    @abstractmethod
    def delete_product(
        self,
        product_id: str,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    @abstractmethod
    def sync_inventory(
        self,
        product,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Order
    # --------------------------------------------------

    @abstractmethod
    def get_orders(self):
        raise NotImplementedError

    @abstractmethod
    def confirm_order(
        self,
        order,
    ):
        raise NotImplementedError

    @abstractmethod
    def cancel_order(
        self,
        order,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Shipment
    # --------------------------------------------------

    @abstractmethod
    def register_invoice(
        self,
        shipment,
    ):
        raise NotImplementedError

    @abstractmethod
    def update_delivery(
        self,
        shipment,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Return
    # --------------------------------------------------

    @abstractmethod
    def return_order(
        self,
        order,
    ):
        raise NotImplementedError

    @abstractmethod
    def exchange_order(
        self,
        order,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Health Check
    # --------------------------------------------------

    @abstractmethod
    def ping(self):
        raise NotImplementedError