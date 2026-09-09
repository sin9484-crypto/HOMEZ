"""
=========================================================
Homez OS

Marketplace Service
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.base_service import BaseService

from app.domains.marketplace.model import Marketplace
from app.domains.marketplace.repository import MarketplaceRepository
from app.domains.marketplace.schema import (
    MarketplaceCreate,
    MarketplaceUpdate,
)


class MarketplaceService(BaseService[MarketplaceRepository]):

    def __init__(
        self,
        db: Session,
    ):

        super().__init__(
            db=db,
            repository=MarketplaceRepository(db),
        )

    def get_all(self):

        return self.repository.get_all()

    def get(
        self,
        marketplace_id: int,
    ):

        return self.repository.get(
            marketplace_id
        )

    def create(
        self,
        marketplace: MarketplaceCreate,
    ):

        obj = Marketplace(
            **marketplace.model_dump()
        )

        return self.repository.create(obj)

    def update(
        self,
        marketplace_id: int,
        marketplace: MarketplaceUpdate,
    ):

        obj = self.repository.get(
            marketplace_id
        )

        if obj is None:
            return None

        values = marketplace.model_dump(
            exclude_unset=True
        )

        for key, value in values.items():

            setattr(
                obj,
                key,
                value,
            )

        return self.repository.update(obj)

    def delete(
        self,
        marketplace_id: int,
    ):

        obj = self.repository.get(
            marketplace_id
        )

        if obj is None:
            return False

        self.repository.delete(obj)

        return True