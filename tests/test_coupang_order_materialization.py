from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundException
from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.inventory.model import InventorySku
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.collection_persistence import CoupangOrderCollectionPersistence
from app.domains.order.coupang_normalizer import normalize_coupang_order
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.order.order_materialization import CoupangOrderMaterializationService
from app.domains.order.order_materialization import decimal_to_legacy_float
from tests.test_coupang_order_normalizer import order


class CoupangOrderMaterializationTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine, tables=[
            InventorySku.__table__, Order.__table__, OrderItem.__table__,
            OrderChannelFulfillment.__table__, UnresolvedOrderItem.__table__,
            OrderSkuResolution.__table__,
        ])
        self.db = sessionmaker(bind=self.engine)()
        self.service = CoupangOrderMaterializationService(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def staged(self):
        normalized = normalize_coupang_order(order())
        persistence = CoupangOrderCollectionPersistence(self.db)
        fulfillment, _ = persistence.upsert_fulfillment(1, 10, normalized)
        items, _ = persistence.preserve_unresolved_items(1, fulfillment.id, normalized)
        self.service.ensure_orders(1, 10, [normalized])
        self.db.refresh(fulfillment)
        return normalized, fulfillment, items[0]

    def test_creates_order_and_links_fulfillment_without_losing_identity(self):
        normalized, fulfillment, _ = self.staged()
        created = self.db.query(Order).one()
        self.assertEqual(created.channel_order_id, normalized.channel_order_id)
        self.assertEqual(created.channel_code, "COUPANG#10")
        self.assertEqual(fulfillment.order_id, created.id)
        self.assertEqual(created.buyer_name, "합성주문자")

    def test_resolve_creates_order_item_and_remembers_mapping(self):
        _, _, unresolved = self.staged()
        sku = InventorySku(
            company_id=1, product_candidate_id=1, sku_code="HOMEZ-SKU-1",
            option_label="기본", available_qty=0, reserved_qty=0,
            safety_stock=0, is_active=True,
        )
        self.db.add(sku)
        self.db.commit()
        self.db.refresh(sku)
        with patch(
            "app.domains.order.order_materialization.SafetyService.is_emergency_stop_active",
            return_value=True,
        ), patch(
            "app.domains.purchase_task.order_sync_service.PurchaseTaskOrderSyncService.sync_order",
        ) as sync_order:
            item = self.service.resolve_item(1, unresolved.id, sku.id, 9)
        self.assertEqual(item.inventory_sku_id, sku.id)
        self.db.refresh(unresolved)
        self.assertEqual(unresolved.status, "RESOLVED")
        self.assertEqual(unresolved.resolved_order_item_id, item.id)
        mapping = self.db.query(OrderSkuResolution).one()
        self.assertEqual(mapping.channel_sku, unresolved.channel_sku)
        sync_order.assert_not_called()
        self.assertEqual(self.db.query(UnresolvedOrderItem).filter(
            UnresolvedOrderItem.status == "MAPPING_REQUIRED",
        ).count(), 0)

    def test_other_company_inventory_sku_is_rejected(self):
        _, _, unresolved = self.staged()
        sku = InventorySku(
            company_id=2, product_candidate_id=1, sku_code="OTHER-SKU",
            option_label="기본", available_qty=0, reserved_qty=0,
            safety_stock=0, is_active=True,
        )
        self.db.add(sku)
        self.db.commit()
        with self.assertRaises(NotFoundException):
            self.service.resolve_item(1, unresolved.id, sku.id, 9)

    def test_normal_resolution_retries_inventory_and_purchase_task(self):
        _, _, unresolved = self.staged()
        sku = InventorySku(
            company_id=1, product_candidate_id=1, sku_code="HOMEZ-SKU-2",
            option_label="기본", available_qty=0, reserved_qty=0,
            safety_stock=0, is_active=True,
        )
        self.db.add(sku)
        self.db.commit()
        self.db.refresh(sku)
        with patch(
            "app.domains.order.order_materialization.SafetyService.is_emergency_stop_active",
            return_value=False,
        ), patch(
            "app.domains.order.order_materialization.InventoryService.reserve",
            side_effect=BadRequestException("재고 부족"),
        ) as reserve, patch(
            "app.domains.purchase_task.order_sync_service.PurchaseTaskOrderSyncService.sync_order",
        ) as sync_order:
            item = self.service.resolve_item(1, unresolved.id, sku.id, 9)
        reserve.assert_called_once()
        sync_order.assert_called_once()
        self.assertEqual(item.status, "OUT_OF_STOCK")

    def test_auto_resolve_preserves_collection_actor_for_audit_chain(self):
        _, _, unresolved = self.staged()
        sku = InventorySku(
            company_id=1, product_candidate_id=1, sku_code="HOMEZ-SKU-AUTO",
            option_label="기본", available_qty=1, reserved_qty=0,
            safety_stock=0, is_active=True,
        )
        self.db.add(sku)
        self.db.commit()
        self.db.refresh(sku)
        self.db.add(OrderSkuResolution(
            company_id=1,
            store_connection_id=10,
            channel_sku=unresolved.channel_sku,
            inventory_sku_id=sku.id,
            created_by=77,
        ))
        self.db.commit()

        with patch.object(self.service, "resolve_item") as resolve_item:
            resolved = self.service.auto_resolve(
                1, 10, actor_user_id=77,
            )

        self.assertEqual(resolved, 1)
        resolve_item.assert_called_once_with(
            1,
            unresolved.id,
            sku.id,
            actor_user_id=77,
            remember_mapping=False,
        )

    def test_decimal_boundary_is_explicit_and_rejects_invalid_values(self):
        self.assertEqual(decimal_to_legacy_float(Decimal("10.12345")), 10.1235)
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-1")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decimal_to_legacy_float(value)


if __name__ == "__main__":
    unittest.main()
