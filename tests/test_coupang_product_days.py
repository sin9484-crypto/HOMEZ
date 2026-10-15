"""
=========================================================
Homez OS

File : tests/test_coupang_product_days.py

2026-10-15 — 쿠팡에 이미 등록된 상품의 기준 출고일(outboundShippingTimeDay) 변경 규칙을 고정한다. 공식 "상품 수정(승인불필요)"의
`PUT .../seller-products/{id}/partial` 요청 형식을 따르고, 승인완료 상품만·사용자가 본 현재 값과 같을 때만·한 번만 보내며
성공 뒤 상품을 다시 읽어 확인한다. 격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import os
import tempfile
import threading
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
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_product_days_service import CoupangProductDaysService
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.store_connection.model import StoreConnection
from tests.test_coupang_invoice_upload import response

VENDOR = "A00012345"
REF = "HOMEZ_TEST:store_connection:product-days"
PRODUCT = "16415163636"


def detail_body(days=(1, 1), status="승인완료"):
    return {"code": "SUCCESS", "message": "", "data": {
        "sellerProductId": int(PRODUCT), "statusName": status,
        "items": [{"vendorItemId": 100 + i, "outboundShippingTimeDay": d} for i, d in enumerate(days)]}}


def ok_put():
    return {"code": "SUCCESS", "message": "", "data": PRODUCT}


class FakeTransport:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.requests = []
        self.get = [response(200, detail_body())]
        self.put = [response(200, ok_put())]

    def request(self, method, url, *, headers, json_body, timeout):
        kind = "put" if method == "PUT" else "get"
        with self.lock:
            self.calls.append((kind, method))
            self.requests.append((kind, method, url, json_body))
            queue = self.put if kind == "put" else self.get
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def count(self, kind):
        return len([c for c in self.calls if c[0] == kind])


def provider(transport):
    return CoupangChannelControlProvider({"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR}, transport=transport)


class FakeSafety:
    def __init__(self, mode=AutomationMode.OPERATOR_APPROVAL, estop=False):
        self.mode, self.estop = mode, estop

    def get_current_mode(self):
        return self.mode

    def is_emergency_stop_active(self):
        return self.estop


class ProviderTests(unittest.TestCase):

    def test_the_request_follows_the_official_partial_update_shape(self):
        t = FakeTransport()
        result = provider(t).update_outbound_days(PRODUCT, 2)
        self.assertEqual(result.kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method), ("put", "PUT"))
        self.assertTrue(url.endswith(f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{PRODUCT}/partial"))
        self.assertEqual(body, {"sellerProductId": int(PRODUCT), "outboundShippingTimeDay": 2})

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for pid, days in (("abc", 2), ("", 2), (PRODUCT, 0), (PRODUCT, 15), (PRODUCT, -1), (PRODUCT, True), (PRODUCT, 1.5), (PRODUCT, "2")):
            self.assertEqual(p.update_outbound_days(pid, days).kind, CallKind.ACTION_REQUIRED, (pid, days))
        self.assertEqual(t.calls, [])

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_put()), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR", "message": "bad"}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "no code"}), CallKind.UNKNOWN),
            (response(200, {"code": "WEIRD"}), CallKind.UNKNOWN),
            (response(400, {"message": "bad"}), CallKind.ACTION_REQUIRED),
            (response(403, {}), CallKind.ACTION_REQUIRED),
            (response(429, {}), CallKind.RETRYABLE),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
            (requests.ConnectionError(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.put = [outcome]
            self.assertEqual(provider(t).update_outbound_days(PRODUCT, 2).kind, expected, outcome)

    def test_reading_returns_status_and_every_option_day(self):
        t = FakeTransport()
        t.get = [response(200, detail_body(days=(1, 2, 1)))]
        snap = provider(t).read_product_days(PRODUCT)
        self.assertTrue(snap.ok)
        self.assertEqual((snap.status_name, snap.days), ("승인완료", (1, 2, 1)))
        self.assertEqual(t.calls, [("get", "GET")])

    def test_an_unreadable_product_is_a_failure_not_a_value(self):
        broken = [
            response(500, {}), response(404, {"message": "no"}), response(200, {"data": {}}), response(200, {"data": {"items": []}}),
            response(200, {"data": {"statusName": "승인완료", "items": [{"vendorItemId": 1}]}}),
            response(200, {"data": {"statusName": "승인완료", "items": [{"outboundShippingTimeDay": "x"}]}}),
            response(200, {"data": {"statusName": "승인완료", "items": [{"outboundShippingTimeDay": True}]}}),
            requests.Timeout(),
        ]
        for outcome in broken:
            t = FakeTransport()
            t.get = [outcome]
            try:
                snap = provider(t).read_product_days(PRODUCT)
            except requests.RequestException:
                self.fail("읽기 통신 오류는 예외로 새지 않고 실패 값이어야 한다")
            self.assertFalse(snap.ok, outcome)

    def test_applied_check_needs_every_option_to_match(self):
        t = FakeTransport()
        t.get = [response(200, detail_body(days=(2, 2)))]
        self.assertTrue(provider(t).read_outbound_days_applied(PRODUCT, 2).value)
        t.get = [response(200, detail_body(days=(2, 1)))]
        self.assertFalse(provider(t).read_outbound_days_applied(PRODUCT, 2).value)
        t.get = [response(500, {})]
        result = provider(t).read_outbound_days_applied(PRODUCT, 2)
        self.assertFalse(result.ok)
        self.assertIsNone(result.value)


class ServiceBase(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = Path(path)
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(bind=self.engine, tables=[StoreConnection.__table__, ChannelActionRequest.__table__])
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.db.execute(text(
            "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
            "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50))"))
        self.db.commit()
        self.addCleanup(self._cleanup)
        self.store = self.add_store("홈즈쿠팡", "s1")
        self.creds = InMemoryCredentialStore()
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        self.transport = FakeTransport()
        self.safety = FakeSafety()

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        try:
            self.path.unlink()
        except OSError:
            pass

    def add_store(self, name, key, status="CONNECTED", company=1, marketplace="COUPANG"):
        store = StoreConnection(
            company_id=company, marketplace_code=marketplace, display_name=name, seller_identifier=name,
            connection_status=status, created_by=1, creation_idempotency_key=key,
            creation_request_fingerprint=key * 32, credential_reference=REF,
        )
        self.db.add(store)
        self.db.commit()
        return store

    def service(self):
        def factory(session):
            return CoupangChannelActions(
                session, credential_store=self.creds, now_factory=lambda: datetime(2026, 10, 15, 14, 0, 0),
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
            )
        return CoupangProductDaysService(self.db, actions_factory=factory, safety=self.safety)

    def rows(self):
        self.db.expire_all()
        return self.db.query(ChannelActionRequest).all()

    def codes(self, plan):
        return {b.code for b in plan.blockers}


class PlanTests(ServiceBase):

    def test_a_ready_product_shows_current_values_read_from_coupang(self):
        plan = self.service().plan(1, PRODUCT, 2)
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.coupang_status, plan.current_days), ("승인완료", [1, 1]))
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_local_only_preview_makes_no_request(self):
        plan = self.service().plan(1, PRODUCT, 2, live=False)
        self.assertFalse(plan.sendable)
        self.assertEqual(self.transport.calls, [])

    def test_invalid_input_blocks_before_any_request(self):
        for pid, days, code in (("abc", 2, "INVALID_PRODUCT_ID"), (PRODUCT, 0, "INVALID_DAYS"), (PRODUCT, 15, "INVALID_DAYS")):
            self.assertIn(code, self.codes(self.service().plan(1, pid, days)))
        self.assertEqual(self.transport.calls, [])

    def test_store_resolution_never_guesses(self):
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, PRODUCT, 2)))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.service().plan(1, PRODUCT, 2)))
        self.assertEqual(self.transport.calls, [])

    def test_other_companies_disabled_and_other_marketplace_stores_are_ignored(self):
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.add_store("other-company", "k1", company=2)
        self.add_store("disabled", "k2", status="DISABLED")
        self.add_store("naver", "k3", marketplace="NAVER")
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, PRODUCT, 2)))

    def test_only_an_approved_product_can_be_changed(self):
        for status in ("승인대기중", "임시저장중", "승인반려", "DENIED", "IN_REVIEW", ""):
            self.transport.get = [response(200, detail_body(status=status))]
            self.assertIn("STATUS_NOT_ALLOWED", self.codes(self.service().plan(1, PRODUCT, 2)), status)

    def test_english_and_korean_approved_statuses_are_accepted(self):
        for status in ("승인완료", "APPROVED"):
            self.transport.get = [response(200, detail_body(status=status))]
            self.assertTrue(self.service().plan(1, PRODUCT, 2).sendable, status)

    def test_an_already_applied_value_is_reported_not_resent(self):
        self.transport.get = [response(200, detail_body(days=(2, 2)))]
        plan = self.service().plan(1, PRODUCT, 2)
        self.assertTrue(plan.already_applied)
        self.assertFalse(plan.sendable)

    def test_a_partially_applied_product_is_still_sendable(self):
        self.transport.get = [response(200, detail_body(days=(2, 1)))]
        plan = self.service().plan(1, PRODUCT, 2)
        self.assertFalse(plan.already_applied)
        self.assertTrue(plan.sendable)

    def test_a_failed_read_blocks_instead_of_guessing(self):
        self.transport.get = [response(500, {})]
        plan = self.service().plan(1, PRODUCT, 2)
        self.assertIn("PRODUCT_READ_FAILED", self.codes(plan))
        self.assertFalse(plan.live_checked)

    def test_gate_reasons_are_shown_without_hiding_the_data_checks(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.service().plan(1, PRODUCT, 2)
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)
        self.assertTrue(plan.live_checked)

    def test_the_plan_never_contains_credentials(self):
        import json
        body = json.dumps(self.service().plan(1, PRODUCT, 2).to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key", REF):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, **override):
        args = dict(expected_current_days=[1, 1])
        args.update(override)
        return self.service().send(1, 7, PRODUCT, 2, **args)

    def test_a_confirmed_send_goes_out_once_is_recorded_and_verified(self):
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual((self.transport.count("put"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("PRODUCT_OUTBOUND_DAYS", f"product:{PRODUCT}:days:2", ChannelActionStatus.SUCCEEDED, 7))

    def test_a_success_response_that_did_not_change_the_product_is_flagged_unverified(self):
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(1, 1)))]
        result = self.send()
        self.assertEqual(result["outcome"], "SUCCEEDED")
        self.assertIs(result["verified"], False)

    def test_a_failed_verification_read_is_reported_as_unknown_not_true(self):
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(500, {})]
        self.assertIsNone(self.send()["verified"])

    def test_sending_again_after_success_is_a_no_op(self):
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        self.send()
        again = self.send()
        self.assertEqual((again["outcome"], again["executed"]), ("ALREADY_DONE", False))
        self.assertEqual(self.transport.count("put"), 1)

    def test_a_different_current_value_than_the_user_saw_refuses_to_send(self):
        with self.assertRaises(ConflictException):
            self.send(expected_current_days=[1])
        with self.assertRaises(ConflictException):
            self.send(expected_current_days=[1, 2])
        self.assertEqual(self.transport.count("put"), 0)

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.send()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_a_blocked_product_is_refused_with_its_reasons(self):
        self.transport.get = [response(200, detail_body(status="승인대기중"))]
        with self.assertRaises(BadRequestException) as caught:
            self.send()
        self.assertIn("STATUS_NOT_ALLOWED", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual(self.transport.count("put"), 0)

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append("put" if method == "PUT" else "get")
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1], before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("put"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1], before_send=deny)
        self.assertEqual((other.count("put"), self.rows()), (0, []))

    def test_a_rejection_is_not_repeated_without_a_person(self):
        self.transport.put = [response(200, {"code": "ERROR", "message": "no"})]
        self.assertEqual(self.send()["outcome"], "ACTION_REQUIRED")
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.put = [response(200, ok_put())]
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading_coupang(self):
        self.transport.put = [requests.Timeout()]
        self.assertEqual(self.send()["outcome"], "UNKNOWN")
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.get = [response(200, detail_body(days=(2, 2)))]
        self.assertEqual(self.service().reconcile(1, PRODUCT, 2)["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("put"), 1)

    def test_changing_to_a_different_value_is_a_new_request(self):
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        self.send()
        self.transport.get = [response(200, detail_body(days=(2, 2))), response(200, detail_body(days=(3, 3)))]
        result = self.service().send(1, 7, PRODUCT, 3, expected_current_days=[2, 2])
        self.assertEqual((result["outcome"], result["executed"]), ("SUCCEEDED", True))
        self.assertEqual(self.transport.count("put"), 2)

    def test_an_audit_record_is_written_for_a_send(self):
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("COUPANG_PRODUCT_OUTBOUND_DAYS", "coupang_product", PRODUCT, 7)])


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_preview_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("preview_coupang_product_days", "send_coupang_product_days", "reconcile_coupang_product_days"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        preview = router.split("def preview_coupang_product_days(")[1].split("@router.post")[0]
        self.assertNotIn(".send(", preview)

    def test_the_request_schema_is_strict(self):
        from pydantic import ValidationError
        from app.domains.purchase_task.coupang_product_days_schema import CoupangProductDaysSendRequest
        good = dict(days=2, expected_current_days=[1])
        CoupangProductDaysSendRequest(**good)
        for bad in ({"extra": 1}, {"days": 0}, {"days": 15}, {"expected_current_days": []}):
            with self.assertRaises(ValidationError):
                CoupangProductDaysSendRequest(**{**good, **bad})

    def test_the_action_type_fits_the_ledger_column(self):
        from app.domains.purchase_task.constants import ChannelActionType
        self.assertIn(ChannelActionType.PRODUCT_OUTBOUND_DAYS, ChannelActionType.ALL)
        self.assertLessEqual(len(ChannelActionType.PRODUCT_OUTBOUND_DAYS), 30)


if __name__ == "__main__":
    unittest.main()
