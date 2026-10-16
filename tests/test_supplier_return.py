"""
=========================================================
Homez OS

File : tests/test_supplier_return.py

2026-10-16 — 쿠팡에 반품이 접수되면 공급처(온채널)에 반품을 요청하고 결과를 이어서 기록하는 `SupplierReturnService`를 고정한다.
온채널에는 반품 요청 API가 없어(OpenAPI v0.1 전체 확인) 접수는 온채널 화면에서 하고, HOMEZ는 요청 내용 준비·요청 기록·API 읽기로 결과 확인만 한다.
  - 쿠팡에서 끝나지 않은 반품만, 공급처 발주(온채널 주문 코드)와 묶어 후보로 보여 준다
  - "요청했어요"는 같은 접수에 한 번만 기록하고, 온채널로 아무것도 보내지 않으며 구매 작업·금액을 바꾸지 않는다
  - 온채널 주문 상태(`취소반품`·환불)를 읽어 확인됨으로 올리고, 요청 전·무관한 상태에서는 기록하지 않는다
  - 다른 회사는 보이지 않고, 기록에는 개인정보가 없다
=========================================================
"""

import json
import re
import unittest
from pathlib import Path

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.order.supplier_return_service import SupplierReturnService
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseTask
from tests.test_easy_view_server import Base_
from tests.test_easy_view_server import make

ROOT = Path(__file__).resolve().parents[1]
CODE = "GO_1018547475"


class Fixture(Base_):

    def setUp(self):
        super().setUp()
        self.db.execute(text(
            "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
            "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at VARCHAR(40))"))
        self.db.commit()
        self.svc_ret = SupplierReturnService(self.db)

    def event(self, entity_id, payload, company=1):
        self.db.execute(text("INSERT INTO audit_logs (company_id, action, entity, entity_id, description) VALUES (:c, 'COUPANG_AFTER_SALES_EVENT', 'after_sales', :e, :d)"),
                        {"c": company, "e": entity_id, "d": json.dumps(payload, ensure_ascii=False)})
        self.db.commit()

    def open_return(self, receipt="1919873062", status="RETURNS_UNCHECKED", number="8103602504198"):
        self.event(f"RETURN:{receipt}", {"k": "RETURN", "o": "_".join(number[i:i + 4] for i in range(0, len(number), 4)), "s": status,
                                          "rc": "고객변심", "f": "CUSTOMER", "n": 1})

    def with_supplier_order(self, number="8103602504198", code=CODE):
        order = self.order(number=number, status="SHIPPED")
        task = self.task(order, status="DELIVERED", key=f"k{number}")
        self.add(make(PurchaseOrderSubmissionAttempt, company_id=1, purchase_task_id=task.id, status="SUCCEEDED",
                      idempotency_key=f"a{number}", external_order_code=code))
        return order, task

    def audit_events(self):
        self.db.expire_all()
        return [dict(r._mapping) for r in self.db.execute(text("SELECT entity_id, description, user_id FROM audit_logs WHERE action='HOMEZ_SUPPLIER_RETURN_EVENT' ORDER BY id"))]


class CandidateTests(Fixture):

    def test_an_open_coupang_return_is_joined_with_the_supplier_order(self):
        _order, task = self.with_supplier_order()
        self.open_return()
        c = self.svc_ret.candidates(1)[0]
        self.assertEqual((c["receipt_id"], c["channel_order_id"], c["supplier_order_code"], c["supplier"]), ("1919873062", "8103602504198", CODE, "ONCH"))
        self.assertEqual((c["task_id"], c["supplier_state"], c["reason_category"], c["fault_by"]), (task.id, "NOT_REQUESTED", "고객변심", "CUSTOMER"))

    def test_a_finished_return_is_not_a_candidate(self):
        self.with_supplier_order()
        self.open_return(status="RETURNS_COMPLETED")
        self.assertEqual(self.svc_ret.candidates(1), [])

    def test_a_return_for_an_order_without_a_supplier_order_has_no_supplier_code(self):
        self.order(number="8103602504198")
        self.open_return()
        c = self.svc_ret.candidates(1)[0]
        self.assertIsNone(c["supplier_order_code"])

    def test_nothing_is_listed_without_return_events_or_without_the_log_table(self):
        self.assertEqual(self.svc_ret.candidates(1), [])
        self.db.execute(text("DROP TABLE audit_logs"))
        self.db.commit()
        self.assertEqual(SupplierReturnService(self.db).candidates(1), [])

    def test_other_companies_orders_are_not_joined(self):
        self.with_supplier_order()
        self.open_return()
        c = self.svc_ret.candidates(2)[0]  # 이벤트는 회사 구분 없이 쿠팡 응답이지만, 주문·발주는 회사 안에서만 찾는다
        self.assertIsNone(c["supplier_order_code"])


class RequestedTests(Fixture):

    def setUp(self):
        super().setUp()
        self.with_supplier_order()
        self.open_return()

    def test_marking_requested_records_it_once_without_touching_the_task(self):
        before = self.db.query(PurchaseTask).one()
        snapshot = (before.status, before.version)
        c = self.svc_ret.mark_requested(1, "1919873062", user_id=7)
        self.assertEqual(c["supplier_state"], "REQUESTED")
        again = self.svc_ret.mark_requested(1, "1919873062", user_id=7)
        self.assertEqual(again["supplier_state"], "REQUESTED")
        self.assertEqual(len(self.audit_events()), 1)
        self.db.expire_all()
        row = self.db.query(PurchaseTask).one()
        self.assertEqual((row.status, row.version), snapshot)

    def test_it_cannot_be_recorded_without_a_supplier_order(self):
        self.db.query(PurchaseOrderSubmissionAttempt).delete()
        self.db.commit()
        with self.assertRaises(BadRequestException):
            self.svc_ret.mark_requested(1, "1919873062", user_id=7)

    def test_an_unknown_receipt_is_not_found(self):
        with self.assertRaises(NotFoundException):
            self.svc_ret.mark_requested(1, "999", user_id=7)

    def test_the_record_carries_the_order_code_and_the_user_but_no_personal_data(self):
        self.svc_ret.mark_requested(1, "1919873062", user_id=7)
        event = self.audit_events()[0]
        payload = json.loads(event["description"])
        self.assertEqual((payload["s"], payload["u"]), ("REQUESTED", 7))
        self.assertNotIn("c", payload)  # 온채널 주문 코드는 중앙 가림 규칙에 가려지므로 기록에 넣지 않고 발주 기록에서 읽는다
        self.assertEqual(self.svc_ret.candidates(1)[0]["supplier_order_code"], CODE)
        self.assertEqual(event["user_id"], 7)
        for forbidden in ("010", "@", "김영준", "서울"):
            self.assertNotIn(forbidden, event["description"])


class SupplierStatusTests(Fixture):

    def setUp(self):
        super().setUp()
        self.with_supplier_order()
        self.open_return()

    def test_before_the_request_is_recorded_a_return_like_status_changes_nothing(self):
        result = self.svc_ret.apply_supplier_status(1, "1919873062", "취소반품", 7)
        self.assertEqual(result["supplier_state"], "NOT_REQUESTED")
        self.assertEqual(self.audit_events(), [])

    def test_a_return_status_after_the_request_confirms_it(self):
        self.svc_ret.mark_requested(1, "1919873062", 7)
        result = self.svc_ret.apply_supplier_status(1, "1919873062", "취소반품", 7)
        self.assertEqual((result["supplier_state"], result["supplier_status_text"]), ("CONFIRMED", "취소반품"))

    def test_a_refund_status_marks_it_refunded(self):
        self.svc_ret.mark_requested(1, "1919873062", 7)
        self.svc_ret.apply_supplier_status(1, "1919873062", "취소반품", 7)
        self.assertEqual(self.svc_ret.apply_supplier_status(1, "1919873062", "반품/환불 완료", 7)["supplier_state"], "REFUNDED")

    def test_an_unrelated_status_after_the_request_changes_nothing(self):
        self.svc_ret.mark_requested(1, "1919873062", 7)
        before = len(self.audit_events())
        result = self.svc_ret.apply_supplier_status(1, "1919873062", "발송완료", 7)
        self.assertEqual(result["supplier_state"], "REQUESTED")
        self.assertEqual(len(self.audit_events()), before)

    def test_the_same_status_is_not_written_twice(self):
        self.svc_ret.mark_requested(1, "1919873062", 7)
        self.svc_ret.apply_supplier_status(1, "1919873062", "취소반품", 7)
        count = len(self.audit_events())
        self.svc_ret.apply_supplier_status(1, "1919873062", "취소반품", 7)
        self.assertEqual(len(self.audit_events()), count)

    def test_a_finished_confirmation_is_not_downgraded_by_an_older_looking_status(self):
        self.svc_ret.mark_requested(1, "1919873062", 7)
        self.svc_ret.apply_supplier_status(1, "1919873062", "반품/환불 완료", 7)
        self.assertEqual(self.svc_ret.apply_supplier_status(1, "1919873062", "취소반품", 7)["supplier_state"], "REFUNDED")


class TodayIntegrationTests(Fixture):

    def test_today_shows_the_candidate_and_it_follows_the_state(self):
        self.with_supplier_order()
        self.open_return()
        self.assertEqual([c["supplier_state"] for c in self.svc.build_today(1)["supplier_returns"]], ["NOT_REQUESTED"])
        self.svc_ret.mark_requested(1, "1919873062", 7)
        self.assertEqual([c["supplier_state"] for c in self.svc.build_today(1)["supplier_returns"]], ["REQUESTED"])

    def test_today_is_empty_without_returns(self):
        self.assertEqual(self.svc.build_today(1)["supplier_returns"], [])


class WiringTests(unittest.TestCase):

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_the_three_endpoints_exist_are_admin_only_and_validate_the_receipt_number(self):
        source = self.read("app/domains/purchase_task/router.py")
        for path in ('@router.get("/supplier-returns")', '@router.post("/supplier-returns/{receipt_id}/requested")', '@router.post("/supplier-returns/{receipt_id}/check")'):
            self.assertIn(path, source)
        block = source.split('"/supplier-returns")')[1].split("# 매입처 사건 기록")[0]
        self.assertGreaterEqual(block.count("Depends(AdminGuard)"), 3)
        self.assertEqual(block.count("receipt_id.isdigit()"), 2)

    def test_the_check_only_reads_the_supplier_order(self):
        block = self.read("app/domains/purchase_task/router.py").split('"/supplier-returns/{receipt_id}/check")')[1].split("# 매입처 사건 기록")[0]
        self.assertIn("service.lookup_order(", block)
        for forbidden in ("submit_order", "apply_for_sale", "regist", "cancel", '"POST"'):
            self.assertNotIn(forbidden, block.replace('@router.post("/supplier-returns', ""))

    def test_the_service_sends_nothing_outside_and_does_not_change_tasks(self):
        code = re.sub(r'""".*?"""', "", self.read("app/domains/order/supplier_return_service.py"), flags=re.S)
        for forbidden in ("requests", "urllib", "request_return", "mark_delivered", "email", "notify"):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertIsNone(re.search(r"\.status\s*=[^=]", code), "구매 작업 상태를 바꾸는 대입이 있다")

    def test_the_screen_asks_before_reading_and_never_calls_on_open(self):
        js = self.read("app/web/easy/easy_view.js")
        check = js.split("async function supplierReturnCheck(")[1].split("\n  }\n")[0]
        self.assertLess(check.index("B.askConfirm("), check.index("/check"))
        self.assertIn('method: "POST"', check)
        today = js.split("async function loadToday()")[1].split("\n  async function ")[0]
        self.assertNotIn("/supplier-returns", today.split("2-d)")[1].split("// 3)")[0].replace("supplierReturnDialog(c)", "").replace("supplierReturnCheck(c)", ""))

    def test_recording_goes_through_a_dialog_with_the_details(self):
        js = self.read("app/web/easy/easy_view.js")
        dialog = js.split("async function supplierReturnDialog(")[1].split("\n  }\n")[0]
        self.assertIn("ezDialog(", dialog)
        self.assertLess(dialog.index("ezDialog("), dialog.index("/requested"))
        self.assertIn("easy.sr.howto", dialog)


if __name__ == "__main__":
    unittest.main()
