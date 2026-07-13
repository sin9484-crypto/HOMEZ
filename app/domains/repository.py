"""
HOMEZ AI Commerce OS
Build 06

Product Repository
"""

from sqlalchemy.orm import Session

from app.shared.repositories.base_repository import BaseRepository
from app.models.product import Product


class ProductRepository(BaseRepository[Product]):

    def __init__(self, db: Session):
        super().__init__(db)

    def get_by_id(self, product_id: int):

        return (
            self.db.query(Product)
            .filter(Product.id == product_id)
            .first()
        )

    def get_all(self):

        return (
            self.db.query(Product)
            .order_by(Product.id.desc())
            .all()
        )

    def find_by_name(self, keyword: str):

        return (
            self.db.query(Product)
            .filter(Product.name.contains(keyword))
            .all()
        )

    def exists(self, product_name: str) -> bool:

        return (
            self.db.query(Product)
            .filter(Product.name == product_name)
            .first()
            is not None
        )