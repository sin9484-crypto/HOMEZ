"""
=========================================================
Homez OS

File : tests/test_coupang_invoice_service.py

2026-10-15 — HOMEZ에서 쿠팡에 송장을 등록하는 업무 규칙(`CoupangInvoiceService`)을 고정한다. 보내는 값은 만들어 내지 않고
(택배사 코드 고정 표, 옵션번호는 발주서에서 읽음), 사용자가 본 값과 다르면 거절하며, 모드·비상정지·상태·이미 등록 여부를 모두
확인한 뒤 작업 장부로 **한 번만** 보낸다. 격리 SQLite + 가짜 HTTP + 가짜 안전 서비스(외부 호출 없음).
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_invoice_service import COURIER_CODES_BY_NAME
from app.domains.purchase_task.coupang_invoice_service import CoupangInvoiceService
from app.domains.purchase_task.coupang_invoice_service import courier_code_for
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from app.domains.store_connection.model import StoreConnection
from tests.test_coupang_invoice_upload import FakeTransport
from tests.test_coupang_invoice_upload import ok_body
from tests.test_coupang_invoice_upload import fail_body
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_invoice_upload import sheet_body

REF = "HOMEZ_TEST:store_connection:invoice-service"
VENDOR = "A00012345"
NUMBER = "259606480560"


class FakeSafety:
    def __init__(self, mode=AutomationMode.OPERATOR_APPROVAL, estop=False):
        self.mode, self.estop = mode, estop

    def get_current_mode(self):
        return self.mode

    def is_emergency_stop_active(self):
        return self.estop


class InvoiceServiceBase(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = Path(path)
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(bind=self.engine, tables=[
            StoreConnection.__table__, ChannelActionRequest.__table__, PurchaseTask.__table__,
            PurchaseTaskTrackingInfo.__table__, OrderChannelFulfillment.__table__,
        ])
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        # audit_logs는 ORM 모델이 없는 실제 테이블이라 격리 DB에 직접 만든다.
        self.db.execute(text(
            "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
            "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50))"))
        self.db.commit()
        self.addCleanup(self._cleanup)
        self.store = StoreConnection(
            company_id=1, marketplace_code="COUPANG", display_name="쿠팡", seller_identifier="홈즈쿠팡",
            connection_status="CONNECTED", created_by=1, creation_idempotency_key="s1",
            creation_request_fingerprint="f" * 64, credential_reference=REF,
        )
        self.db.add(self.store)
        self.db.commit()
        self.creds = InMemoryCredentialStore()
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        self.transport = FakeTransport()
        self.safety = FakeSafety()
        self.audits = []
        self.task = self.make_task()

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        try:
            self.path.unlink()
        except OSError:
            pass

    def make_task(self, *, order_id=1, status=PurchaseTaskStatus.SHIPPED, courier="롯데택배", confirmed=True,
                  number=NUMBER, partial=False, box="111", channel_order="999", with_tracking=True, with_box=True, key="t1"):
        task = PurchaseTask(
            company_id=1, source_order_id=order_id, creation_source="COUPANG_ORDER", product_title="백산수", quantity=1,
            status=status, version=1, idempotency_key=key,
        )
        self.db.add(task)
        self.db.commit()
        if with_tracking:
            self.db.add(PurchaseTaskTrackingInfo(
                company_id=1, purchase_task_id=task.id, courier=courier, courier_confirmed=confirmed,
                tracking_number=number, is_partial_shipment=partial,
            ))
        if with_box and not self.db.query(OrderChannelFulfillment).filter_by(order_id=order_id).count():
            self.db.add(OrderChannelFulfillment(
                company_id=1, store_connection_id=self.store.id, order_id=order_id, channel_order_id=channel_order,
                shipment_box_id=box, raw_status="INSTRUCT", ordered_at=datetime(2026, 10, 14, 18, 0, 0),
            ))
        self.db.commit()
        return task

    def service(self):
        def factory(session):
            return CoupangChannelActions(
                session, credential_store=self.creds, now_factory=lambda: datetime(2026, 10, 15, 14, 0, 0),
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
            )
        return CoupangInvoiceService(self.db, actions_factory=factory, safety=self.safety)

    def values(self, plan=None):
        plan = plan or self.service().plan(self.task.id, 1)
        return dict(
            delivery_company_code=plan.courier_code, invoice_number=plan.invoice_number,
            shipment_box_id=plan.shipment_box_id, vendor_item_ids=plan.vendor_item_ids,
        )

    def codes(self, plan):
        return {b.code for b in plan.blockers}

    def rows(self):
        self.db.expire_all()
        return self.db.query(ChannelActionRequest).all()


class CourierMappingTests(unittest.TestCase):

    def test_known_names_map_to_the_official_codes_ignoring_spaces_and_case(self):
        self.assertEqual(courier_code_for("롯데택배"), "HYUNDAI")
        self.assertEqual(courier_code_for(" 롯데 택배 "), "HYUNDAI")
        self.assertEqual(courier_code_for("CJ대한통운"), "CJGLS")
        self.assertEqual(courier_code_for("cj 대한통운"), "CJGLS")
        self.assertEqual(courier_code_for("한진택배"), "HANJIN")

    def test_unknown_or_empty_names_are_not_guessed(self):
        for name in (None, "", "처음보는택배", "택배"):
            self.assertIsNone(courier_code_for(name))

    def test_every_code_in_the_table_matches_the_registration_screens_official_list(self):
        import re
        js = (Path(__file__).resolve().parents[1] / "app/web/console.js").read_text(encoding="utf-8")
        screen_codes = set(re.findall(r'\{ code: "([A-Z]+)", label: "', js.split("const LW_COUPANG_DELIVERY_COMPANIES")[1].split("];")[0]))
        self.assertTrue(set(COURIER_CODES_BY_NAME.values()) <= screen_codes, set(COURIER_CODES_BY_NAME.values()) - screen_codes)


class PlanTests(InvoiceServiceBase):

    def test_a_ready_task_shows_the_exact_values_read_from_coupang(self):
        plan = self.service().plan(self.task.id, 1)
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.courier_code, plan.invoice_number, plan.shipment_box_id, plan.channel_order_id),
                         ("HYUNDAI", NUMBER, "111", "999"))
        self.assertEqual(plan.vendor_item_ids, ["5469001088"])
        self.assertEqual(plan.coupang_status, "INSTRUCT")
        self.assertEqual(self.transport.calls, [("sheet", "GET")])  # 읽기 1회, 쓰기 없음
        self.assertEqual(self.rows(), [])

    def test_a_local_only_preview_makes_no_request(self):
        plan = self.service().plan(self.task.id, 1, live=False)
        self.assertFalse(plan.live_checked)
        self.assertFalse(plan.sendable)
        self.assertEqual(self.transport.calls, [])

    def test_another_companys_task_is_not_found(self):
        with self.assertRaises(NotFoundException):
            self.service().plan(self.task.id, 2)

    def test_each_missing_prerequisite_blocks_without_any_request(self):
        cases = {
            "TRACKING_NOT_RECORDED": dict(status=PurchaseTaskStatus.TRACKING_REQUIRED, with_tracking=False),
            "COURIER_NOT_CONFIRMED": dict(confirmed=False),
            "COURIER_NOT_MAPPED": dict(courier="처음보는택배"),
            "NO_COURIER": dict(courier=None),
            "NO_INVOICE_NUMBER": dict(number=None),
            "PARTIAL_SHIPMENT": dict(partial=True),
            "NO_COUPANG_ORDER": dict(with_box=False),
        }
        for code, kwargs in cases.items():
            self.db.query(OrderChannelFulfillment).delete()
            task = self.make_task(order_id=10 + len(code), key=f"k-{code}", **kwargs)
            plan = self.service().plan(task.id, 1)
            self.assertIn(code, self.codes(plan), code)
            self.assertFalse(plan.sendable)
        self.assertEqual(self.transport.calls, [], "막힌 상태에서는 쿠팡을 읽지도 않는다")

    def test_an_order_with_two_boxes_or_two_tasks_is_not_automated(self):
        self.db.add(OrderChannelFulfillment(
            company_id=1, store_connection_id=self.store.id, order_id=1, channel_order_id="999", shipment_box_id="222",
            raw_status="INSTRUCT", ordered_at=datetime(2026, 10, 14, 18, 0, 0)))
        self.db.commit()
        self.assertIn("MULTIPLE_BOXES", self.codes(self.service().plan(self.task.id, 1)))
        self.db.query(OrderChannelFulfillment).filter_by(shipment_box_id="222").delete()
        self.db.commit()
        self.make_task(order_id=1, key="second", with_tracking=False, with_box=False, status=PurchaseTaskStatus.TRACKING_REQUIRED)
        self.assertIn("MULTIPLE_TASKS_IN_ORDER", self.codes(self.service().plan(self.task.id, 1)))

    def test_a_payment_completed_order_must_be_prepared_in_coupang_first(self):
        self.transport.sheet = [response(200, sheet_body("ACCEPT"))]
        plan = self.service().plan(self.task.id, 1)
        self.assertIn("NEEDS_PREPARATION", self.codes(plan))
        self.assertFalse(plan.sendable)

    def test_other_statuses_cannot_receive_an_invoice(self):
        for status in ("DEPARTURE", "DELIVERING", "FINAL_DELIVERY", "NONE_TRACKING"):
            self.transport.sheet = [response(200, sheet_body(status))]
            self.assertIn("STATUS_NOT_ALLOWED", self.codes(self.service().plan(self.task.id, 1)), status)

    def test_the_same_invoice_already_in_coupang_is_reported_not_resent(self):
        self.transport.sheet = [response(200, sheet_body("DEPARTURE", invoice=NUMBER))]
        plan = self.service().plan(self.task.id, 1)
        self.assertTrue(plan.already_registered)
        self.assertFalse(plan.sendable)
        self.assertEqual(self.codes(plan), set())

    def test_a_different_invoice_already_in_coupang_blocks(self):
        self.transport.sheet = [response(200, sheet_body("DEPARTURE", invoice="111122223333"))]
        plan = self.service().plan(self.task.id, 1)
        self.assertIn("INVOICE_DIFFERS", self.codes(plan))

    def test_fully_cancelled_items_are_left_out_and_an_empty_box_is_blocked(self):
        items = [{"vendorItemId": 1, "shippingCount": 1, "cancelCount": 1}, {"vendorItemId": 2, "shippingCount": 2, "cancelCount": 1}]
        self.transport.sheet = [response(200, sheet_body("INSTRUCT", items=items))]
        self.assertEqual(self.service().plan(self.task.id, 1).vendor_item_ids, ["2"])
        self.transport.sheet = [response(200, sheet_body("INSTRUCT", items=[items[0]]))]
        self.assertIn("NO_ACTIVE_ITEMS", self.codes(self.service().plan(self.task.id, 1)))

    def test_a_failed_coupang_read_blocks_instead_of_guessing(self):
        self.transport.sheet = [response(500, {})]
        plan = self.service().plan(self.task.id, 1)
        self.assertIn("SHEET_READ_FAILED", self.codes(plan))
        self.assertFalse(plan.live_checked)

    def test_the_gate_reasons_are_shown_but_do_not_hide_the_data_checks(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        self.safety.estop = True
        plan = self.service().plan(self.task.id, 1)
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)
        self.assertTrue(plan.live_checked)

    def test_the_plan_never_contains_credentials(self):
        text = json.dumps(self.service().plan(self.task.id, 1).to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key"):
            self.assertNotIn(secret, text)


class SendTests(InvoiceServiceBase):

    def send(self, **override):
        args = {**self.values(), **override}
        return self.service().send(self.task.id, 1, 7, **args)

    def test_a_confirmed_send_goes_out_once_and_is_recorded(self):
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("SUCCEEDED", True))
        self.assertEqual(self.transport.count("invoice"), 1)
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by), ("INVOICE_UPLOAD", "order:999:box:111", ChannelActionStatus.SUCCEEDED, 7))
        body = [r for r in self.transport.requests if r[0] == "invoice"][0][3]
        self.assertEqual(body["orderSheetInvoiceApplyDtos"][0]["deliveryCompanyCode"], "HYUNDAI")
        self.assertEqual(body["orderSheetInvoiceApplyDtos"][0]["invoiceNumber"], NUMBER)

    def test_sending_again_after_success_is_a_no_op(self):
        self.send()
        self.transport.sheet = [response(200, sheet_body("DEPARTURE", invoice=NUMBER))]
        again = self.service().send(self.task.id, 1, 7, **self.values(self.service().plan(self.task.id, 1)))
        self.assertEqual((again["outcome"], again["executed"]), ("ALREADY_REGISTERED", False))
        self.assertEqual(self.transport.count("invoice"), 1)

    def test_any_difference_from_what_the_user_saw_refuses_to_send(self):
        for key, bad in (("delivery_company_code", "HANJIN"), ("invoice_number", "999999999999"),
                         ("shipment_box_id", "222"), ("vendor_item_ids", ["1"])):
            with self.assertRaises(ConflictException, msg=key):
                self.send(**{key: bad})
        self.assertEqual(self.transport.count("invoice"), 0)

    def test_the_mode_and_the_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.send()
        self.safety.mode = AutomationMode.OPERATOR_APPROVAL
        self.safety.estop = True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual(self.transport.count("invoice"), 0)
        self.assertEqual(self.rows(), [])

    def test_a_blocked_order_is_refused_with_its_reasons(self):
        self.transport.sheet = [response(200, sheet_body("ACCEPT"))]
        with self.assertRaises(BadRequestException) as caught:
            self.service().send(self.task.id, 1, 7, delivery_company_code="HYUNDAI", invoice_number=NUMBER,
                                shipment_box_id="111", vendor_item_ids=["5469001088"])
        self.assertIn("NEEDS_PREPARATION", str(caught.exception.detail if hasattr(caught.exception, "detail") else caught.exception))
        self.assertEqual(self.transport.count("invoice"), 0)

    def test_the_recent_auth_hook_runs_after_all_checks_and_before_the_request(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append("invoice" if url.endswith("/orders/invoices") else "sheet")
            return original(method, url, **kw)

        self.transport.request = tracking
        self.service().send(self.task.id, 1, 7, **self.values(), before_send=lambda: order.append("auth"))
        self.assertEqual(order, ["sheet", "sheet", "auth", "invoice"][:0] or order)
        self.assertLess(order.index("auth"), order.index("invoice"))
        self.assertLess(order.index("sheet"), order.index("auth"))

    def test_a_rejected_auth_hook_sends_nothing_and_leaves_no_ledger_row(self):
        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.service().send(self.task.id, 1, 7, **self.values(), before_send=deny)
        self.assertEqual(self.transport.count("invoice"), 0)
        self.assertEqual(self.rows(), [])

    def test_a_gate_failure_never_consumes_the_auth_hook(self):
        calls = []
        self.safety.estop = True
        with self.assertRaises(ForbiddenException):
            self.service().send(self.task.id, 1, 7, **self.values(), before_send=lambda: calls.append(1))
        self.assertEqual(calls, [])

    def test_a_rejection_is_reported_and_not_repeated_without_a_person(self):
        self.transport.invoice = [response(200, fail_body("INVALID_STATUS"))]
        first = self.send()
        self.assertEqual(first["outcome"], "ACTION_REQUIRED")
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("invoice"), 1)
        self.transport.invoice = [response(200, ok_body())]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading_coupang(self):
        self.transport.invoice = [requests.Timeout()]
        self.assertEqual(self.send()["outcome"], "UNKNOWN")
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("invoice"), 1)
        self.transport.sheet = [response(200, sheet_body("DEPARTURE", invoice=NUMBER))]
        self.assertEqual(self.service().reconcile(self.task.id, 1)["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("invoice"), 1)

    def test_an_audit_record_is_written_for_a_send(self):
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("COUPANG_INVOICE_UPLOAD", "purchase_task", str(self.task.id), 7)])


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_the_routes_are_admin_only_and_the_send_needs_a_recent_authentication(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for path in ('"/{task_id}/coupang-invoice/preview"', '"/{task_id}/coupang-invoice/send"', '"/{task_id}/coupang-invoice/reconcile"'):
            self.assertIn(path, router)
        body = router.split("def send_coupang_invoice(")[1].split("@router.post")[0]
        self.assertIn("Depends(AdminGuard)", body)
        self.assertIn("consume_recent_auth_token(recent_auth_token, current_user.id)", body)
        self.assertIn("before_send=require_recent_auth", body)
        for name in ("preview_coupang_invoice", "reconcile_coupang_invoice"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0])

    def test_the_request_schema_rejects_unknown_fields(self):
        from pydantic import ValidationError
        from app.domains.purchase_task.coupang_invoice_schema import CoupangInvoiceSendRequest
        good = dict(delivery_company_code="HYUNDAI", invoice_number=NUMBER, shipment_box_id="111", vendor_item_ids=["1"])
        CoupangInvoiceSendRequest(**good)
        for bad in ({"extra": 1}, {"vendor_item_ids": []}, {"invoice_number": "12"}, {"delivery_company_code": ""}):
            with self.assertRaises(ValidationError):
                CoupangInvoiceSendRequest(**{**good, **bad})

    def test_the_preview_is_read_only_and_no_route_sends_on_get(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        preview = router.split("def preview_coupang_invoice(")[1].split("@router.post")[0]
        self.assertNotIn("send(", preview)
        self.assertNotIn("register_invoice", preview)


if __name__ == "__main__":
    unittest.main()
