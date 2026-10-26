"""
=========================================================
Homez OS

File : tests/test_coupang_return_pickup.py

2026-10-27 — 쿠팡 쓰기 7번: 반품 회수 송장 등록 규칙을 고정한다. 공식 "회수 송장 등록"
`POST .../v4/vendors/{vendorId}/return-exchange-invoices/manual`({returnExchangeDeliveryType: RETURN, receiptId(number), deliveryCompanyCode,
invoiceNumber, regNumber?})과 반품 단건 조회(returnDeliveryDtos)를 따른다. 반품접수 상태이고 회수 송장이 아직 없고 사용자가 본 상태와 같을 때만,
공식 택배사 코드로만, 직접 회수 확인 뒤에만 한 번 보낸다. 격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import unittest
from pathlib import Path

import requests
from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.external_call.constants import CallPurpose
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_return_pickup_service import CoupangReturnPickupService
from app.domains.purchase_task.coupang_return_pickup_service import courier_code
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_return_action import RCPT
from tests.test_coupang_return_action import FakeTransport as _ReturnTransport
from tests.test_coupang_return_action import ServiceBase as _ReturnBase
from tests.test_coupang_return_action import ret
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR
from tests.test_coupang_stock import provider

INVOICE = "584712345678"


def with_invoices(*pairs, status="RETURNS_UNCHECKED"):
    body = ret(status=status)
    body["data"][0]["returnDeliveryDtos"] = [{"deliveryCompanyCode": c, "deliveryInvoiceNo": n} for c, n in pairs]
    return body


def ok_post():
    return {"code": 200, "message": "OK", "data": {"deliveryCompanyCode": "CJGLS", "invoiceNumber": INVOICE, "invoiceNumberId": 26125633,
                                                   "receiptId": int(RCPT), "returnDeliveryId": 25726758, "returnExchangeDeliveryType": "RETURN"}}


class FakeTransport(_ReturnTransport):
    """GET·POST를 따로 센다."""

    def __init__(self):
        super().__init__()
        self.get = [response(200, ret())]
        self.post = [response(200, ok_post())]

    def request(self, method, url, *, headers, json_body, timeout):
        kind = "post" if method == "POST" else "get"
        with self.lock:
            self.calls.append((kind, method))
            self.requests.append((kind, method, url, json_body))
            queue = self.post if kind == "post" else self.get
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PureTests(unittest.TestCase):

    def test_courier_names_map_only_through_the_official_table(self):
        self.assertEqual([courier_code(v) for v in ("CJ대한통운", " cj 대한통운 ", "CJGLS", "한진택배", "우체국", "KDEXP")],
                         ["CJGLS", "CJGLS", "CJGLS", "HANJIN", "EPOST", "KDEXP"])
        for unknown in ("모르는택배", "cjgls", "", None, 3):
            self.assertIsNone(courier_code(unknown), unknown)


class ProviderTests(unittest.TestCase):

    def test_existing_pickup_invoices_are_read_without_duplicates(self):
        t = FakeTransport()
        t.get = [response(200, with_invoices(("CJGLS", INVOICE), ("CJGLS", INVOICE), ("CJGLS", ""), ("CJGLS", None)))]
        snap = provider(t).read_return_request(RCPT)
        self.assertEqual(snap.return_invoices, (("CJGLS", INVOICE),))
        self.assertEqual(snap.to_dict()["return_invoices"], [{"courier": "CJGLS", "invoice": INVOICE}])

    def test_the_registration_follows_the_official_shape(self):
        t = FakeTransport()
        self.assertEqual(provider(t).register_pickup_invoice(RCPT, "CJGLS", INVOICE).kind, CallKind.SUCCEEDED)
        self.assertEqual(provider(t).register_pickup_invoice(RCPT, "HANJIN", INVOICE, "R1234").kind, CallKind.SUCCEEDED)
        (_, m1, u1, b1), (_, _, _, b2) = t.requests
        self.assertEqual(m1, "POST")
        self.assertTrue(u1.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/return-exchange-invoices/manual"), u1)
        self.assertEqual(b1, {"returnExchangeDeliveryType": "RETURN", "receiptId": int(RCPT), "deliveryCompanyCode": "CJGLS", "invoiceNumber": INVOICE})
        self.assertIsInstance(b1["receiptId"], int)
        self.assertEqual(b2["regNumber"], "R1234")

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for args in (("abc", "CJGLS", INVOICE), (True, "CJGLS", INVOICE), (RCPT, "NOPE", INVOICE), (RCPT, "cjgls", INVOICE),
                     (RCPT, "CJGLS", "12345"), (RCPT, "CJGLS", "1" * 31), (RCPT, "CJGLS", "1234 5678"), (RCPT, "CJGLS", 12345678),
                     (RCPT, "CJGLS", INVOICE, "R-1"), (RCPT, "CJGLS", INVOICE, ""), (RCPT, "CJGLS", INVOICE, "1" * 31)):
            self.assertEqual(p.register_pickup_invoice(*args).kind, CallKind.ACTION_REQUIRED, args)
        self.assertEqual(t.calls, [])

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_post()), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR"}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "x"}), CallKind.UNKNOWN),
            (response(400, {"message": "배송송장과 같은 송장번호는 입력할 수 없습니다."}), CallKind.ACTION_REQUIRED),
            (response(500, {"message": "null"}), CallKind.UNKNOWN),
            (response(429, {}), CallKind.RETRYABLE),
            (requests.Timeout(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.post = [outcome]
            self.assertEqual(provider(t).register_pickup_invoice(RCPT, "CJGLS", INVOICE).kind, expected, outcome)

    def test_the_applied_check(self):
        cases = [(with_invoices(("CJGLS", INVOICE)), (True, True)), (ret(), (True, False)),
                 (with_invoices(("HANJIN", "999999999")), (False, None)), (ret(status="RETURNS_COMPLETED"), (False, None))]
        for body, expected in cases:
            t = FakeTransport()
            t.get = [response(200, body)]
            result = provider(t).read_pickup_invoice_applied(RCPT, INVOICE)
            self.assertEqual((result.ok, result.value), expected, body["data"][0]["receiptStatus"])
        t = FakeTransport()
        t.get = [response(500, {})]
        self.assertFalse(provider(t).read_pickup_invoice_applied(RCPT, INVOICE).ok)


class ServiceBase(_ReturnBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()

    def service(self):
        stock = super(_ReturnBase, self).service()
        return CoupangReturnPickupService(self.db, actions_factory=stock._actions_factory, safety=self.safety)

    def plan(self, courier="CJ대한통운", invoice=INVOICE, **kw):
        return self.service().plan(1, RCPT, courier, invoice, **kw)


class PlanTests(ServiceBase):

    def test_a_new_return_is_ready(self):
        plan = self.plan()
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.courier_code, plan.invoice_number, plan.status), ("CJGLS", INVOICE, "RETURNS_UNCHECKED"))
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_existing_invoices_are_reported_not_overwritten(self):
        self.transport.get = [response(200, with_invoices(("CJGLS", INVOICE)))]
        self.assertTrue(self.plan().already_applied)
        self.transport.get = [response(200, with_invoices(("HANJIN", "999999999")))]
        plan = self.plan()
        self.assertIn("HAS_OTHER_INVOICE", self.codes(plan))
        self.assertIn("999999999", next(b.message for b in plan.blockers if b.code == "HAS_OTHER_INVOICE"))

    def test_only_a_new_return_accepts_a_pickup_invoice(self):
        for status in ("RELEASE_STOP_UNCHECKED", "VENDOR_WAREHOUSE_CONFIRM", "REQUEST_COUPANG_CHECK", "RETURNS_COMPLETED"):
            self.transport.get = [response(200, ret(status=status))]
            plan = self.plan()
            self.assertIn("WRONG_RETURN_STATUS", self.codes(plan), status)
            self.assertNotIn(status, next(b.message for b in plan.blockers if b.code == "WRONG_RETURN_STATUS"))   # 한글 상태 이름

    def test_invalid_input_blocks_before_any_request(self):
        cases = ((dict(courier="모르는택배"), "UNKNOWN_COURIER"), (dict(courier=None), "UNKNOWN_COURIER"), (dict(invoice="12345"), "INVALID_INVOICE_NUMBER"),
                 (dict(invoice=None), "INVALID_INVOICE_NUMBER"), (dict(invoice="1234 5678"), "INVALID_INVOICE_NUMBER"),
                 (dict(reg_number="R-1"), "INVALID_REG_NUMBER"), (dict(reg_number=5), "INVALID_REG_NUMBER"), (dict(reg_number="1" * 31), "INVALID_REG_NUMBER"))
        for kw, code in cases:
            self.assertIn(code, self.codes(self.plan(**kw)), kw)
        for receipt in ("abc", True, "1" * 21):
            self.assertIn("INVALID_RECEIPT_ID", self.codes(self.service().plan(1, receipt, "CJ대한통운", INVOICE)))
        self.assertEqual(self.transport.calls, [])

    def test_an_empty_reg_number_means_none(self):
        plan = self.plan(reg_number="  ")
        self.assertTrue(plan.sendable)
        self.assertIsNone(plan.reg_number)

    def test_a_failed_read_blocks(self):
        self.transport.get = [response(400, {"message": "취소반품접수내역이 존재하지 않습니다."})]
        self.assertIn("RETURN_READ_FAILED", self.codes(self.plan()))

    def test_store_and_gates(self):
        from app.domains.store_connection.model import StoreConnection

        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        self.assertEqual({b.code for b in self.plan().gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.plan()))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.plan()))

    def test_the_plan_never_contains_credentials_or_personal_data(self):
        body = json.dumps(self.plan().to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "access_key", REF, VENDOR, "홍길동", "010-1234"):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, courier="CJ대한통운", invoice=INVOICE, **override):
        args = dict(expected_status="RETURNS_UNCHECKED", confirm_pickup=True)
        args.update(override)
        return self.service().send(1, 7, RCPT, courier, invoice, **args)

    def test_a_confirmed_registration_goes_out_once_and_is_verified(self):
        self.transport.get = [response(200, ret()), response(200, with_invoices(("CJGLS", INVOICE)))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual((self.transport.count("post"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("RETURN_PICKUP_INVOICE", f"rcpt:{RCPT}:PICKUP", ChannelActionStatus.SUCCEEDED, 7))

    def test_a_success_without_the_invoice_after_is_flagged_and_a_failed_reread_is_unknown(self):
        self.transport.get = [response(200, ret()), response(200, ret())]
        self.assertEqual(self.send()["verified"], False)
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, ret()), response(500, {})]
        self.assertIsNone(self.send()["verified"])

    def test_a_different_status_than_the_user_saw_refuses(self):
        for expected in ("VENDOR_WAREHOUSE_CONFIRM", None, 1):
            with self.assertRaises(ConflictException):
                self.send(expected_status=expected)
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_the_pickup_must_be_confirmed(self):
        for confirm in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.send(confirm_pickup=confirm)
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_an_already_registered_invoice_is_reported_not_resent(self):
        self.transport.get = [response(200, with_invoices(("CJGLS", INVOICE)))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("ALREADY_DONE", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.send()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append(method.lower())
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, ret()), response(200, with_invoices(("CJGLS", INVOICE)))]
        self.send(before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("post"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.send(before_send=deny)
        self.assertEqual((other.count("post"), self.rows()), (0, []))

    def test_a_rejection_is_not_repeated_without_a_person(self):
        self.transport.post = [response(400, {"message": "배송송장과 같은 송장번호는 입력할 수 없습니다."})]
        first = self.send()
        self.assertEqual((first["outcome"], first["verified"]), ("ACTION_REQUIRED", None))
        self.assertEqual(self.transport.count("get"), 1)
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.post = [response(200, ok_post())]
        self.transport.get = [response(200, ret()), response(200, with_invoices(("CJGLS", INVOICE)))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading(self):
        self.transport.post = [response(500, {"message": "null"})]
        unknown = self.send()
        self.assertEqual((unknown["outcome"], unknown["verified"]), ("UNKNOWN", None))
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.get = [response(200, with_invoices(("HANJIN", "999999999")))]
        self.assertEqual(self.service().reconcile(1, RCPT, INVOICE)["outcome"], "UNKNOWN")
        self.transport.get = [response(200, with_invoices(("CJGLS", INVOICE)))]
        self.assertEqual(self.service().reconcile(1, RCPT, INVOICE)["outcome"], "SUCCEEDED")
        for args in (("abc", INVOICE), (RCPT, ""), (RCPT, None), (True, INVOICE)):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_the_audit_record(self):
        self.transport.get = [response(200, ret()), response(200, with_invoices(("CJGLS", INVOICE)))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_RETURN_PICKUP_INVOICE", "coupang_return_request", RCPT, 7)])
        self.assertIn("CJGLS", rows[0][4])
        self.assertIn(INVOICE[-4:], rows[0][4])
        self.assertNotIn(INVOICE, rows[0][4])                                       # 감사 기록의 중앙 가림 규칙이 긴 번호를 가린다


class AllowanceTests(ServiceBase):

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=CallPurpose.RETURN_PROCESS, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.plan()))
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_registration(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, ret()), response(200, ret()), response(200, with_invoices(("CJGLS", INVOICE)))]
        self.assertTrue(self.plan().sendable)
        result = self.service().send(1, 7, RCPT, "CJGLS", INVOICE, expected_status="RETURNS_UNCHECKED", confirm_pickup=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("post")), (3, 1))

    def test_a_missing_write_allowance_stops_before_the_post(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.service().send(1, 7, RCPT, "CJGLS", INVOICE, expected_status="RETURNS_UNCHECKED", confirm_pickup=True)
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_preview_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("preview_coupang_return_pickup", "send_coupang_return_pickup", "reconcile_coupang_return_pickup"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        self.assertNotIn(".send(", router.split("def preview_coupang_return_pickup(")[1].split("@router")[0])
        self.assertLess(router.index('"/coupang-returns/{receipt_id}/pickup-invoice/preview"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError

        from app.domains.purchase_task.coupang_return_pickup_schema import CoupangReturnPickupRequest

        good = dict(courier="CJ대한통운", invoice_number=INVOICE, expected_status="RETURNS_UNCHECKED", confirm_pickup=True)
        CoupangReturnPickupRequest(**good)
        CoupangReturnPickupRequest(**{**good, "reg_number": "R1234"})
        for bad in ({"extra": 1}, {"courier": ""}, {"invoice_number": "12345"}, {"invoice_number": "1234 5678"}, {"invoice_number": 12345678},
                    {"reg_number": "R-1"}, {"expected_status": ""}, {"confirm_pickup": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangReturnPickupRequest(**{**good, **bad})

    def test_the_action_type_uses_the_return_processing_allowance(self):
        self.assertIn(ChannelActionType.RETURN_PICKUP_INVOICE, ChannelActionType.ALL)
        self.assertLessEqual(len(ChannelActionType.RETURN_PICKUP_INVOICE), 30)
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.RETURN_PICKUP_INVOICE], (CallPurpose.RETURN_PROCESS, "POST"))
        self.assertEqual(CoupangChannelActions.pickup_invoice_key(" 363585 "), "rcpt:363585:PICKUP")


if __name__ == "__main__":
    unittest.main()
