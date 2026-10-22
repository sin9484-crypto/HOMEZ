"""
=========================================================
Homez OS

File : tests/test_supplier_offer_rules.py

2026-10-23 — 상품 서칭 AI S3(반영 D): 공급처 비의존 공급 조건 평가(`supplier/offer_rules.py`, 순수·AI 없음·외부 호출 없음)를 고정한다.

  - 계산이 HOMEZ가 기록한 값과 맞는다: 백산수 승인 경제성(판매 12,050·매입 5,310·배송 3,000 → 2,197.60원·18.24%), 2026-10-21 마진 시뮬레이션(최저 판매가 14,451/17,919/21,676/26,012원).
  - 모르는 값은 정상이 아니다: 공급가·배송비를 모르거나 가격이 회원 전용이면 HOLD, 위탁이 안 되면 EXCLUDE, 모르면 HOLD.
  - 경계값: 배송비 비중 30%, 반품 부담 50%, 최소 주문 1, 기대 판매가가 최저 판매가에 걸리는 경우.
  - 순위는 결정적이다(입력 순서가 달라도 같다). 공급처 이름을 규칙이 모른다.
=========================================================
"""

import json
import random
import re
import unittest
from decimal import Decimal
from pathlib import Path

from app.domains.supplier import offer_rules as r
from app.domains.supplier.offer_rules import MarginAssumptions
from app.domains.supplier.offer_rules import SupplierOffer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/supplier/offer_rules.py").read_text(encoding="utf-8")
D = Decimal


def offer(cost="7000", ship="3000", **kw):
    base = dict(supplier_code="S1", external_id="A", title="상품", unit_cost=None if cost is None else D(cost), shipping_cost=None if ship is None else D(ship),
                return_shipping_cost=D("3000"), min_order_qty=1, price_visible=True, dropship_supported=True, api_available=True, domestic_stock=True)
    base.update(kw)
    return SupplierOffer(**base)


def codes(result, level=None):
    return [x["code"] for x in result["reasons"] if level is None or x["level"] == level]


class CalculationTests(unittest.TestCase):

    def test_it_reproduces_the_recorded_baeksansu_economics(self):
        o = offer("5310", "3000")
        a = MarginAssumptions()
        profit = D("12050") * (1 - a.fee_rate - a.return_reserve_rate) - D("5310") - D("3000")
        self.assertEqual(profit.quantize(D("0.01")), D("2197.60"))
        self.assertEqual((r.margin_at(o, D("12050"), a) * 100).quantize(D("0.01")), D("18.24"))

    def test_floor_prices_and_multiples_match_the_2026_10_21_simulation(self):
        expected = {"7000": (14451, 2.06), "9400": (17919, 1.91), "12000": (21676, 1.81), "15000": (26012, 1.73)}
        for cost, (floor, multiple) in expected.items():
            result = r.evaluate_offer(offer(cost))
            self.assertEqual(result["floor_price"], floor, cost)
            self.assertEqual(result["cost_multiple"], multiple, cost)

    def test_margin_table_matches_the_simulation(self):
        table = {
            ("7000", "19900"): "36.9", ("7000", "59800"): "70.5", ("9400", "29900"): "45.7", ("12000", "19900"): "11.8", ("15000", "19900"): "-3.3",
            ("15000", "59800"): "57.1",
        }
        for (cost, price), expected in table.items():
            value = r.margin_at(offer(cost), D(price), MarginAssumptions()) * 100
            self.assertEqual(value.quantize(D("0.1")), D(expected), (cost, price))

    def test_the_floor_price_gives_exactly_the_minimum_margin(self):
        for cost in ("7000", "9400", "12000", "15000", "5310"):
            o = offer(cost)
            floor = r.floor_price(o, MarginAssumptions())
            self.assertEqual(r.margin_at(o, floor, MarginAssumptions()).quantize(D("0.000001")), D("0.18"))

    def test_custom_assumptions_change_the_result(self):
        o = offer("10000", "0")
        base = r.floor_price(o, MarginAssumptions())
        richer = r.floor_price(o, MarginAssumptions(fee_rate=D("0.2")))
        self.assertGreater(richer, base)
        self.assertLess(r.floor_price(o, MarginAssumptions(min_margin=D("0.1"))), base)

    def test_invalid_assumptions_hold_the_offer(self):
        for bad in (MarginAssumptions(fee_rate=D("0.5"), return_reserve_rate=D("0.3"), min_margin=D("0.3")), MarginAssumptions(fee_rate=D("-0.1")),
                    MarginAssumptions(fee_rate=D("1")), MarginAssumptions(min_margin=D("NaN"))):
            result = r.evaluate_offer(offer(), assumptions=bad)
            self.assertEqual(result["decision"], "HOLD")
            self.assertIn("ASSUMPTIONS_INVALID", codes(result))
            self.assertIsNone(result["floor_price"])

    def test_free_shipping_is_a_valid_zero_cost(self):
        result = r.evaluate_offer(offer("9000", "0"))
        self.assertEqual(result["decision"], "READY")
        self.assertEqual(result["shipping_share_percent"], 0)
        self.assertIsNotNone(result["floor_price"])

    def test_rounding_is_half_up_to_the_won(self):
        self.assertEqual(r._won(D("2.5")), 3)
        self.assertEqual(r._won(D("3.5")), 4)
        self.assertEqual(r._won(D("2.4999")), 2)


class UnknownValueTests(unittest.TestCase):

    def test_missing_or_invalid_costs_hold_and_never_pass(self):
        for kwargs in (dict(cost=None), dict(ship=None), dict(cost="0"), dict(cost="-5"), dict(cost="NaN"), dict(cost="Infinity"), dict(ship="-1"), dict(ship="NaN")):
            result = r.evaluate_offer(offer(**kwargs))
            self.assertEqual(result["decision"], "HOLD", kwargs)
            self.assertIn("COST_UNKNOWN", codes(result), kwargs)
            self.assertIsNone(result["floor_price"], kwargs)
            self.assertIsNone(result["cost_multiple"], kwargs)
            self.assertIsNone(result["shipping_share_percent"], kwargs)

    def test_a_hidden_price_holds_even_when_numbers_are_given(self):
        result = r.evaluate_offer(offer(price_visible=False))
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn("PRICE_HIDDEN", codes(result, "HOLD"))

    def test_dropship_rules(self):
        self.assertEqual(r.evaluate_offer(offer(dropship_supported=False))["decision"], "EXCLUDE")
        self.assertIn("DROPSHIP_NO", codes(r.evaluate_offer(offer(dropship_supported=False)), "BLOCK"))
        unknown = r.evaluate_offer(offer(dropship_supported=None))
        self.assertEqual(unknown["decision"], "HOLD")
        self.assertIn("DROPSHIP_UNKNOWN", codes(unknown))
        self.assertEqual(r.evaluate_offer(offer(dropship_supported=True))["decision"], "READY")

    def test_minimum_order_quantity(self):
        for qty, expected in ((None, "READY"), (0, "READY"), (1, "READY"), (2, "EXCLUDE"), (50, "EXCLUDE")):
            self.assertEqual(r.evaluate_offer(offer(min_order_qty=qty))["decision"], expected, qty)
        self.assertEqual([x["params"] for x in r.evaluate_offer(offer(min_order_qty=5))["reasons"] if x["code"] == "MIN_ORDER_ABOVE_ONE"], [{"qty": 5}])

    def test_block_wins_over_hold_and_hold_wins_over_ready(self):
        both = r.evaluate_offer(offer(cost=None, dropship_supported=False))
        self.assertEqual(both["decision"], "EXCLUDE")
        self.assertIn("COST_UNKNOWN", codes(both))

    def test_info_reasons_never_change_the_decision(self):
        result = r.evaluate_offer(offer(api_available=False, domestic_stock=False))
        self.assertEqual(result["decision"], "READY")
        self.assertEqual(sorted(codes(result, "INFO")), ["NO_API", "NO_DOMESTIC_STOCK"])
        self.assertIn("API_UNKNOWN", codes(r.evaluate_offer(offer(api_available=None))))
        self.assertEqual(r.evaluate_offer(offer(api_available=None))["decision"], "READY")


class WarningBoundaryTests(unittest.TestCase):

    def test_shipping_share_warns_at_thirty_percent_and_above(self):
        self.assertIn("HIGH_SHIPPING_SHARE", codes(r.evaluate_offer(offer("7000", "3000")), "WARN"))      # 정확히 30%
        self.assertNotIn("HIGH_SHIPPING_SHARE", codes(r.evaluate_offer(offer("7001", "3000"))))            # 29.99%
        self.assertIn("HIGH_SHIPPING_SHARE", codes(r.evaluate_offer(offer("100", "3000"))))
        self.assertEqual(r.evaluate_offer(offer("7000", "3000"))["shipping_share_percent"], 30)

    def test_a_warning_does_not_block(self):
        self.assertEqual(r.evaluate_offer(offer("1000", "3000"))["decision"], "READY")

    def test_return_burden_warns_at_half_the_unit_cost_and_above(self):
        self.assertIn("RETURN_BURDEN_HIGH", codes(r.evaluate_offer(offer("12000", "3000", return_shipping_cost=D("6000"))), "WARN"))   # 정확히 50%
        self.assertNotIn("RETURN_BURDEN_HIGH", codes(r.evaluate_offer(offer("12001", "3000", return_shipping_cost=D("6000")))))
        self.assertEqual(r.evaluate_offer(offer("9400", "3000", return_shipping_cost=D("6000")))["return_burden_percent"], 64)

    def test_unknown_return_cost_is_info(self):
        for value in (None, D("-1")):
            result = r.evaluate_offer(offer(return_shipping_cost=value))
            self.assertIn("RETURN_COST_UNKNOWN", codes(result, "INFO"))
            self.assertIsNone(result["return_burden_percent"])
        self.assertIn("RETURN_COST_UNKNOWN", codes(r.evaluate_offer(offer(cost=None, return_shipping_cost=None))))

    def test_zero_return_shipping_is_known_and_free(self):
        result = r.evaluate_offer(offer(return_shipping_cost=D("0")))
        self.assertNotIn("RETURN_COST_UNKNOWN", codes(result))
        self.assertEqual(result["return_burden_percent"], 0)


class TargetPriceTests(unittest.TestCase):

    def test_a_target_below_the_floor_excludes_and_reports_the_floor(self):
        result = r.evaluate_offer(offer("15000"), target_price=D("19900"))
        self.assertEqual(result["decision"], "EXCLUDE")
        reason = [x for x in result["reasons"] if x["code"] == "TARGET_PRICE_BELOW_FLOOR"][0]
        self.assertEqual((reason["level"], reason["params"]), ("BLOCK", {"floor": 26012}))
        self.assertEqual(result["margin_at_target_percent"], -3.3)

    def test_the_boundary_at_the_floor_price(self):
        self.assertEqual(r.evaluate_offer(offer("7000"), target_price=D("14451"))["decision"], "READY")
        self.assertEqual(r.evaluate_offer(offer("7000"), target_price=D("14450"))["decision"], "EXCLUDE")
        self.assertEqual(r.evaluate_offer(offer("7000"), target_price=D("14450.87"))["decision"], "READY")

    def test_a_margin_exactly_at_the_minimum_passes(self):
        # 수수료 10%·반품 예비 0·기준 마진 20%에서 비용 70원 → 최저 판매가가 정확히 100원이라 마진이 딱 20%다(경계는 통과).
        exact = MarginAssumptions(fee_rate=D("0.1"), return_reserve_rate=D("0"), min_margin=D("0.2"))
        o = offer("70", "0")
        self.assertEqual(r.floor_price(o, exact), D("100"))
        self.assertEqual(r.margin_at(o, D("100"), exact), D("0.2"))
        self.assertEqual(r.evaluate_offer(o, target_price=D("100"), assumptions=exact)["decision"], "READY")
        self.assertEqual(r.evaluate_offer(o, target_price=D("99.99"), assumptions=exact)["decision"], "EXCLUDE")

    def test_no_target_means_no_margin_claim(self):
        result = r.evaluate_offer(offer())
        self.assertIsNone(result["margin_at_target_percent"])
        self.assertNotIn("TARGET_PRICE_BELOW_FLOOR", codes(result))

    def test_invalid_targets_do_not_pass_silently(self):
        for target in (D("0"), D("-100")):
            result = r.evaluate_offer(offer(), target_price=target)
            self.assertIsNone(result["margin_at_target_percent"])
            self.assertNotIn("TARGET_PRICE_BELOW_FLOOR", codes(result))   # 계산할 수 없는 값은 "미달"로도 "충분"으로도 단정하지 않는다

    def test_unknown_cost_with_a_target_does_not_invent_a_margin(self):
        result = r.evaluate_offer(offer(cost=None), target_price=D("29900"))
        self.assertIsNone(result["margin_at_target_percent"])
        self.assertEqual(result["decision"], "HOLD")


class RankingTests(unittest.TestCase):

    def offers(self):
        return [
            offer("7000", supplier_code="B", external_id="2", dropship_supported=False),     # EXCLUDE
            offer("9400", supplier_code="A", external_id="9"),                                # READY
            offer("12000", supplier_code="A", external_id="1"),                               # READY(마진 더 낮음)
            offer(None, supplier_code="C", external_id="3"),                                  # HOLD
            offer("7000", supplier_code="A", external_id="5"),                                # READY(마진 가장 높음)
            offer("9000", supplier_code="Z", external_id="7", price_visible=False),           # HOLD
        ]

    def test_ready_then_hold_then_exclude_with_margin_first(self):
        ranked = r.rank_offers(self.offers(), target_price=D("29900"))
        self.assertEqual([x["decision"] for x in ranked], ["READY", "READY", "READY", "HOLD", "HOLD", "EXCLUDE"])
        self.assertEqual([(x["supplier_code"], x["external_id"]) for x in ranked[:3]], [("A", "5"), ("A", "9"), ("A", "1")])
        self.assertEqual([x["rank"] for x in ranked], [1, 2, 3, 4, 5, 6])

    def test_without_a_target_the_lower_floor_price_comes_first(self):
        ranked = r.rank_offers(self.offers())
        self.assertEqual([(x["supplier_code"], x["external_id"]) for x in ranked[:3]], [("A", "5"), ("A", "9"), ("A", "1")])

    def test_unknown_floor_sorts_after_known_within_the_same_decision(self):
        ranked = r.rank_offers(self.offers())
        holds = [x for x in ranked if x["decision"] == "HOLD"]
        self.assertEqual([(x["supplier_code"], x["external_id"]) for x in holds], [("Z", "7"), ("C", "3")])
        self.assertIsNotNone(holds[0]["floor_price"])   # 가격이 숨겨졌어도 값이 있으면 최저 판매가는 계산된다
        self.assertIsNone(holds[1]["floor_price"])

    def test_the_order_does_not_depend_on_the_input_order(self):
        expected = [(x["supplier_code"], x["external_id"]) for x in r.rank_offers(self.offers(), D("29900"))]
        for seed in range(20):
            shuffled = self.offers()
            random.Random(seed).shuffle(shuffled)
            self.assertEqual([(x["supplier_code"], x["external_id"]) for x in r.rank_offers(shuffled, D("29900"))], expected, seed)

    def test_ties_break_by_supplier_then_number(self):
        ranked = r.rank_offers([offer(supplier_code="B", external_id="1"), offer(supplier_code="A", external_id="2"), offer(supplier_code="A", external_id="1")])
        self.assertEqual([(x["supplier_code"], x["external_id"]) for x in ranked], [("A", "1"), ("A", "2"), ("B", "1")])

    def test_the_limit_value_is_pinned(self):
        self.assertEqual(r.MAX_OFFERS, 50)

    def test_the_limit(self):
        many = [offer(external_id=str(i)) for i in range(r.MAX_OFFERS)]
        self.assertEqual(len(r.rank_offers(many)), r.MAX_OFFERS)
        with self.assertRaises(ValueError):
            r.rank_offers(many + [offer(external_id="x")])
        self.assertEqual(r.rank_offers([]), [])

    def test_results_are_json_serializable_and_inputs_are_untouched(self):
        offers = self.offers()
        before = [o.__dict__.copy() for o in offers]
        json.dumps(r.rank_offers(offers, D("29900")), ensure_ascii=False)
        self.assertEqual([o.__dict__ for o in offers], before)


class PurityTests(unittest.TestCase):

    def test_the_rules_do_not_know_supplier_names(self):
        code = SOURCE.split('"""', 2)[2]   # 모듈 설명 뒤의 코드만(설명에는 배경으로 이름이 나온다)
        for name in ("onch", "ownerclan", "domeme", "domeggook", "coupang", "naver", "1688", "alibaba", "http"):
            self.assertNotIn(name, code.lower(), name)

    def test_no_io_or_randomness(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "random", "datetime", "os"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (CalculationTests, UnknownValueTests, WarningBoundaryTests, TargetPriceTests, RankingTests, PurityTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
