"""
=========================================================
Homez OS

File : tests/test_coupang_inquiry_reply.py

2026-10-25 — 쿠팡 쓰기 4번: 상품 문의 답변 규칙을 고정한다. 공식 "상품별 고객문의 답변"
`POST .../vendors/{vendorId}/onlineInquiries/{inquiryId}/replies`({content, vendorId, replyBy})과 "상품별 고객문의 조회"
`GET .../onlineInquiries`(commentDtoList)를 따른다. 한 문의에 한 번만, 쿠팡에 답이 없을 때만, 고객에게 바로 보인다는 확인 뒤에만,
WING 아이디가 자격증명에 있을 때만 보내고, 초안 가림 표시가 남은 글은 보내지 않는다. 격리 SQLite + 가짜 HTTP(외부 호출 없음).
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
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.external_call.constants import CallPurpose
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_inquiry_reply_provider import MAX_REPLY_CHARS
from app.domains.purchase_task.coupang_inquiry_reply_provider import normalize_reply
from app.domains.purchase_task.coupang_inquiry_reply_service import CoupangInquiryReplyService
from app.domains.purchase_task.coupang_inquiry_reply_service import reply_problem
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR
from tests.test_coupang_stock import FakeTransport as _StockTransport
from tests.test_coupang_stock import ServiceBase as _StockBase

INQ = "846"
WING = "homez_wing"
REPLY = "안녕하세요. 내일(10월 26일) 출고 예정입니다.\n감사합니다."
QUESTION = "언제 받을 수 있나요? 010-1234-5678로 연락 주세요"


def inquiry(inquiry_id=846, comments=(), question=QUESTION):
    return {"inquiryId": inquiry_id, "productId": 1, "sellerProductId": 15000123456, "sellerItemId": 1, "vendorItemId": 70012345678,
            "content": question, "inquiryAt": "2026-10-25T09:00:00", "orderIds": [111, 222],
            "commentDtoList": [{"inquiryCommentId": 9, "inquiryId": inquiry_id, "content": c, "inquiryCommentAt": "2026-10-25T10:00:00"}
                               for c in comments]}


def inquiries(*items, total_pages=1):
    return {"code": 200, "message": "OK", "data": {"content": list(items),
            "pagination": {"currentPage": 1, "totalPages": total_pages, "totalElements": len(items), "countPerPage": 50}}}


def ok_post():
    return {"code": "200", "message": "OK"}


class FakeTransport(_StockTransport):
    """GET·POST를 따로 센다(답변 등록은 POST)."""

    def __init__(self):
        super().__init__()
        self.get = [response(200, inquiries(inquiry()))]
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


def provider(transport, wing=WING):
    cred = {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR}
    if wing is not None:
        cred["wing_user_id"] = wing
    return CoupangChannelControlProvider(cred, transport=transport)


class PureTests(unittest.TestCase):

    def test_reply_text_rules(self):
        self.assertIsNone(reply_problem(REPLY))
        self.assertIsNone(reply_problem("가" * MAX_REPLY_CHARS))
        for bad in ("", "   ", "가" * (MAX_REPLY_CHARS + 1), None, 3):
            self.assertEqual(reply_problem(bad), "INVALID_CONTENT", repr(bad))
        for left in ("연락처 [전화번호]로 주세요", "[이름]님 안녕하세요", "답변\n※ 초안입니다. 사람이 읽고 고친 뒤"):
            self.assertEqual(reply_problem(left), "DRAFT_MARK_LEFT", left)

    def test_line_breaks_are_normalized_only(self):
        self.assertEqual(normalize_reply("  가\r\n나\r다 \n"), "가\n나\n다")


class ProviderTests(unittest.TestCase):

    def test_the_reply_follows_the_official_shape(self):
        t = FakeTransport()
        self.assertEqual(provider(t).reply_online_inquiry(INQ, "  " + REPLY.replace("\n", "\r\n") + " ").kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method), ("post", "POST"))
        self.assertTrue(url.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/onlineInquiries/{INQ}/replies"), url)
        self.assertEqual(body, {"content": REPLY, "vendorId": VENDOR, "replyBy": WING})

    def test_without_a_wing_id_nothing_is_sent(self):
        for wing in (None, "", "  "):
            t = FakeTransport()
            result = provider(t, wing=wing).reply_online_inquiry(INQ, REPLY)
            self.assertEqual((result.kind, result.error_class), (CallKind.ACTION_REQUIRED, "MISSING_WING_USER_ID"), wing)
            self.assertEqual(t.calls, [])

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for inquiry_id, content in (("abc", REPLY), ("", REPLY), ("1" * 21, REPLY), (True, REPLY), (INQ, ""), (INQ, "  "),
                                    (INQ, "가" * (MAX_REPLY_CHARS + 1)), (INQ, None)):
            self.assertEqual(p.reply_online_inquiry(inquiry_id, content).kind, CallKind.ACTION_REQUIRED, (inquiry_id, content))
        self.assertEqual(t.calls, [])

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_post()), CallKind.SUCCEEDED),
            (response(200, {"code": "SUCCESS"}), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR", "message": "x"}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "no code"}), CallKind.UNKNOWN),
            (response(400, {"code": 400, "message": "동일한 답변이 존재합니다."}), CallKind.ACTION_REQUIRED),
            (response(400, {"message": "삭제된 상품문의에는 더 이상 답변할 수 없습니다."}), CallKind.ACTION_REQUIRED),
            (response(429, {}), CallKind.RETRYABLE),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.post = [outcome]
            self.assertEqual(provider(t).reply_online_inquiry(INQ, REPLY).kind, expected, outcome)

    def test_finding_an_inquiry_reads_all_answer_states_and_keeps_the_question_for_display(self):
        t = FakeTransport()
        t.get = [response(200, inquiries(inquiry(845), inquiry(846, comments=("기존 답",))))]
        found = provider(t).find_online_inquiry(INQ)
        self.assertEqual((found.ok, found.found, found.reply_ready), (True, True, True))
        self.assertEqual(found.row["question"], QUESTION)
        self.assertEqual((found.row["answered"], found.row["order_count"], found.row["vendor_item_id"]), (True, 2, "70012345678"))
        query = parse_qs(urlparse(t.requests[0][2]).query)
        self.assertEqual(query["answeredType"], ["ALL"])
        self.assertEqual(provider(FakeTransport(), wing=None).find_online_inquiry(INQ).reply_ready, False)

    def test_the_unanswered_list_asks_coupang_for_noanswer(self):
        t = FakeTransport()
        provider(t).read_inquiries_for_reply()
        self.assertEqual(parse_qs(urlparse(t.requests[0][2]).query)["answeredType"], ["NOANSWER"])
        self.assertFalse(provider(t).read_inquiries_for_reply(answered_type="X").ok)

    def test_a_long_question_is_clipped_for_display(self):
        t = FakeTransport()
        t.get = [response(200, inquiries(inquiry(question="가" * 2500)))]
        row = provider(t).find_online_inquiry(INQ).row
        self.assertEqual((len(row["question"]), row["question_clipped"]), (2000, True))

    def test_an_unreadable_list_is_a_failure(self):
        for outcome in (response(500, {}), response(200, {"code": 200, "data": None}), requests.Timeout()):
            t = FakeTransport()
            t.get = [outcome]
            self.assertFalse(provider(t).find_online_inquiry(INQ).ok, outcome)
        self.assertFalse(provider(FakeTransport()).find_online_inquiry("abc").ok)

    def test_the_applied_check_never_reads_another_answer_as_not_sent(self):
        cases = [((REPLY,), (True, True)), ((REPLY.replace("\n", "\r\n") + " ",), (True, True)), ((), (True, False)),
                 (("다른 사람이 윙에서 쓴 답",), (False, None))]
        for comments, expected in cases:
            t = FakeTransport()
            t.get = [response(200, inquiries(inquiry(comments=comments)))]
            result = provider(t).read_inquiry_reply_applied(INQ, REPLY)
            self.assertEqual((result.ok, result.value), expected, comments)
        t = FakeTransport()
        t.get = [response(200, inquiries(inquiry(845)))]
        self.assertEqual(provider(t).read_inquiry_reply_applied(INQ, REPLY).ok, False)     # 못 찾으면 판단하지 않는다


class ServiceBase(_StockBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR, "wing_user_id": WING})

    def service(self):
        stock = super().service()
        return CoupangInquiryReplyService(self.db, actions_factory=stock._actions_factory, safety=self.safety)


class PlanTests(ServiceBase):

    def test_a_ready_inquiry_shows_the_question(self):
        plan = self.service().plan(1, INQ, REPLY)
        self.assertTrue(plan.sendable, plan.to_dict())
        d = plan.to_dict()
        self.assertEqual(d["inquiry"]["question"], QUESTION)
        self.assertEqual((d["inquiry"]["answer_count"], d["content_chars"]), (0, len(REPLY)))
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_an_answered_inquiry_is_reported_not_answered_again(self):
        self.transport.get = [response(200, inquiries(inquiry(comments=("윙에서 쓴 답",))))]
        plan = self.service().plan(1, INQ, REPLY)
        self.assertTrue(plan.already_answered)
        self.assertFalse(plan.sendable)

    def test_an_inquiry_outside_the_window_is_not_guessed(self):
        self.transport.get = [response(200, inquiries(inquiry(845), total_pages=5))] * 3
        plan = self.service().plan(1, INQ, REPLY)
        self.assertIn("INQUIRY_NOT_FOUND", self.codes(plan))
        self.assertIn("일부만", next(b.message for b in plan.blockers if b.code == "INQUIRY_NOT_FOUND"))

    def test_a_missing_wing_id_blocks(self):
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        self.assertIn("MISSING_WING_USER_ID", self.codes(self.service().plan(1, INQ, REPLY)))

    def test_invalid_input_blocks_before_any_request(self):
        for inquiry_id, content, code in (("abc", REPLY, "INVALID_INQUIRY_ID"), ("1" * 21, REPLY, "INVALID_INQUIRY_ID"),
                                          (True, REPLY, "INVALID_INQUIRY_ID"), (INQ, "", "INVALID_CONTENT"),
                                          (INQ, "[전화번호]로 연락", "DRAFT_MARK_LEFT")):
            self.assertIn(code, self.codes(self.service().plan(1, inquiry_id, content)), (inquiry_id, content))
        self.assertEqual(self.transport.calls, [])

    def test_a_failed_read_blocks(self):
        self.transport.get = [response(500, {})]
        self.assertIn("INQUIRY_READ_FAILED", self.codes(self.service().plan(1, INQ, REPLY)))

    def test_store_resolution_never_guesses(self):
        from app.domains.store_connection.model import StoreConnection

        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, INQ, REPLY)))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.service().plan(1, INQ, REPLY)))
        self.assertEqual(self.transport.calls, [])

    def test_gate_reasons_are_shown(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.service().plan(1, INQ, REPLY)
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)

    def test_the_unanswered_list_drops_answers_and_answered_rows(self):
        self.transport.get = [response(200, inquiries(inquiry(845), inquiry(846, comments=("답",))))]
        listed = self.service().unanswered(1)
        self.assertTrue(listed["ok"])
        self.assertEqual([r["inquiry_id"] for r in listed["rows"]], [845])
        self.assertNotIn("comments", listed["rows"][0])
        self.assertEqual(listed["rows"][0]["question"], QUESTION)

    def test_the_plan_never_contains_credentials(self):
        body = json.dumps(self.service().plan(1, INQ, REPLY).to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key", REF, VENDOR, WING):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, content=REPLY, **override):
        args = dict(confirm_public=True)
        args.update(override)
        return self.service().send(1, 7, INQ, content, **args)

    def test_a_confirmed_reply_goes_out_once_and_is_verified(self):
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry(comments=(REPLY,))))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual((self.transport.count("post"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("INQUIRY_REPLY", f"inq:{INQ}", ChannelActionStatus.SUCCEEDED, 7))

    def test_a_success_without_our_answer_after_is_flagged(self):
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry()))]
        self.assertEqual(self.send()["verified"], False)

    def test_a_failed_verification_read_is_unknown(self):
        self.transport.get = [response(200, inquiries(inquiry())), response(500, {})]
        self.assertIsNone(self.send()["verified"])

    def test_an_inquiry_missing_on_the_verification_read_is_unknown_not_an_error(self):
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry(845)))]
        result = self.send()
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", None))

    def test_one_inquiry_is_answered_once_even_with_a_different_text(self):
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry()))]
        self.send()
        self.transport.get = [response(200, inquiries(inquiry()))]
        again = self.send(content="다른 글로 다시")
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("post"), 1)

    def test_an_already_answered_inquiry_is_never_sent(self):
        self.transport.get = [response(200, inquiries(inquiry(comments=("윙 답",))))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("ALREADY_ANSWERED", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_public_visibility_must_be_confirmed(self):
        for confirm in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.send(confirm_public=confirm)
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_draft_marks_are_never_sent(self):
        with self.assertRaises(BadRequestException) as caught:
            self.send(content="[이름]님 내일 출고됩니다")
        self.assertIn("DRAFT_MARK_LEFT", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual(self.transport.calls, [])

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
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry(comments=(REPLY,))))]
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
        self.transport.post = [response(400, {"message": "The value of replyBy is incorrect."})]
        first = self.send()
        self.assertEqual((first["outcome"], first["verified"]), ("ACTION_REQUIRED", None))
        self.assertEqual(self.transport.count("get"), 1)
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.post = [response(200, ok_post())]
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry(comments=(REPLY,))))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading(self):
        self.transport.post = [requests.Timeout()]
        unknown = self.send()
        self.assertEqual((unknown["outcome"], unknown["verified"]), ("UNKNOWN", None))
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.get = [response(200, inquiries(inquiry(comments=("윙에서 쓴 다른 답",))))]
        self.assertEqual(self.service().reconcile(1, INQ, REPLY)["outcome"], "UNKNOWN")       # 다른 답이면 단정하지 않는다
        self.transport.get = [response(200, inquiries(inquiry(comments=(REPLY,))))]
        self.assertEqual(self.service().reconcile(1, INQ, REPLY)["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("post"), 1)
        for args in (("abc", REPLY), (INQ, ""), ("1" * 21, REPLY)):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_the_audit_record_keeps_no_text(self):
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry(comments=(REPLY,))))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_INQUIRY_REPLY", "coupang_online_inquiry", INQ, 7)])
        self.assertIn(f"{len(REPLY)}자", rows[0][4])
        self.assertNotIn("출고", rows[0][4])
        self.assertNotIn("010", rows[0][4])


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

    def test_the_after_sales_read_allowance_does_not_open_replies(self):
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.constants import AllowanceMode

        from tests.test_external_call_enforcement import _grant

        _grant(self.db, kind=ExtKind.READ, purpose=CallPurpose.AFTER_SALES_CHECK, mode=AllowanceMode.COUNTED, count=5)
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.service().plan(1, INQ, REPLY)))
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_whole_reply(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, inquiries(inquiry())), response(200, inquiries(inquiry())), response(200, inquiries(inquiry(comments=(REPLY,))))]
        self.assertTrue(self.service().plan(1, INQ, REPLY).sendable)
        result = self.send_ok()
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("post")), (3, 1))

    def send_ok(self):
        return self.service().send(1, 7, INQ, REPLY, confirm_public=True)

    def test_a_missing_write_allowance_stops_before_the_post(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.send_ok()
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_reads_never_send(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("list_coupang_inquiries_for_reply", "preview_coupang_inquiry_reply", "send_coupang_inquiry_reply", "reconcile_coupang_inquiry_reply"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        for name in ("list_coupang_inquiries_for_reply", "preview_coupang_inquiry_reply"):
            self.assertNotIn(".send(", router.split(f"def {name}(")[1].split("@router")[0], name)
        self.assertLess(router.index('"/coupang-inquiries/{inquiry_id}/reply/preview"'), router.index('"/{task_id}/coupang-invoice/preview"'))
        self.assertLess(router.index('"/coupang-inquiries/unanswered"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError

        from app.domains.purchase_task.coupang_inquiry_reply_schema import CoupangInquiryReplyRequest

        good = dict(content=REPLY, confirm_public=True)
        CoupangInquiryReplyRequest(**good)
        for bad in ({"extra": 1}, {"content": ""}, {"content": "가" * (MAX_REPLY_CHARS + 1)}, {"content": 5},
                    {"confirm_public": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangInquiryReplyRequest(**{**good, **bad})

    def test_the_action_type_and_allowance_purpose_are_registered(self):
        from app.domains.external_call.constants import AllowanceMode
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.service import _DEFAULT_REQUEST

        self.assertIn(ChannelActionType.INQUIRY_REPLY, ChannelActionType.ALL)
        self.assertIn(CallPurpose.INQUIRY_REPLY, CallPurpose.ALL)
        self.assertIn(CallPurpose.INQUIRY_REPLY, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.INQUIRY_REPLY], "쿠팡 문의 답변")
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.INQUIRY_REPLY], (CallPurpose.INQUIRY_REPLY, "POST"))
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.INQUIRY_REPLY, ExtKind.READ)], (AllowanceMode.COUNTED, 3, None))
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.INQUIRY_REPLY, ExtKind.WRITE)], (AllowanceMode.COUNTED, 1, None))
        self.assertEqual(CoupangChannelActions.inquiry_reply_key(" 846 "), "inq:846")


if __name__ == "__main__":
    unittest.main()
