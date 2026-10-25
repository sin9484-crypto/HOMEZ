"""
=========================================================
Homez OS

File : tests/test_coupang_price.py

2026-10-25 — 쿠팡 쓰기 2번: 옵션별 판매가 변경 규칙을 고정한다. 공식 "상품 아이템별 가격 변경"
`PUT .../vendor-items/{vendorItemId}/prices/{price}`(본문 없음, 10원 단위, forceSalePriceUpdate 미전송)과 재고 조회 `GET .../inventories`
(data.salePrice)를 따르고, 사용자가 입력한 매입가·배송비로 계산한 최저 판매가 아래면·쿠팡 기본 변경 폭(50% 인하·100% 인상) 밖이면 보내지 않으며,
사용자가 본 지금 판매가와 같을 때만·20% 이상 변경은 따로 확인했을 때만·요청마다 한 번만 보내고 성공 뒤 판매가를 다시 읽어 확인한다.
격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import unittest
from decimal import Decimal
from pathlib import Path

import requests
from pydantic import ValidationError
from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.product.model import Product  # noqa: F401 — 마진 계산(공급처 패키지)이 등록하는 Supplier 매퍼가 Product를 찾게 한다
from app.domains.external_call.constants import CallPurpose
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import MAX_SALE_PRICE
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_price_service import CoupangPriceService
from app.domains.purchase_task.coupang_price_service import MAX_COST
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import ITEM
from tests.test_coupang_stock import REQ
from tests.test_coupang_stock import FakeTransport as _StockTransport
from tests.test_coupang_stock import ServiceBase as _StockBase
from tests.test_coupang_stock import provider

# 매입가 6,000 + 배송비 3,000 → 9,000 / (1 − 9.8% − 3% − 18%) = 13,005.78… → 최저 판매가 13,010원(10원 단위 올림)
COST, SHIP, FLOOR = 6000, 3000, 13010


def inventory(price=15000, amount=5, on_sale=True):
    return {"code": "SUCCESS", "message": "", "data": {"sellerItemId": int(ITEM), "amountInStock": amount, "salePrice": price, "onSale": on_sale}}


def ok_put():
    return {"code": "SUCCESS", "message": "가격 변경을 완료했습니다."}


class FakeTransport(_StockTransport):
    def __init__(self):
        super().__init__()
        self.get = [response(200, inventory())]
        self.put = [response(200, ok_put())]


class ProviderTests(unittest.TestCase):

    def test_the_request_follows_the_official_shape_without_force(self):
        t = FakeTransport()
        result = provider(t).update_price(ITEM, 16000)
        self.assertEqual(result.kind, CallKind.SUCCEEDED)
        kind, method, url, body = t.requests[0]
        self.assertEqual((kind, method, body), ("put", "PUT", None))
        self.assertTrue(url.endswith(f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{ITEM}/prices/16000"), url)
        self.assertNotIn("force", url.lower())                                  # 변경 폭 제한을 풀지 않는다
        self.assertNotIn("apMinSalePrice", url)

    def test_invalid_input_makes_no_request(self):
        t = FakeTransport()
        p = provider(t)
        for item, price in (("abc", 1000), ("", 1000), ("1" * 21, 1000), (ITEM, 0), (ITEM, 5), (ITEM, 1005), (ITEM, MAX_SALE_PRICE + 10),
                            (ITEM, True), (ITEM, 1000.0), (ITEM, "1000"), (ITEM, None), (ITEM, -1000)):
            self.assertEqual(p.update_price(item, price).kind, CallKind.ACTION_REQUIRED, (item, price))
        self.assertEqual(t.calls, [])
        self.assertIsNone(p.validate_price(ITEM, 10))
        self.assertIsNone(p.validate_price(ITEM, MAX_SALE_PRICE))
        self.assertEqual(p.validate_price("x", 1000).error_class, "INVALID_TARGET")
        self.assertEqual(p.validate_price(ITEM, 1001).error_class, "INVALID_PRICE")

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_put()), CallKind.SUCCEEDED),
            (response(200, {"code": "ERROR", "message": "가격 변경 범위를 벗어났습니다."}), CallKind.ACTION_REQUIRED),
            (response(200, {"message": "no code"}), CallKind.UNKNOWN),
            (response(200, {"code": "200"}), CallKind.UNKNOWN),
            (response(400, {"message": "가격은 10원 단위로 입력해주세요."}), CallKind.ACTION_REQUIRED),
            (response(403, {}), CallKind.ACTION_REQUIRED),
            (response(429, {}), CallKind.RETRYABLE),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
            (requests.ConnectionError(), CallKind.UNKNOWN),
        ]
        for outcome, expected in cases:
            t = FakeTransport()
            t.put = [outcome]
            self.assertEqual(provider(t).update_price(ITEM, 16000).kind, expected, outcome)

    def test_applied_check_reads_the_sale_price(self):
        t = FakeTransport()
        t.get = [response(200, inventory(price=16000))]
        self.assertTrue(provider(t).read_price_applied(ITEM, 16000).value)
        t.get = [response(200, inventory(price=15000))]
        self.assertFalse(provider(t).read_price_applied(ITEM, 16000).value)
        t.get = [response(200, inventory(price=17000))]
        self.assertFalse(provider(t).read_price_applied(ITEM, 16000).value)      # 높아도 '같지 않다'다
        for broken in (response(500, {}), response(200, {"code": "SUCCESS", "data": {"amountInStock": 3, "salePrice": "x"}})):
            t.get = [broken]
            result = provider(t).read_price_applied(ITEM, 16000)
            self.assertEqual((result.ok, result.value), (False, None), broken)


class ServiceBase(_StockBase):

    def setUp(self):
        super().setUp()
        Base.metadata.create_all(bind=self.engine, tables=[CompanyChannelPolicySettings.__table__])
        self.transport = FakeTransport()

    def service(self):
        stock = super().service()
        return CoupangPriceService(self.db, actions_factory=stock._actions_factory, safety=self.safety)

    def plan(self, price=16000, unit_cost=COST, shipping_cost=SHIP, **kw):
        kw.setdefault("request_ref", REQ)
        return self.service().plan(1, ITEM, price, unit_cost=unit_cost, shipping_cost=shipping_cost, **kw)


class PlanTests(ServiceBase):

    def test_a_ready_option_shows_the_current_price_and_the_margin(self):
        plan = self.plan()
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual((plan.current_price, plan.current_stock, plan.on_sale, plan.floor_price, plan.big_change), (15000, 5, True, FLOOR, False))
        self.assertEqual(plan.margin_rate.quantize(Decimal("0.0001")), Decimal("0.3095"))   # (16000×0.872 − 9000) / 16000
        margin = plan.to_dict()["margin"]
        self.assertEqual((margin["fee_rate"], margin["return_reserve_rate"], margin["min_margin"]), (0.098, 0.03, 0.18))
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_local_only_preview_makes_no_request(self):
        plan = self.plan(live=False)
        self.assertFalse(plan.sendable)
        self.assertEqual(plan.floor_price, FLOOR)
        self.assertEqual(self.transport.calls, [])

    def test_a_price_below_the_floor_is_blocked_and_the_floor_is_rounded_up(self):
        self.assertIn("BELOW_MARGIN_FLOOR", self.codes(self.plan(price=FLOOR - 10, live=False)))
        self.assertNotIn("BELOW_MARGIN_FLOOR", self.codes(self.plan(price=FLOOR, live=False)))
        self.assertEqual(self.transport.calls, [])

    def test_the_company_margin_setting_is_used(self):
        self.db.add(CompanyChannelPolicySettings(company_id=1, min_target_margin_rate=Decimal("0.25"), updated_by=1))
        self.db.commit()
        plan = self.plan(live=False)
        # 9,000 / (1 − 0.098 − 0.03 − 0.25) = 14,469.45… → 14,470
        self.assertEqual((plan.floor_price, plan.to_dict()["margin"]["min_margin"]), (14470, 0.25))
        self.assertIn("BELOW_MARGIN_FLOOR", self.codes(self.plan(price=14460, live=False)))

    def test_an_impossible_margin_setting_blocks_instead_of_guessing(self):
        self.db.add(CompanyChannelPolicySettings(company_id=1, min_target_margin_rate=Decimal("0.90"), updated_by=1))
        self.db.commit()
        plan = self.plan(live=False)
        self.assertIn("MARGIN_UNKNOWN", self.codes(plan))
        self.assertIsNone(plan.floor_price)

    def test_invalid_input_blocks_before_any_request(self):
        cases = (
            dict(price=16005), dict(price=0), dict(price=True), dict(price="16000"), dict(price=MAX_SALE_PRICE + 10),
            dict(unit_cost=0), dict(unit_cost=None), dict(unit_cost=True), dict(unit_cost=MAX_COST + 1), dict(shipping_cost=-1),
            dict(shipping_cost="0"), dict(request_ref="short"), dict(request_ref="bad ref!!"),
        )
        expected = ("INVALID_PRICE",) * 5 + ("INVALID_COST",) * 6 + ("INVALID_REQUEST_REF",) * 2
        for override, code in zip(cases, expected):
            self.assertIn(code, self.codes(self.plan(**override)), override)
        for item in ("abc", "1" * 21):
            self.assertIn("INVALID_VENDOR_ITEM_ID", self.codes(self.service().plan(1, item, 16000, unit_cost=COST, shipping_cost=SHIP)))
        self.assertEqual(self.transport.calls, [])

    def test_zero_shipping_is_a_valid_cost(self):
        plan = self.plan(shipping_cost=0, live=False)
        self.assertNotIn("INVALID_COST", self.codes(plan))
        self.assertEqual(plan.floor_price, 8680)                                # 6,000 / 0.692 = 8,670.52… → 8,680

    def test_the_coupang_change_range_is_checked_locally(self):
        cheap = dict(unit_cost=1000, shipping_cost=0)                          # 최저 판매가 1,450원
        self.assertTrue(self.plan(price=7500, **cheap).sendable)                # 정확히 50% 인하는 받는다
        self.assertIn("OUT_OF_COUPANG_RANGE", self.codes(self.plan(price=7490, **cheap)))
        self.assertTrue(self.plan(price=30000, **cheap).sendable)               # 정확히 100% 인상은 받는다
        self.assertIn("OUT_OF_COUPANG_RANGE", self.codes(self.plan(price=30010, **cheap)))
        self.assertEqual(self.transport.count("put"), 0)

    def test_a_big_change_is_flagged(self):
        self.assertTrue(self.plan(price=18000).big_change)                      # +20%
        self.assertFalse(self.plan(price=17990).big_change)
        self.assertTrue(self.plan(price=12000, unit_cost=1000).big_change)      # −20%
        self.assertFalse(self.plan(price=12010, unit_cost=1000).big_change)
        self.assertAlmostEqual(self.plan(price=18000).to_dict()["change_rate"], 0.2)

    def test_the_same_price_is_reported_not_resent(self):
        self.transport.get = [response(200, inventory(price=16000))]
        plan = self.plan()
        self.assertTrue(plan.already_applied)
        self.assertFalse(plan.sendable)

    def test_a_failed_or_missing_price_read_blocks(self):
        for broken in (response(500, {}), response(200, {"code": "SUCCESS", "data": {"amountInStock": 3}}),
                       response(200, {"code": "SUCCESS", "data": {"amountInStock": 3, "salePrice": 0}})):
            self.transport.get = [broken]
            plan = self.plan()
            self.assertIn("PRICE_READ_FAILED", self.codes(plan), broken)
            self.assertFalse(plan.live_checked)
            self.assertIsNone(plan.current_price)

    def test_store_resolution_never_guesses(self):
        from app.domains.store_connection.model import StoreConnection

        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.plan()))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.plan()))
        self.assertEqual(self.transport.calls, [])

    def test_gate_reasons_are_shown_without_hiding_the_data_checks(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.plan()
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)
        self.assertTrue(plan.live_checked)

    def test_the_plan_never_contains_credentials(self):
        from tests.test_coupang_stock import REF
        from tests.test_coupang_stock import VENDOR

        body = json.dumps(self.plan().to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key", REF, VENDOR):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, price=16000, **override):
        args = dict(unit_cost=COST, shipping_cost=SHIP, expected_current_price=15000, request_ref=REQ)
        args.update(override)
        return self.service().send(1, 7, ITEM, price, **args)

    def test_a_confirmed_send_goes_out_once_is_recorded_and_verified(self):
        self.transport.get = [response(200, inventory()), response(200, inventory(price=16000))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"], result["verified"], result["after_price"]), ("SUCCEEDED", True, True, 16000))
        self.assertEqual((self.transport.count("put"), self.transport.count("get")), (1, 2))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.target_key, row.status, row.triggered_by),
                         ("PRICE_UPDATE", f"vi:{ITEM}:price:16000:req:{REQ}", ChannelActionStatus.SUCCEEDED, 7))

    def test_a_success_with_a_different_price_after_is_flagged_not_trusted(self):
        self.transport.get = [response(200, inventory()), response(200, inventory(price=15000))]
        result = self.send()
        self.assertEqual((result["outcome"], result["verified"], result["after_price"]), ("SUCCEEDED", False, 15000))

    def test_a_failed_verification_read_is_reported_as_unknown_not_true(self):
        for after in (response(500, {}), response(200, {"code": "SUCCESS", "data": {"amountInStock": 3}})):
            self.db.query(ChannelActionRequest).delete()
            self.db.commit()
            self.transport.get = [response(200, inventory()), after]
            result = self.send()
            self.assertEqual((result["verified"], result["after_price"]), (None, None), after)

    def test_the_same_request_cannot_be_sent_twice(self):
        self.transport.get = [response(200, inventory()), response(200, inventory(price=15500))]
        self.send()
        self.transport.get = [response(200, inventory(price=15500))]
        again = self.send(expected_current_price=15500)
        self.assertEqual((again["outcome"], again["executed"]), ("ALREADY_DONE", False))
        self.assertEqual(self.transport.count("put"), 1)

    def test_a_different_current_price_than_the_user_saw_refuses_to_send(self):
        for expected in (14990, 15010, True):
            with self.assertRaises(ConflictException):
                self.send(expected_current_price=expected)
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_a_below_floor_price_is_never_sent(self):
        with self.assertRaises(BadRequestException) as caught:
            self.send(price=FLOOR - 10)
        self.assertIn("BELOW_MARGIN_FLOOR", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_a_big_change_needs_its_own_confirmation(self):
        for confirm in (False, "yes", 1):
            with self.assertRaises(BadRequestException):
                self.send(price=18000, confirm_big_change=confirm)
        self.assertEqual(self.transport.count("put"), 0)
        self.transport.get = [response(200, inventory()), response(200, inventory(price=18000))]
        result = self.send(price=18000, confirm_big_change=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))

    def test_out_of_range_is_never_sent(self):
        with self.assertRaises(BadRequestException) as caught:
            self.send(price=30010, unit_cost=1000, shipping_cost=0, confirm_big_change=True)
        self.assertIn("OUT_OF_COUPANG_RANGE", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual(self.transport.count("put"), 0)

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

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append("put" if method == "PUT" else "get")
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, inventory()), response(200, inventory(price=16000))]
        self.send(before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("put"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.send(request_ref="req-20261025-0009", before_send=deny)
        self.assertEqual((other.count("put"), self.rows()), (0, []))

    def test_a_rejection_is_not_repeated_without_a_person(self):
        self.transport.put = [response(200, {"code": "ERROR", "message": "가격 변경 범위를 벗어났습니다."})]
        first = self.send()
        self.assertEqual((first["outcome"], first["verified"], first["after_price"]), ("ACTION_REQUIRED", None, None))
        self.assertEqual(self.transport.count("get"), 1)                        # 거부된 뒤에는 다시 읽지 않는다
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.put = [response(200, ok_put())]
        self.transport.get = [response(200, inventory()), response(200, inventory(price=16000))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_an_unknown_outcome_is_settled_only_by_reading_coupang(self):
        self.transport.put = [requests.Timeout()]
        unknown = self.send()
        self.assertEqual((unknown["outcome"], unknown["verified"]), ("UNKNOWN", None))
        self.assertEqual(self.transport.count("get"), 1)                        # 결과불명은 여기서 확인됐다고 하지 않는다
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.get = [response(200, inventory(price=16000))]
        self.assertEqual(self.service().reconcile(1, ITEM, 16000, REQ)["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("put"), 1)
        for args in ((ITEM, 16000, "bad"), ("abc", 16000, REQ), (ITEM, 16005, REQ), (ITEM, True, REQ), (ITEM, 0, REQ), (ITEM, MAX_SALE_PRICE + 10, REQ)):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_an_audit_record_is_written_for_a_send(self):
        self.transport.get = [response(200, inventory()), response(200, inventory(price=16000))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_PRICE_UPDATE", "coupang_vendor_item", ITEM, 7)])
        self.assertIn("15000 → 16000", rows[0][4])
        self.assertIn("매입가 6000", rows[0][4])


class AllowanceTests(ServiceBase):
    """승인 센터 허용이 **판매가 변경 목적으로** 세지고, 없으면 쿠팡으로 아무것도 나가지 않는다."""

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count, purpose=None):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=purpose or CallPurpose.PRICE_UPDATE, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang_and_a_request_is_raised(self):
        from app.domains.external_call.constants import AllowanceStatus
        from app.domains.external_call.model import ExternalCallAllowance

        plan = self.plan()
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(plan))
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "PRICE_UPDATE")])

    def test_a_stock_allowance_does_not_open_price_changes(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3, purpose=CallPurpose.STOCK_UPDATE)
        self.grant(ExtKind.WRITE, 1, purpose=CallPurpose.STOCK_UPDATE)
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.plan()))
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_whole_change(self):
        from app.domains.external_call.constants import AllowanceStatus
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.model import ExternalCallAllowance

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, inventory()), response(200, inventory()), response(200, inventory(price=16000))]
        # 미리보기(조회 1) → 전송 직전 재확인(조회 2) → 변경(변경 1) → 사후 확인(조회 3)
        self.assertTrue(self.plan().sendable)
        result = self.service().send(1, 7, ITEM, 16000, unit_cost=COST, shipping_cost=SHIP, expected_current_price=15000, request_ref=REQ)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("put")), (3, 1))
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.EXHAUSTED).count(), 2)

    def test_a_missing_write_allowance_stops_before_the_put(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.service().send(1, 7, ITEM, 16000, unit_cost=COST, shipping_cost=SHIP, expected_current_price=15000, request_ref=REQ)
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertIn("EXTERNAL_ALLOWANCE_REQUIRED", result["detail"])
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_preview_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("preview_coupang_price", "send_coupang_price", "reconcile_coupang_price"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        preview = router.split("def preview_coupang_price(")[1].split("@router.post")[0]
        self.assertNotIn(".send(", preview)
        self.assertLess(router.index('"/coupang-vendor-items/{vendor_item_id}/price/preview"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from app.domains.purchase_task.coupang_price_schema import CoupangPriceSendRequest

        good = dict(price=16000, unit_cost=COST, shipping_cost=SHIP, expected_current_price=15000, request_ref=REQ)
        CoupangPriceSendRequest(**good)
        CoupangPriceSendRequest(**{**good, "shipping_cost": 0})
        for bad in ({"extra": 1}, {"price": 16005}, {"price": 0}, {"price": MAX_SALE_PRICE + 10}, {"price": "16000"}, {"price": 1.5},
                    {"unit_cost": 0}, {"unit_cost": MAX_COST + 1}, {"shipping_cost": -1}, {"expected_current_price": 0},
                    {"expected_current_price": "15000"}, {"request_ref": "short"}, {"confirm_big_change": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangPriceSendRequest(**{**good, **bad})

    def test_the_action_type_and_allowance_purpose_are_registered(self):
        self.assertIn(ChannelActionType.PRICE_UPDATE, ChannelActionType.ALL)
        self.assertIn(CallPurpose.PRICE_UPDATE, CallPurpose.ALL)
        self.assertIn(CallPurpose.PRICE_UPDATE, CallPurpose.ENFORCED)          # 허용이 없으면 막는다
        self.assertEqual(CallPurpose.LABELS[CallPurpose.PRICE_UPDATE], "쿠팡 판매가 변경")
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.PRICE_UPDATE], (CallPurpose.PRICE_UPDATE, "PUT"))
        key = CoupangChannelActions.price_key(ITEM, MAX_SALE_PRICE, "x" * 40)
        self.assertLessEqual(len(key), 200)

    def test_the_default_allowance_is_counted_read_3_write_1(self):
        from app.domains.external_call.constants import AllowanceMode
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.service import _DEFAULT_REQUEST

        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.PRICE_UPDATE, ExtKind.READ)], (AllowanceMode.COUNTED, 3, None))
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.PRICE_UPDATE, ExtKind.WRITE)], (AllowanceMode.COUNTED, 1, None))


if __name__ == "__main__":
    unittest.main()
