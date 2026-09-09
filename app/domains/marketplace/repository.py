"""
=========================================================
Homez OS

Marketplace Repository
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.marketplace.model import Marketplace


class MarketplaceRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    def get_all(self):

        return self.db.query(Marketplace).all()

    def get(
        self,
        marketplace_id: int,
    ):

        return (
            self.db.query(Marketplace)
            .filter(
                Marketplace.id == marketplace_id
            )
            .first()
        )

    def create(
        self,
        obj: Marketplace,
    ):

        self.db.add(obj)

        self.db.commit()

        self.db.refresh(obj)

        return obj

    def update(
        self,
        obj: Marketplace,
    ):

        self.db.commit()

        self.db.refresh(obj)

        return obj

    def delete(
        self,
        obj: Marketplace,
    ):

        self.db.delete(obj)

        self.db.commit()