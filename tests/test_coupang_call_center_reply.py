"""
=========================================================
Homez OS

File : tests/test_coupang_call_center_reply.py

2026-10-26 — 쿠팡 쓰기 6번: 고객센터(콜센터) 이관 문의 답변 규칙을 고정한다. 공식 "쿠팡 고객센터 문의답변"
`POST .../v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/replies`({vendorId, inquiryId, content 2~1,000자, replyBy, parentAnswerId}),
"쿠팡 고객센터 문의 단건 조회" `GET .../v5/vendors/callCenterInquiries/{inquiryId}`(replies[].answerId·partnerTransferStatus·answerType)를 따른다.
상담사가 '답변 요청'으로 넘긴 글이 정확히 하나일 때만, 사용자가 본 그 글이 그대로일 때만, 공개 확인 뒤에만 한 번 보내고 개인정보는 읽지 않는다.
격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import unittest
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
from app.domains.purchase_task.coupang_call_center_reply_provider import MAX_CC_REPLY_CHARS
from app.domains.purchase_task.coupang_call_center_reply_provider import parse_call_center_inquiry
from app.domains.purchase_task.coupang_call_center_reply_service import CoupangCallCenterReplyService
from app.domains.purchase_task.coupang_call_center_reply_service import call_center_text_problem
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_inquiry_reply import WING
from tests.test_coupang_inquiry_reply import provider
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR
from tests.test_coupang_stock import FakeTransport as _StockTransport
from tests.test_coupang_stock import ServiceBase as _StockBase

INQ = "622088519"
PARENT = "77001"
REPLY = "안녕하세요. 공급처 확인 결과 내일(10월 27일) 출고됩니다. 불편을 드려 죄송합니다."
TRANSFER = "고객이 배송 일정을 문의하셨습니다. 판매자 확인 부탁드립니다."


def agent(answer_id=77001, status="requestAnswer", content=TRANSFER):
    return {"answerId": answer_id, "parentAnswerId": None, "partnerTransferStatus": status,
            "partnerTransferCompleteReason": "NONE", "answerType": "csAgent", "needAnswer": status == "requestAnswer",
            "receptionistName": "상담사김", "receptionist": "cs_kim", "replyAt": "2026-10-26T10:00:00", "content": content}


def vendor(content=REPLY, answer_id=88001):
    return {"answerId": answer_id, "parentAnswerId": PARENT, "partnerTransferStatus": "answered", "answerType": "vendor",
            "needAnswer": False, "replyAt": "2026-10-26T11:00:00", "content": content}


def cc(replies=None, status="progress", inquiry_id=622088519, vendor_item=(70012345678,)):
    return {"inquiryId": inquiry_id, "inquiryStatus": status, "csPartnerCounselingStatus": "requestAnswer",
            "vendorItemId": list(vendor_item), "itemName": "레이펄스 바디워시", "content": "배송이 언제 되나요?",
            "replies": replies if replies is not None else [agent()], "inquiryAt": "2026-10-26T09:30:00",
            "buyerEmail": "", "buyerPhone": "010-9999-8888", "orderId": 31100000001, "orderDate": "2026-10-24T08:00:00",
            "receiptCategory": "배송 > 배송 > 배송"}


def one(**kw):
    return {"code": 200, "message": "OK", "data": cc(**kw)}


def listing(*items):
    return {"code": 200, "message": "OK", "data": {"content": list(items), "pagination": {"currentPage": 1, "totalPages": 1}}}


def ok_post():
    return {"code": "200", "message": "OK"}


class FakeTransport(_StockTransport):
    def __init__(self):
        super().__init__()
        self.get = [response(200, one())]
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

    def test_text_rules(self):
        self.assertIsNone(call_center_text_problem(REPLY))
        self.assertIsNone(call_center_text_problem("네."))
        for bad in ("", "가", " 가 ", "가" * (MAX_CC_REPLY_CHARS + 1), None):
            self.assertEqual(call_center_text_problem(bad), "INVALID_CONTENT", repr(bad))
        self.assertEqual(call_center_text_problem("[전화번호]로 연락드릴게요"), "DRAFT_MARK_LEFT")

    def test_parsing_keeps_no_personal_data_and_picks_exactly_one_pending_transfer(self):
        row = parse_call_center_inquiry(cc())
        self.assertEqual((row["pending_count"], row["pending_answer_id"], row["pending_text"]), (1, PARENT, TRANSFER))
        self.assertEqual((row["vendor_item_id"], row["order_id"], row["inquiry_status"]), ("70012345678", "31100000001", "progress"))
        dumped = json.dumps(row, ensure_ascii=False)
        for personal in ("010-9999-8888", "상담사김", "cs_kim"):
            self.assertNotIn(personal, dumped)
        two = parse_call_center_inquiry(cc(replies=[agent(1), agent(2)]))
        self.assertEqual((two["pending_count"], two["pending_answer_id"]), (2, None))
        done = parse_call_center_inquiry(cc(replies=[agent(status="answered"), vendor()]))
        self.assertEqual((done["pending_count"], done["pending_answer_id"]), (0, None))
        self.assertEqual(parse_call_center_inquiry(cc(vendor_item=()))["vendor_item_id"], None)
        odd_vendor = dict(vendor(), partnerTransferStatus="requestAnswer")            # 판매자 글은 답할 이관글이 아니다
        self.assertEqual(parse_call_center_inquiry(cc(replies=[odd_vendor]))["pending_count"], 0)
        self.assertIsNone(parse_call_center_inquiry({"inquiryId": "abc"}))


class ProviderTests(unittest.TestCase):

    def test_the_reply_follows_the_official_shape(self):
        t = FakeTransport()
        self.assertEqual(provider(t).reply_call_center(INQ, PARENT, "  " + REPLY + "\r\n").kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method), ("post", "POST"))
        self.assertTrue(url.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/callCenterInquiries/{INQ}/replies"), url)
        self.assertEqual(body, {"vendorId": VENDOR, "inquiryId": INQ, "content": REPLY, "replyBy": WING, "parentAnswerId": PARENT})

    def test_the_single_read_uses_the_documented_path_without_vendor(self):
        t = FakeTransport()
        snap = provider(t).read_call_center_inquiry(INQ)
        self.assertTrue(snap.ok)
        self.assertTrue(t.requests[0][2].endswith(f"/v2/providers/openapi/apis/api/v5/vendors/callCenterInquiries/{INQ}"), t.requests[0][2])
        self.assertEqual(snap.row["pending_answer_id"], PARENT)

    def test_the_list_asks_for_unanswered_with_page_size_30(self):
        t = FakeTransport()
        t.get = [response(200, listing(cc()))]
        read = provider(t).read_call_center_for_reply()
        self.assertTrue(read.ok)
        query = parse_qs(urlparse(t.requests[0][2]).query)
        self.assertEqual((query["partnerCounselingStatus"], query["pageSize"]), (["NO_ANSWER"], ["30"]))

    def test_without_a_wing_id_nothing_is_sent(self):
        for wing in (None, ""):
            t = FakeTransport()
            result = provider(t, wing=wing).reply_call_center(INQ, PARENT, REPLY)
            self.assertEqual((result.kind, result.error_class), (CallKind.ACTION_REQUIRED, "MISSING_WING_USER_ID"))
            self.assertEqual(t.calls, [])
        self.assertFalse(provider(FakeTransport(), wing=None).read_call_center_inquiry(INQ).reply_ready)

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for args in (("abc", PARENT, REPLY), (INQ, "x", REPLY), (INQ, None, REPLY), (True, PARENT, REPLY), ("1" * 21, PARENT, REPLY),
                     (INQ, PARENT, "가"), (INQ, PARENT, "가" * (MAX_CC_REPLY_CHARS + 1)), (INQ, PARENT, None)):
            self.assertEqual(p.reply_call_center(*args).kind, CallKind.ACTION_REQUIRED, args)
        self.assertEqual(t.calls, [])

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_post()), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR"}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "x"}), CallKind.UNKNOWN),
            (response(400, {"message": "The inquiry can't be answer."}), CallKind.ACTION_REQUIRED),
            (response(400, {"message": "상담이 종료된 문의입니다 - 522"}), CallKind.ACTION_REQUIRED),
            (response(429, {}), CallKind.RETRYABLE),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.post = [outcome]
            self.assertEqual(provider(t).reply_call_center(INQ, PARENT, REPLY).kind, expected, outcome)

    def test_an_unreadable_inquiry_is_a_failure(self):
        for outcome in (response(500, {}), response(400, {}), response(200, {"code": 200, "data": None}),
                        response(200, one(inquiry_id=111)), requests.Timeout()):
            t = FakeTransport()
            t.get = [outcome]
            self.assertFalse(provider(t).read_call_center_inquiry(INQ).ok, outcome)
        self.assertFalse(provider(FakeTransport()).read_call_center_inquiry("abc").ok)

    def test_the_applied_check(self):
        cases = [([agent(status="answered"), vendor()], (True, True)), ([agent(status="answered"), vendor(REPLY + " ")], (True, True)),
                 ([agent()], (True, False)), ([agent(status="answered"), vendor("다른 답")], (False, None)),
                 ([agent(status="answered")], (False, None))]
        for replies, expected in cases:
            t = FakeTransport()
            t.get = [response(200, one(replies=replies))]
            result = provider(t).read_call_center_reply_applied(INQ, PARENT, REPLY)
            self.assertEqual((result.ok, result.value), expected, replies)


class ServiceBase(_StockBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR, "wing_user_id": WING})

    def service(self):
        stock = super().service()
        return CoupangCallCenterReplyService(self.db, actions_factory=stock._actions_factory, safety=self.safety)


class PlanTests(ServiceBase):

    def test_a_ready_inquiry_shows_the_transfer_message(self):
        plan = self.service().plan(1, INQ, REPLY)
        self.assertTrue(plan.sendable, plan.to_dict())
        d = plan.to_dict()
        self.assertEqual((d["parent_answer_id"], d["inquiry"]["pending_text"], d["inquiry"]["question"]), (PARENT, TRANSFER, "배송이 언제 되나요?"))
        self.assertNotIn("replies", d["inquiry"])
        self.assertEqual(self.transport.calls, [("get", "GET")])

    def test_closed_answered_or_ambiguous_inquiries_are_not_sent(self):
        self.transport.get = [response(200, one(status="complete"))]
        self.assertIn("INQUIRY_CLOSED", self.codes(self.service().plan(1, INQ, REPLY)))
        self.transport.get = [response(200, one(replies=[agent(status="answered"), vendor()]))]
        self.assertTrue(self.service().plan(1, INQ, REPLY).already_answered)
        self.transport.get = [response(200, one(replies=[agent(1), agent(2)]))]
        self.assertIn("MULTIPLE_PENDING", self.codes(self.service().plan(1, INQ, REPLY)))

    def test_a_missing_wing_id_blocks(self):
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        self.assertIn("MISSING_WING_USER_ID", self.codes(self.service().plan(1, INQ, REPLY)))

    def test_invalid_input_blocks_before_any_request(self):
        for inquiry_id, content, code in (("abc", REPLY, "INVALID_INQUIRY_ID"), (True, REPLY, "INVALID_INQUIRY_ID"),
                                          (INQ, "가", "INVALID_CONTENT"), (INQ, "[이름]님", "DRAFT_MARK_LEFT")):
            self.assertIn(code, self.codes(self.service().plan(1, inquiry_id, content)), (inquiry_id, content))
        self.assertEqual(self.transport.calls, [])

    def test_a_failed_read_blocks(self):
        self.transport.get = [response(500, {})]
        self.assertIn("INQUIRY_READ_FAILED", self.codes(self.service().plan(1, INQ, REPLY)))

    def test_store_and_gates(self):
        from app.domains.store_connection.model import StoreConnection

        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.service().plan(1, INQ, REPLY)
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, INQ, REPLY)))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.service().plan(1, INQ, REPLY)))

    def test_the_unanswered_list_keeps_only_open_transfers_without_personal_data(self):
        closed = cc(inquiry_id=1, status="complete")
        answered = cc(inquiry_id=2, replies=[agent(status="answered"), vendor()])
        self.transport.get = [response(200, listing(cc(), closed, answered))]
        listed = self.service().unanswered(1)
        self.assertTrue(listed["ok"])
        self.assertEqual([r["inquiry_id"] for r in listed["rows"]], [INQ])
        self.assertNotIn("replies", listed["rows"][0])                              # 이관글 목록 전체는 내보내지 않는다
        body = json.dumps(listed, ensure_ascii=False)
        for secret in ("010-9999-8888", "상담사김", "cs_kim", "AK", VENDOR, WING):
            self.assertNotIn(secret, body)

    def test_the_plan_never_contains_credentials_or_personal_data(self):
        body = json.dumps(self.service().plan(1, INQ, REPLY).to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "access_key", REF, VENDOR, WING, "010-9999-8888", "상담사김"):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, content=REPLY, **override):
        args = dict(expected_parent_answer_id=PARENT, confirm_public=True)
        args.update(override)
        return self.service().send(1, 7, INQ, content, **args)

    def test_a_confirmed_reply_goes_out_once_and_is_verified(self):
        self.transport.get = [response(200, one()), response(200, one(replies=[agent(status="answered"), vendor()]))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual((self.transport.count("post"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("CALL_CENTER_REPLY", f"cc:{INQ}:{PARENT}", ChannelActionStatus.SUCCEEDED, 7))

    def test_a_success_without_our_answer_is_flagged_and_a_failed_reread_is_unknown(self):
        self.transport.get = [response(200, one()), response(200, one())]
        self.assertEqual(self.send()["verified"], False)
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, one()), response(500, {})]
        self.assertIsNone(self.send()["verified"])

    def test_only_a_seller_reply_counts_as_ours(self):
        echoed = dict(agent(), content=REPLY)                                        # 상담사 글이 우연히 같은 내용이어도 우리 답이 아니다
        self.transport.get = [response(200, one()), response(200, one(replies=[echoed]))]
        self.assertEqual(self.send()["verified"], False)

    def test_a_different_transfer_than_the_user_saw_refuses(self):
        for parent in ("77002", None, 77001):
            with self.assertRaises(ConflictException):
                self.send(expected_parent_answer_id=parent)
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_public_use_must_be_confirmed(self):
        for confirm in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.send(confirm_public=confirm)
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_an_answered_inquiry_is_never_sent(self):
        self.transport.get = [response(200, one(replies=[agent(status="answered"), vendor("윙 답")]))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("ALREADY_ANSWERED", False))
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
        self.transport.get = [response(200, one()), response(200, one(replies=[agent(status="answered"), vendor()]))]
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
        self.transport.post = [response(400, {"message": "userId가 올바르지 않습니다 - 520"})]
        first = self.send()
        self.assertEqual((first["outcome"], first["verified"]), ("ACTION_REQUIRED", None))
        self.assertEqual(self.transport.count("get"), 1)
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.post = [response(200, ok_post())]
        self.transport.get = [response(200, one()), response(200, one(replies=[agent(status="answered"), vendor()]))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading(self):
        self.transport.post = [requests.Timeout()]
        unknown = self.send()
        self.assertEqual((unknown["outcome"], unknown["verified"]), ("UNKNOWN", None))
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.get = [response(200, one(replies=[agent(status="answered"), vendor("윙에서 쓴 다른 답")]))]
        self.assertEqual(self.service().reconcile(1, INQ, PARENT, REPLY)["outcome"], "UNKNOWN")
        self.transport.get = [response(200, one(replies=[agent(status="answered"), vendor()]))]
        self.assertEqual(self.service().reconcile(1, INQ, PARENT, REPLY)["outcome"], "SUCCEEDED")
        for args in (("abc", PARENT, REPLY), (INQ, None, REPLY), (INQ, PARENT, "가"), ("1" * 21, PARENT, REPLY)):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_a_retransferred_inquiry_is_a_new_request(self):
        self.transport.get = [response(200, one()), response(200, one(replies=[agent(status="answered"), vendor()]))]
        self.send()
        self.transport.get = [response(200, one(replies=[agent(status="answered"), vendor(), agent(77002)])),
                              response(200, one(replies=[agent(status="answered"), vendor(), agent(77002, "answered"), vendor("두 번째 답", 88002)]))]
        second = self.send(content="두 번째 답", expected_parent_answer_id="77002")
        self.assertEqual((second["outcome"], second["executed"], second["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual(sorted(r.target_key for r in self.rows()), [f"cc:{INQ}:{PARENT}", f"cc:{INQ}:77002"])

    def test_the_audit_record_keeps_no_text(self):
        self.transport.get = [response(200, one()), response(200, one(replies=[agent(status="answered"), vendor()]))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_CALL_CENTER_REPLY", "coupang_call_center_inquiry", INQ, 7)])
        self.assertIn(f"{len(REPLY)}자", rows[0][4])
        self.assertNotIn("출고", rows[0][4])


class AllowanceTests(ServiceBase):

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count, purpose=None):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=purpose or CallPurpose.INQUIRY_REPLY, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.service().plan(1, INQ, REPLY)))
        self.assertFalse(self.service().unanswered(1)["ok"])
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_whole_reply(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, one()), response(200, one()), response(200, one(replies=[agent(status="answered"), vendor()]))]
        self.assertTrue(self.service().plan(1, INQ, REPLY).sendable)
        result = self.service().send(1, 7, INQ, REPLY, expected_parent_answer_id=PARENT, confirm_public=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("post")), (3, 1))

    def test_a_missing_write_allowance_stops_before_the_post(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.service().send(1, 7, INQ, REPLY, expected_parent_answer_id=PARENT, confirm_public=True)
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_reads_never_send(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        names = ("list_coupang_call_center_for_reply", "preview_coupang_call_center_reply", "send_coupang_call_center_reply",
                 "reconcile_coupang_call_center_reply")
        for name in names:
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        for name in names[:2]:
            self.assertNotIn(".send(", router.split(f"def {name}(")[1].split("@router")[0], name)
        self.assertLess(router.index('"/coupang-call-center-inquiries/unanswered"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError

        from app.domains.purchase_task.coupang_call_center_reply_schema import CoupangCallCenterReplyRequest

        good = dict(content=REPLY, expected_parent_answer_id=PARENT, confirm_public=True)
        CoupangCallCenterReplyRequest(**good)
        CoupangCallCenterReplyRequest(content=REPLY)
        for bad in ({"extra": 1}, {"content": "가"}, {"content": "가" * (MAX_CC_REPLY_CHARS + 1)}, {"content": 5},
                    {"expected_parent_answer_id": "abc"}, {"expected_parent_answer_id": 77001}, {"confirm_public": "yes"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangCallCenterReplyRequest(**{**good, **bad})

    def test_the_action_type_uses_the_inquiry_reply_allowance(self):
        self.assertIn(ChannelActionType.CALL_CENTER_REPLY, ChannelActionType.ALL)
        self.assertLessEqual(len(ChannelActionType.CALL_CENTER_REPLY), 30)
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.CALL_CENTER_REPLY], (CallPurpose.INQUIRY_REPLY, "POST"))
        self.assertEqual(CoupangChannelActions.call_center_reply_key(" 1 ", " 2 "), "cc:1:2")


if __name__ == "__main__":
    unittest.main()
