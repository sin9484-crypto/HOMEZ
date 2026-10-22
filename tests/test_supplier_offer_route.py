"""
=========================================================
Homez OS

File : tests/test_supplier_offer_route.py

2026-10-23 — 상품 서칭 AI S3: `POST /product-import/supplier-offers/rank`(공급처 무관 공급 조건 비교)의 입력 검증·응답·권한을 고정한다. 순수 계산이라 DB·네트워크 0.
=========================================================
"""

import sys
import unittest
from decimal import Decimal
from types import SimpleNamespace

from pydantic import ValidationError

import app.domains.product_import.router  # noqa: F401 — sys.modules에 올린다
from app.domains.product_import import schema

ROUTER = sys.modules["app.domains.product_import.router"]
USER = SimpleNamespace(id=1, company_id=1)


def body(**overrides):
    base = {"offers": [{"supplier_code": "S1", "external_id": "A", "unit_cost": "7000", "shipping_cost": "3000", "return_shipping_cost": "3000",
                        "min_order_qty": 1, "dropship_supported": True, "api_available": False, "domestic_stock": True}]}
    base.update(overrides)
    return schema.SupplierOfferRankRequest(**base)


def run(request):
    return ROUTER.rank_supplier_offers(data=request, current_user=USER, db=None)


class RouteTests(unittest.TestCase):

    def test_the_route_is_mounted_with_post_and_the_wizard_permission(self):
        routes = [r for r in ROUTER.router.routes if r.path == "/product-import/supplier-offers/rank"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(sorted(routes[0].methods), ["POST"])
        import inspect

        # 권한 검사는 가드 객체가 만든 안쪽 함수(이름 'dependency')로 들어간다 — 함수 정의의 기본값이 위저드 생성 권한 가드인지로 확인한다.
        names = {getattr(d.call, "__name__", type(d.call).__name__) for d in routes[0].dependant.dependencies}
        self.assertIn("dependency", names)
        self.assertIn("ListingWizardPermissionGuard(LISTING_WIZARD_CREATE)", inspect.getsource(ROUTER.rank_supplier_offers))

    def test_a_ranked_result_with_the_assumptions_and_their_note(self):
        result = run(body(target_price="29900"))
        row = result["results"][0]
        self.assertEqual((row["rank"], row["decision"], row["floor_price"], row["cost_multiple"]), (1, "READY", 14451, 2.06))
        self.assertEqual(row["margin_at_target_percent"], 53.8)
        self.assertEqual(result["assumptions"], {"fee_rate": "0.098", "return_reserve_rate": "0.03", "min_margin": "0.18"})
        self.assertIn("가정값", result["assumptions_note"])
        self.assertIn("확인하지 못했습니다", result["assumptions_note"])

    def test_real_assumptions_replace_the_defaults(self):
        result = run(body(fee_rate="0.2"))
        self.assertGreater(result["results"][0]["floor_price"], 14451)
        self.assertEqual(result["assumptions"]["fee_rate"], "0.2")

    def test_unknown_values_hold_and_hidden_prices_hold(self):
        offers = [
            {"supplier_code": "S1", "external_id": "A"},
            {"supplier_code": "S1", "external_id": "B", "unit_cost": "9000", "shipping_cost": "3000", "price_visible": False, "dropship_supported": True},
            {"supplier_code": "S1", "external_id": "C", "unit_cost": "9000", "shipping_cost": "3000", "dropship_supported": False},
        ]
        rows = {r["external_id"]: r for r in run(body(offers=offers))["results"]}
        self.assertEqual({k: v["decision"] for k, v in rows.items()}, {"A": "HOLD", "B": "HOLD", "C": "EXCLUDE"})
        self.assertIsNone(rows["A"]["floor_price"])

    def test_the_order_is_stable_for_the_same_request(self):
        offers = [{"supplier_code": "S%d" % (i % 3), "external_id": str(i), "unit_cost": str(5000 + i * 700), "shipping_cost": "3000", "dropship_supported": True} for i in range(12)]
        first = [(r["supplier_code"], r["external_id"]) for r in run(body(offers=offers))["results"]]
        second = [(r["supplier_code"], r["external_id"]) for r in run(body(offers=list(reversed(offers))))["results"]]
        self.assertEqual(first, second)


class ValidationTests(unittest.TestCase):

    def test_invalid_numbers_are_rejected_before_any_calculation(self):
        offer = {"supplier_code": "S", "external_id": "1"}
        for bad in ({"unit_cost": "-1"}, {"shipping_cost": "-1"}, {"return_shipping_cost": "-5"}, {"unit_cost": "NaN"}, {"unit_cost": "Infinity"}, {"min_order_qty": -1},
                    {"min_order_qty": 100001}, {"unit_cost": "abc"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                body(offers=[{**offer, **bad}])

    def test_size_and_identifier_limits(self):
        with self.assertRaises(ValidationError):
            body(offers=[])
        one = {"supplier_code": "S", "external_id": "1"}
        body(offers=[{**one, "external_id": str(i)} for i in range(50)])
        with self.assertRaises(ValidationError):
            body(offers=[{**one, "external_id": str(i)} for i in range(51)])
        for bad in ({"supplier_code": ""}, {"external_id": ""}, {"supplier_code": "x" * 31}, {"external_id": "x" * 61}, {"title": "x" * 121}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                body(offers=[{**one, **bad}])

    def test_rates_and_target_price_limits(self):
        for bad in ({"fee_rate": "1"}, {"fee_rate": "-0.1"}, {"return_reserve_rate": "1"}, {"min_margin": "1"}, {"target_price": "0"}, {"target_price": "-1"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                body(**bad)
        self.assertEqual(body().fee_rate, Decimal("0.098"))
        self.assertIsNone(body().target_price)

    def test_rates_that_add_up_to_one_or_more_hold_every_offer_instead_of_crashing(self):
        result = run(body(fee_rate="0.5", return_reserve_rate="0.3", min_margin="0.3"))
        self.assertEqual(result["results"][0]["decision"], "HOLD")
        self.assertEqual([x["code"] for x in result["results"][0]["reasons"] if x["code"] == "ASSUMPTIONS_INVALID"], ["ASSUMPTIONS_INVALID"])


if __name__ == "__main__":
    unittest.main()
