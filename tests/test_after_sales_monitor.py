"""
=========================================================
Homez OS

File : tests/test_after_sales_monitor.py

2026-10-16 — 배송 이후(반품·상품 문의·콜센터 문의·정산)를 쿠팡에서 읽는 `AfterSalesMonitorService`와 제공자 읽기를 고정한다
(가짜 HTTP, 외부 호출 없음. 응답 모양은 쿠팡 공식 문서 기준이고, 개인정보를 일부러 섞었다).
  - 허용(AFTER_SALES_CHECK)이 없으면 호출 0, 허용 요청만 남는다
  - GET만 나가고 쿼리 형식·기간 상한(반품 31일·문의 7일)이 문서와 같다
  - 상태가 바뀐 것만 이벤트로 남기고 같은 상태는 다시 쓰지 않는다
  - 고객 이름·연락처·이메일·문의 본문·계좌는 기록에 남지 않는다
  - 한 종류의 실패가 나머지를 막지 않고, 쪽수는 상한이 있으며, 정산은 6시간마다만 읽는다
  - 자동 처리(승인·환불·답변·작업 상태 변경)는 없다
=========================================================
"""

import json
import re
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

import requests
from sqlalchemy import text

from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.order.after_sales_monitor_service import AfterSalesMonitorService
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.model import PurchaseTask
from tests.test_coupang_invoice_service import InvoiceServiceBase
from tests.test_coupang_invoice_upload import response
from tests.test_external_call_enforcement import _install

NOW = datetime(2026, 10, 16, 6, 30, 0)  # UTC → 한국 15:30

PII = ("홍길동", "010-1234-5678", "buyer@example.com", "국민은행", "123-456-789012", "배송 언제 오나요", "개인 사정으로 반품합니다", "서울시 반품 주소 1-2", "회수 기사 김철수")


def return_body(status="RETURNS_UNCHECKED", receipt=500001, order=8103602504198, invoice=""):
    return {"code": 200, "message": "OK", "data": [{
        "receiptId": receipt, "orderId": order, "receiptType": "RETURN", "receiptStatus": status,
        "createdAt": "2026-10-16T15:30:00", "modifiedAt": "2026-10-16T15:31:00", "cancelReasonCategory1": "구매자 변심",
        "cancelReason": PII[6], "faultByType": "CUSTOMER", "requesterName": PII[0], "requesterPhoneNumber": PII[1],
        "returnItems": [{"vendorItemId": 111, "cancelCount": 1, "purchaseCount": 1, "shipmentBoxId": 742634322542636}],
        "returnDeliveryType": "RETURN", "returnDeliveryDtos": [{"deliveryCompanyCode": "HYUNDAI", "deliveryInvoiceNo": invoice, "returnAddress": PII[7], "senderName": PII[8]}],
    }], "nextToken": ""}


def online_body(answered=False, inquiry=11, pages=1):
    comments = [{"inquiryCommentId": 1, "inquiryId": inquiry, "content": "답변드립니다"}] if answered else []
    return {"code": 200, "message": "OK", "data": {"content": [{
        "inquiryId": inquiry, "content": PII[5], "inquiryAt": "2026-10-16T14:00:00.000000+09:00", "orderIds": [8103602504198],
        "sellerProductId": 16411745286, "commentDtoList": comments}],
        "pagination": {"currentPage": 1, "totalPages": pages, "totalElements": pages, "countPerPage": 50}}}


def call_body(needs=True, inquiry=21, pages=1):
    return {"code": 200, "message": "OK", "data": {"content": [{
        "inquiryId": inquiry, "inquiryStatus": "progress", "csPartnerCounselingStatus": "requestAnswer" if needs else "answered",
        "orderId": 8103602504198, "buyerEmail": PII[2], "buyerPhone": PII[1], "content": PII[5], "inquiryAt": "2026-10-16T13:00:00",
        "replies": [{"answerId": 1, "needAnswer": needs, "content": "x"}]}],
        "pagination": {"currentPage": 1, "totalPages": pages, "totalElements": pages, "countPerPage": 30}}}


def settle_body(status="SUBJECT", final=7608):
    return {"code": 200, "message": "OK", "data": [{
        "settlementType": "WEEKLY", "settlementDate": "2026-10-23", "revenueRecognitionYearMonth": "2026-10", "totalSale": 12050,
        "serviceFee": 1181, "settlementTargetAmount": 10869, "settlementAmount": 7608, "lastAmount": 3261, "finalAmount": final,
        "status": status, "bankAccountHolder": PII[0], "bankName": PII[3], "bankAccount": PII[4]}]}


def bare_settlement(rows=None):
    """실제 쿠팡 응답(2026-10-16): 최상위가 배열이다. 내역이 없으면 `[]`."""
    return rows if rows is not None else []


class AfterSalesTransport:
    """경로로 어떤 읽기인지 가려 응답을 준다. 마지막 응답은 반복한다."""

    def __init__(self):
        self.queues = {"returns": [response(200, return_body())], "online": [response(200, online_body())],
                       "call": [response(200, call_body())], "settlement": [response(200, settle_body())]}
        self.calls = []

    @staticmethod
    def kind(url):
        path = urlparse(url).path
        for key, marker in (("returns", "/returnRequests"), ("online", "/onlineInquiries"), ("call", "/callCenterInquiries"), ("settlement", "/settlement-histories")):
            if path.endswith(marker):
                return key
        return "other"

    def request(self, method, url, *, headers, json_body, timeout):
        kind = self.kind(url)
        self.calls.append((kind, method, url, json_body))
        queue = self.queues.get(kind, [response(404, {"code": 404})])
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def count(self, kind=None):
        return len([c for c in self.calls if kind is None or c[0] == kind])


class Base_(InvoiceServiceBase):

    def setUp(self):
        super().setUp()
        _install(self.db)
        self.transport = AfterSalesTransport()
        self.clock = [NOW]

    def monitor(self):
        def factory(session):
            return CoupangChannelActions(
                session, credential_store=self.creds, now_factory=lambda: self.clock[0],
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport, now_factory=lambda: __import__("datetime").datetime(2026, 10, 16, 6, 30, tzinfo=__import__("datetime").timezone.utc)),
            )
        return AfterSalesMonitorService(self.db, actions_factory=factory, now_factory=lambda: self.clock[0])

    def grant(self, per_hour=30):
        return ExternalCallPolicyService(self.db).grant(
            1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.AFTER_SALES_CHECK,
            mode=AllowanceMode.STANDING, per_hour_limit=per_hour, hours=24, user_id=1,
        )

    def events(self):
        self.db.expire_all()
        return [dict(r._mapping) for r in self.db.execute(text("SELECT entity_id, description FROM audit_logs WHERE action='COUPANG_AFTER_SALES_EVENT' ORDER BY id"))]


class GateTests(Base_):

    def test_without_an_allowance_nothing_is_called_and_a_request_is_left(self):
        summary = self.monitor().refresh(1)
        self.assertEqual((summary["ok"], summary["blocked"]), (False, True))
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "AFTER_SALES_CHECK")])

    def test_other_allowances_do_not_open_it(self):
        ExternalCallPolicyService(self.db).grant(
            1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_TRACKING,
            mode=AllowanceMode.STANDING, per_hour_limit=30, hours=24, user_id=1)
        self.assertTrue(self.monitor().refresh(1)["blocked"])
        self.assertEqual(self.transport.calls, [])

    def test_no_coupang_connection_means_nothing_to_read(self):
        self.store.connection_status = "DISCONNECTED"
        self.db.commit()
        self.grant()
        summary = self.monitor().refresh(1)
        self.assertTrue(summary["no_connection"])
        self.assertEqual(self.transport.calls, [])

    def test_the_hourly_cap_stops_the_run_without_calling_further(self):
        self.grant(per_hour=2)
        summary = self.monitor().refresh(1, settlement=False)
        self.assertTrue(summary["blocked"])
        self.assertEqual(self.transport.count(), 2)

    def test_other_companies_have_no_connection(self):
        self.grant()
        self.assertTrue(self.monitor().refresh(2)["no_connection"])


class RequestShapeTests(Base_):

    def setUp(self):
        super().setUp()
        self.grant()
        self.monitor().refresh(1)

    def test_only_get_requests_go_out(self):
        self.assertEqual({m for _k, m, _u, _b in self.transport.calls}, {"GET"})
        self.assertTrue(all(b is None for *_x, b in self.transport.calls))

    def test_the_four_paths_match_the_official_documents(self):
        paths = {k: urlparse(u).path for k, _m, u, _b in self.transport.calls}
        self.assertEqual(paths["returns"], "/v2/providers/openapi/apis/api/v6/vendors/A00012345/returnRequests")
        self.assertEqual(paths["online"], "/v2/providers/openapi/apis/api/v5/vendors/A00012345/onlineInquiries")
        self.assertEqual(paths["call"], "/v2/providers/openapi/apis/api/v5/vendors/A00012345/callCenterInquiries")
        self.assertEqual(paths["settlement"], "/v2/providers/marketplace_openapi/apis/api/v1/settlement-histories")

    def test_the_return_query_uses_a_minute_range_inside_the_31_day_limit(self):
        url = next(u for k, _m, u, _b in self.transport.calls if k == "returns")
        q = parse_qs(urlparse(url).query)
        self.assertEqual((q["searchType"], q["cancelType"]), (["timeFrame"], ["RETURN"]))
        start, end = (datetime.strptime(q[k][0], "%Y-%m-%dT%H:%M") for k in ("createdAtFrom", "createdAtTo"))
        self.assertEqual(end, datetime(2026, 10, 16, 15, 30))  # 한국 시간
        self.assertLessEqual((end - start).days, 31)
        for forbidden in ("nextToken", "maxPerPage", "orderId"):  # timeFrame에서는 미지원
            self.assertNotIn(forbidden, q)

    def test_inquiry_queries_stay_inside_seven_days_and_use_the_documented_page_sizes(self):
        for kind, size_max in (("online", 50), ("call", 30)):
            url = next(u for k, _m, u, _b in self.transport.calls if k == kind)
            q = parse_qs(urlparse(url).query)
            start, end = (datetime.strptime(q[k][0], "%Y-%m-%d") for k in ("inquiryStartAt", "inquiryEndAt"))
            self.assertLessEqual((end - start).days, 7)
            self.assertLessEqual(int(q["pageSize"][0]), size_max)
        self.assertEqual(parse_qs(urlparse(next(u for k, _m, u, _b in self.transport.calls if k == "online")).query)["answeredType"], ["ALL"])
        self.assertEqual(parse_qs(urlparse(next(u for k, _m, u, _b in self.transport.calls if k == "call")).query)["partnerCounselingStatus"], ["NONE"])

    def test_settlement_reads_this_month_and_last_month(self):
        months = [parse_qs(urlparse(u).query)["revenueRecognitionYearMonth"][0] for k, _m, u, _b in self.transport.calls if k == "settlement"]
        self.assertEqual(months, ["2026-10", "2026-09"])

    def test_every_call_is_logged_as_after_sales_check_with_its_kind(self):
        self.db.expire_all()
        logs = self.db.query(ExternalCallLog).order_by(ExternalCallLog.id).all()
        self.assertEqual(sorted({l.target for l in logs}), ["call", "online", "returns", "settlement"])
        self.assertEqual({(l.purpose, l.kind, l.outcome) for l in logs}, {("AFTER_SALES_CHECK", "READ", "SUCCEEDED")})


class EventTests(Base_):

    def setUp(self):
        super().setUp()
        self.grant()

    def test_new_items_become_events_with_their_state(self):
        summary = self.monitor().refresh(1)
        ids = sorted(e["entity_id"] for e in self.events())
        self.assertEqual(ids, ["INQ_CALL:21", "INQ_ONLINE:11", "RETURN:500001", "SETTLEMENT:2026-09:WEEKLY:2026-10-23", "SETTLEMENT:2026-10:WEEKLY:2026-10-23"])
        self.assertEqual(len(summary["new_events"]), 5)
        ret = json.loads(next(e for e in self.events() if e["entity_id"] == "RETURN:500001")["description"])
        self.assertEqual((ret["s"], ret["o"], ret["ty"], ret["n"]), ("RETURNS_UNCHECKED", "8103_6025_0419_8", "RETURN", 1))
        latest = {e["entity_id"]: e for e in AfterSalesMonitorService.latest_events(self.db)}
        self.assertEqual(latest["RETURN:500001"]["o"], "8103602504198")  # 읽을 때는 원래 주문번호로

    def test_inquiries_are_marked_unanswered_until_answered(self):
        self.monitor().refresh(1)
        states = {e["entity_id"]: json.loads(e["description"])["s"] for e in self.events()}
        self.assertEqual((states["INQ_ONLINE:11"], states["INQ_CALL:21"]), ("UNANSWERED", "UNANSWERED"))
        self.transport.queues["online"] = [response(200, online_body(answered=True))]
        self.transport.queues["call"] = [response(200, call_body(needs=False))]
        self.clock[0] = datetime(2026, 10, 16, 6, 45)
        self.monitor().refresh(1)
        self.assertEqual(len(self.events()), 7)  # 첫 5건 + 답변으로 바뀐 2건(이전 상태 기록은 지우지 않는다)
        latest = {e["entity_id"]: e["s"] for e in AfterSalesMonitorService.latest_events(self.db)}
        self.assertEqual((latest["INQ_ONLINE:11"], latest["INQ_CALL:21"]), ("ANSWERED", "ANSWERED"))

    def test_the_same_state_is_not_written_again(self):
        self.monitor().refresh(1)
        first = len(self.events())
        again = self.monitor().refresh(1)
        self.assertEqual(len(self.events()), first)
        self.assertEqual(again["new_events"], [])

    def test_a_status_change_is_a_new_event_and_the_latest_state_wins(self):
        self.monitor().refresh(1)
        self.transport.queues["returns"] = [response(200, return_body(status="RETURNS_COMPLETED"))]
        summary = self.monitor().refresh(1)
        self.assertEqual([e["entity_id"] for e in summary["new_events"]], ["RETURN:500001"])
        latest = {e["entity_id"]: e["s"] for e in AfterSalesMonitorService.latest_events(self.db)}
        self.assertEqual(latest["RETURN:500001"], "RETURNS_COMPLETED")

    def test_a_settlement_that_gets_paid_is_a_new_event(self):
        self.monitor().refresh(1)
        self.transport.queues["settlement"] = [response(200, settle_body(status="DONE"))]
        self.clock[0] = datetime.utcnow() + timedelta(hours=6, minutes=5)  # 호출 기록은 실제 시계를 쓰므로 그 기준으로 6시간 뒤
        summary = self.monitor().refresh(1)
        self.assertTrue(any(e["k"] == "SETTLEMENT" and e["st"] == "DONE" for e in summary["new_events"]))

    def test_no_personal_data_is_stored_anywhere(self):
        self.monitor().refresh(1)
        dump = " ".join(str(r[0]) for r in self.db.execute(text("SELECT description FROM audit_logs")))
        dump += " ".join(str(r) for r in self.db.execute(text("SELECT * FROM external_call_logs")).fetchall())
        for secret in PII:
            self.assertNotIn(secret, dump, secret)


class ReturnPickupTests(Base_):
    """2026-10-16 — 반품 회수 택배(고객이 보낸 송장): 택배사·송장번호만 읽고, 송장이 생기면 새 이벤트."""

    def setUp(self):
        super().setUp()
        self.grant()

    def latest_return(self):
        return {e["entity_id"]: e for e in AfterSalesMonitorService.latest_events(self.db)}["RETURN:500001"]

    def test_no_pickup_invoice_yet_is_recorded_as_none(self):
        self.monitor().refresh(1)
        event = self.latest_return()
        self.assertEqual((event["dc"], event["di"], event["dt"]), ("HYUNDAI", None, "RETURN"))

    def test_a_pickup_invoice_that_appears_is_a_new_event_even_if_the_status_is_the_same(self):
        self.monitor().refresh(1)
        self.transport.queues["returns"] = [response(200, return_body(invoice="259606480561"))]
        summary = self.monitor().refresh(1)
        self.assertEqual([e["entity_id"] for e in summary["new_events"]], ["RETURN:500001"])
        self.assertEqual(self.latest_return()["di"], "259606480561")
        self.assertEqual(self.latest_return()["s"], "RETURNS_UNCHECKED")

    def test_the_same_invoice_and_status_is_not_written_again(self):
        self.transport.queues["returns"] = [response(200, return_body(invoice="259606480561"))]
        self.monitor().refresh(1)
        self.assertEqual([e for e in self.monitor().refresh(1)["new_events"] if e["k"] == "RETURN"], [])

    def test_the_invoice_is_stored_in_a_form_the_central_masking_leaves_alone(self):
        self.transport.queues["returns"] = [response(200, return_body(invoice="259606480561"))]
        self.monitor().refresh(1)
        raw = self.db.execute(text("SELECT description FROM audit_logs WHERE entity_id='RETURN:500001'")).scalar()
        self.assertIn("2596_0648_0561", raw)
        self.assertNotIn("*", raw)

    def test_return_address_and_pickup_driver_are_not_read(self):
        self.transport.queues["returns"] = [response(200, return_body(invoice="259606480561"))]
        self.monitor().refresh(1)
        dump = " ".join(str(r[0]) for r in self.db.execute(text("SELECT description FROM audit_logs")))
        for secret in (PII[7], PII[8]):
            self.assertNotIn(secret, dump)


class RealResponseShapeTests(Base_):
    """2026-10-16 첫 실호출에서 나온 모양을 고정한다(최상위 배열 정산 응답, 가려진 긴 접수번호)."""

    def setUp(self):
        super().setUp()
        self.grant()

    def test_a_bare_empty_array_settlement_response_is_an_empty_result_not_a_failure(self):
        self.transport.queues["settlement"] = [response(200, bare_settlement())]
        summary = self.monitor().refresh(1)
        self.assertFalse([f for f in summary["failures"] if f.startswith("settlement")])
        self.assertEqual([e for e in summary["new_events"] if e["k"] == "SETTLEMENT"], [])

    def test_a_bare_array_with_rows_is_read(self):
        self.transport.queues["settlement"] = [response(200, settle_body()["data"])]
        summary = self.monitor().refresh(1)
        self.assertTrue(any(e["k"] == "SETTLEMENT" for e in summary["new_events"]))

    def test_an_old_record_whose_id_was_masked_is_still_read_and_not_rewritten_every_cycle(self):
        """2026-10-16 실기록 `{"k":"RETURN","id":******3062,...}` — JSON이 깨져 읽히지 않으면 화면에 안 뜨고 10분마다 중복 기록된다."""
        self.db.execute(text("INSERT INTO audit_logs (company_id, action, entity, entity_id, description) VALUES (1,'COUPANG_AFTER_SALES_EVENT','after_sales','RETURN:1919873062', :d)"),
                        {"d": '{"k":"RETURN","id":******3062,"o":"8103_6025_0419_8","s":"RETURNS_UNCHECKED","ty":"RETURN","rc":"고객변심","f":"CUSTOMER","n":1}'})
        self.db.commit()
        latest = {e["entity_id"]: e for e in AfterSalesMonitorService.latest_events(self.db)}
        self.assertEqual((latest["RETURN:1919873062"]["id"], latest["RETURN:1919873062"]["s"]), ("1919873062", "RETURNS_UNCHECKED"))
        self.transport.queues["returns"] = [response(200, return_body(receipt=1919873062))]
        before = len(self.events())
        self.monitor().refresh(1)
        # 수정 전에 쓴 기록에는 회수 택배 필드가 없으므로, 새 필드가 생기는 첫 한 번만 갱신한다(그 뒤로는 같은 값이면 쓰지 않는다).
        self.assertEqual(len([e for e in self.events() if e["entity_id"] == "RETURN:1919873062"]), 2)
        self.assertEqual(len(self.events()), before + 5)
        self.monitor().refresh(1)
        self.assertEqual(len([e for e in self.events() if e["entity_id"] == "RETURN:1919873062"]), 2)

    def test_a_non_json_settlement_body_is_a_failure(self):
        self.transport.queues["settlement"] = [response(200, None)]
        self.assertTrue([f for f in self.monitor().refresh(1)["failures"] if f.startswith("settlement")])

    def test_a_ten_digit_receipt_id_survives_the_central_masking(self):
        self.transport.queues["returns"] = [response(200, return_body(receipt=1919873062))]
        self.monitor().refresh(1)
        latest = {e["entity_id"]: e for e in AfterSalesMonitorService.latest_events(self.db)}
        self.assertEqual(latest["RETURN:1919873062"]["id"], "1919873062")
        self.assertEqual(latest["RETURN:1919873062"]["o"], "8103602504198")
        raw = self.db.execute(text("SELECT description FROM audit_logs WHERE entity_id='RETURN:1919873062'")).scalar()
        self.assertNotIn("1919873062", raw)
        self.assertNotIn("*", raw)  # 새 기록은 가려질 번호를 JSON에 넣지 않는다


class RobustnessTests(Base_):

    def setUp(self):
        super().setUp()
        self.grant()

    def test_one_failing_read_does_not_hide_the_others(self):
        self.transport.queues["returns"] = [response(500, {"code": 500, "message": "boom"})]
        summary = self.monitor().refresh(1)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["blocked"])
        self.assertTrue(summary["failures"][0].startswith("returns"))
        self.assertTrue(any(e["entity_id"].startswith("INQ_ONLINE") for e in summary["new_events"]))

    def test_a_timeout_is_a_failure_not_a_value(self):
        self.transport.queues["online"] = [requests.Timeout("slow")]
        summary = self.monitor().refresh(1)
        self.assertIn("online", " ".join(summary["failures"]))
        self.assertFalse([e for e in self.events() if e["entity_id"].startswith("INQ_ONLINE")])

    def test_an_unexpected_shape_is_not_guessed(self):
        self.transport.queues["returns"] = [response(200, {"code": 200, "data": "oops"})]
        summary = self.monitor().refresh(1)
        self.assertIn("returns", " ".join(summary["failures"]))
        self.assertFalse([e for e in self.events() if e["entity_id"].startswith("RETURN")])

    def test_inquiry_paging_is_capped(self):
        self.transport.queues["online"] = [response(200, online_body(pages=10, inquiry=i)) for i in range(1, 6)]
        summary = self.monitor().refresh(1, settlement=False)
        self.assertEqual(self.transport.count("online"), 3)
        self.assertEqual(summary["calls"], 1 + 3 + 1)

    def test_settlement_is_read_only_every_six_hours(self):
        self.monitor().refresh(1)
        self.assertEqual(self.transport.count("settlement"), 2)
        self.clock[0] = datetime.utcnow() + timedelta(hours=1)
        self.monitor().refresh(1)
        self.assertEqual(self.transport.count("settlement"), 2)
        self.clock[0] = datetime.utcnow() + timedelta(hours=6, minutes=5)
        self.monitor().refresh(1)
        self.assertEqual(self.transport.count("settlement"), 4)

    def test_an_empty_result_is_fine(self):
        self.transport.queues["returns"] = [response(200, {"code": 200, "message": "OK", "data": [], "nextToken": ""})]
        self.assertEqual([e for e in self.monitor().refresh(1)["new_events"] if e["k"] == "RETURN"], [])

    def test_nothing_in_homez_is_changed_automatically(self):
        task = self.make_task(order_id=9, key="t9", with_box=False)
        before = (task.status, task.version)
        self.monitor().refresh(1)
        self.db.expire_all()
        row = self.db.get(PurchaseTask, task.id)
        self.assertEqual((row.status, row.version), before)


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def read(self, rel):
        return (self.ROOT / rel).read_text(encoding="utf-8")

    def test_the_job_runs_every_ten_minutes(self):
        source = self.read("app/domains/scheduler/jobs.py")
        self.assertIn('AFTER_SALES_JOB_ID = "coupang_after_sales_check"', source)
        self.assertIn("AFTER_SALES_INTERVAL_MINUTES = 10", source)
        self.assertIn("run_after_sales_job,\n        minutes=AFTER_SALES_INTERVAL_MINUTES", source)

    def test_the_manual_endpoint_is_admin_only_and_registered_before_the_dynamic_routes(self):
        source = self.read("app/domains/order/router.py")
        self.assertIn('@router.post("/after-sales/refresh")', source)
        self.assertLess(source.index('"/after-sales/refresh"'), source.index('"/{order_id}'))
        self.assertIn("current_user: User = Depends(admin_guard)", source.split('"/after-sales/refresh"')[1].split("@router")[0])

    def test_the_reading_code_has_no_write_or_reply_operations(self):
        for rel in ("app/domains/purchase_task/coupang_after_sales_provider.py", "app/domains/order/after_sales_monitor_service.py"):
            source = self.read(rel)
            code = re.sub(r'""".*?"""', "", source, flags=re.S)
            for forbidden in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "mark_delivered", "approve", "refund(", "answer("):
                self.assertNotIn(forbidden, code, f"{rel}: {forbidden}")

    def test_buyer_contact_fields_are_never_read(self):
        code = re.sub(r'""".*?"""', "", self.read("app/domains/purchase_task/coupang_after_sales_provider.py"), flags=re.S)
        code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
        for field in ("buyerEmail", "buyerPhone", "requesterName", "requesterPhoneNumber", "bankAccount", "bankAccountHolder", 'item.get("content")', 'item.get("cancelReason")'):
            self.assertNotIn(field, code, field)

    def test_the_purpose_is_enforced_and_labelled(self):
        self.assertIn(CallPurpose.AFTER_SALES_CHECK, CallPurpose.ENFORCED)
        self.assertTrue(CallPurpose.LABELS[CallPurpose.AFTER_SALES_CHECK])


if __name__ == "__main__":
    unittest.main()
