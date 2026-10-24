"""
=========================================================
Homez OS

File : tests/test_coupang_stock.py

2026-10-24 — 쿠팡 쓰기 1번: 옵션별 재고 수량 변경 규칙을 고정한다. 공식 "상품 아이템별 수량 변경"
`PUT .../vendor-items/{vendorItemId}/quantities/{quantity}`(본문 없음)과 재고 조회 `GET .../inventories`(data.amountInStock)를 따르고,
사용자가 본 현재 재고와 같을 때만·0은 따로 확인했을 때만·요청마다 한 번만 보내며 성공 뒤 재고를 다시 읽어 확인한다.
격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import requests
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.external_call.constants import CallPurpose
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_channel_control_provider import MAX_STOCK_QUANTITY
from app.domains.purchase_task.coupang_stock_service import CoupangStockService
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.store_connection.model import StoreConnection
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_product_days import FakeSafety
from tests.test_coupang_product_days import FakeTransport as _DaysTransport

VENDOR = "A00012345"
REF = "HOMEZ_TEST:store_connection:stock"
ITEM = "70012345678"
REQ = "req-20261024-0001"


def inventory(amount=5, price=12050, on_sale=True):
    return {"code": "SUCCESS", "message": "", "data": {"sellerItemId": int(ITEM), "amountInStock": amount, "salePrice": price, "onSale": on_sale}}


def ok_put():
    return {"code": "SUCCESS", "message": "재고 변경을 완료했습니다."}


class FakeTransport(_DaysTransport):
    def __init__(self):
        super().__init__()
        self.get = [response(200, inventory())]
        self.put = [response(200, ok_put())]


def provider(transport):
    return CoupangChannelControlProvider({"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR}, transport=transport)


class ProviderTests(unittest.TestCase):

    def test_the_request_follows_the_official_shape(self):
        t = FakeTransport()
        result = provider(t).update_stock(ITEM, 12)
        self.assertEqual(result.kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method, body), ("put", "PUT", None))
        self.assertTrue(url.endswith(f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{ITEM}/quantities/12"), url)

    def test_zero_and_the_upper_limit_are_valid_requests(self):
        t = FakeTransport()
        self.assertEqual(provider(t).update_stock(ITEM, 0).kind, CallKind.SUCCEEDED)
        self.assertEqual(provider(t).update_stock(ITEM, MAX_STOCK_QUANTITY).kind, CallKind.SUCCEEDED)
        self.assertTrue(t.requests[0][2].endswith("/quantities/0"))
        self.assertEqual(MAX_STOCK_QUANTITY, 99_999)

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for item, qty in (("abc", 1), ("", 1), ("1" * 21, 1), (ITEM, -1), (ITEM, MAX_STOCK_QUANTITY + 1), (ITEM, True), (ITEM, 1.5), (ITEM, "3"), (ITEM, None)):
            self.assertEqual(p.update_stock(item, qty).kind, CallKind.ACTION_REQUIRED, (item, qty))
        self.assertEqual(t.calls, [])
        self.assertEqual(p.validate_stock(ITEM, 5), None)
        self.assertEqual(p.validate_stock("x", 5).error_class, "INVALID_TARGET")
        self.assertEqual(p.validate_stock(ITEM, -5).error_class, "INVALID_QUANTITY")

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_put()), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR", "message": "유효하지 않은 재고수량입니다."}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "no code"}), CallKind.UNKNOWN),
            (response(200, {"code": "200"}), CallKind.UNKNOWN),          # 이 API의 성공 표기는 SUCCESS뿐(문서)
            (response(400, {"message": "삭제된 상품은 변경이 불가능합니다."}), CallKind.ACTION_REQUIRED),
            (response(403, {}), CallKind.ACTION_REQUIRED),
            (response(429, {}), CallKind.RETRYABLE),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
            (requests.ConnectionError(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.put = [outcome]
            self.assertEqual(provider(t).update_stock(ITEM, 3).kind, expected, outcome)

    def test_reading_returns_stock_price_and_sale_state(self):
        t = FakeTransport()
        t.get = [response(200, inventory(amount=7, price=9900, on_sale=False))]
        snap = provider(t).read_stock(ITEM)
        self.assertEqual((snap.ok, snap.amount_in_stock, snap.sale_price, snap.on_sale), (True, 7, 9900, False))
        self.assertTrue(t.requests[0][2].endswith(f"/vendor-items/{ITEM}/inventories"))
        self.assertEqual(t.calls, [("get", "GET")])

    def test_an_unreadable_stock_is_a_failure_not_a_value(self):
        broken = [
            response(500, {}), response(400, {"message": "유효한 옵션이 없습니다"}), response(200, {"code": "ERROR", "data": {}}),
            response(200, {"code": "SUCCESS", "data": None}), response(200, {"code": "SUCCESS", "data": {"amountInStock": "5"}}),
            response(200, {"code": "SUCCESS", "data": {"amountInStock": True}}), response(200, {"code": "SUCCESS", "data": {"amountInStock": -1}}),
            response(200, {"code": "SUCCESS", "data": {"salePrice": 100}}), requests.Timeout(),
            response(200, {"code": "ERROR", "message": "유효한 옵션이 없습니다", "data": {"amountInStock": 5}}),   # 실패 코드면 값이 있어도 읽지 않는다
        ]
        for outcome in broken:
            t = FakeTransport()
            t.get = [outcome]
            snap = provider(t).read_stock(ITEM)
            self.assertFalse(snap.ok, outcome)
            self.assertIsNone(snap.amount_in_stock, outcome)
        self.assertFalse(provider(FakeTransport()).read_stock("abc").ok)

    def test_odd_price_or_sale_fields_do_not_make_the_stock_unreadable(self):
        t = FakeTransport()
        t.get = [response(200, {"code": "SUCCESS", "data": {"amountInStock": 3, "salePrice": "x", "onSale": "yes"}})]
        snap = provider(t).read_stock(ITEM)
        self.assertEqual((snap.ok, snap.amount_in_stock, snap.sale_price, snap.on_sale), (True, 3, None, None))

    def test_applied_check(self):
        t = FakeTransport()
        t.get = [response(200, inventory(amount=12))]
        self.assertTrue(provider(t).read_stock_applied(ITEM, 12).value)
        t.get = [response(200, inventory(amount=11))]
        self.assertFalse(provider(t).read_stock_applied(ITEM, 12).value)
        t.get = [response(200, inventory(amount=13))]
        self.assertFalse(provider(t).read_stock_applied(ITEM, 12).value)          # 많아도 '같지 않다'다
        t.get = [response(500, {})]
        result = provider(t).read_stock_applied(ITEM, 12)
        self.assertEqual((result.ok, result.value), (False, None))


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
                session, credential_store=self.creds, now_factory=lambda: datetime(2026, 10, 24, 10, 0, 0),
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
            )
        return CoupangStockService(self.db, actions_factory=factory, safety=self.safety)

    def rows(self):
        self.db.expire_all()
        return self.db.query(ChannelActionRequest).all()

    def codes(self, plan):
        return {b.code for b in plan.blockers}


class PlanTests(ServiceBase):

    def test_a_ready_option_shows_the_current_stock_read_from_coupang(self):
        plan = self.service().plan(1, ITEM, 12, request_ref=REQ)
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.current_stock, plan.sale_price, plan.on_sale, plan.zero_warning), (5, 12050, True, False))
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_local_only_preview_makes_no_request(self):
        plan = self.service().plan(1, ITEM, 12, live=False)
        self.assertFalse(plan.sendable)
        self.assertEqual(self.transport.calls, [])

    def test_invalid_input_blocks_before_any_request(self):
        for item, qty, ref, code in (("abc", 1, None, "INVALID_VENDOR_ITEM_ID"), ("1" * 21, 1, None, "INVALID_VENDOR_ITEM_ID"),
                                     (ITEM, -1, None, "INVALID_QUANTITY"), (ITEM, MAX_STOCK_QUANTITY + 1, None, "INVALID_QUANTITY"),
                                     (ITEM, True, None, "INVALID_QUANTITY"), (ITEM, 1, "short", "INVALID_REQUEST_REF"), (ITEM, 1, "bad ref!!", "INVALID_REQUEST_REF")):
            self.assertIn(code, self.codes(self.service().plan(1, item, qty, request_ref=ref)), (item, qty, ref))
        self.assertEqual(self.transport.calls, [])

    def test_zero_is_allowed_but_flagged(self):
        plan = self.service().plan(1, ITEM, 0, request_ref=REQ)
        self.assertTrue(plan.zero_warning)
        self.assertTrue(plan.sendable)

    def test_store_resolution_never_guesses(self):
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, ITEM, 3)))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.service().plan(1, ITEM, 3)))
        self.assertEqual(self.transport.calls, [])

    def test_other_companies_disabled_and_other_marketplace_stores_are_ignored(self):
        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.add_store("other-company", "k1", company=2)
        self.add_store("disabled", "k2", status="DISABLED")
        self.add_store("naver", "k3", marketplace="NAVER")
        self.assertIn("NO_COUPANG_STORE", self.codes(self.service().plan(1, ITEM, 3)))

    def test_the_same_quantity_is_reported_not_resent(self):
        self.transport.get = [response(200, inventory(amount=12))]
        plan = self.service().plan(1, ITEM, 12, request_ref=REQ)
        self.assertTrue(plan.already_applied)
        self.assertFalse(plan.sendable)

    def test_a_failed_read_blocks_instead_of_guessing(self):
        self.transport.get = [response(500, {})]
        plan = self.service().plan(1, ITEM, 12, request_ref=REQ)
        self.assertIn("STOCK_READ_FAILED", self.codes(plan))
        self.assertFalse(plan.live_checked)
        self.assertIsNone(plan.current_stock)

    def test_gate_reasons_are_shown_without_hiding_the_data_checks(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.service().plan(1, ITEM, 12, request_ref=REQ)
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)
        self.assertTrue(plan.live_checked)

    def test_the_plan_never_contains_credentials(self):
        body = json.dumps(self.service().plan(1, ITEM, 12, request_ref=REQ).to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key", REF, VENDOR):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, quantity=12, **override):
        args = dict(expected_current_stock=5, request_ref=REQ)
        args.update(override)
        return self.service().send(1, 7, ITEM, quantity, **args)

    def test_a_confirmed_send_goes_out_once_is_recorded_and_verified(self):
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=12))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"], result["after_stock"]), ("SUCCEEDED", True, True, 12))
        self.assertEqual((self.transport.count("put"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("STOCK_UPDATE", f"vi:{ITEM}:qty:12:req:{REQ}", ChannelActionStatus.SUCCEEDED, 7))

    def test_a_success_response_with_a_different_stock_after_is_flagged_not_trusted(self):
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=11))]
        result = self.send()
        self.assertEqual((result["outcome"], result["verified"], result["after_stock"]), ("SUCCEEDED", False, 11))

    def test_a_failed_verification_read_is_reported_as_unknown_not_true(self):
        self.transport.get = [response(200, inventory(amount=5)), response(500, {})]
        result = self.send()
        self.assertEqual((result["verified"], result["after_stock"]), (None, None))

    def test_the_same_request_cannot_be_sent_twice(self):
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=3))]   # 그사이 주문으로 3이 됐다
        self.send()
        self.transport.get = [response(200, inventory(amount=3))]
        again = self.send(expected_current_stock=3)
        self.assertEqual((again["outcome"], again["executed"]), ("ALREADY_DONE", False))
        self.assertEqual(self.transport.count("put"), 1)

    def test_a_new_request_can_set_the_same_quantity_again_later(self):
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=12))]
        self.send()
        self.transport.get = [response(200, inventory(amount=4)), response(200, inventory(amount=12))]
        later = self.send(expected_current_stock=4, request_ref="req-20261024-0002")
        self.assertEqual((later["outcome"], later["executed"]), ("SUCCEEDED", True))
        self.assertEqual(self.transport.count("put"), 2)

    def test_a_different_current_stock_than_the_user_saw_refuses_to_send(self):
        for expected in (4, 6, True):
            with self.assertRaises(ConflictException):
                self.send(expected_current_stock=expected)
        self.transport.get = [response(200, inventory(amount=1))]
        with self.assertRaises(ConflictException):
            self.send(expected_current_stock=True)                                  # 참/거짓은 1과 같게 보지 않는다
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_zero_needs_its_own_confirmation(self):
        with self.assertRaises(BadRequestException):
            self.send(quantity=0)
        with self.assertRaises(BadRequestException):
            self.send(quantity=0, confirm_zero="yes")
        self.assertEqual(self.transport.count("put"), 0)
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=0))]
        result = self.send(quantity=0, confirm_zero=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual([r[2].rsplit("/", 1)[1] for r in self.transport.requests if r[0] == "put"], ["0"])

    def test_a_bad_request_ref_is_refused_before_reading(self):
        for ref in ("", "short", "has space 123", "x" * 41, None):
            with self.assertRaises(BadRequestException):
                self.send(request_ref=ref)
        self.assertEqual(self.transport.calls, [])

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.send()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_a_failed_read_refuses_with_its_reason(self):
        self.transport.get = [response(500, {})]
        with self.assertRaises(BadRequestException) as caught:
            self.send()
        self.assertIn("STOCK_READ_FAILED", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual(self.transport.count("put"), 0)

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append("put" if method == "PUT" else "get")
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=12))]
        self.service().send(1, 7, ITEM, 12, expected_current_stock=5, request_ref=REQ, before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("put"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.service().send(1, 7, ITEM, 12, expected_current_stock=5, request_ref="req-20261024-0009", before_send=deny)
        self.assertEqual((other.count("put"), self.rows()), (0, []))

    def test_a_rejection_is_not_repeated_without_a_person(self):
        self.transport.put = [response(200, {"code": "ERROR", "message": "유효하지 않은 재고수량입니다."})]
        self.assertEqual(self.send()["outcome"], "ACTION_REQUIRED")
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.put = [response(200, ok_put())]
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=12))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading_coupang(self):
        self.transport.put = [requests.Timeout()]
        self.assertEqual(self.send()["outcome"], "UNKNOWN")
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.get = [response(200, inventory(amount=12))]
        self.assertEqual(self.service().reconcile(1, ITEM, 12, REQ)["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("put"), 1)
        with self.assertRaises(BadRequestException):
            self.service().reconcile(1, ITEM, 12, "bad")

    def test_an_audit_record_is_written_for_a_send(self):
        self.transport.get = [response(200, inventory(amount=5)), response(200, inventory(amount=12))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_STOCK_UPDATE", "coupang_vendor_item", ITEM, 7)])
        self.assertIn("5 → 12", rows[0][4])


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_preview_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("preview_coupang_stock", "send_coupang_stock", "reconcile_coupang_stock"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        preview = router.split("def preview_coupang_stock(")[1].split("@router.post")[0]
        self.assertNotIn(".send(", preview)
        self.assertLess(router.index('"/coupang-vendor-items/{vendor_item_id}/stock/preview"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from app.domains.purchase_task.coupang_stock_schema import CoupangStockSendRequest

        good = dict(quantity=12, expected_current_stock=5, request_ref=REQ)
        CoupangStockSendRequest(**good)
        for bad in ({"extra": 1}, {"quantity": -1}, {"quantity": MAX_STOCK_QUANTITY + 1}, {"quantity": "12"}, {"quantity": 1.5},
                    {"expected_current_stock": -1}, {"expected_current_stock": "5"}, {"request_ref": "short"}, {"request_ref": "bad ref 1234"},
                    {"confirm_zero": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangStockSendRequest(**{**good, **bad})

    def test_the_action_type_and_allowance_purpose_are_registered(self):
        self.assertIn(ChannelActionType.STOCK_UPDATE, ChannelActionType.ALL)
        self.assertLessEqual(len(ChannelActionType.STOCK_UPDATE), 30)
        self.assertIn(CallPurpose.STOCK_UPDATE, CallPurpose.ALL)
        self.assertIn(CallPurpose.STOCK_UPDATE, CallPurpose.ENFORCED)          # 허용이 없으면 막는다
        self.assertEqual(CallPurpose.LABELS[CallPurpose.STOCK_UPDATE], "쿠팡 재고 수량 변경")
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.STOCK_UPDATE], (CallPurpose.STOCK_UPDATE, "PUT"))
        key = CoupangChannelActions.stock_key(ITEM, MAX_STOCK_QUANTITY, "x" * 40)
        self.assertLessEqual(len(key), 200)                                       # 장부 대상 키 열 길이


if __name__ == "__main__":
    unittest.main()
