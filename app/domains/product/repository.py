"""
HOMEZ AI Commerce OS
Build 06

Product Repository
"""

from sqlalchemy.orm import Session

from app.shared.repositories.base_repository import BaseRepository
from app.domains.product.model import Product


class ProductRepository(BaseRepository[Product]):

    def __init__(self, db: Session):
        super().__init__(db)

    def get_by_id(self, product_id: int):
        """
        상품 ID 조회
        """
        return (
            self.db.query(Product)
            .filter(Product.id == product_id)
            .first()
        )

    def get_all(self):
        """
        전체 상품 조회
        """
        return (
            self.db.query(Product)
            .order_by(Product.id.desc())
            .all()
        )

    def find_by_name(self, keyword: str):
        """
        상품명 검색
        """
        return (
            self.db.query(Product)
            .filter(Product.name.contains(keyword))
            .all()
        )

    def find_duplicate(
        self,
        name: str,
        supplier: str,
    ):
        """
        동일 공급처의 동일 상품 중복 검사
        """
        return (
            self.db.query(Product)
            .filter(
                Product.name == name,
                Product.supplier == supplier,
            )
            .first()
        )

    def get_active_products(self):
        """
        활성 상품 조회
        """
        return (
            self.db.query(Product)
            .filter(Product.is_active.is_(True))
            .all()
        )