"""
HOMEZ AI Commerce OS
Build 06

Product Service
"""

from sqlalchemy.orm import Session

from app.domains.product.repository import ProductRepository
from app.domains.product.policy import ProductPolicy


class ProductService:

    def __init__(self, db: Session):
        self.repository = ProductRepository(db)

    def get_products(self):
        return self.repository.get_all()

    def get_product(self, product_id: int):
        return self.repository.get_by_id(product_id)

    def search(self, keyword: str):
        return self.repository.find_by_name(keyword)

    def can_register(self, margin_rate: float, supplier_active: bool) -> bool:

        if not ProductPolicy.is_margin_valid(margin_rate):
            return False

        if not ProductPolicy.can_register(supplier_active):
            return False

        return True