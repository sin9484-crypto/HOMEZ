from __future__ import annotations

from app.domains.inventory.model import Inventory
from app.domains.product.model import Product


class ProductPolicy:
    """
    Product Domain Policy

    상품 등록, 판매, 수정,
    노출 정책 관리
    """

    @staticmethod
    def can_view(
        product: Product,
    ) -> bool:

        return (
            product.is_active
            and product.status == "ACTIVE"
        )


    @staticmethod
    def can_sell(
        product: Product,
        inventory: Inventory,
        quantity: int = 1,
    ) -> bool:
        """
        판매 가능 여부.

        Product는 카탈로그 상태만 판정하고,
        재고는 Inventory.available_quantity를 SoT로 사용한다.
        """

        if not (
            product.is_active
            and product.status == "ACTIVE"
        ):
            return False

        return (
            inventory.is_active
            and inventory.deleted_at is None
            and inventory.available_quantity >= quantity
        )


    @staticmethod
    def can_update(
        product: Product,
    ) -> bool:

        return (
            product.status != "DELETED"
        )
    @staticmethod
    def can_change_price(
        product: Product,
    ) -> bool:

        return (
            product.status != "DELETED"
        )


    @staticmethod
    def can_delete(
        product: Product,
    ) -> bool:

        return (
            product.sales_count == 0
            and product.status != "ACTIVE_SALE"
        )


    @staticmethod
    def can_recommend(
        product: Product,
    ) -> bool:

        return (
            product.is_active
            and product.ai_score is not None
            and product.status == "ACTIVE"
        )
    @staticmethod
    def validate_status(
        status: str,
    ) -> bool:

        allowed_status = {
            "ACTIVE",
            "INACTIVE",
            "SOLD_OUT",
            "PENDING",
            "DELETED",
        }

        return (
            status in allowed_status
        )


    @staticmethod
    def validate_price(
        price: float,
    ) -> bool:

        return (
            price >= 0
        )


    @staticmethod
    def has_margin(
        product: Product,
    ) -> bool:

        if product.cost_price is None:
            return False

        return (
            product.price
            >
            product.cost_price
        )


__all__ = [
    "ProductPolicy",
]
