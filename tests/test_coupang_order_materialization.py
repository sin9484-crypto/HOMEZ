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
from app.domains.order.order_materialization import list_recovery_review_candidates
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

    # ---- 2026-09-17 Phase 7A 사후 감사 2차(결함 2, High) ----------------
    # 완료 상태 등 ACCEPT가 아닌 상태로 처음 발견된 주문이 신규 Order로
    # 잘못 생성되지 않는지, 대신 복구 검토 대상으로만 분류되는지 검증한다.

    def test_non_accept_status_for_unseen_order_becomes_recovery_candidate(self):
        for i, status in enumerate((
            "INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY", "NONE_TRACKING",
        )):
            with self.subTest(status=status):
                normalized = normalize_coupang_order(
                    order(status=status, orderId=9000 + i),
                )
                result = self.service.ensure_orders(1, 10, [normalized])
                self.assertEqual(
                    self.db.query(Order)
                    .filter(Order.channel_order_id == normalized.channel_order_id)
                    .count(),
                    0,
                )
                self.assertEqual(len(result.recovery_candidates), 1)
                candidate = result.recovery_candidates[0]
                self.assertEqual(candidate.observed_status, status)
                self.assertTrue(candidate.requires_user_approval)
                self.assertEqual(result.rejected_unknown_status_count, 0)

    def test_recovery_candidate_does_not_create_purchase_task_or_order_item(self):
        normalized = normalize_coupang_order(order(status="FINAL_DELIVERY", orderId=9100))
        self.service.ensure_orders(1, 10, [normalized])
        self.assertEqual(self.db.query(Order).count(), 0)
        self.assertEqual(self.db.query(OrderItem).count(), 0)

    def test_recovery_candidate_channel_order_id_is_masked(self):
        normalized = normalize_coupang_order(
            order(status="FINAL_DELIVERY", orderId=123456789),
        )
        result = self.service.ensure_orders(1, 10, [normalized])
        candidate = result.recovery_candidates[0]
        self.assertNotEqual(candidate.masked_channel_order_id, "123456789")
        self.assertTrue(candidate.masked_channel_order_id.startswith("*"))
        self.assertTrue(candidate.masked_channel_order_id.endswith("6789"))

    def test_existing_order_updates_normally_regardless_of_later_status(self):
        first = normalize_coupang_order(order(orderId=9200))
        self.service.ensure_orders(1, 10, [first])
        created = (
            self.db.query(Order).filter(Order.channel_order_id == "9200").one()
        )

        second = normalize_coupang_order(order(orderId=9200, status="INSTRUCT"))
        result = self.service.ensure_orders(1, 10, [second])

        self.assertEqual(
            self.db.query(Order).filter(Order.channel_order_id == "9200").count(), 1,
        )
        self.assertEqual(result.recovery_candidates, ())
        self.assertIn("9200", result.orders)
        self.assertEqual(result.orders["9200"].id, created.id)

    def test_unknown_raw_status_is_rejected_fail_closed(self):
        normalized = normalize_coupang_order(
            order(status="RETURNED_UNKNOWN_STATE", orderId=9300),
        )
        result = self.service.ensure_orders(1, 10, [normalized])
        self.assertEqual(self.db.query(Order).count(), 0)
        self.assertEqual(result.rejected_unknown_status_count, 1)
        self.assertEqual(result.recovery_candidates, ())

    def test_multiple_items_are_summed_into_total_amount(self):
        from tests.test_coupang_order_normalizer import item

        normalized = normalize_coupang_order(order(orderId=9500, orderItems=[
            item(sequenceNo="001", vendorItemId=1),
            item(sequenceNo="002", vendorItemId=2),
        ]))
        self.service.ensure_orders(1, 10, [normalized])
        created = self.db.query(Order).filter(Order.channel_order_id == "9500").one()
        expected_total = sum(
            (i.order_price.amount for i in normalized.items), Decimal("0"),
        )
        self.assertEqual(created.total_amount, decimal_to_legacy_float(expected_total))

    def test_mixed_batch_creates_eligible_order_and_defers_ineligible_one(self):
        """한 번의 ensure_orders() 호출 안에 신규생성 가능한 주문과
        불가능한 주문이 섞여 있어도 서로 영향을 주지 않는다."""

        eligible = normalize_coupang_order(order(orderId=9600, status="ACCEPT"))
        ineligible = normalize_coupang_order(order(orderId=9601, status="DELIVERING"))
        result = self.service.ensure_orders(1, 10, [eligible, ineligible])

        self.assertIn("9600", result.orders)
        self.assertEqual(
            self.db.query(Order).filter(Order.channel_order_id == "9600").count(), 1,
        )
        self.assertEqual(
            self.db.query(Order).filter(Order.channel_order_id == "9601").count(), 0,
        )
        self.assertEqual(len(result.recovery_candidates), 1)
        self.assertEqual(result.recovery_candidates[0].observed_status, "DELIVERING")

    def test_recovery_candidate_fulfillment_is_not_auto_resolved(self):
        """복구 검토 대상은 order_id가 없는 채로 남는다 — 이 fulfillment를
        auto_resolve()가 건드리면 resolve_item()의 "연결할 HOMEZ 주문이
        아직 준비되지 않았습니다" 예외로 배치 전체가 죽는 연쇄 결함이
        있었다(이번 라운드에서 발견·수정). 미리 SKU 매핑이 있어도
        건드리지 않아야 한다."""

        normalized = normalize_coupang_order(order(status="FINAL_DELIVERY", orderId=9400))
        persistence = CoupangOrderCollectionPersistence(self.db)
        fulfillment, _ = persistence.upsert_fulfillment(1, 10, normalized)
        items, _ = persistence.preserve_unresolved_items(1, fulfillment.id, normalized)

        result = self.service.ensure_orders(1, 10, [normalized])
        self.db.refresh(fulfillment)
        self.assertIsNone(fulfillment.order_id)
        self.assertEqual(len(result.recovery_candidates), 1)

        self.db.add(OrderSkuResolution(
            company_id=1, store_connection_id=10,
            channel_sku=items[0].channel_sku, inventory_sku_id=999999,
            created_by=1,
        ))
        self.db.commit()

        resolved = self.service.auto_resolve(1, 10, actor_user_id=1)
        self.assertEqual(resolved, 0)
        self.db.refresh(items[0])
        self.assertEqual(items[0].status, "MAPPING_REQUIRED")

    # ---- 2026-09-18 Phase 7B 진입 전 잔여 4항목 확인(항목 3) ----------------
    # 복구 후보가 응답에만 존재하지 않고 DB에 지속적으로 보존되며,
    # 재조회·재시작 후에도 다시 식별 가능하고 중복되지 않는지 검증한다.

    def test_recovery_candidate_is_reidentifiable_via_dedicated_query(self):
        normalized = normalize_coupang_order(
            order(status="FINAL_DELIVERY", orderId=9700, shipmentBoxId=87001234),
        )
        CoupangOrderCollectionPersistence(self.db).upsert_fulfillment(1, 10, normalized)
        result = self.service.ensure_orders(1, 10, [normalized])
        response_candidate = result.recovery_candidates[0]

        queried = list_recovery_review_candidates(self.db, 1)
        self.assertEqual(len(queried), 1)
        stored_candidate = queried[0]
        self.assertEqual(stored_candidate.company_id, 1)
        self.assertEqual(stored_candidate.store_connection_id, 10)
        self.assertEqual(stored_candidate.observed_status, "FINAL_DELIVERY")
        self.assertEqual(stored_candidate.reason, response_candidate.reason)
        self.assertEqual(
            stored_candidate.masked_channel_order_id,
            response_candidate.masked_channel_order_id,
        )
        self.assertNotEqual(stored_candidate.masked_shipment_box_id, "87001234")
        self.assertTrue(stored_candidate.masked_shipment_box_id.endswith("1234"))

    def test_recovery_candidate_survives_fresh_session_simulating_restart(self):
        """새 세션(프로세스 재시작에 해당)에서도 같은 결과를 조회할 수
        있어야 한다 — 별도 테이블이 아니라 이미 커밋된
        OrderChannelFulfillment 행에서 다시 계산되므로, 원래 세션을
        완전히 닫아도 사라지지 않는다."""

        normalized = normalize_coupang_order(order(status="DELIVERING", orderId=9701))
        CoupangOrderCollectionPersistence(self.db).upsert_fulfillment(1, 10, normalized)
        self.service.ensure_orders(1, 10, [normalized])
        self.db.close()

        fresh_db = sessionmaker(bind=self.engine)()
        try:
            queried = list_recovery_review_candidates(fresh_db, 1)
            self.assertEqual(len(queried), 1)
            self.assertEqual(queried[0].observed_status, "DELIVERING")
        finally:
            fresh_db.close()
            self.db = sessionmaker(bind=self.engine)()  # tearDown()이 다시 닫을 수 있도록 복구

    def test_recovery_candidate_requery_does_not_duplicate(self):
        """같은 외부 주문을 두 번 수집해도(예: 다음 tick에서 같은
        구간이 겹쳐 다시 조회됨) fulfillment 행이 늘어나지 않고,
        복구 후보 조회 결과도 여전히 1건이다."""

        normalized = normalize_coupang_order(order(status="INSTRUCT", orderId=9702))
        persistence = CoupangOrderCollectionPersistence(self.db)
        persistence.upsert_fulfillment(1, 10, normalized)
        self.service.ensure_orders(1, 10, [normalized])
        persistence.upsert_fulfillment(1, 10, normalized)
        self.service.ensure_orders(1, 10, [normalized])

        self.assertEqual(
            self.db.query(OrderChannelFulfillment)
            .filter(OrderChannelFulfillment.channel_order_id == "9702")
            .count(),
            1,
        )
        queried = list_recovery_review_candidates(self.db, 1)
        self.assertEqual(len(queried), 1)

    def test_recovery_candidate_fulfillment_persists_despite_later_unrelated_failure(self):
        """복구 후보의 fulfillment 행은 auto_resolve() 등 이후 단계에서
        무관한 예외가 나더라도(이미 별도 트랜잭션으로 커밋됐으므로)
        사라지지 않는다 — "보존되지 않은 후보를 남겨두고 체크포인트만
        전진시키지 않는다"의 반대 방향(후보 보존은 항상 유지됨)을
        증명한다."""

        normalized = normalize_coupang_order(order(status="NONE_TRACKING", orderId=9703))
        CoupangOrderCollectionPersistence(self.db).upsert_fulfillment(1, 10, normalized)
        self.service.ensure_orders(1, 10, [normalized])

        with patch.object(
            self.service, "auto_resolve", side_effect=RuntimeError("무관한 이후 단계 실패"),
        ):
            with self.assertRaises(RuntimeError):
                self.service.auto_resolve(1, 10, actor_user_id=1)

        queried = list_recovery_review_candidates(self.db, 1)
        self.assertEqual(len(queried), 1)
        self.assertEqual(queried[0].observed_status, "NONE_TRACKING")

    def test_recovery_candidate_query_scoped_by_company_and_connection(self):
        other_service = CoupangOrderMaterializationService(self.db)
        persistence = CoupangOrderCollectionPersistence(self.db)
        normalized_a = normalize_coupang_order(order(status="DEPARTURE", orderId=9704))
        persistence.upsert_fulfillment(1, 10, normalized_a)
        self.service.ensure_orders(1, 10, [normalized_a])
        normalized_b = normalize_coupang_order(order(status="DEPARTURE", orderId=9705))
        persistence.upsert_fulfillment(1, 11, normalized_b)
        other_service.ensure_orders(1, 11, [normalized_b])

        self.assertEqual(len(list_recovery_review_candidates(self.db, 1)), 2)
        self.assertEqual(
            len(list_recovery_review_candidates(self.db, 1, store_connection_id=10)), 1,
        )
        self.assertEqual(len(list_recovery_review_candidates(self.db, 2)), 0)


if __name__ == "__main__":
    unittest.main()
