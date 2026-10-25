"""
=========================================================
Homez OS

File : tests/test_coupang_return_action.py

2026-10-26 — 쿠팡 쓰기 5번: 반품 입고 확인·반품 승인 규칙을 고정한다. 공식 "반품상품 입고 확인처리"
`PATCH .../v4/vendors/{vendorId}/returnRequests/{receiptId}/receiveConfirmation`({vendorId, receiptId}, 반품접수 상태만),
"반품요청 승인 처리" `PATCH .../approval`({vendorId, receiptId, cancelCount}, 입고완료 상태만 — 환불 진행),
"반품요청 단건 조회" `GET .../v6/.../returnRequests/{receiptId}`를 따른다. 사용자가 본 상태·수량과 같고, 상품 수령(승인은 환불도)을 확인했을 때만
접수번호·동작마다 한 번 보내고, 보낸 뒤 상태를 다시 읽어 확인한다. 격리 SQLite + 가짜 HTTP(외부 호출 없음).
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
from app.domains.purchase_task.coupang_return_action_service import CoupangReturnActionService
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR
from tests.test_coupang_stock import FakeTransport as _StockTransport
from tests.test_coupang_stock import ServiceBase as _StockBase
from tests.test_coupang_stock import provider

RCPT = "363585"


def ret(status="RETURNS_UNCHECKED", counts=(1,), receipt=363585, pre_refund=False, as_list=True):
    record = {
        "receiptId": receipt, "orderId": 500004398, "receiptType": "RETURN", "receiptStatus": status,
        "requesterName": "홍길동", "requesterPhoneNumber": "010-1234-5678", "requesterAddress": "서울시 어딘가",
        "returnItems": [{"vendorItemId": 70012345678 + i, "purchaseCount": c, "cancelCount": c, "releaseStatus": "Y"} for i, c in enumerate(counts)],
        "faultByType": "CUSTOMER", "preRefund": pre_refund, "returnShippingCharge": {"currencyCode": "KRW", "units": -3000},
        "completeConfirmType": "UNDEFINED",
    }
    return {"code": 200, "message": "OK", "data": [record] if as_list else record}


def ok_patch():
    return {"code": "200", "message": "OK"}


class FakeTransport(_StockTransport):
    """GET·PATCH를 따로 센다."""

    def __init__(self):
        super().__init__()
        self.get = [response(200, ret())]
        self.patch = [response(200, ok_patch())]

    def request(self, method, url, *, headers, json_body, timeout):
        kind = "patch" if method == "PATCH" else "get"
        with self.lock:
            self.calls.append((kind, method))
            self.requests.append((kind, method, url, json_body))
            queue = self.patch if kind == "patch" else self.get
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ProviderTests(unittest.TestCase):

    def test_reading_one_return_keeps_no_personal_data(self):
        t = FakeTransport()
        t.get = [response(200, ret(counts=(2, 1), pre_refund=True))]
        snap = provider(t).read_return_request(RCPT)
        self.assertTrue(snap.ok)
        self.assertEqual((snap.status, snap.cancel_count, snap.item_count, snap.order_id, snap.fault_by, snap.pre_refund, snap.return_shipping_charge),
                         ("RETURNS_UNCHECKED", 3, 2, "500004398", "CUSTOMER", True, -3000))
        self.assertTrue(t.requests[0][2].endswith(f"/v2/providers/openapi/apis/api/v6/vendors/{VENDOR}/returnRequests/{RCPT}"))
        dumped = json.dumps(snap.to_dict(), ensure_ascii=False)
        for personal in ("홍길동", "010-1234-5678", "서울시"):
            self.assertNotIn(personal, dumped)

    def test_an_unclear_fast_refund_flag_is_unknown(self):
        for value in ("Y", None, 1):
            t = FakeTransport()
            body = ret()
            body["data"][0]["preRefund"] = value
            t.get = [response(200, body)]
            self.assertIsNone(provider(t).read_return_request(RCPT).pre_refund, value)

    def test_a_single_object_response_is_accepted_too(self):
        t = FakeTransport()
        t.get = [response(200, ret(as_list=False))]
        self.assertTrue(provider(t).read_return_request(RCPT).ok)

    def test_an_unreadable_return_is_a_failure(self):
        bad = [response(400, {"message": "취소반품접수내역이 존재하지 않습니다."}), response(500, {}), response(200, {"code": 200, "data": []}),
               response(200, {"code": 200, "data": None}), response(200, ret(receipt=999)), response(200, ret(status="SOMETHING")),
               requests.Timeout()]
        for outcome in bad:
            t = FakeTransport()
            t.get = [outcome]
            snap = provider(t).read_return_request(RCPT)
            self.assertFalse(snap.ok, outcome)
            self.assertIsNone(snap.status, outcome)
        self.assertFalse(provider(FakeTransport()).read_return_request("abc").ok)
        self.assertFalse(provider(FakeTransport()).read_return_request(True).ok)

    def test_an_unclear_count_is_unknown_not_guessed(self):
        for counts in ((0,), (1, None), ()):
            t = FakeTransport()
            body = ret(counts=tuple(c or 0 for c in counts))
            if counts == (1, None):
                body["data"][0]["returnItems"][1]["cancelCount"] = None
            t.get = [response(200, body)]
            self.assertIsNone(provider(t).read_return_request(RCPT).cancel_count, counts)

    def test_the_two_actions_follow_the_official_shapes(self):
        t = FakeTransport()
        self.assertEqual(provider(t).process_return(RCPT, "RECEIVE").kind, CallKind.SUCCEEDED)
        self.assertEqual(provider(t).process_return(RCPT, "APPROVE", 2).kind, CallKind.SUCCEEDED)
        (k1, m1, u1, b1), (k2, m2, u2, b2) = t.requests
        self.assertEqual((m1, m2), ("PATCH", "PATCH"))
        self.assertTrue(u1.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/returnRequests/{RCPT}/receiveConfirmation"), u1)
        self.assertTrue(u2.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/returnRequests/{RCPT}/approval"), u2)
        self.assertEqual(b1, {"vendorId": VENDOR, "receiptId": 363585})
        self.assertEqual(b2, {"vendorId": VENDOR, "receiptId": 363585, "cancelCount": 2})
        self.assertIsInstance(b1["receiptId"], int)                             # 문서: number 타입

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for args in (("abc", "RECEIVE"), ("", "RECEIVE"), ("1" * 21, "RECEIVE"), (True, "RECEIVE"), (RCPT, "REFUND"),
                     (RCPT, "APPROVE"), (RCPT, "APPROVE", 0), (RCPT, "APPROVE", True), (RCPT, "APPROVE", "1")):
            self.assertEqual(p.process_return(*args).kind, CallKind.ACTION_REQUIRED, args)
        self.assertEqual(t.calls, [])

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_patch()), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR", "message": "x"}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "no code"}), CallKind.UNKNOWN),
            (response(400, {"message": "이미 반품이 완료 되었습니다."}), CallKind.ACTION_REQUIRED),
            (response(400, {"message": "취소반품접수수량과 cancelCount가 일치하지 않습니다."}), CallKind.ACTION_REQUIRED),
            (response(500, {"message": "승인 할 수 없는 상태"}), CallKind.UNKNOWN),          # 500은 처리됐을 수 있다 — 조회로만 확인
            (response(429, {}), CallKind.RETRYABLE),
            (requests.Timeout(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.patch = [outcome]
            self.assertEqual(provider(t).process_return(RCPT, "APPROVE", 1).kind, expected, outcome)

    def test_the_applied_check(self):
        cases = [("RECEIVE", "VENDOR_WAREHOUSE_CONFIRM", (True, True)), ("RECEIVE", "RETURNS_COMPLETED", (True, True)),
                 ("RECEIVE", "REQUEST_COUPANG_CHECK", (True, True)), ("RECEIVE", "RETURNS_UNCHECKED", (True, False)),
                 ("RECEIVE", "RELEASE_STOP_UNCHECKED", (False, None)), ("APPROVE", "RETURNS_COMPLETED", (True, True)),
                 ("APPROVE", "VENDOR_WAREHOUSE_CONFIRM", (True, False)), ("APPROVE", "REQUEST_COUPANG_CHECK", (False, None))]
        for action, status, expected in cases:
            t = FakeTransport()
            t.get = [response(200, ret(status=status))]
            result = provider(t).read_return_action_applied(RCPT, action)
            self.assertEqual((result.ok, result.value), expected, (action, status))
        t = FakeTransport()
        t.get = [response(500, {})]
        self.assertFalse(provider(t).read_return_action_applied(RCPT, "RECEIVE").ok)


class ServiceBase(_StockBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()

    def service(self):
        stock = super().service()
        return CoupangReturnActionService(self.db, actions_factory=stock._actions_factory, safety=self.safety)


class PlanTests(ServiceBase):

    def test_receive_is_ready_on_a_new_return(self):
        plan = self.service().plan(1, RCPT, "RECEIVE")
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.status, plan.cancel_count, plan.to_dict()["required_status"]), ("RETURNS_UNCHECKED", 1, "RETURNS_UNCHECKED"))
        self.assertEqual(plan.to_dict()["coupang"]["pre_refund"], False)
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_each_action_needs_its_own_status(self):
        cases = [("RECEIVE", "RELEASE_STOP_UNCHECKED", "WRONG_RETURN_STATUS"), ("APPROVE", "RETURNS_UNCHECKED", "WRONG_RETURN_STATUS"),
                 ("APPROVE", "REQUEST_COUPANG_CHECK", "WRONG_RETURN_STATUS"), ("APPROVE", "RELEASE_STOP_UNCHECKED", "WRONG_RETURN_STATUS")]
        for action, status, code in cases:
            self.transport.get = [response(200, ret(status=status))]
            self.assertIn(code, self.codes(self.service().plan(1, RCPT, action)), (action, status))
        self.transport.get = [response(200, ret(status="RETURNS_UNCHECKED"))]
        message = next(b.message for b in self.service().plan(1, RCPT, "APPROVE").blockers if b.code == "WRONG_RETURN_STATUS")
        self.assertIn("'반품접수'", message)                                  # 화면에는 한글 상태 이름
        self.assertIn("'입고완료'", message)
        self.assertNotIn("RETURNS_UNCHECKED", message)
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM", counts=(2,)))]
        plan = self.service().plan(1, RCPT, "APPROVE")
        self.assertTrue(plan.sendable)
        self.assertEqual(plan.cancel_count, 2)

    def test_done_returns_are_reported_not_resent(self):
        for action, status in (("RECEIVE", "VENDOR_WAREHOUSE_CONFIRM"), ("RECEIVE", "RETURNS_COMPLETED"), ("APPROVE", "RETURNS_COMPLETED")):
            self.transport.get = [response(200, ret(status=status))]
            plan = self.service().plan(1, RCPT, action)
            self.assertTrue(plan.already_applied, (action, status))
            self.assertFalse(plan.sendable)

    def test_an_unknown_count_blocks_approval(self):
        body = ret(status="VENDOR_WAREHOUSE_CONFIRM")
        body["data"][0]["returnItems"][0]["cancelCount"] = None
        self.transport.get = [response(200, body)]
        self.assertIn("CANCEL_COUNT_UNKNOWN", self.codes(self.service().plan(1, RCPT, "APPROVE")))

    def test_invalid_input_blocks_before_any_request(self):
        for receipt, action, code in (("abc", "RECEIVE", "INVALID_RECEIPT_ID"), ("1" * 21, "RECEIVE", "INVALID_RECEIPT_ID"),
                                      (True, "RECEIVE", "INVALID_RECEIPT_ID"), (RCPT, "REFUND", "INVALID_ACTION"), (RCPT, None, "INVALID_ACTION")):
            self.assertIn(code, self.codes(self.service().plan(1, receipt, action)), (receipt, action))
        self.assertEqual(self.transport.calls, [])

    def test_a_failed_read_blocks(self):
        self.transport.get = [response(400, {"message": "취소반품접수내역이 존재하지 않습니다."})]
        plan = self.service().plan(1, RCPT, "RECEIVE")
        self.assertIn("RETURN_READ_FAILED", self.codes(plan))
        self.assertIn("철회", next(b.message for b in plan.blockers if b.code == "RETURN_READ_FAILED"))

    def test_store_resolution_never_guesses(self):
        from app.domains.store_connection.model import StoreConnection

        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, RCPT, "RECEIVE")))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.service().plan(1, RCPT, "RECEIVE")))
        self.assertEqual(self.transport.calls, [])

    def test_gate_reasons_are_shown(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.service().plan(1, RCPT, "RECEIVE")
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)
        self.assertTrue(plan.live_checked)

    def test_the_plan_never_contains_credentials_or_personal_data(self):
        body = json.dumps(self.service().plan(1, RCPT, "RECEIVE").to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key", REF, VENDOR, "홍길동", "010-1234", "서울시"):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def receive(self, **override):
        args = dict(expected_status="RETURNS_UNCHECKED", confirm_received=True)
        args.update(override)
        return self.service().send(1, 7, RCPT, "RECEIVE", **args)

    def approve(self, **override):
        args = dict(expected_status="VENDOR_WAREHOUSE_CONFIRM", expected_cancel_count=1, confirm_received=True, confirm_refund=True)
        args.update(override)
        return self.service().send(1, 7, RCPT, "APPROVE", **args)

    def test_receive_goes_out_once_and_is_verified(self):
        self.transport.get = [response(200, ret()), response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        result = self.receive()
        self.assertEqual((result["outcome"], result["executed"], result["verified"], result["after_status"]),
                         ("SUCCEEDED", True, True, "VENDOR_WAREHOUSE_CONFIRM"))
        self.assertEqual((self.transport.count("patch"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("RETURN_RECEIVE", f"rcpt:{RCPT}:RECEIVE", ChannelActionStatus.SUCCEEDED, 7))

    def test_approve_sends_the_count_coupang_gave(self):
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM", counts=(2, 1))), response(200, ret(status="RETURNS_COMPLETED"))]
        result = self.approve(expected_cancel_count=3)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        body = next(r[3] for r in self.transport.requests if r[0] == "patch")
        self.assertEqual(body["cancelCount"], 3)
        self.assertEqual(self.rows()[0].action_type, "RETURN_APPROVE")

    def test_a_different_status_or_count_than_the_user_saw_refuses(self):
        for override in (dict(expected_status="RETURNS_COMPLETED"), dict(expected_status=None), dict(expected_status=1)):
            with self.assertRaises(ConflictException):
                self.receive(**override)
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM", counts=(2,)))]
        for count in (1, 3, None, True):
            with self.assertRaises(ConflictException):
                self.approve(expected_cancel_count=count)
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM", counts=(1,)))]
        with self.assertRaises(ConflictException):
            self.approve(expected_cancel_count=True)                            # 참/거짓은 1개와 같게 보지 않는다
        self.assertEqual((self.transport.count("patch"), self.rows()), (0, []))

    def test_confirmations_are_required(self):
        for value in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.receive(confirm_received=value)
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        for override in (dict(confirm_refund=False), dict(confirm_refund="yes"), dict(confirm_received=False)):
            with self.assertRaises(BadRequestException):
                self.approve(**override)
        self.assertEqual((self.transport.count("patch"), self.rows()), (0, []))

    def test_receive_does_not_need_the_refund_confirmation(self):
        self.transport.get = [response(200, ret()), response(200, ret(status="RETURNS_COMPLETED", pre_refund=True))]
        self.assertEqual(self.receive(confirm_refund=False)["outcome"], "SUCCEEDED")

    def test_a_wrong_status_is_never_sent(self):
        self.transport.get = [response(200, ret(status="RELEASE_STOP_UNCHECKED"))]
        with self.assertRaises(BadRequestException) as caught:
            self.receive(expected_status="RELEASE_STOP_UNCHECKED")
        self.assertIn("WRONG_RETURN_STATUS", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual(self.transport.count("patch"), 0)

    def test_a_done_return_is_reported_not_resent(self):
        self.transport.get = [response(200, ret(status="RETURNS_COMPLETED"))]
        result = self.approve(expected_status="RETURNS_COMPLETED")
        self.assertEqual((result["outcome"], result["executed"]), ("ALREADY_DONE", False))
        self.assertEqual((self.transport.count("patch"), self.rows()), (0, []))

    def test_a_success_without_the_new_status_is_flagged(self):
        self.transport.get = [response(200, ret()), response(200, ret())]
        result = self.receive()
        self.assertEqual((result["verified"], result["after_status"]), (False, "RETURNS_UNCHECKED"))

    def test_an_unexpected_status_after_success_is_not_called_verified(self):
        self.transport.get = [response(200, ret()), response(200, ret(status="RELEASE_STOP_UNCHECKED"))]
        result = self.receive()
        self.assertEqual((result["verified"], result["after_status"]), (False, "RELEASE_STOP_UNCHECKED"))

    def test_a_failed_verification_read_is_unknown(self):
        self.transport.get = [response(200, ret()), response(500, {})]
        result = self.receive()
        self.assertEqual((result["verified"], result["after_status"]), (None, None))

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.receive()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.receive()
        self.assertEqual((self.transport.count("patch"), self.rows()), (0, []))

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append(method.lower())
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, ret()), response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        self.service().send(1, 7, RCPT, "RECEIVE", expected_status="RETURNS_UNCHECKED", confirm_received=True,
                            before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("patch"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.service().send(1, 7, RCPT, "RECEIVE", expected_status="RETURNS_UNCHECKED", confirm_received=True, before_send=deny)
        self.assertEqual((other.count("patch"), self.rows()), (0, []))

    def test_a_rejection_is_not_repeated_without_a_person(self):
        self.transport.patch = [response(400, {"message": "취소반품접수수량과 cancelCount가 일치하지 않습니다."})]
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        first = self.approve()
        self.assertEqual((first["outcome"], first["verified"]), ("ACTION_REQUIRED", None))
        self.assertEqual(self.transport.count("get"), 1)
        second = self.approve()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("patch"), 1)
        self.transport.patch = [response(200, ok_patch())]
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM")), response(200, ret(status="RETURNS_COMPLETED"))]
        third = self.approve(retry_action_required=True)                        # 사람이 원인을 고친 뒤 다시 보낼 때만
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))
        self.assertEqual(self.transport.count("patch"), 2)

    def test_an_unknown_outcome_is_settled_only_by_reading(self):
        self.transport.patch = [response(500, {"message": "x"})]
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        unknown = self.approve()
        self.assertEqual((unknown["outcome"], unknown["verified"]), ("UNKNOWN", None))
        again = self.approve()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("patch"), 1)
        self.transport.get = [response(200, ret(status="REQUEST_COUPANG_CHECK"))]
        self.assertEqual(self.service().reconcile(1, RCPT, "APPROVE")["outcome"], "UNKNOWN")     # 다른 상태면 단정하지 않는다
        self.transport.get = [response(200, ret(status="RETURNS_COMPLETED"))]
        self.assertEqual(self.service().reconcile(1, RCPT, "APPROVE")["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("patch"), 1)
        for args in (("abc", "APPROVE"), (RCPT, "REFUND"), ("1" * 21, "RECEIVE"), (True, "RECEIVE")):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_receive_and_approve_are_separate_requests(self):
        self.transport.get = [response(200, ret()), response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        self.receive()
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM")), response(200, ret(status="RETURNS_COMPLETED"))]
        self.approve()
        self.assertEqual(self.transport.count("patch"), 2)
        self.assertEqual(sorted(r.action_type for r in self.rows()), ["RETURN_APPROVE", "RETURN_RECEIVE"])

    def test_the_audit_record(self):
        self.transport.get = [response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM", counts=(2,))), response(200, ret(status="RETURNS_COMPLETED"))]
        self.approve(expected_cancel_count=2)
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_RETURN_APPROVE", "coupang_return_request", RCPT, 7)])
        self.assertIn("수량 2", rows[0][4])
        self.assertNotIn("홍길동", rows[0][4])


class AllowanceTests(ServiceBase):

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count, purpose=None):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=purpose or CallPurpose.RETURN_PROCESS, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.service().plan(1, RCPT, "RECEIVE")))
        self.assertEqual(self.transport.calls, [])

    def test_the_after_sales_read_allowance_does_not_open_return_processing(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 5, purpose=CallPurpose.AFTER_SALES_CHECK)
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.service().plan(1, RCPT, "RECEIVE")))
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_whole_action(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, ret()), response(200, ret()), response(200, ret(status="VENDOR_WAREHOUSE_CONFIRM"))]
        self.assertTrue(self.service().plan(1, RCPT, "RECEIVE").sendable)
        result = self.service().send(1, 7, RCPT, "RECEIVE", expected_status="RETURNS_UNCHECKED", confirm_received=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("patch")), (3, 1))

    def test_a_missing_write_allowance_stops_before_the_patch(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.service().send(1, 7, RCPT, "RECEIVE", expected_status="RETURNS_UNCHECKED", confirm_received=True)
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual((self.transport.count("patch"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_preview_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("preview_coupang_return_action", "send_coupang_return_action", "reconcile_coupang_return_action"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        self.assertNotIn(".send(", router.split("def preview_coupang_return_action(")[1].split("@router")[0])
        self.assertLess(router.index('"/coupang-returns/{receipt_id}/preview"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError

        from app.domains.purchase_task.coupang_return_action_schema import CoupangReturnActionRequest

        good = dict(action="APPROVE", expected_status="VENDOR_WAREHOUSE_CONFIRM", expected_cancel_count=1, confirm_received=True, confirm_refund=True)
        CoupangReturnActionRequest(**good)
        CoupangReturnActionRequest(action="RECEIVE", expected_status="RETURNS_UNCHECKED", confirm_received=True)
        for bad in ({"extra": 1}, {"action": "REFUND"}, {"expected_status": ""}, {"expected_status": 1}, {"expected_cancel_count": 0},
                    {"expected_cancel_count": "1"}, {"confirm_received": "yes"}, {"confirm_refund": 1}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangReturnActionRequest(**{**good, **bad})

    def test_the_action_types_and_allowance_purpose_are_registered(self):
        from app.domains.external_call.constants import AllowanceMode
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.service import _DEFAULT_REQUEST

        for action_type in (ChannelActionType.RETURN_RECEIVE, ChannelActionType.RETURN_APPROVE):
            self.assertIn(action_type, ChannelActionType.ALL)
            self.assertLessEqual(len(action_type), 30)
            self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[action_type], (CallPurpose.RETURN_PROCESS, "PATCH"))
        self.assertIn(CallPurpose.RETURN_PROCESS, CallPurpose.ALL)
        self.assertIn(CallPurpose.RETURN_PROCESS, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.RETURN_PROCESS], "쿠팡 반품 처리")
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.RETURN_PROCESS, ExtKind.READ)], (AllowanceMode.COUNTED, 3, None))
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.RETURN_PROCESS, ExtKind.WRITE)], (AllowanceMode.COUNTED, 1, None))
        self.assertEqual(CoupangChannelActions.return_action_key(" 363585 ", "APPROVE"), "rcpt:363585:APPROVE")
        self.assertEqual(CoupangChannelActions.return_action_type("RECEIVE"), "RETURN_RECEIVE")


if __name__ == "__main__":
    unittest.main()
