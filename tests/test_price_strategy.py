"""
=========================================================
Homez OS

File : tests/test_price_strategy.py

2026-10-18 — 마케팅 AI M3: 가격 전략 제안(`pricing/price_strategy.py`)과 요청 모델, 계산 연결을 고정한다. AI·바깥 호출·저장 없음.

  - 순수 규칙: 마진 하한가·시장 구간(M2)·권장 구간. **어떤 제안·가격선도 마진 하한가 아래로 내려가지 않는다**(격자 시험으로 확인).
  - 시장 위치별 권장: 경쟁 가격 없음 / 1곳 / 하한가 ≤ 중앙값(경쟁력 있음) / 중앙값 초과 / 최고가 초과(가격 경쟁력 없음).
  - 계산 연결: 손으로 구한 값(원가 5,720 + 배송 3,000, 수수료 10.8%, 반품 3%, 기준 18% → 하한가 12,790원)과 일치. 모르는 값·확인 안 한 위험·AI 금액 미확인이면 가격을 만들지 않는다.
  - 가격은 바꾸지 않는다: 소스에 쓰기·가격 변경 호출이 없다.
=========================================================
"""

import re
import unittest
from decimal import Decimal
from pathlib import Path

from app.domains.company.model import Company  # noqa: F401
from app.domains.pricing import price_strategy as ps
from app.domains.pricing.price_strategy import Market
from app.domains.pricing.price_strategy import plan
from app.domains.pricing.price_strategy import sensitivity
from app.domains.pricing.price_strategy import strategy_report
from app.domains.product_import import schema
from app.domains.product_import.channel_fee_source import UserConfirmedFeeStore
from app.domains.product_import.url_analysis_service import CalcInput
from tests.test_url_analysis_service import DbBase

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/pricing/price_strategy.py").read_text(encoding="utf-8")
D = Decimal
FLOOR = D("12790")


def fake_margin(price):
    """원가 8,720(5,720+3,000), 수수료+반품 13.8%의 단순 모델 — 손으로 계산할 수 있다."""

    margin = price * D("0.862") - D("8720")
    return {"margin_amount": margin, "margin_rate": margin / price, "meets_min": margin >= price * D("0.18")}


def market(n, mn, med=None, mx=None, basis="total"):
    return Market(n=n, basis=basis, min=D(mn), median=None if med is None else D(med), max=None if mx is None else D(mx))


class RoundingTests(unittest.TestCase):

    def test_up_and_down_to_ten_won(self):
        self.assertEqual((ps.up10(D("12781")), ps.up10(D("12780")), ps.down10(D("12789")), ps.down10(D("12790"))), (D(12790), D(12780), D(12780), D(12790)))

    def test_the_fake_model_puts_the_floor_exactly_at_the_target(self):
        self.assertTrue(fake_margin(FLOOR)["meets_min"])
        self.assertFalse(fake_margin(FLOOR - 10)["meets_min"])


class PositionTests(unittest.TestCase):

    def run_plan(self, mk=None, **kw):
        return plan(FLOOR, mk, fake_margin, **kw)

    def test_no_market_uses_only_the_floor_and_says_it_cannot_compare(self):
        r = self.run_plan(None)
        self.assertEqual(r["position"], "NO_MARKET")
        self.assertEqual(r["recommended"], {"low": "12790", "high": None, "suggested": "12790"})
        self.assertIsNone(r["market"])
        self.assertTrue(any("비교하지 못했다" in w for w in r["warnings"]))

    def test_one_reference_makes_no_market_range(self):
        r = self.run_plan(market(1, "13000"))
        self.assertEqual(r["position"], "ONE_REFERENCE")
        self.assertEqual(r["recommended"], {"low": "12790", "high": "13000", "suggested": "12790"})
        self.assertTrue(any("1곳뿐" in w for w in r["warnings"]))

    def test_one_reference_below_the_floor_never_pulls_the_price_down(self):
        r = self.run_plan(market(1, "9000"))
        self.assertEqual(r["recommended"], {"low": "12790", "high": "12790", "suggested": "12790"})

    def test_floor_below_the_median_recommends_from_the_floor_or_market_min_up_to_the_median(self):
        r = self.run_plan(market(3, "11000", "15900", "15900"))
        self.assertEqual((r["position"], r["recommended"]), ("COMPETITIVE", {"low": "12790", "high": "15900", "suggested": "12790"}))
        higher = self.run_plan(market(3, "13500", "15900", "15900"))
        self.assertEqual(higher["recommended"], {"low": "13500", "high": "15900", "suggested": "13500"})  # 시장 최저 아래로는 권하지 않는다

    def test_floor_above_the_median_but_within_the_market_warns_and_starts_at_the_floor(self):
        r = self.run_plan(market(3, "9000", "11000", "14000"))
        self.assertEqual((r["position"], r["recommended"]), ("ABOVE_MEDIAN", {"low": "12790", "high": "14000", "suggested": "12790"}))
        self.assertTrue(any("중앙값보다 높다" in w for w in r["warnings"]))

    def test_floor_above_the_whole_market_says_there_is_no_price_competitiveness(self):
        r = self.run_plan(market(3, "9000", "10000", "12000"))
        self.assertEqual((r["position"], r["recommended"]), ("ABOVE_MARKET", {"low": "12790", "high": None, "suggested": "12790"}))
        self.assertTrue(any("가격 경쟁력이 없다" in w for w in r["warnings"]))

    def test_boundaries_are_inclusive_at_the_median_and_the_max(self):
        at_median = self.run_plan(market(3, "9000", "12790", "14000"))
        self.assertEqual(at_median["position"], "COMPETITIVE")      # 하한가 = 중앙값이면 아직 경쟁력이 있다
        at_max = self.run_plan(market(3, "9000", "10000", "12790"))
        self.assertEqual(at_max["position"], "ABOVE_MEDIAN")        # 하한가 = 최고가이면 아직 시장 안이다
        beyond = self.run_plan(market(3, "9000", "10000", "12780"))
        self.assertEqual(beyond["position"], "ABOVE_MARKET")

    def test_a_display_basis_market_warns_that_shipping_is_not_compared(self):
        r = self.run_plan(market(2, "11000", "12000", "13000", basis="display"))
        self.assertTrue(any("배송비를 뺀 표시가" in w for w in r["warnings"]))

    def test_the_floor_is_included_and_meets_the_minimum(self):
        r = self.run_plan(market(3, "11000", "15900", "15900"))
        floor_row = next(p for p in r["points"] if p["key"] == "floor")
        self.assertEqual((floor_row["price"], floor_row["meets_min"]), ("12790", True))
        self.assertEqual(floor_row["margin_amount"], "2304.98")  # 12790×0.862 − 8720

    def test_nothing_is_ever_below_the_floor_across_a_grid_of_markets(self):
        mins = [D(v) for v in (1000, 9000, 12000, 12790, 13000, 20000)]
        count = 0
        for mn in mins:
            for med_gap in (0, 500, 3000, 20000):
                for max_gap in (0, 700, 9000):
                    med, mx = mn + med_gap, mn + med_gap + max_gap
                    for mk in (market(3, mn, med, mx), market(2, mn, med, mx, basis="display"), market(1, mn), None):
                        for entered in (None, D("5000"), D("12790"), D("40000")):
                            r = plan(FLOOR, mk, fake_margin, entered)
                            count += 1
                            rec = r["recommended"]
                            self.assertGreaterEqual(D(rec["low"]), FLOOR, (mn, med, mx))
                            self.assertGreaterEqual(D(rec["suggested"]), FLOOR, (mn, med, mx))
                            if rec["high"] is not None:
                                self.assertGreaterEqual(D(rec["high"]), D(rec["low"]), (mn, med, mx))
                            prices = [D(p["price"]) for p in r["points"] if p["key"] != "entered"]
                            self.assertEqual(prices, sorted(set(prices)))          # 오름차순·중복 없음
                            self.assertTrue(all(price >= FLOOR for price in prices), (mn, med, mx))
                            self.assertTrue(all(p["meets_min"] for p in r["points"] if p["key"] != "entered"), (mn, med, mx))
        self.assertGreater(count, 500)


class EnteredPriceTests(unittest.TestCase):

    def entered(self, price, mk=None):
        return plan(FLOOR, mk, fake_margin, D(price))

    def test_below_the_floor_is_flagged_and_does_not_meet_the_minimum(self):
        r = self.entered("12000")
        self.assertEqual((r["entered"]["vs_floor"], r["entered"]["meets_min"]), ("BELOW_FLOOR", False))
        self.assertTrue(any("마진 하한가보다 낮다" in w for w in r["warnings"]))

    def test_the_floor_itself_and_higher_prices_are_fine(self):
        self.assertEqual(self.entered("12790")["entered"]["vs_floor"], "OK")
        self.assertTrue(self.entered("15000")["entered"]["meets_min"])

    def test_position_against_the_market(self):
        mk = market(3, "11000", "15900", "18000")
        for price, expected in (("10000", "BELOW_MARKET_MIN"), ("13000", "WITHIN"), ("15900", "WITHIN"), ("16000", "ABOVE_MEDIAN"), ("19000", "ABOVE_MARKET_MAX")):
            self.assertEqual(self.entered(price, mk)["entered"]["vs_market"], expected, price)
        self.assertEqual(self.entered("13000")["entered"]["vs_market"], "NO_MARKET")
        self.assertEqual(self.entered("12000", market(1, "11000"))["entered"]["vs_market"], "WITHIN")

    def test_no_entered_price_means_no_entered_block(self):
        self.assertNotIn("entered", plan(FLOOR, None, fake_margin))


class SensitivityTests(unittest.TestCase):

    def test_four_cases_that_move_the_right_way(self):
        def margin_with(price, fee_delta, reserve_delta):
            rate = D("0.862") - fee_delta - reserve_delta
            margin = price * rate - D("8720")
            return {"margin_rate": margin / price, "meets_min": margin >= price * D("0.18")}

        rows = sensitivity(D("13000"), margin_with)
        by = {r["label"]: r for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertLess(D(by["수수료율 +2%p"]["margin_rate"]), D(by["수수료율 -2%p"]["margin_rate"]))
        self.assertLess(D(by["반품 예비율 +2%p"]["margin_rate"]), D(by["반품 예비율 -2%p"]["margin_rate"]))
        self.assertFalse(by["수수료율 +2%p"]["meets_min"])
        self.assertTrue(by["수수료율 -2%p"]["meets_min"])


class RequestModelTests(unittest.TestCase):

    def test_market_input_rules(self):
        schema.MarketInput(n=1, basis="total", min=D("1000"))
        schema.MarketInput(n=3, basis="display", min=D("1000"), median=D("1500"), max=D("2000"))
        bad = [
            dict(n=0, basis="total", min=D(1)), dict(n=11, basis="total", min=D(1), median=D(1), max=D(1)), dict(n=2, basis="total", min=D(1)),
            dict(n=2, basis="total", min=D(5), median=D(4), max=D(9)), dict(n=2, basis="total", min=D(1), median=D(9), max=D(5)),
            dict(n=1, basis="total", min=D(1), median=D(1)), dict(n=2, basis="other", min=D(1), median=D(1), max=D(1)), dict(n=2, basis="total", min=D(-1), median=D(1), max=D(1)),
            dict(n=2, basis="total", min=D(1), median=D(1), max=D(1), extra=1),
        ]
        for kwargs in bad:
            with self.assertRaises(Exception, msg=str(kwargs)):
                schema.MarketInput(**kwargs)

    def test_strategy_request_extends_the_calculation_request_and_stays_strict(self):
        request = schema.StrategyRequest(supply_price="5720", vat_state="INCLUDED", shipping_cost="3000", sale_price="13000",
                                         market=dict(n=2, basis="total", min="11000", median="12000", max="13000"))
        self.assertEqual(request.market.n, 2)
        self.assertEqual(request.sale_price, D("13000"))
        with self.assertRaises(Exception):
            schema.StrategyRequest(market=None, unknown_field=1)
        self.assertIsNone(schema.StrategyRequest().market)


class StrategyWiringTests(DbBase):

    def setUp(self):
        super().setUp()
        UserConfirmedFeeStore(self.db).save(self.user_id, self.company_id, channel="COUPANG", category_code="*", rate="0.108")

    def inp(self, **kw):
        base = dict(supply_price=D("5720"), vat_state="INCLUDED", shipping_cost=D("3000"))
        base.update(kw)
        return CalcInput(**base)

    def report(self, mk=None, **kw):
        return strategy_report(self.db, self.company_id, self.user_id, self.inp(**kw), mk)

    def test_hand_computed_floor_and_margins(self):
        r = self.report(market(3, "13500", "15900", "15900"))
        self.assertEqual((r["status"], r["floor_price"], r["min_margin_rate"]), ("OK", "12790", "0.18"))
        self.assertEqual(r["position"], "COMPETITIVE")
        self.assertEqual(r["recommended"], {"low": "13500", "high": "15900", "suggested": "13500"})
        rows = {p["price"]: p for p in r["points"]}
        self.assertEqual((rows["12790"]["margin_amount"], rows["12790"]["margin_rate"]), ("2304.98", "0.1802"))
        # 13,500 − 수수료 10.8%(1,458) − 반품 3%(405) − 원가 8,720 = 2,917
        self.assertEqual((rows["13500"]["margin_amount"], rows["13500"]["margin_rate"]), ("2917.00", "0.2161"))
        self.assertEqual(rows["13500"]["labels"], ["시장 최저가", "제안가"])  # 같은 가격은 한 줄에 이름만 더한다
        self.assertTrue(all(p["meets_min"] for p in r["points"]))
        self.assertEqual(len(r["sensitivity"]), 4)
        self.assertTrue(r["notes"])

    def test_without_a_market_only_the_floor_is_proposed(self):
        r = self.report(None)
        self.assertEqual((r["position"], r["recommended"]["suggested"]), ("NO_MARKET", "12790"))

    def test_the_company_minimum_rate_raises_the_floor(self):
        from app.domains.channel_policy.model import CompanyChannelPolicySettings

        self.db.add(CompanyChannelPolicySettings(company_id=self.company_id, min_target_margin_rate=D("0.25"), updated_by=1))
        self.db.commit()
        r = self.report(None)
        self.assertEqual(r["min_margin_rate"], "0.25")
        self.assertEqual(r["floor_price"], "14250")

    def test_unknown_values_make_no_price(self):
        for kw, code in (({"supply_price": None}, "SUPPLY_PRICE"), ({"vat_state": None}, "VAT_STATE"), ({"shipping_cost": None}, "SHIPPING_COST")):
            r = self.report(market(3, "13500", "15900", "15900"), **kw)
            self.assertEqual(r["status"], "HOLD", code)
            self.assertIn(code, [m["code"] for m in r["missing"]])
            self.assertNotIn("floor_price", r)
            self.assertNotIn("recommended", r)

    def test_an_unknown_fee_makes_no_price(self):
        self.db.execute(__import__("sqlalchemy").text("DELETE FROM user_settings"))
        self.db.commit()
        r = self.report(None)
        self.assertEqual(r["status"], "HOLD")
        self.assertIn("CHANNEL_FEE", [m["code"] for m in r["missing"]])
        self.assertNotIn("points", r)

    def test_ai_read_money_must_be_confirmed_first(self):
        r = self.report(None, ai_sourced=["supply_price"])
        self.assertEqual(r["status"], "HOLD")
        self.assertIn("AI_CONFIRM", [m["code"] for m in r["missing"]])
        self.assertEqual(self.report(None, ai_sourced=["supply_price"], ai_values_confirmed=True)["status"], "OK")

    def test_risks_hold_or_block_the_price(self):
        held = self.report(None, risk_codes=["KC_CERT"])
        self.assertEqual(held["status"], "HOLD")
        self.assertNotIn("recommended", held)
        self.assertEqual(self.report(None, risk_codes=["KC_CERT"], acknowledged_risks=["KC_CERT"])["status"], "OK")
        blocked = self.report(None, risk_codes=["PROHIBITED"], acknowledged_risks=["PROHIBITED"])
        self.assertEqual(blocked["status"], "NOT_RECOMMENDED")
        self.assertNotIn("recommended", blocked)

    def test_an_impossible_fee_structure_is_not_recommended(self):
        UserConfirmedFeeStore(self.db).save(self.user_id, self.company_id, channel="COUPANG", category_code="*", rate="0.5")
        r = self.report(None, return_reserve_rate=D("0.5"))
        self.assertEqual(r["status"], "NOT_RECOMMENDED")
        self.assertNotIn("recommended", r)

    def test_a_low_entered_price_does_not_make_the_item_not_recommended(self):
        r = self.report(market(3, "13500", "15900", "15900"), sale_price=D("9000"))
        self.assertEqual(r["status"], "OK")  # 품목 판정과 입력 판매가 평가를 따로 한다
        self.assertEqual((r["entered"]["vs_floor"], r["entered"]["meets_min"]), ("BELOW_FLOOR", False))
        self.assertEqual(r["entered"]["vs_market"], "BELOW_MARKET_MIN")
        self.assertGreaterEqual(D(r["recommended"]["suggested"]), D(r["floor_price"]))

    def test_vat_excluded_supply_raises_the_floor(self):
        self.assertEqual(self.report(None, vat_state="EXCLUDED")["floor_price"], "13630")  # (6,292+3,000)/0.682

    def test_it_reads_only_the_database_and_changes_nothing(self):
        tables = ("user_settings", "audit_logs", "product_candidates", "listing_wizards")
        count = lambda: [self.db.execute(__import__("sqlalchemy").text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]  # noqa: E731
        before = count()
        self.report(market(3, "13500", "15900", "15900"), sale_price=D("14000"))
        self.assertEqual(before, count())


class SourceTests(unittest.TestCase):

    def test_the_module_has_no_ai_network_or_price_change_calls(self):
        for forbidden in (r"call_llm", r"\brequests\b", r"urllib", r"socket", r"subprocess", r"\bopen\(", r"\.commit\(", r"\bdb\.add\(", r"write_audit_log",
                          r"propose_price_change", r"update_price", r"\bprint\("):
            self.assertIsNone(re.search(forbidden, SOURCE), forbidden)

    def test_the_fixed_notes_say_it_does_not_change_prices_and_never_goes_below_the_floor(self):
        text = " ".join(ps.NOTES)
        for phrase in ("실제 판매 실적이 아니에요", "가격을 바꾸지 않아요", "마진 하한가 아래의 가격은 제안하지 않아요"):
            self.assertIn(phrase, text)

    def test_floats_are_never_used_for_money(self):
        self.assertIsNone(re.search(r"\bfloat\(", SOURCE))


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for cls in (RoundingTests, PositionTests, EnteredPriceTests, SensitivityTests, RequestModelTests, StrategyWiringTests, SourceTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
