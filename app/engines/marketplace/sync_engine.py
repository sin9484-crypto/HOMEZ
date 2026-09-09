"""
=========================================================
Homez OS

File : app/engines/marketplace/sync_engine.py
Version : 1.0.0

Marketplace Sync Engine
=========================================================
"""

from abc import ABC
from abc import abstractmethod

from sqlalchemy.orm import Session


class MarketplaceSyncEngine(ABC):

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Order
    # --------------------------------------------------

    @abstractmethod
    def sync_order(
        self,
        order,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Shipment
    # --------------------------------------------------

    @abstractmethod
    def sync_shipment(
        self,
        shipment,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Inventory
    # --------------------------------------------------

    @abstractmethod
    def sync_inventory(
        self,
        inventory,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Product
    # --------------------------------------------------

    @abstractmethod
    def sync_product(
        self,
        product,
    ):
        raise NotImplementedError

    # --------------------------------------------------
    # Cancel
    # --------------------------------------------------

    @abstractmethod
    def cancel_order(
        self,
        order,
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