"""
=========================================================
Homez OS

File : tests/test_delivery_tracking.py

2026-10-16 — 진행 중인 쿠팡 주문·배송 상태를 10분마다 읽는 `DeliveryTrackingService`를 고정한다(가짜 HTTP, 외부 호출 없음).
  - 허용(ORDER_TRACKING)이 없으면 쿠팡을 부르지 않고 허용 요청만 남기며, 한 건에서 막히면 나머지도 시도하지 않는다
  - 쿠팡 상태가 바뀌면 출고 상자의 원문 상태·확인 시각을 갱신하고(상태가 같아도 확인 시각은 남긴다) 감사 기록을 남긴다
  - 배송완료(FINAL_DELIVERY)면 SHIPPED 작업만 기존 배송완료 처리로 넘기고, 이미 끝난 것은 다시 읽지 않는다
  - 전량 취소는 자동 처리하지 않고 감사 기록만 한 번 남긴다
  - 쿠팡에는 GET만 나가고, 금액·정산·환불은 건드리지 않는다
=========================================================
"""

import unittest
from datetime import datetime

from sqlalchemy import text

from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.delivery_tracking_service import DeliveryTrackingService
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from tests.test_coupang_invoice_service import InvoiceServiceBase
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_invoice_upload import sheet_body
from tests.test_external_call_enforcement import _install


def grant_tracking(db, per_hour=30):
    return ExternalCallPolicyService(db).grant(
        1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_TRACKING,
        mode=AllowanceMode.STANDING, per_hour_limit=per_hour, hours=24, user_id=1,
    )


class Base_(InvoiceServiceBase):

    def setUp(self):
        super().setUp()
        _install(self.db)

    def tracker(self, **kw):
        base = self.service()._actions_factory
        return DeliveryTrackingService(self.db, actions_factory=base, **kw)

    def box(self, order_id=1):
        self.db.expire_all()
        return self.db.query(OrderChannelFulfillment).filter_by(order_id=order_id).one()

    def task_row(self, task):
        self.db.expire_all()
        return self.db.get(PurchaseTask, task.id)

    def audits_of(self, action):
        return self.db.execute(text("SELECT description FROM audit_logs WHERE action=:a"), {"a": action}).fetchall()

    def sheet(self, status, **kw):
        self.transport.sheet = [response(200, sheet_body(status=status, **kw))]


class AllowanceGateTests(Base_):

    def test_without_an_allowance_coupang_is_not_called_and_a_request_is_left(self):
        result = self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual((result["ok"], result["blocked"]), (False, True))
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "ORDER_TRACKING")])

    def test_one_blocked_order_stops_the_rest_of_the_run(self):
        self.make_task(order_id=2, key="t2", box="222", channel_order="888")
        summary = self.tracker().refresh_all(1)
        self.assertTrue(summary["blocked"])
        self.assertEqual(len(summary["results"]), 1)
        self.assertEqual(self.transport.calls, [])

    def test_an_invoice_allowance_does_not_open_tracking(self):
        ExternalCallPolicyService(self.db).grant(
            1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.INVOICE_UPLOAD,
            mode=AllowanceMode.COUNTED, count=5, user_id=1,
        )
        self.assertTrue(self.tracker().refresh_task(self.task.id, 1)["blocked"])
        self.assertEqual(self.transport.calls, [])

    def test_the_hourly_cap_is_respected(self):
        grant_tracking(self.db, per_hour=1)
        self.sheet("INSTRUCT")
        self.assertTrue(self.tracker().refresh_task(self.task.id, 1)["ok"])
        second = self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual((second["ok"], second["blocked"]), (False, True))
        self.assertEqual(self.transport.count("sheet"), 1)

    def test_every_call_is_a_get_and_is_logged_as_order_tracking(self):
        grant_tracking(self.db)
        self.sheet("INSTRUCT")
        self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual({m for _k, m in self.transport.calls}, {"GET"})
        logs = self.db.query(ExternalCallLog).all()
        self.assertEqual([(l.purpose, l.kind, l.outcome) for l in logs], [("ORDER_TRACKING", "READ", "SUCCEEDED")])


class StatusUpdateTests(Base_):

    def setUp(self):
        super().setUp()
        grant_tracking(self.db)

    def test_a_new_status_updates_the_box_and_leaves_an_audit_record(self):
        self.sheet("DELIVERING")
        result = self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual((result["ok"], result["changed"], result["coupang_status"], result["previous_status"]), (True, True, "DELIVERING", "INSTRUCT"))
        self.assertEqual(self.box().raw_status, "DELIVERING")
        self.assertIn("INSTRUCT→DELIVERING", self.audits_of("COUPANG_ORDER_STATUS_CHANGED")[0][0])

    def test_an_unchanged_status_still_records_that_it_was_just_checked(self):
        before = self.box().updated_at
        self.sheet("INSTRUCT")
        result = self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual((result["ok"], result["changed"]), (True, False))
        self.assertGreater(self.box().updated_at, before)
        self.assertEqual(self.audits_of("COUPANG_ORDER_STATUS_CHANGED"), [])

    def test_in_transit_is_set_from_the_coupang_status_but_delivered_is_never_downgraded(self):
        self.sheet("DEPARTURE")
        self.tracker().refresh_task(self.task.id, 1)
        self.db.expire_all()
        self.assertEqual(self.db.query(PurchaseTaskTrackingInfo).one().delivery_status, "IN_TRANSIT")
        info = self.db.query(PurchaseTaskTrackingInfo).one()
        info.delivery_status = "DELIVERED"
        self.db.commit()
        self.sheet("DELIVERING")
        self.tracker().refresh_task(self.task.id, 1)
        self.db.expire_all()
        self.assertEqual(self.db.query(PurchaseTaskTrackingInfo).one().delivery_status, "DELIVERED")

    def test_a_failed_read_changes_nothing(self):
        self.transport.sheet = [response(500, {"code": 500, "message": "boom"})]
        result = self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual((result["ok"], result["blocked"]), (False, False))
        self.assertEqual(self.box().raw_status, "INSTRUCT")

    def test_an_order_without_a_shipment_box_is_reported_not_guessed(self):
        task = self.make_task(order_id=5, key="t5", with_box=False)
        result = self.tracker().refresh_task(task.id, 1)
        self.assertEqual(result["ok"], False)
        self.assertEqual(self.transport.calls, [])

    def test_other_companies_tasks_are_not_visible(self):
        from app.core.exceptions import NotFoundException
        with self.assertRaises(NotFoundException):
            self.tracker().refresh_task(self.task.id, 2)


class DeliveredTests(Base_):

    def setUp(self):
        super().setUp()
        grant_tracking(self.db)

    def test_final_delivery_moves_a_shipped_task_to_delivered_with_an_audit_record(self):
        self.sheet("FINAL_DELIVERY")
        result = self.tracker().refresh_task(self.task.id, 1)
        self.assertTrue(result["delivered_marked"])
        self.assertEqual(self.task_row(self.task).status, PurchaseTaskStatus.DELIVERED)
        self.db.expire_all()
        self.assertEqual(self.db.query(PurchaseTaskTrackingInfo).one().delivery_status, "DELIVERED")
        self.assertEqual(len(self.audits_of("COUPANG_DELIVERY_CONFIRMED")), 1)

    def test_only_shipped_tasks_are_moved(self):
        task = self.make_task(order_id=3, key="t3", status=PurchaseTaskStatus.PURCHASE_READY, box="333", channel_order="777")
        self.sheet("FINAL_DELIVERY")
        result = self.tracker().refresh_task(task.id, 1)
        self.assertFalse(result["delivered_marked"])
        self.assertEqual(self.task_row(task).status, PurchaseTaskStatus.PURCHASE_READY)

    def test_a_delivered_order_is_not_read_again(self):
        self.sheet("FINAL_DELIVERY")
        self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual(self.tracker().tracked_task_ids(1), [])
        calls = self.transport.count("sheet")
        self.tracker().refresh_all(1)
        self.assertEqual(self.transport.count("sheet"), calls)

    def test_money_and_task_amounts_are_left_alone(self):
        before = (self.task.coupang_sale_amount, self.task.coupang_fee_amount, self.task.expected_net_profit)
        self.sheet("FINAL_DELIVERY")
        self.tracker().refresh_task(self.task.id, 1)
        row = self.task_row(self.task)
        self.assertEqual((row.coupang_sale_amount, row.coupang_fee_amount, row.expected_net_profit), before)


class CancelTests(Base_):

    def setUp(self):
        super().setUp()
        grant_tracking(self.db)

    def cancelled(self):
        return [{"vendorItemId": 5469001088, "shippingCount": 1, "cancelCount": 1}]

    def test_a_fully_cancelled_order_is_recorded_once_and_not_processed_automatically(self):
        self.sheet("FINAL_DELIVERY", items=self.cancelled())
        first = self.tracker().refresh_task(self.task.id, 1)
        self.assertTrue(first["cancelled"])
        self.assertFalse(first["delivered_marked"])
        self.assertEqual(self.task_row(self.task).status, PurchaseTaskStatus.SHIPPED)
        self.tracker().refresh_task(self.task.id, 1)
        self.assertEqual(len(self.audits_of("COUPANG_ORDER_CANCEL_DETECTED")), 1)

    def test_a_partial_cancel_is_not_flagged_as_cancelled(self):
        items = [{"vendorItemId": 1, "shippingCount": 2, "cancelCount": 1}]
        self.sheet("INSTRUCT", items=items)
        self.assertFalse(self.tracker().refresh_task(self.task.id, 1)["cancelled"])


class RunTests(Base_):

    def setUp(self):
        super().setUp()
        grant_tracking(self.db)

    def test_only_unfinished_tasks_with_a_box_are_tracked(self):
        self.make_task(order_id=2, key="t2", status=PurchaseTaskStatus.COMPLETED, box="222", channel_order="888")
        self.make_task(order_id=3, key="t3", status=PurchaseTaskStatus.SKIPPED_INVENTORY_AVAILABLE, box="333", channel_order="777")
        self.make_task(order_id=4, key="t4", status=PurchaseTaskStatus.PURCHASE_READY, box="444", channel_order="666")
        self.assertEqual(self.tracker().tracked_task_ids(1), [self.task.id, 4])

    def test_a_run_reads_each_tracked_order_once_and_summarises(self):
        self.make_task(order_id=4, key="t4", status=PurchaseTaskStatus.PURCHASE_READY, box="444", channel_order="666")
        self.sheet("DELIVERING")
        summary = self.tracker().refresh_all(1)
        self.assertEqual((summary["checked"], summary["changed"], summary["failed"], summary["blocked"]), (2, 2, 0, False))
        self.assertEqual(self.transport.count("sheet"), 2)

    def test_the_run_is_capped(self):
        for n in range(2, 6):
            self.make_task(order_id=n, key=f"t{n}", box=str(100 + n), channel_order=str(900 + n))
        self.sheet("INSTRUCT")
        summary = self.tracker(max_per_run=3).refresh_all(1)
        self.assertEqual(summary["checked"], 3)

    def test_one_failure_does_not_stop_the_others(self):
        self.make_task(order_id=2, key="t2", box="222", channel_order="888")
        self.transport.sheet = [response(500, {"code": 500, "message": "boom"}), response(200, sheet_body(status="DELIVERING"))]
        summary = self.tracker().refresh_all(1)
        self.assertEqual((summary["failed"], summary["checked"]), (1, 1))


class WiringTests(unittest.TestCase):

    def test_the_scheduler_registers_a_ten_minute_tracking_job(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "app/domains/scheduler/jobs.py").read_text(encoding="utf-8")
        self.assertIn('DELIVERY_TRACKING_JOB_ID = "coupang_delivery_tracking"', source)
        self.assertIn("DELIVERY_TRACKING_INTERVAL_MINUTES = 10", source)
        self.assertIn("run_delivery_tracking_job,\n        minutes=DELIVERY_TRACKING_INTERVAL_MINUTES", source)

    def test_the_service_never_writes_to_coupang(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "app/domains/order/delivery_tracking_service.py").read_text(encoding="utf-8")
        for forbidden in ("upload_invoice", "cancel_order_items", "stop_vendor_item_sale", "update_outbound_days", "register_invoice", '"POST"', '"PUT"'):
            self.assertNotIn(forbidden, source)

    def test_the_purpose_is_enforced_and_has_a_label(self):
        self.assertIn(CallPurpose.ORDER_TRACKING, CallPurpose.ENFORCED)
        self.assertIn(CallPurpose.ORDER_TRACKING, CallPurpose.ALL)
        self.assertTrue(CallPurpose.LABELS[CallPurpose.ORDER_TRACKING])


if __name__ == "__main__":
    unittest.main()
