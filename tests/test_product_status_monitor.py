"""
=========================================================
Homez OS

File : tests/test_product_status_monitor.py

2026-10-17 — 등록한 상품의 쿠팡 심사·판매 상태를 읽어 바뀐 것만 기록하는 `ProductStatusMonitorService`를 고정한다(가짜 HTTP, 외부 호출 0).
오늘 레이펄스가 쿠팡에서 승인반려가 됐는데 HOMEZ는 몰랐던 문제(연동 C1·C2)를 막는다.

  - 허용(PRODUCT_STATUS_CHECK)이 없으면 호출 0, 허용 요청만 남는다. 다른 목적의 허용은 열어 주지 않는다.
  - 대상은 쿠팡에 전송 성공한 최근 30일 상품뿐이고, 한 번에 5개까지만 읽는다.
  - 상태가 바뀐 것만 기록한다. 승인완료는 상품마다 정해진 하루 한 번 자리에서만 다시 읽고, 삭제는 읽지 않는다.
  - 승인반려가 되면 이력을 한 번 더 읽어 사유를 기록하고, 같은 반려는 다시 읽지 않는다. 사유가 없으면 "사유 없음"으로 남긴다.
  - GET만 나가고, 한 상품의 실패가 나머지를 막지 않는다. 쿠팡에 쓰지 않는다.
=========================================================
"""

import json
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text

import tests.test_after_sales_monitor as asm
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.easy_view.service import EasyViewService
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.order.product_status_monitor_service import APPROVED_RECHECK_SLOTS
from app.domains.order.product_status_monitor_service import MAX_PRODUCTS_PER_RUN
from app.domains.order.product_status_monitor_service import ProductStatusActions
from app.domains.order.product_status_monitor_service import ProductStatusMonitorService
from app.domains.purchase_task.coupang_product_history_provider import CoupangProductStatusProvider
from tests.test_coupang_invoice_upload import response

ROOT = Path(__file__).resolve().parents[1]
JOBS = (ROOT / "app/domains/scheduler/jobs.py").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 17, 6, 0, 0)
REASON = "상세설명에 포함된 모든 이미지를 다운받지 못했습니다"


class ProductTransport:
    """상품 번호별로 상태·이력 응답을 준다. 번호가 없으면 승인대기다."""

    def __init__(self):
        self.status = {}
        self.history = {}
        self.fail = set()
        self.calls = []

    def request(self, method, url, *, headers, json_body, timeout):
        path = urlparse(url).path
        self.calls.append((method, path, json_body))
        ref = path.split("/seller-products/")[1].split("/")[0]
        if ref in self.fail:
            return response(500, {"code": 500})
        if path.endswith("/histories"):
            return response(200, {"code": "SUCCESS", "data": self.history.get(ref, [])})
        name = self.status.get(ref, "승인대기")
        return response(200, {"code": "SUCCESS", "data": {"statusName": name, "sellerProductName": f"상품 {ref[-3:]}"}})

    def count(self, kind=None):
        return len([c for c in self.calls if kind is None or (c[1].endswith("/histories") == (kind == "history"))])


class Base_(asm.Base_):

    def setUp(self):
        super().setUp()
        MarketplaceSubmission.__table__.create(bind=self.engine, checkfirst=True)
        self.product_transport = ProductTransport()
        self.n = 0

    def submission(self, ref, *, status="SUBMITTED", age_days=1):
        self.n += 1
        self.db.add(MarketplaceSubmission(
            company_id=1, listing_id=self.n, selection_id=self.n, marketplace_account_id=1, external_submission_ref=ref,
            status=status, safety_decision="ALLOW", idempotency_key=f"k{self.n}",
            created_at=NOW - timedelta(days=age_days), attempted_at=NOW - timedelta(days=age_days),
        ))
        self.db.commit()

    def monitor(self):
        def factory(session):
            return ProductStatusActions(
                session, credential_store=self.creds, now_factory=lambda: self.clock[0],
                provider_factory=lambda cred: CoupangProductStatusProvider(cred, transport=self.product_transport, now_factory=lambda: NOW),
            )
        return ProductStatusMonitorService(self.db, actions_factory=factory, now_factory=lambda: self.clock[0])

    def allow(self, per_hour=12):
        return ExternalCallPolicyService(self.db).grant(
            1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.PRODUCT_STATUS_CHECK,
            mode=AllowanceMode.STANDING, per_hour_limit=per_hour, hours=24, user_id=1,
        )

    def events(self):
        self.db.expire_all()
        rows = self.db.execute(text("SELECT entity_id, description FROM audit_logs WHERE action='COUPANG_PRODUCT_STATUS_EVENT' ORDER BY id"))
        return [(r[0], json.loads(r[1])) for r in rows]


class GateTests(Base_):

    def test_without_an_allowance_nothing_is_called_and_a_request_is_left(self):
        self.submission("16417638496")
        summary = self.monitor().refresh(1)
        self.assertEqual((summary["ok"], summary["blocked"]), (False, True))
        self.assertEqual(self.product_transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "PRODUCT_STATUS_CHECK")])

    def test_another_purposes_allowance_does_not_open_it(self):
        self.submission("16417638496")
        ExternalCallPolicyService(self.db).grant(
            1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.AFTER_SALES_CHECK,
            mode=AllowanceMode.STANDING, per_hour_limit=30, hours=24, user_id=1)
        self.assertTrue(self.monitor().refresh(1)["blocked"])
        self.assertEqual(self.product_transport.calls, [])

    def test_no_coupang_connection_means_nothing_to_read(self):
        self.store.connection_status = "DISCONNECTED"
        self.db.commit()
        self.allow()
        self.assertTrue(self.monitor().refresh(1)["no_connection"])
        self.assertEqual(self.product_transport.calls, [])

    def test_the_hourly_cap_stops_the_run(self):
        for ref in ("1001", "1002", "1003"):
            self.submission(ref)
        self.allow(per_hour=2)
        summary = self.monitor().refresh(1)
        self.assertTrue(summary["blocked"])
        self.assertEqual(self.product_transport.count(), 2)


class TargetTests(Base_):

    def test_only_recent_successful_submissions_with_a_number_are_read(self):
        self.submission("2001")
        self.submission("2002", status="FAILED")
        self.submission("2003", age_days=40)
        self.submission(None)
        self.submission("abc")
        self.allow()
        self.monitor().refresh(1)
        self.assertEqual([c[1].split("/seller-products/")[1] for c in self.product_transport.calls], ["2001"])

    def test_a_run_reads_at_most_five_products_and_the_same_number_once(self):
        for i in range(8):
            self.submission(str(3000 + i))
        self.submission("3000")  # 같은 상품 번호 중복
        self.allow()
        summary = self.monitor().refresh(1)
        self.assertEqual(summary["checked"], MAX_PRODUCTS_PER_RUN)
        self.assertEqual(len({c[1] for c in self.product_transport.calls}), MAX_PRODUCTS_PER_RUN)


class RecordingTests(Base_):

    def setUp(self):
        super().setUp()
        self.submission("16417638496")
        self.allow()

    def test_a_change_is_recorded_and_the_same_state_is_not_written_again(self):
        self.product_transport.status["16417638496"] = "승인대기"
        self.monitor().refresh(1)
        self.monitor().refresh(1)
        self.assertEqual([(e, p["s"]) for e, p in self.events()], [("PROD:16417638496", "UNKNOWN")])
        self.product_transport.status["16417638496"] = "APPROVING"
        summary = self.monitor().refresh(1)
        self.assertEqual(len(summary["new_events"]), 1)
        self.assertEqual([p["s"] for _e, p in self.events()], ["UNKNOWN", "APPROVING"])

    def test_the_raw_wording_is_kept_and_the_number_is_only_in_the_entity_id(self):
        self.product_transport.status["16417638496"] = "승인반려"
        self.monitor().refresh(1)
        entity, payload = self.events()[0]
        self.assertEqual(entity, "PROD:16417638496")
        self.assertEqual((payload["s"], payload["r"]), ("DENIED", "승인반려"))
        self.assertNotIn("16417638496", json.dumps(payload))

    def test_approved_products_are_read_only_in_their_own_daily_slot(self):
        self.product_transport.status["16417638496"] = "승인완료"
        self.monitor().refresh(1)
        service = self.monitor()
        slot_ref = 16417638496 % APPROVED_RECHECK_SLOTS
        hits = 0
        for step in range(APPROVED_RECHECK_SLOTS):
            self.clock[0] = NOW + timedelta(minutes=30 * step)
            hits += 1 if service.due("16417638496") else 0
        self.assertEqual(hits, 1)
        self.assertIsInstance(slot_ref, int)

    def test_deleted_products_are_not_read_again(self):
        self.product_transport.status["16417638496"] = "DELETED"
        self.monitor().refresh(1)
        calls = self.product_transport.count()
        self.monitor().refresh(1)
        self.assertEqual(self.product_transport.count(), calls)

    def test_one_failing_product_does_not_stop_the_others(self):
        self.submission("5002")
        self.product_transport.fail.add("5002")
        summary = self.monitor().refresh(1)
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["checked"], 1)
        self.assertEqual([e for e, _p in self.events()], ["PROD:16417638496"])

    def test_only_get_goes_out(self):
        self.monitor().refresh(1)
        self.assertEqual({m for m, _p, _b in self.product_transport.calls}, {"GET"})
        self.assertTrue(all(b is None for *_x, b in self.product_transport.calls))


class DeniedReasonTests(Base_):

    def setUp(self):
        super().setUp()
        self.submission("16417638496")
        self.allow()
        self.product_transport.status["16417638496"] = "승인반려"
        self.product_transport.history["16417638496"] = [
            {"status": "승인대기", "comment": "컨텐츠 확인 대기중", "createdBy": "홍길동", "createdAt": "2026-10-17T14:00:00"},
            {"status": "승인반려", "comment": REASON, "createdBy": "쿠팡", "createdAt": "2026-10-17T14:30:00"},
        ]

    def test_the_reason_is_read_once_and_recorded(self):
        self.monitor().refresh(1)
        self.monitor().refresh(1)
        self.assertEqual(self.product_transport.count("history"), 1)
        why = [p for e, p in self.events() if e == "PRODWHY:16417638496"]
        self.assertEqual(len(why), 1)
        self.assertEqual((why[0]["s"], why[0]["c"]), ("REASON", REASON))
        self.assertNotIn("홍길동", json.dumps(self.events(), ensure_ascii=False))

    def test_a_denial_without_wording_is_recorded_as_no_reason(self):
        self.product_transport.history["16417638496"] = [{"status": "승인대기", "comment": "컨텐츠 확인 대기중", "createdAt": "2026-10-17T14:00:00"}]
        self.monitor().refresh(1)
        why = [p for e, p in self.events() if e == "PRODWHY:16417638496"][0]
        self.assertEqual((why["s"], why["c"]), ("NO_REASON", ""))

    def test_a_new_denial_after_an_approval_reads_the_reason_again(self):
        self.monitor().refresh(1)
        self.product_transport.status["16417638496"] = "승인완료"
        self.clock[0] = NOW + timedelta(hours=1)
        self.monitor().refresh(1)  # 승인완료는 자리가 아니면 읽지 않으므로 직접 읽기를 강제한다
        service = self.monitor()
        service.due = lambda ref: True
        service.refresh(1)
        self.product_transport.status["16417638496"] = "승인반려"
        self.product_transport.history["16417638496"] = [{"status": "승인반려", "comment": "두 번째 반려 사유", "createdAt": "2026-10-18T09:00:00"}]
        self.clock[0] = NOW + timedelta(hours=3)
        service = self.monitor()
        service.due = lambda ref: True
        service.refresh(1)
        reasons = [p["c"] for e, p in self.events() if e == "PRODWHY:16417638496"]
        self.assertEqual(reasons, [REASON, "두 번째 반려 사유"])


class AggregationTests(Base_):

    def test_the_latest_state_and_the_current_reason_reach_the_screen(self):
        self.submission("16417638496")
        self.allow()
        self.product_transport.status["16417638496"] = "승인반려"
        self.product_transport.history["16417638496"] = [{"status": "승인반려", "comment": REASON, "createdAt": "2026-10-17T14:30:00"}]
        self.monitor().refresh(1)
        latest = ProductStatusMonitorService.latest_by_product(self.db)
        self.assertEqual(latest["16417638496"]["status"], "DENIED")
        self.assertEqual(latest["16417638496"]["reason"], REASON)
        service = EasyViewService(self.db)
        items = service._product_status([{"seller_product_id": "16417638496", "product_name": "레이펄스", "wizard_id": 22}])
        self.assertEqual((items[0]["status"], items[0]["reason"], items[0]["wizard_id"]), ("DENIED", REASON, 22))

    def test_an_old_reason_is_not_shown_for_a_newer_status(self):
        self.submission("16417638496")
        self.allow()
        self.product_transport.status["16417638496"] = "승인반려"
        self.product_transport.history["16417638496"] = [{"status": "승인반려", "comment": REASON, "createdAt": "2026-10-17T14:30:00"}]
        self.monitor().refresh(1)
        self.product_transport.status["16417638496"] = "승인완료"
        service = self.monitor()
        service.due = lambda ref: True
        service.refresh(1)
        item = EasyViewService(self.db)._product_status([{"seller_product_id": "16417638496", "product_name": "x", "wizard_id": 1}])[0]
        self.assertEqual(item["status"], "APPROVED")
        self.assertEqual(item["reason"], "")

    def test_a_product_never_read_has_an_empty_status(self):
        item = EasyViewService(self.db)._product_status([{"seller_product_id": "9", "product_name": "x", "wizard_id": 1}])[0]
        self.assertIsNone(item["status"])


class WiringTests(unittest.TestCase):

    def test_the_job_is_registered_every_thirty_minutes_and_reads_only(self):
        self.assertIn('PRODUCT_STATUS_JOB_ID = "coupang_product_status_check"', JOBS)
        self.assertIn("PRODUCT_STATUS_INTERVAL_MINUTES = 30", JOBS)
        self.assertIn("run_product_status_job,", JOBS.split("def register_all_jobs")[1])
        body = JOBS.split("def run_product_status_job")[1].split("def register_all_jobs")[0]
        self.assertIn("ProductStatusMonitorService(db).refresh(company_id)", body)
        for forbidden in ("PUT", "POST", "upload", "resubmit"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_the_service_has_no_write_path(self):
        source = (ROOT / "app/domains/order/product_status_monitor_service.py").read_text(encoding="utf-8")
        for forbidden in ('"POST"', '"PUT"', "upload_invoice", "stop_vendor_item_sale", "cancel_order_items", "update_outbound_days"):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == "__main__":
    unittest.main()
