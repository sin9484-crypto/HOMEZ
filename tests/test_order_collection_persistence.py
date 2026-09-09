from __future__ import annotations

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.order.collection_model import (
    OrderChannelFulfillment, UnresolvedOrderItem,
)
from app.domains.order.collection_persistence import (
    CoupangOrderCollectionPersistence,
)
from app.domains.order.coupang_normalizer import normalize_coupang_order
from tests.test_coupang_order_normalizer import item, order


class OrderCollectionPersistenceTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(
            self.engine,
            tables=[OrderChannelFulfillment.__table__, UnresolvedOrderItem.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.service = CoupangOrderCollectionPersistence(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def normalized(self, **overrides):
        return normalize_coupang_order(order(**overrides))

    def test_same_order_with_two_shipment_boxes_is_preserved(self):
        first, first_created = self.service.upsert_fulfillment(
            1, 10, self.normalized(orderId=22, shipmentBoxId=11),
        )
        second, second_created = self.service.upsert_fulfillment(
            1, 10, self.normalized(orderId=22, shipmentBoxId=12),
        )
        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(self.db.query(OrderChannelFulfillment).count(), 2)

    def test_recollection_updates_status_without_duplicate(self):
        first, _ = self.service.upsert_fulfillment(
            1, 10, self.normalized(status="ACCEPT"),
        )
        second, created = self.service.upsert_fulfillment(
            1, 10, self.normalized(status="INSTRUCT"),
        )
        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.raw_status, "INSTRUCT")

    def test_identity_isolated_by_company_and_connection(self):
        ids = set()
        for company, connection in ((1, 10), (2, 10), (1, 11)):
            record, _ = self.service.upsert_fulfillment(
                company, connection, self.normalized(),
            )
            ids.add(record.id)
        self.assertEqual(len(ids), 3)

    def test_unresolved_items_are_preserved_idempotently(self):
        fulfillment, _ = self.service.upsert_fulfillment(1, 10, self.normalized())
        saved, created = self.service.preserve_unresolved_items(
            1, fulfillment.id, self.normalized(),
        )
        again, created_again = self.service.preserve_unresolved_items(
            1, fulfillment.id, self.normalized(),
        )
        self.assertEqual(created, 1)
        self.assertEqual(created_again, 0)
        self.assertEqual(saved[0].id, again[0].id)

    def test_zero_fulfillable_quantity_is_kept_but_not_actionable(self):
        normalized = self.normalized(
            orderItems=[item(shippingCount=2, holdCountForCancel=1, cancelCount=1)],
        )
        fulfillment, _ = self.service.upsert_fulfillment(1, 10, normalized)
        saved, _ = self.service.preserve_unresolved_items(1, fulfillment.id, normalized)
        self.assertEqual(saved[0].quantity, 0)
        self.assertEqual(saved[0].status, "NO_FULFILLABLE_QUANTITY")

    def test_company_list_isolation(self):
        for company in (1, 2):
            normalized = self.normalized(shipmentBoxId=10 + company)
            fulfillment, _ = self.service.upsert_fulfillment(company, 10, normalized)
            self.service.preserve_unresolved_items(company, fulfillment.id, normalized)
        self.assertEqual(len(self.service.list_unresolved(1)), 1)
        self.assertEqual(self.service.list_unresolved(1)[0].company_id, 1)


if __name__ == "__main__":
    unittest.main()
