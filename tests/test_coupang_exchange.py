"""
=========================================================
Homez OS

File : tests/test_coupang_exchange.py

2026-10-27 — 쿠팡 쓰기 8번: 교환 거부·교환 회수 송장 등록 규칙을 고정한다. 공식 "교환요청 목록조회"
`GET .../v4/vendors/{vendorId}/exchangeRequests`(7일 미만, nextToken), "교환요청 거부 처리" `PATCH .../exchangeRequests/{id}/rejection`
({exchangeId(string), vendorId, exchangeRejectCode: SOLDOUT|WITHDRAW}, data.resultCode), 회수 송장 등록(returnExchangeDeliveryType=EXCHANGE)을 따른다.
거부는 쿠팡이 거부 가능으로 알린 열린 교환만, 회수 송장은 회수연동전에 송장이 없을 때만, 사용자가 본 상태와 같고 확인 뒤에만 한 번 보낸다.
구매자 개인정보는 읽지 않는다. 격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

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
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_exchange_provider import parse_exchange
from app.domains.purchase_task.coupang_exchange_service import CoupangExchangeService
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR
from tests.test_coupang_stock import FakeTransport as _StockTransport
from tests.test_coupang_stock import ServiceBase as _StockBase

EXCH = "100000070"
INVOICE = "584712345678"


def exchange(exchange_id=100000070, status="RECEIPT", collect="BeforeDirection", rejectable=True, invoices=()):
    return {
        "exchangeId": exchange_id, "orderId": 31100000001, "vendorId": VENDOR, "exchangeStatus": status, "collectStatus": collect,
        "deliveryStatus": "BeforeDirection", "faultType": "CUSTOMER", "reasonCodeText": "색상 변경", "reasonEtcDetail": "010-5555-6666로 연락",
        "createdAt": "2026-10-26T10:00:00", "successable": False, "rejectable": rejectable,
        "exchangeItemDtoV1s": [{"orderItemName": "바디워시 화이트", "targetItemName": "바디워시 블랙", "quantity": 1}],
        "exchangeAddressDtoV1": {"returnCustomerName": "홍길동", "returnMobile": "010-1234-5678", "returnAddress": "서울시 어딘가"},
        "returnDeliveryDtos": [{"deliveryCompanyCode": c, "deliveryInvoiceNo": n} for c, n in invoices],
    }


def page(*items, token=""):
    return {"code": 200, "message": "OK", "data": list(items), "nextToken": token}


def ok_reject():
    return {"code": "200", "message": "SUCCESS", "data": {"resultCode": "SUCCESS", "resultMessage": "요청 처리완료"}}


def ok_pickup():
    return {"code": 200, "message": "OK", "data": {"invoiceNumber": INVOICE, "returnExchangeDeliveryType": "EXCHANGE"}}


class FakeTransport(_StockTransport):
    def __init__(self):
        super().__init__()
        self.get = [response(200, page(exchange()))]
        self.patch = [response(200, ok_reject())]
        self.post = [response(200, ok_pickup())]

    def request(self, method, url, *, headers, json_body, timeout):
        kind = {"PATCH": "patch", "POST": "post"}.get(method, "get")
        with self.lock:
            self.calls.append((kind, method))
            self.requests.append((kind, method, url, json_body))
            queue = {"patch": self.patch, "post": self.post}.get(kind, self.get)
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def provider(transport):
    from datetime import timezone

    return CoupangChannelControlProvider({"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR}, transport=transport,
                                         now_factory=lambda: datetime(2026, 10, 27, 3, 0, 0, tzinfo=timezone.utc))


class PureTests(unittest.TestCase):

    def test_parsing_keeps_no_personal_data_or_free_text(self):
        row = parse_exchange(exchange(invoices=[("CJGLS", INVOICE), ("CJGLS", INVOICE), ("CJGLS", ""), ("CJGLS", None)]))
        self.assertEqual((row["exchange_id"], row["status"], row["collect_status"], row["rejectable"]), (EXCH, "RECEIPT", "BeforeDirection", True))
        self.assertEqual(row["items"], [{"order_item": "바디워시 화이트", "target_item": "바디워시 블랙", "quantity": 1}])
        self.assertEqual(row["return_invoices"], [{"courier": "CJGLS", "invoice": INVOICE}])
        dumped = json.dumps(row, ensure_ascii=False)
        for personal in ("홍길동", "010-1234-5678", "서울시", "010-5555-6666"):
            self.assertNotIn(personal, dumped)

    def test_unknown_shapes_are_dropped_not_guessed(self):
        self.assertIsNone(parse_exchange(exchange(status="WHATEVER")))
        self.assertIsNone(parse_exchange({"exchangeId": "abc", "exchangeStatus": "RECEIPT"}))
        self.assertFalse(parse_exchange(exchange(rejectable="true"))["rejectable"])        # 참/거짓 값만 믿는다


class ProviderTests(unittest.TestCase):

    def test_the_list_uses_a_window_under_seven_days_and_follows_next_token(self):
        t = FakeTransport()
        t.get = [response(200, page(exchange(1), token="abc")), response(200, page(exchange(2)))]
        read = provider(t).read_exchanges()
        self.assertTrue(read.ok)
        self.assertEqual([r["exchange_id"] for r in read.rows], ["1", "2"])
        first, second = (parse_qs(urlparse(r[2]).query) for r in t.requests)
        self.assertTrue(t.requests[0][2].split("?")[0].endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/exchangeRequests"))
        self.assertEqual((first["createdAtFrom"], first["createdAtTo"]), (["2026-10-20T12:01:00"], ["2026-10-27T12:00:00"]))   # KST, 7일 미만
        self.assertNotIn("nextToken", first)
        self.assertEqual(second["nextToken"], ["abc"])

    def test_too_many_pages_are_reported_as_truncated(self):
        t = FakeTransport()
        t.get = [response(200, page(exchange(1), token="more"))]
        read = provider(t).read_exchanges()
        self.assertEqual((read.ok, read.truncated, t.count("get")), (True, True, 5))

    def test_an_unreadable_list_is_a_failure(self):
        for outcome in (response(400, {"message": "createdAtTo - createdAtFrom should less then 7day."}), response(500, {}),
                        response(200, {"code": 200, "data": None}), response(200, {"code": 200, "data": {"exchangeId": 1}}), requests.Timeout()):
            t = FakeTransport()
            t.get = [outcome]
            self.assertFalse(provider(t).read_exchanges().ok, outcome)
        self.assertFalse(provider(FakeTransport()).find_exchange("abc").ok)

    def test_the_rejection_follows_the_official_shape(self):
        t = FakeTransport()
        self.assertEqual(provider(t).reject_exchange(EXCH, "SOLDOUT").kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method), ("patch", "PATCH"))
        self.assertTrue(url.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/exchangeRequests/{EXCH}/rejection"), url)
        self.assertEqual(body, {"exchangeId": EXCH, "vendorId": VENDOR, "exchangeRejectCode": "SOLDOUT"})
        self.assertIsInstance(body["exchangeId"], str)                             # 문서: 본문의 exchangeId는 string

    def test_invalid_rejections_make_no_request(self):
        t = FakeTransport()
        for args in (("abc", "SOLDOUT"), (True, "SOLDOUT"), (EXCH, "OTHER"), (EXCH, None), (EXCH, "soldout")):
            self.assertEqual(provider(t).reject_exchange(*args).kind, CallKind.ACTION_REQUIRED, args)
        self.assertEqual(t.calls, [])

    def test_the_rejection_judgement(self):
        cases = [
            (response(200, ok_reject()), CallKind.SUCCEEDED),
            (response(200, {"code": "200", "data": {"resultCode": "FAIL", "resultMessage": "거부 불가"}}), CallKind.ACTION_REQUIRED),
            (response(200, {"code": "ERROR"}), CallKind.ACTION_REQUIRED),
            (response(200, {"code": "200", "message": "SUCCESS"}), CallKind.UNKNOWN),            # 안쪽 결과가 없으면 단정하지 않는다
            (response(400, {"message": "x"}), CallKind.ACTION_REQUIRED),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.patch = [outcome]
            self.assertEqual(provider(t).reject_exchange(EXCH, "WITHDRAW").kind, expected, outcome)

    def test_the_exchange_pickup_uses_the_exchange_type(self):
        t = FakeTransport()
        self.assertEqual(provider(t).register_pickup_invoice(EXCH, "CJGLS", INVOICE, None, "EXCHANGE").kind, CallKind.SUCCEEDED)
        body = t.requests[0][3]
        self.assertEqual(body, {"returnExchangeDeliveryType": "EXCHANGE", "receiptId": int(EXCH), "deliveryCompanyCode": "CJGLS", "invoiceNumber": INVOICE})
        self.assertEqual(provider(FakeTransport()).register_pickup_invoice(EXCH, "CJGLS", INVOICE, None, "OTHER").kind, CallKind.ACTION_REQUIRED)

    def test_the_applied_checks(self):
        cases = [(exchange(status="REJECT"), (True, True)), (exchange(), (True, False)), (exchange(status="PROGRESS"), (True, False)),
                 (exchange(status="SUCCESS"), (False, None)), (exchange(status="CANCEL"), (False, None)), (exchange(1), (False, None))]
        for item, expected in cases:
            t = FakeTransport()
            t.get = [response(200, page(item))]
            result = provider(t).read_exchange_reject_applied(EXCH)
            self.assertEqual((result.ok, result.value), expected, item["exchangeStatus"])
        cases = [(exchange(invoices=[("CJGLS", INVOICE)]), (True, True)), (exchange(), (True, False)),
                 (exchange(invoices=[("HANJIN", "999999999")]), (False, None)), (exchange(collect="Delivering"), (False, None))]
        for item, expected in cases:
            t = FakeTransport()
            t.get = [response(200, page(item))]
            result = provider(t).read_exchange_pickup_applied(EXCH, INVOICE)
            self.assertEqual((result.ok, result.value), expected, item)


class ServiceBase(_StockBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()

    def service(self):
        stock = super().service()
        return CoupangExchangeService(self.db, actions_factory=stock._actions_factory, safety=self.safety)

    def reject_plan(self, code="SOLDOUT"):
        return self.service().plan(1, EXCH, "REJECT", reject_code=code)

    def pickup_plan(self, courier="CJ대한통운", invoice=INVOICE):
        return self.service().plan(1, EXCH, "PICKUP", courier=courier, invoice_number=invoice)


class PlanTests(ServiceBase):

    def test_an_open_rejectable_exchange_is_ready(self):
        plan = self.reject_plan()
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.watched_status, plan.reject_code), ("RECEIPT", "SOLDOUT"))
        self.assertNotIn("홍길동", json.dumps(plan.to_dict(), ensure_ascii=False))

    def test_rejection_needs_coupangs_rejectable_flag_and_an_open_exchange(self):
        for item in (exchange(rejectable=False), exchange(status="SUCCESS"), exchange(status="CANCEL")):
            self.transport.get = [response(200, page(item))]
            self.assertIn("NOT_REJECTABLE", self.codes(self.reject_plan()), item["exchangeStatus"])
        self.transport.get = [response(200, page(exchange(status="REJECT")))]
        self.assertTrue(self.reject_plan().already_applied)

    def test_a_pickup_needs_before_direction_and_no_invoice(self):
        self.assertTrue(self.pickup_plan().sendable)
        self.assertEqual(self.pickup_plan().watched_status, "BeforeDirection")
        self.transport.get = [response(200, page(exchange(collect="Delivering")))]
        self.assertIn("WRONG_COLLECT_STATUS", self.codes(self.pickup_plan()))
        self.transport.get = [response(200, page(exchange(invoices=[("HANJIN", "999999999")])))]
        self.assertIn("HAS_OTHER_INVOICE", self.codes(self.pickup_plan()))
        self.transport.get = [response(200, page(exchange(invoices=[("CJGLS", INVOICE)])))]
        self.assertTrue(self.pickup_plan().already_applied)

    def test_a_missing_exchange_is_not_guessed(self):
        self.transport.get = [response(200, page(exchange(1), token="more"))]
        plan = self.reject_plan()
        self.assertIn("EXCHANGE_NOT_FOUND", self.codes(plan))
        self.assertIn("일부만", next(b.message for b in plan.blockers if b.code == "EXCHANGE_NOT_FOUND"))

    def test_invalid_input_blocks_before_any_request(self):
        cases = [(dict(exchange_id="abc", action="REJECT", reject_code="SOLDOUT"), "INVALID_EXCHANGE_ID"),
                 (dict(exchange_id=EXCH, action="REJECT", reject_code="OTHER"), "INVALID_REJECT_CODE"),
                 (dict(exchange_id=EXCH, action="REJECT"), "INVALID_REJECT_CODE"),
                 (dict(exchange_id=EXCH, action="PICKUP", courier="모르는택배", invoice_number=INVOICE), "UNKNOWN_COURIER"),
                 (dict(exchange_id=EXCH, action="PICKUP", courier="CJ대한통운", invoice_number="12345"), "INVALID_INVOICE_NUMBER"),
                 (dict(exchange_id=EXCH, action="RECEIVE"), "INVALID_ACTION")]
        for kw, code in cases:
            exchange_id = kw.pop("exchange_id")
            action = kw.pop("action")
            self.assertIn(code, self.codes(self.service().plan(1, exchange_id, action, **kw)), (action, kw))
        self.assertEqual(self.transport.calls, [])

    def test_a_failed_read_blocks(self):
        self.transport.get = [response(500, {})]
        self.assertIn("EXCHANGE_READ_FAILED", self.codes(self.reject_plan()))

    def test_store_and_gates(self):
        from app.domains.store_connection.model import StoreConnection

        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        self.assertEqual({b.code for b in self.reject_plan().gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.reject_plan()))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.reject_plan()))

    def test_the_open_list_keeps_only_open_exchanges_without_personal_data(self):
        self.transport.get = [response(200, page(exchange(1), exchange(2, status="PROGRESS"), exchange(3, status="SUCCESS"), exchange(4, status="CANCEL")))]
        listed = self.service().open_exchanges(1)
        self.assertEqual([r["exchange_id"] for r in listed["rows"]], ["1", "2"])
        body = json.dumps(listed, ensure_ascii=False)
        for secret in ("홍길동", "010-1234-5678", "010-5555", "AK", REF):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def reject(self, **override):
        args = dict(reject_code="SOLDOUT", expected_status="RECEIPT", confirm=True)
        args.update(override)
        return self.service().send(1, 7, EXCH, "REJECT", **args)

    def pickup(self, **override):
        args = dict(courier="CJ대한통운", invoice_number=INVOICE, expected_status="BeforeDirection", confirm=True)
        args.update(override)
        return self.service().send(1, 7, EXCH, "PICKUP", **args)

    def test_a_confirmed_rejection_goes_out_once_and_is_verified(self):
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(status="REJECT")))]
        result = self.reject()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual(self.transport.count("patch"), 1)
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status), ("EXCHANGE_REJECT", f"exch:{EXCH}:REJECT", ChannelActionStatus.SUCCEEDED))

    def test_a_confirmed_pickup_goes_out_once_and_is_verified(self):
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(invoices=[("CJGLS", INVOICE)])))]
        result = self.pickup()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual(self.transport.count("post"), 1)
        self.assertEqual(self.transport.requests[-2][3]["returnExchangeDeliveryType"], "EXCHANGE")
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key), ("EXCHANGE_PICKUP_INVOICE", f"exch:{EXCH}:PICKUP"))

    def test_unchanged_results_are_flagged_and_failed_rereads_are_unknown(self):
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange()))]
        self.assertEqual(self.reject()["verified"], False)
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, page(exchange())), response(500, {})]
        self.assertIsNone(self.reject()["verified"])
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(1)))]
        self.assertIsNone(self.reject()["verified"])                               # 다시 못 찾으면 확인 안 됨
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(status="PROGRESS")))]
        self.assertEqual(self.reject()["verified"], False)                         # 거부가 아닌 다른 상태로 바뀐 것은 확인이 아니다
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(invoices=[("HANJIN", "999999999")])))]
        self.assertEqual(self.pickup()["verified"], False)                         # 다른 송장만 붙어 있으면 확인이 아니다

    def test_a_different_status_than_the_user_saw_refuses(self):
        for expected in ("PROGRESS", None, 1):
            with self.assertRaises(ConflictException):
                self.reject(expected_status=expected)
        with self.assertRaises(ConflictException):
            self.pickup(expected_status="Delivering")
        self.assertEqual((self.transport.count("patch"), self.transport.count("post"), self.rows()), (0, 0, []))

    def test_the_confirmation_is_required(self):
        for confirm in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.reject(confirm=confirm)
            with self.assertRaises(BadRequestException):
                self.pickup(confirm=confirm)
        self.assertEqual((self.transport.count("patch"), self.transport.count("post"), self.rows()), (0, 0, []))

    def test_already_applied_is_reported_not_resent(self):
        self.transport.get = [response(200, page(exchange(status="REJECT")))]
        result = self.reject(expected_status="REJECT")
        self.assertEqual((result["outcome"], result["executed"]), ("ALREADY_DONE", False))
        self.assertEqual(self.transport.count("patch"), 0)

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.reject()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.pickup()
        self.assertEqual(self.rows(), [])

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append(method.lower())
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(status="REJECT")))]
        self.reject(before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("patch"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.reject(before_send=deny)
        self.assertEqual((other.count("patch"), self.rows()), (0, []))

    def test_a_rejection_is_not_repeated_without_a_person(self):
        self.transport.patch = [response(200, {"code": "200", "data": {"resultCode": "FAIL", "resultMessage": "거부 불가"}})]
        first = self.reject()
        self.assertEqual((first["outcome"], first["verified"]), ("ACTION_REQUIRED", None))
        second = self.reject()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("patch"), 1)
        self.transport.patch = [response(200, ok_reject())]
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(status="REJECT")))]
        third = self.reject(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_unknown_outcomes_are_settled_only_by_reading(self):
        self.transport.patch = [requests.Timeout()]
        self.assertEqual(self.reject()["outcome"], "UNKNOWN")
        self.assertFalse(self.reject()["executed"])
        self.assertEqual(self.transport.count("patch"), 1)
        self.transport.get = [response(200, page(exchange(status="SUCCESS")))]
        self.assertEqual(self.service().reconcile(1, EXCH, "REJECT")["outcome"], "UNKNOWN")
        self.transport.get = [response(200, page(exchange(status="REJECT")))]
        self.assertEqual(self.service().reconcile(1, EXCH, "REJECT")["outcome"], "SUCCEEDED")
        self.transport.post = [requests.Timeout()]
        self.transport.get = [response(200, page(exchange()))]
        self.assertEqual(self.pickup()["outcome"], "UNKNOWN")
        self.transport.get = [response(200, page(exchange(invoices=[("CJGLS", INVOICE)])))]
        self.assertEqual(self.service().reconcile(1, EXCH, "PICKUP", INVOICE)["outcome"], "SUCCEEDED")
        for args in (("abc", "REJECT"), (EXCH, "RECEIVE"), (EXCH, "PICKUP"), (EXCH, "PICKUP", "  ")):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_audit_records(self):
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange(status="REJECT")))]
        self.reject()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_EXCHANGE_REJECT", "coupang_exchange_request", EXCH, 7)])
        self.assertIn("SOLDOUT", rows[0][4])


class AllowanceTests(ServiceBase):

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count, purpose=None):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=purpose or CallPurpose.RETURN_PROCESS, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.reject_plan()))
        self.assertFalse(self.service().open_exchanges(1)["ok"])
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_rejection(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, page(exchange())), response(200, page(exchange())), response(200, page(exchange(status="REJECT")))]
        self.assertTrue(self.reject_plan().sendable)
        result = self.service().send(1, 7, EXCH, "REJECT", reject_code="SOLDOUT", expected_status="RECEIPT", confirm=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))

    def test_a_missing_write_allowance_stops_before_the_patch(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.service().send(1, 7, EXCH, "REJECT", reject_code="SOLDOUT", expected_status="RECEIPT", confirm=True)
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual((self.transport.count("patch"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_reads_never_send(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        names = ("list_coupang_open_exchanges", "preview_coupang_exchange_action", "send_coupang_exchange_action", "reconcile_coupang_exchange_action")
        for name in names:
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        for name in names[:2]:
            self.assertNotIn(".send(", router.split(f"def {name}(")[1].split("@router")[0], name)
        self.assertLess(router.index('"/coupang-exchanges/open"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError

        from app.domains.purchase_task.coupang_exchange_schema import CoupangExchangeActionRequest

        CoupangExchangeActionRequest(action="REJECT", reject_code="SOLDOUT", expected_status="RECEIPT", confirm=True)
        CoupangExchangeActionRequest(action="PICKUP", courier="CJ대한통운", invoice_number=INVOICE, expected_status="BeforeDirection", confirm=True)
        good = dict(action="REJECT", reject_code="SOLDOUT", expected_status="RECEIPT", confirm=True)
        for bad in ({"extra": 1}, {"action": "RECEIVE"}, {"reject_code": "OTHER"}, {"invoice_number": "12345"}, {"expected_status": ""},
                    {"confirm": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangExchangeActionRequest(**{**good, **bad})

    def test_the_action_types_use_the_return_processing_allowance(self):
        for action_type, method in ((ChannelActionType.EXCHANGE_REJECT, "PATCH"), (ChannelActionType.EXCHANGE_PICKUP_INVOICE, "POST")):
            self.assertIn(action_type, ChannelActionType.ALL)
            self.assertLessEqual(len(action_type), 30)
            self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[action_type], (CallPurpose.RETURN_PROCESS, method))
        self.assertEqual(CoupangChannelActions.exchange_action_key(" 1 ", "REJECT"), "exch:1:REJECT")


if __name__ == "__main__":
    unittest.main()
