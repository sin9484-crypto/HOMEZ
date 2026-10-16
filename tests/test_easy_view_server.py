"""
=========================================================
Homez OS

File : tests/test_easy_view_server.py

2026-10-16 — "쉬운 보기"가 쓰는 읽기 전용 집계(`app/domains/easy_view`)와 "이미 했어요" 기록을 고정한다.
  - 주문 5단계(주문→발주→발송→송장→도착) 판정과 다음 할 일, 앞 단계 보정, 회사 격리, 개인정보(이름) 마스킹
  - 오늘 할 일: 흐름 숫자, 출고일 확인이 필요한 등록 상품(저장값이 없거나 1일, 장부에 2일 이상 성공이 있으면 제외)
  - 집계는 DB에 쓰지 않고 외부를 부르지 않는다
  - 송장 "이미 했어요": 쿠팡을 부르지 않고 장부에 성공으로 남기며 이후 같은 송장을 보내지 않는다
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.easy_view.service import EasyViewService
from app.domains.easy_view.service import mask_name
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from tests.test_coupang_invoice_service import InvoiceServiceBase

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 16, 3, 0, 0)

TABLES = [
    Order.__table__, OrderItem.__table__, OrderChannelFulfillment.__table__, PurchaseTask.__table__,
    PurchaseTaskTrackingInfo.__table__, PurchaseOrderSubmissionAttempt.__table__, ChannelActionRequest.__table__,
    ListingWizard.__table__, MarketplaceSubmission.__table__,
]


def make(model, **kw):
    """NOT NULL 열을 종류별 임시 값으로 채워 행을 만든다(테스트에서 중요한 열만 직접 지정)."""

    values = dict(kw)
    for column in model.__table__.columns:
        if column.name in values or column.nullable or column.default is not None or column.server_default is not None:
            continue
        if column.primary_key:
            continue
        python_type = column.type.python_type
        values[column.name] = {
            int: 1, str: "x", float: 0.0, bool: False, datetime: NOW, Decimal: Decimal("0"),
        }.get(python_type, "x")
    return model(**values)


class Base_(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine, tables=TABLES)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.addCleanup(self._cleanup)
        self.svc = EasyViewService(self.db)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def add(self, *rows):
        self.db.add_all(rows)
        self.db.commit()
        return rows[0] if len(rows) == 1 else rows

    def order(self, company=1, number="9001", status="PENDING", buyer="김영준", name="백산수", qty=1, ordered=NOW):
        order = self.add(make(Order, company_id=company, channel_code="COUPANG#1", channel_order_id=number, order_number=number,
                              status=status, buyer_name=buyer, total_amount=Decimal("12050"), ordered_at=ordered))
        self.add(make(OrderItem, company_id=company, order_id=order.id, product_name_snapshot=name, quantity=qty))
        return order

    def task(self, order, status="SEARCH_REQUIRED", company=1, key=None):
        return self.add(make(PurchaseTask, company_id=company, source_order_id=order.id, product_title="백산수", quantity=1,
                             status=status, idempotency_key=key or f"k{order.id}"))

    def box(self, order, raw="ACCEPT", company=1):
        return self.add(make(OrderChannelFulfillment, company_id=company, store_connection_id=1, order_id=order.id,
                             channel_order_id=order.channel_order_id, shipment_box_id="555", raw_status=raw, ordered_at=NOW))

    def tracking(self, task, number="259606480560", delivery=None, company=1):
        return self.add(make(PurchaseTaskTrackingInfo, company_id=company, purchase_task_id=task.id, courier="롯데택배",
                             courier_confirmed=True, tracking_number=number, delivery_status=delivery, is_partial_shipment=False))

    def attempt(self, task, status="SUCCEEDED", company=1):
        return self.add(make(PurchaseOrderSubmissionAttempt, company_id=company, purchase_task_id=task.id, status=status,
                             idempotency_key=f"a{task.id}{status}", started_at=NOW))

    def ledger(self, order, status=ChannelActionStatus.SUCCEEDED, company=1):
        return self.add(make(ChannelActionRequest, company_id=company, store_connection_id=1, action_type="INVOICE_UPLOAD",
                             target_key=f"order:{order.channel_order_id}:box:555", status=status, attempt_count=1,
                             requested_at=NOW))

    def row(self, company=1):
        items = self.svc.build_orders(company)["items"]
        return items[0] if items else None

    @staticmethod
    def states(row):
        return [s["state"] for s in row["steps"]]


class MaskTests(unittest.TestCase):

    def test_names_keep_only_the_first_and_last_character(self):
        self.assertEqual([mask_name(n) for n in ("김영준", "김영", "김", "", None, "남궁민수")], ["김*준", "김*", "김", "", "", "남**수"])


class OrderStepTests(Base_):

    def test_a_new_order_waits_for_the_supplier_order(self):
        task = self.task(self.order())
        row = self.row()
        self.assertEqual(self.states(row), ["done", "now", "todo", "todo", "todo"])
        self.assertEqual((row["now_step"], row["next_action"]["code"], row["next_action"]["view"]), (2, "ORDER_FROM_SUPPLIER", "purchase-task-detail"))
        self.assertEqual(row["task"], {"id": task.id, "status": "SEARCH_REQUIRED"})

    def test_an_order_without_any_task_points_to_the_task_list(self):
        self.order()
        row = self.row()
        self.assertEqual(row["next_action"], {"code": "ORDER_FROM_SUPPLIER", "view": "purchase-task"})

    def test_after_the_supplier_order_it_waits_for_the_shipment(self):
        task = self.task(self.order(), status="TRACKING_REQUIRED")
        self.attempt(task)
        row = self.row()
        self.assertEqual(self.states(row), ["done", "done", "now", "todo", "todo"])
        self.assertEqual(row["next_action"]["code"], "WAIT_SUPPLIER_SHIPMENT")

    def test_a_failed_attempt_does_not_count_as_ordered(self):
        task = self.task(self.order())
        self.attempt(task, status="FAILED")
        self.assertEqual(self.states(self.row())[1], "now")

    def test_with_a_tracking_number_the_invoice_is_the_next_step(self):
        order = self.order()
        task = self.task(order, status="SHIPPED")
        self.tracking(task)
        self.box(order)
        row = self.row()
        self.assertEqual(self.states(row), ["done", "done", "done", "now", "todo"])
        self.assertEqual((row["now_step"], row["next_action"]["code"]), (4, "REGISTER_INVOICE"))
        self.assertEqual(row["tracking"]["number"], "259606480560")
        self.assertEqual(row["shipment_box_id"], "555")

    def test_without_coupang_order_info_the_invoice_cannot_be_registered_yet(self):
        task = self.task(self.order(), status="SHIPPED")
        self.tracking(task)
        self.assertEqual(self.row()["next_action"]["code"], "CHECK_COUPANG_ORDER")

    def test_a_successful_ledger_row_finishes_the_invoice_step(self):
        order = self.order()
        task = self.task(order, status="SHIPPED")
        self.tracking(task)
        self.box(order)
        self.ledger(order)
        row = self.row()
        self.assertEqual(self.states(row), ["done", "done", "done", "done", "now"])
        self.assertEqual(row["next_action"]["code"], "WAIT_DELIVERY")

    def test_other_ledger_statuses_do_not_count(self):
        order = self.order()
        task = self.task(order, status="SHIPPED")
        self.tracking(task)
        self.box(order)
        for status in (ChannelActionStatus.UNKNOWN, ChannelActionStatus.ACTION_REQUIRED, ChannelActionStatus.REQUESTING):
            self.db.query(ChannelActionRequest).delete()
            self.db.commit()
            self.ledger(order, status=status)
            self.assertEqual(self.states(self.row())[3], "now", status)

    def test_a_later_coupang_status_also_means_the_invoice_is_registered(self):
        order = self.order()
        task = self.task(order, status="SHIPPED")
        self.tracking(task)
        self.box(order, raw="DEPARTURE")
        row = self.row()
        self.assertEqual(self.states(row)[3], "done")
        self.assertEqual(row["steps"][3]["detail"], "쿠팡 반영 확인")

    def test_delivery_completes_everything(self):
        order = self.order(status="DELIVERED")
        task = self.task(order, status="DELIVERED")
        self.tracking(task, delivery="DELIVERED")
        self.box(order, raw="FINAL_DELIVERY")
        row = self.row()
        self.assertEqual(self.states(row), ["done"] * 5)
        self.assertEqual(row["next_action"]["code"], "DONE")

    def test_an_earlier_step_is_filled_in_when_a_later_one_is_known_to_be_done(self):
        order = self.order()
        self.box(order, raw="FINAL_DELIVERY")
        row = self.row()
        self.assertEqual(self.states(row)[:4], ["done"] * 4)

    def test_the_buyer_name_is_masked_and_other_companies_are_invisible(self):
        self.order(company=1, buyer="김영준")
        self.order(company=2, number="9002", buyer="이몽룡")
        row = self.row(company=1)
        self.assertEqual(row["buyer_masked"], "김*준")
        self.assertNotIn("김영준", json.dumps(row, ensure_ascii=False))
        self.assertEqual(len(self.svc.build_orders(1)["items"]), 1)
        self.assertEqual(self.svc.build_orders(2)["items"][0]["channel_order_id"], "9002")

    def test_orders_are_listed_newest_first(self):
        self.order(number="1", ordered=datetime(2026, 10, 14))
        self.order(number="2", ordered=datetime(2026, 10, 16))
        self.assertEqual([i["channel_order_id"] for i in self.svc.build_orders(1)["items"]], ["2", "1"])


class TodayTests(Base_):

    def listing_wizard(self, wizard_id, listing_id, days=None, name="방수 생리팬티", company=1):
        selection = {"marketplace_account_id": 1, "required_fields": ({"outboundShippingTimeDay": days} if days is not None else {})}
        return self.add(make(
            ListingWizard, id=wizard_id, company_id=company, status="SUCCEEDED", current_step="RESULTS", creation_idempotency_key=f"w{wizard_id}-{company}",
            draft_json=json.dumps({"product_name": name}, ensure_ascii=False), channel_selections_json=json.dumps([selection]),
            materialized_listing_ids_json=json.dumps([{"listing_id": listing_id, "status": "PENDING"}]),
        ))

    def submission(self, listing_id, ref, status="SUBMITTED", company=1, sub_id=None):
        return self.add(make(MarketplaceSubmission, company_id=company, listing_id=listing_id, selection_id=1, status=status,
                             external_submission_ref=ref, idempotency_key=f"s{listing_id}{ref}{status}", request_fingerprint="f" * 64))

    def days_ledger(self, ref, days, status=ChannelActionStatus.SUCCEEDED):
        return self.add(make(ChannelActionRequest, company_id=1, store_connection_id=1, action_type="PRODUCT_OUTBOUND_DAYS",
                             target_key=f"product:{ref}:days:{days}", status=status, attempt_count=1, requested_at=NOW))

    def todo(self):
        return self.svc.build_today(1)["outbound_days_todo"]

    def test_products_saved_with_no_days_or_one_day_need_a_check(self):
        self.listing_wizard(1, 10, days=None)
        self.submission(10, "16400000001")
        self.listing_wizard(2, 11, days=1)
        self.submission(11, "16400000002")
        self.assertEqual(sorted(p["seller_product_id"] for p in self.todo()), ["16400000001", "16400000002"])
        self.assertEqual({p["seller_product_id"]: p["saved_days"] for p in self.todo()}, {"16400000001": None, "16400000002": 1})

    def test_two_or_more_saved_days_need_no_check(self):
        self.listing_wizard(1, 10, days=2)
        self.submission(10, "16400000001")
        self.assertEqual(self.todo(), [])

    def test_a_successful_days_change_in_the_ledger_removes_it_but_a_failed_one_does_not(self):
        self.listing_wizard(1, 10, days=None)
        self.submission(10, "16400000001")
        self.days_ledger("16400000001", 2, status=ChannelActionStatus.ACTION_REQUIRED)
        self.assertEqual(len(self.todo()), 1)
        self.days_ledger("16400000001", 3)
        self.assertEqual(self.todo(), [])

    def test_a_one_day_ledger_success_is_not_a_fix(self):
        self.listing_wizard(1, 10, days=None)
        self.submission(10, "16400000001")
        self.days_ledger("16400000001", 1)
        self.assertEqual(len(self.todo()), 1)

    def test_only_successful_submissions_with_a_real_product_number_count(self):
        self.listing_wizard(1, 10)
        self.submission(10, None)
        self.listing_wizard(2, 11)
        self.submission(11, "16400000009", status="FAILED")
        self.listing_wizard(3, 12)
        self.submission(12, "not-a-number")
        self.assertEqual(self.todo(), [])
        self.assertEqual(self.svc.build_today(1)["pipeline"]["selling"], 0)

    def test_the_latest_wizard_of_a_listing_supplies_the_saved_value(self):
        self.listing_wizard(1, 10, days=None, name="옛 이름")
        self.listing_wizard(2, 10, days=2, name="새 이름")
        self.submission(10, "16400000001")
        self.assertEqual(self.todo(), [])

    def test_repeated_attempts_for_one_product_show_only_the_current_wizard(self):
        for wizard_id in (1, 2, 3):
            self.listing_wizard(wizard_id, 10)
        self.listing_wizard(4, 11)
        self.submission(10, "16400000001")
        self.submission(11, "16400000002")
        today = self.svc.build_today(1)
        self.assertEqual(sorted(today["selling_wizard_ids"]), [3, 4])
        self.assertEqual(today["pipeline"]["selling"], 2)

    def test_other_companies_are_invisible(self):
        self.listing_wizard(1, 10, company=2)
        self.submission(10, "16400000001", company=2)
        self.assertEqual(self.todo(), [])
        self.assertEqual(self.svc.build_today(1)["pipeline"]["selling"], 0)

    def test_the_pipeline_counts_use_real_orders_only(self):
        self.add(make(PurchaseTask, company_id=1, source_order_id=0, product_title="시험", quantity=1, status="SEARCH_REQUIRED", idempotency_key="t0"))
        order = self.order()
        self.task(order)
        order2 = self.order(number="9002")
        self.task(order2, status="SHIPPED", key="k9002")
        order3 = self.order(number="9003")
        self.task(order3, status="DELIVERED", key="k9003")
        pipeline = self.svc.build_today(1)["pipeline"]
        self.assertEqual((pipeline["new_orders"], pipeline["shipping"], pipeline["settlement"]), (1, 1, 1))

    def test_open_wizards_are_counted_as_ready_to_list(self):
        for index, status in enumerate(("DRAFT", "NEEDS_CORRECTION", "SUCCEEDED", "ARCHIVED"), start=1):
            self.add(make(ListingWizard, id=index, company_id=1, status=status, current_step="MEDIA", channel_selections_json="[]",
                          materialized_listing_ids_json="[]", creation_idempotency_key=f"open{index}"))
        self.assertEqual(self.svc.build_today(1)["pipeline"]["prepare"], 2)

    def test_orders_waiting_for_an_invoice_are_listed(self):
        order = self.order()
        task = self.task(order, status="SHIPPED")
        self.tracking(task)
        self.box(order)
        todo = self.svc.build_today(1)["invoice_todo"]
        self.assertEqual([(t["task_id"], t["channel_order_id"]) for t in todo], [(task.id, "9001")])
        self.ledger(order)
        self.assertEqual(self.svc.build_today(1)["invoice_todo"], [])


class UnresolvedOrderTests(Base_):
    """2026-10-16 — 재고 상품에 연결되지 않은 새 주문이 "오늘 할 일"에서 조용히 빠지지 않는다."""

    def setUp(self):
        super().setUp()
        Base.metadata.create_all(self.engine, tables=[UnresolvedOrderItem.__table__])

    def unresolved(self, number="8200000001", status="MAPPING_REQUIRED", qty=1, company=1, item="1", name="러닝 무릎보호대"):
        box = self.add(make(OrderChannelFulfillment, company_id=company, store_connection_id=1, channel_order_id=number,
                            shipment_box_id=f"b{number}{item}{company}", raw_status="ACCEPT", ordered_at=NOW))
        return self.add(make(UnresolvedOrderItem, company_id=company, fulfillment_id=box.id, channel_item_id=item, vendor_item_id="v1",
                             channel_sku="SKU-X", product_name_snapshot=name, quantity=qty, status=status))

    def test_an_unlinked_new_order_is_listed_and_counted_as_a_new_order(self):
        self.unresolved()
        today = self.svc.build_today(1)
        self.assertEqual(today["pipeline"]["new_orders"], 1)
        row = today["unresolved_orders"][0]
        self.assertEqual((row["channel_order_id"], row["product_name"], row["quantity"]), ("8200000001", "러닝 무릎보호대", 1))

    def test_linked_zero_quantity_and_other_company_items_are_not_listed(self):
        self.unresolved(number="1", status="RESOLVED")
        self.unresolved(number="2", qty=0)
        self.unresolved(number="3", company=2)
        today = self.svc.build_today(1)
        self.assertEqual(today["unresolved_orders"], [])
        self.assertEqual(today["pipeline"]["new_orders"], 0)

    def test_it_adds_to_the_count_of_orders_that_already_became_tasks(self):
        order = self.order(number="7001")
        self.task(order)
        self.unresolved(number="7002")
        self.assertEqual(self.svc.build_today(1)["pipeline"]["new_orders"], 2)

    def test_without_the_table_the_aggregation_still_works(self):
        UnresolvedOrderItem.__table__.drop(self.engine)
        today = self.svc.build_today(1)
        self.assertEqual(today["unresolved_orders"], [])


class DetectionToTodayTests(Base_):
    """2026-10-16 — 쿠팡에서 새 주문이 감지되면(가짜 쿠팡 응답) "오늘 할 일"의 새 주문으로 나타난다. 외부 호출은 없다."""

    def setUp(self):
        super().setUp()
        from app.core.windows_credential_store import InMemoryCredentialStore
        from app.domains.order.collection_model import OrderCollectionCursor
        from app.domains.order.collection_model import OrderSkuResolution
        from app.domains.store_connection.model import StoreConnection

        Base.metadata.create_all(self.engine, tables=[
            UnresolvedOrderItem.__table__, OrderCollectionCursor.__table__, OrderSkuResolution.__table__, StoreConnection.__table__,
        ])
        self.store = InMemoryCredentialStore()
        self.store.save("cred-1", {"vendor_id": "A0001", "access_key": "access", "secret_key": "secret"})
        self.connection = StoreConnection(
            company_id=1, marketplace_code="COUPANG", display_name="쿠팡", seller_identifier="seller", credential_reference="cred-1",
            masked_credential_hint="***", connection_status="CONNECTED", credential_version=1, created_by=1,
            creation_idempotency_key="create-1", creation_request_fingerprint="a" * 64,
        )
        self.db.add(self.connection)
        self.db.commit()

    def collect(self, orders):
        from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionResult
        from app.domains.order.adapters.coupang_collection import CoupangOrderPage
        from app.domains.order.coupang_collection_service import CoupangOrderCollectionService

        class _Provider:
            def __init__(self, result):
                self.result = result

            def collect(self, **_kwargs):
                return self.result

        result = CoupangOrderCollectionResult(True, pages=(CoupangOrderPage(tuple(orders), None),), http_status=200)
        service = CoupangOrderCollectionService(self.db, self.store, provider_factory=lambda credentials: _Provider(result))
        return service.run(1, self.connection.id, "ACCEPT")

    def test_a_newly_detected_order_for_an_unlinked_product_shows_up_as_a_new_order_to_link(self):
        from tests.test_coupang_order_normalizer import order

        before = self.svc.build_today(1)
        self.assertEqual((before["pipeline"]["new_orders"], before["unresolved_orders"]), (0, []))
        response = self.collect([order()])
        self.assertEqual(response.new_fulfillment_count, 1)
        self.assertEqual(response.new_unresolved_item_count, 1)
        after = self.svc.build_today(1)
        self.assertEqual(after["pipeline"]["new_orders"], 1)
        self.assertEqual(len(after["unresolved_orders"]), 1)
        self.assertTrue(after["unresolved_orders"][0]["channel_order_id"])

    def test_detecting_the_same_order_again_does_not_add_another_row(self):
        from tests.test_coupang_order_normalizer import order

        self.collect([order()])
        self.collect([order()])
        self.assertEqual(self.svc.build_today(1)["pipeline"]["new_orders"], 1)


class ReadOnlyTests(unittest.TestCase):

    def test_the_aggregation_never_writes_or_calls_outside(self):
        source = (ROOT / "app/domains/easy_view/service.py").read_text(encoding="utf-8")
        for forbidden in (".commit(", "db.add(", "db.add_all(", "db.delete(", "db.flush(", "db.execute(", ".update(", "requests.", "urllib", "provider", "httpx"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_routes_are_get_only_and_admin_only(self):
        router = (ROOT / "app/domains/easy_view/router.py").read_text(encoding="utf-8")
        self.assertNotIn("@router.post", router)
        self.assertNotIn("@router.put", router)
        self.assertNotIn("@router.delete", router)
        self.assertEqual(router.count("Depends(AdminGuard)"), router.count("@router.get"))


class MarkRegisteredTests(InvoiceServiceBase):

    def test_it_records_success_without_calling_coupang(self):
        result = self.service().mark_registered(self.task.id, 1, 7)
        self.assertEqual(result["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.calls, [])
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by), ("INVOICE_UPLOAD", "order:999:box:111", "SUCCEEDED", 7))
        self.assertIn("직접 등록", row.detail)

    def test_after_it_the_same_invoice_is_never_sent_again(self):
        self.service().mark_registered(self.task.id, 1, 7)
        again = self.service().send(self.task.id, 1, 7, **self.values())
        self.assertEqual(again["executed"], False)
        self.assertEqual(self.transport.count("invoice"), 0)

    def test_it_is_idempotent(self):
        self.service().mark_registered(self.task.id, 1, 7)
        second = self.service().mark_registered(self.task.id, 1, 7)
        self.assertEqual(second["outcome"], "ALREADY_DONE")
        self.assertEqual(len(self.rows()), 1)

    def test_it_settles_an_unknown_or_rejected_row(self):
        for status in (ChannelActionStatus.UNKNOWN, ChannelActionStatus.ACTION_REQUIRED, ChannelActionStatus.RETRYABLE):
            self.db.query(ChannelActionRequest).delete()
            self.db.commit()
            self.db.add(ChannelActionRequest(
                company_id=1, store_connection_id=self.store.id, action_type="INVOICE_UPLOAD", target_key="order:999:box:111",
                status=status, attempt_count=1, requested_at=NOW,
            ))
            self.db.commit()
            self.assertEqual(self.service().mark_registered(self.task.id, 1, 7)["outcome"], "SUCCEEDED", status)
            self.assertEqual(self.rows()[0].status, "SUCCEEDED")

    def test_it_refuses_a_task_without_a_recorded_tracking_number(self):
        task = self.make_task(order_id=5, key="no-track", with_tracking=False, with_box=False, status="TRACKING_REQUIRED")
        with self.assertRaises(BadRequestException):
            self.service().mark_registered(task.id, 1, 7)
        self.assertEqual(self.rows(), [])

    def test_it_writes_an_audit_record(self):
        from sqlalchemy import text

        self.service().mark_registered(self.task.id, 1, 7)
        rows = self.db.execute(text("SELECT action, entity_id, user_id FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("COUPANG_INVOICE_MARKED_REGISTERED", str(self.task.id), 7)])

    def test_the_route_is_admin_only_and_makes_no_external_call(self):
        router = (ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        body = router.split("def mark_coupang_invoice_registered(")[1].split("@router")[0]
        self.assertIn("Depends(AdminGuard)", body)
        self.assertIn(".mark_registered(", body)


if __name__ == "__main__":
    unittest.main()
