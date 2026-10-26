"""
=========================================================
Homez OS

File : tests/test_coupang_call_center_confirm.py

2026-10-27 — 쿠팡 쓰기 9번: 고객센터 업체 이관 문의 확인 규칙을 고정한다. 공식 "쿠팡 고객센터 문의확인"
`POST .../v4/vendors/{vendorId}/callCenterInquiries/{inquiryId}/confirms`({confirmBy: WING 아이디})와 문의 목록(partnerCounselingStatus=TRANSFER)을 따른다.
미확인 목록에 지금 있을 때만, 내용 확인 표시 뒤에만 한 번 보내고, 확인 뒤 상태 값은 문서에 없어 목록에서 빠졌는지로만 본다.
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
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.external_call.constants import CallPurpose
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_call_center_confirm_service import CoupangCallCenterConfirmService
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_call_center_reply import INQ
from tests.test_coupang_call_center_reply import FakeTransport as _CcTransport
from tests.test_coupang_call_center_reply import ServiceBase as _CcBase
from tests.test_coupang_call_center_reply import agent
from tests.test_coupang_call_center_reply import cc
from tests.test_coupang_call_center_reply import listing
from tests.test_coupang_inquiry_reply import WING
from tests.test_coupang_inquiry_reply import provider
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR

OTHER = 622088777


def transfers(*ids):
    return listing(*(cc(inquiry_id=i, replies=[agent(status="answered")]) for i in ids))


class FakeTransport(_CcTransport):
    def __init__(self):
        super().__init__()
        self.get = [response(200, transfers(int(INQ)))]
        self.post = [response(200, {"code": "200", "message": "OK"})]


class ProviderTests(unittest.TestCase):

    def test_the_confirmation_follows_the_official_shape(self):
        t = FakeTransport()
        self.assertEqual(provider(t).confirm_call_center(INQ).kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method), ("post", "POST"))
        self.assertTrue(url.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/callCenterInquiries/{INQ}/confirms"), url)
        self.assertEqual(body, {"confirmBy": WING})

    def test_the_transfer_list_asks_for_unconfirmed(self):
        t = FakeTransport()
        provider(t).read_call_center_transfers()
        self.assertEqual(parse_qs(urlparse(t.requests[0][2]).query)["partnerCounselingStatus"], ["TRANSFER"])

    def test_invalid_input_or_no_wing_id_makes_no_request(self):
        t = FakeTransport()
        for inquiry_id in ("abc", True, "1" * 21, None):
            self.assertEqual(provider(t).confirm_call_center(inquiry_id).kind, CallKind.ACTION_REQUIRED, inquiry_id)
        result = provider(t, wing=None).confirm_call_center(INQ)
        self.assertEqual((result.kind, result.error_class), (CallKind.ACTION_REQUIRED, "MISSING_WING_USER_ID"))
        self.assertEqual(t.calls, [])

    def test_the_response_judgement(self):
        cases = [(response(200, {"code": "200", "message": "OK"}), CallKind.SUCCEEDED), (response(200, {"code": "ERROR"}), CallKind.ACTION_REQUIRED),
                 (response(200, {"message": "x"}), CallKind.UNKNOWN), (response(400, {"message": "상담이 종료된 문의입니다 - 522"}), CallKind.ACTION_REQUIRED),
                 (response(500, {}), CallKind.UNKNOWN), (requests.Timeout(), CallKind.UNKNOWN)]
        for outcome, expected in cases:
            t = FakeTransport()
            t.post = [outcome]
            self.assertEqual(provider(t).confirm_call_center(INQ).kind, expected, outcome)

    def test_the_applied_check_never_reads_absence_as_done(self):
        t = FakeTransport()
        t.get = [response(200, transfers(int(INQ)))]
        result = provider(t).read_call_center_confirm_applied(INQ)
        self.assertEqual((result.ok, result.value), (True, False))
        t.get = [response(200, transfers(OTHER))]
        result = provider(t).read_call_center_confirm_applied(INQ)
        self.assertEqual((result.ok, result.value), (False, None))                   # 빠졌으면 확인인지 24시간 경과인지 모른다
        t.get = [response(500, {})]
        self.assertFalse(provider(t).read_call_center_confirm_applied(INQ).ok)


class ServiceBase(_CcBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()

    def service(self):
        stock = super(_CcBase, self).service()
        return CoupangCallCenterConfirmService(self.db, actions_factory=stock._actions_factory, safety=self.safety)

    def send(self, inquiry_id=INQ, **override):
        args = dict(confirm_seen=True)
        args.update(override)
        return self.service().send(1, 7, inquiry_id, **args)


class ListTests(ServiceBase):

    def test_the_list_shows_pending_transfers_without_personal_data(self):
        listed = self.service().unconfirmed(1)
        self.assertTrue(listed["ok"])
        self.assertEqual([r["inquiry_id"] for r in listed["rows"]], [INQ])
        self.assertEqual(listed["gate_blockers"], [])
        body = json.dumps(listed, ensure_ascii=False)
        for secret in ("010-9999-8888", "상담사김", "cs_kim", "AK", VENDOR, WING, REF):
            self.assertNotIn(secret, body)

    def test_the_list_reports_gates_and_failures(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        self.assertEqual({b["code"] for b in self.service().unconfirmed(1)["gate_blockers"]}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.transport.get = [response(500, {})]
        failed = self.service().unconfirmed(1)
        self.assertEqual((failed["ok"], failed["blockers"][0]["code"]), (False, "INQUIRY_READ_FAILED"))

    def test_store_resolution_never_guesses(self):
        from app.domains.store_connection.model import StoreConnection

        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertEqual(self.service().unconfirmed(1)["blockers"][0]["code"], "NO_COUPANG_STORE")
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertEqual(self.service().unconfirmed(1)["blockers"][0]["code"], "MULTIPLE_COUPANG_STORES")
        with self.assertRaises(BadRequestException):
            self.send()
        self.assertEqual(self.transport.calls, [])


class SendTests(ServiceBase):

    def test_a_confirmation_goes_out_once_and_is_verified_by_leaving_the_list(self):
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual((self.transport.count("post"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("CALL_CENTER_CONFIRM", f"cc:{INQ}:CONFIRM", ChannelActionStatus.SUCCEEDED, 7))

    def test_still_listed_after_success_is_flagged_and_a_failed_reread_is_unknown(self):
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers(int(INQ)))]
        self.assertEqual(self.send()["verified"], False)
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()
        self.transport.get = [response(200, transfers(int(INQ))), response(500, {})]
        self.assertIsNone(self.send()["verified"])

    def test_an_inquiry_not_waiting_is_never_sent(self):
        self.transport.get = [response(200, transfers(OTHER))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("NOT_PENDING", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_the_seen_mark_is_required(self):
        for value in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.send(confirm_seen=value)
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))

    def test_invalid_ids_are_refused_before_reading(self):
        for inquiry_id in ("abc", True, "1" * 21):
            with self.assertRaises(BadRequestException):
                self.send(inquiry_id=inquiry_id)
        self.assertEqual(self.transport.calls, [])

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.send()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual((self.transport.calls, self.rows()), ([], []))

    def test_a_failed_read_refuses(self):
        self.transport.get = [response(500, {})]
        with self.assertRaises(BadRequestException):
            self.send()
        self.assertEqual(self.transport.count("post"), 0)

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append(method.lower())
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
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
        self.transport.post = [response(400, {"message": "상담이 종료된 문의입니다 - 522"})]
        first = self.send()
        self.assertEqual((first["outcome"], first["verified"]), ("ACTION_REQUIRED", None))
        self.assertEqual(self.transport.count("get"), 1)
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.post = [response(200, {"code": "200", "message": "OK"})]
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_when_it_is_still_listed(self):
        self.transport.post = [requests.Timeout()]
        self.assertEqual(self.send()["outcome"], "UNKNOWN")
        self.assertFalse(self.send()["executed"])
        self.assertEqual(self.transport.count("post"), 1)
        self.transport.get = [response(200, transfers())]
        self.assertEqual(self.service().reconcile(1, INQ)["outcome"], "UNKNOWN")         # 빠졌으면 단정하지 않는다
        self.transport.get = [response(200, transfers(int(INQ)))]
        self.assertNotEqual(self.service().reconcile(1, INQ)["outcome"], "SUCCEEDED")   # 그대로면 안 받음
        for bad in ("abc", True):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, bad)

    def test_the_audit_record(self):
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("COUPANG_CALL_CENTER_CONFIRM", "coupang_call_center_inquiry", INQ, 7)])


class AllowanceTests(ServiceBase):

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=CallPurpose.INQUIRY_REPLY, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        listed = self.service().unconfirmed(1)
        self.assertFalse(listed["ok"])
        self.assertEqual(listed["blockers"][0]["code"], "EXTERNAL_CALL_NOT_ALLOWED")   # 읽기 실패가 아니라 허용 없음으로 알린다
        with self.assertRaises(BadRequestException):
            self.send()
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_confirmation(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 2)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
        result = self.send()
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))

    def test_a_missing_write_allowance_stops_before_the_post(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 2)
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual((self.transport.count("post"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_list_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("list_coupang_call_center_unconfirmed", "confirm_coupang_call_center_inquiry", "reconcile_coupang_call_center_confirm"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        self.assertNotIn(".send(", router.split("def list_coupang_call_center_unconfirmed(")[1].split("@router")[0])
        self.assertLess(router.index('"/coupang-call-center-inquiries/unconfirmed"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError

        from app.domains.purchase_task.coupang_call_center_confirm_schema import CoupangCallCenterConfirmRequest

        CoupangCallCenterConfirmRequest(confirm_seen=True)
        for bad in ({"extra": 1}, {"confirm_seen": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangCallCenterConfirmRequest(**{"confirm_seen": True, **bad})

    def test_the_action_type_uses_the_inquiry_reply_allowance(self):
        self.assertIn(ChannelActionType.CALL_CENTER_CONFIRM, ChannelActionType.ALL)
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.CALL_CENTER_CONFIRM], (CallPurpose.INQUIRY_REPLY, "POST"))
        self.assertEqual(CoupangChannelActions.call_center_confirm_key(" 5 "), "cc:5:CONFIRM")


if __name__ == "__main__":
    unittest.main()
