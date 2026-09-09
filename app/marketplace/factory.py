"""
=========================================================
Homez OS

File : app/marketplace/factory.py
Version : 1.0.0

Marketplace Factory
=========================================================
"""

from typing import Dict
from typing import Type

from app.marketplace.marketplace_base import MarketplaceBase
from app.marketplace.exceptions import InvalidMarketplaceError


class MarketplaceFactory:

    _registry: Dict[str, Type[MarketplaceBase]] = {}

    @classmethod
    def register(
        cls,
        marketplace: str,
        adapter: Type[MarketplaceBase],
    ):

        cls._registry[
            marketplace.lower()
        ] = adapter

    @classmethod
    def unregister(
        cls,
        marketplace: str,
    ):

        cls._registry.pop(
            marketplace.lower(),
            None,
        )

    @classmethod
    def exists(
        cls,
        marketplace: str,
    ) -> bool:

        return (
            marketplace.lower()
            in cls._registry
        )

    @classmethod
    def supported_marketplaces(
        cls,
    ):

        return sorted(
            cls._registry.keys()
        )

    @classmethod
    def create(
        cls,
        marketplace: str,
        *args,
        **kwargs,
    ) -> MarketplaceBase:

        adapter = cls._registry.get(
            marketplace.lower()
        )

        if adapter is None:

            raise InvalidMarketplaceError(
                f"{marketplace} is not registered."
            )

        return adapter(
            *args,
            **kwargs,
        )