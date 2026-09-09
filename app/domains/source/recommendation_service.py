"""
=========================================================
Homez OS

File : app/domains/source/recommendation_service.py

source → purchase 연결(2026-08-20 CTO Section 3). PurchaseCreate가
이미 supplier_id/unit_cost를 알고 있다고 가정하던 공백을 메운다 —
OrderItem(OUT_OF_STOCK) → InventorySku → product_candidate_id →
SupplierProductLink(가장 저렴한 활성 연결) → PurchaseService.
create_purchase()로 그대로 넘긴다. 기존 Purchase/Inventory/Order
Service를 재사용하고 로직을 복제하지 않는다.

"발주안"은 새 테이블을 만들지 않는다 — 기존 Purchase.status=
REQUESTED 자체가 발주안(승인 대기) 상태이고, 사용자 승인은 기존
PurchaseService.confirm_purchase()가 그대로 담당한다(요구사항
Section 3의 "발주안 생성 → 사용자 승인"은 이미 그 두 상태로
표현되어 있다).
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.inventory.repository import InventoryRepository
from app.domains.order.constants import OrderItemStatus
from app.domains.order.repository import OrderRepository
from app.domains.purchase.model import Purchase
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.schema import PurchaseItemCreate
from app.domains.purchase.service import PurchaseService
from app.domains.source.service import SourceService


class SourcingRecommendationService:

    def __init__(self, db: Session):

        self.db = db
        self._order_repository = OrderRepository(db)
        self._inventory_repository = InventoryRepository(db)
        self._source_service = SourceService(db)
        self._purchase_service = PurchaseService(db)

    def create_purchase_proposal_for_order_item(
        self, order_item_id: int, company_id: int, triggered_by: int,
        idempotency_key: str,
    ) -> Purchase:
        """
        재고 부족(OUT_OF_STOCK) 주문 품목 1건에 대해, 가장 저렴한
        활성 SupplierProductLink를 찾아 발주안(Purchase, status=
        REQUESTED)을 생성한다. 후보가 없으면 BadRequestException
        (SUPPLIER_REQUIRED)을 던진다 — 임의로 대체 공급처를 지어내지
        않는다.
        """

        order_item = self._order_repository.get_item_for_company(
            order_item_id, company_id,
        )
        if order_item is None:
            raise NotFoundException("주문 품목을 찾을 수 없습니다.")

        if order_item.status != OrderItemStatus.OUT_OF_STOCK:
            raise BadRequestException(
                "재고 부족(OUT_OF_STOCK) 상태의 주문 품목에만 발주안을 "
                f"생성할 수 있습니다 — 현재 상태: {order_item.status}",
            )

        sku = self._inventory_repository.get_sku_for_company(
            order_item.inventory_sku_id, company_id,
        )
        if sku is None:
            raise NotFoundException("연결된 재고 SKU를 찾을 수 없습니다.")

        best_link = self._source_service.get_best_link(
            sku.product_candidate_id, company_id,
        )
        if best_link is None:
            raise BadRequestException(
                "SUPPLIER_REQUIRED: 이 상품에 연결된 활성 공급처가 없습니다 "
                "— 먼저 공급처 검색·연결(SourceService.create_link)을 "
                "완료하세요.",
            )

        return self._purchase_service.create_purchase(
            company_id,
            PurchaseCreate(
                order_id=order_item.order_id,
                supplier_id=best_link.supplier_id,
                items=[
                    PurchaseItemCreate(
                        order_item_id=order_item.id,
                        unit_cost=best_link.unit_cost,
                    ),
                ],
                idempotency_key=idempotency_key,
                memo=(
                    f"자동 소싱 추천(SupplierProductLink #{best_link.id}, "
                    f"SKU={best_link.supplier_sku})"
                ),
            ),
            triggered_by=triggered_by,
        )


__all__ = [
    "SourcingRecommendationService",
]
