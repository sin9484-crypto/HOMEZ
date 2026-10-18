"""
=========================================================
Homez OS

File : tests/test_marketing_outcomes.py

2026-10-18 — 마케팅 AI M4: 매출내역 줄을 옵션별로 집계하는 순수 함수(`ai_learning/marketing_outcomes.py`)를 고정한다. DB·네트워크·AI 없음.

  - 손으로 구한 값과 일치: 판매 수량·주문 수·판매액·실현 수수료율·반품률·합계.
  - 표본 크기 판정: 10건 미만 판단 불가 / 10~49건 참고용 / 50건 이상(학습 검토 최소 표본). 반품이 높다는 표시는 표본이 참고용 이상일 때만.
  - 이중 집계 방지(같은 줄은 한 번), 부호를 알 수 없는 환불은 절댓값으로 따로 모으고 서로 빼지 않는다, 읽을 수 없는 줄은 건너뛰고 센다.
  - 어떤 제안이 맞았는지 판정하지 않는다.
=========================================================
"""

import json
import re
import unittest
from pathlib import Path

from app.domains.ai_learning import marketing_outcomes as mo
from app.domains.ai_learning.constants import INITIAL_MIN_SAMPLE_SIZE
from app.domains.ai_learning.marketing_outcomes import summarize

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/ai_learning/marketing_outcomes.py").read_text(encoding="utf-8")


def row(order="1", item="9001", kind="SALE", qty=1, amount=12900.0, fee=None, settlement=None, date="2026-10-10", name="바디워시 3개입"):
    return {
        "order_id": order, "vendor_item_id": item, "sale_type": kind, "quantity": qty, "sale_amount": amount,
        "service_fee": round(amount * 0.108, 2) if fee is None else fee, "settlement_amount": round(amount * 0.892, 2) if settlement is None else settlement,
        "recognition_date": date, "name": name,
    }


def many(n, item="9001", kind="SALE", start=1):
    return [row(order=f"{item}-{kind}-{i}", item=item, kind=kind) for i in range(start, start + n)]


def entry(result, item="9001"):
    return next(e for e in result["items"] if e["vendor_item_id"] == item)


class HandComputedTests(unittest.TestCase):

    def rows(self):
        return [
            row("1", qty=2, amount=25800), row("2", qty=1, amount=12900), row("3", qty=3, amount=38700),     # 9001: 6개, 77,400원
            row("2", kind="REFUND", qty=1, amount=12900),                                                        # 9001: 반품 1개
            row("4", "9002", qty=1, amount=5000, name="샘플 상품"),
        ]

    def test_per_item_numbers(self):
        r = summarize(self.rows())
        a = entry(r)
        self.assertEqual((a["orders"], a["sale_units"], a["sale_amount"], a["avg_unit_price"]), (3, 6, "77400", "12900"))
        self.assertEqual((a["refund_units"], a["refund_orders"], a["refund_amount"], a["return_rate"]), (1, 1, "12900", "0.1667"))
        self.assertEqual((a["fee_ratio"], a["settlement_amount"]), ("0.1080", "69041"))   # 수수료 10.8%, 정산 합계 69,040.8 → 69,041
        b = entry(r, "9002")
        self.assertEqual((b["orders"], b["sale_units"], b["sale_amount"], b["return_rate"], b["name"]), (1, 1, "5000", "0.0000", "샘플 상품"))

    def test_totals(self):
        t = summarize(self.rows())["totals"]
        self.assertEqual((t["orders"], t["items"], t["sale_units"], t["sale_amount"], t["refund_units"]), (4, 2, 7, "82400", 1))
        self.assertEqual((t["return_rate"], t["fee_ratio"]), ("0.1429", "0.1080"))

    def test_items_are_sorted_by_units_then_refunds_and_the_period_is_read_from_the_rows(self):
        r = summarize(self.rows() + [row("5", "9003", qty=6, date="2026-10-17"), row("6", "9004", qty=6, date="2026-09-20")])
        self.assertEqual([e["vendor_item_id"] for e in r["items"]], ["9001", "9003", "9004", "9002"])
        self.assertEqual(r["period"], {"from": "2026-09-20", "to": "2026-10-17"})

    def test_amounts_are_strings_and_the_result_is_json_serializable(self):
        text = json.dumps(summarize(self.rows()))
        self.assertNotRegex(text, r"\d\.\d{6,}")
        r = summarize(self.rows())
        for key in ("sale_amount", "avg_unit_price", "settlement_amount", "refund_amount"):
            self.assertIsInstance(entry(r)[key], str)

    def test_empty_input_is_an_empty_report_not_an_error(self):
        r = summarize([])
        self.assertEqual((r["items"], r["period"], r["totals"]["orders"], r["totals"]["return_rate"], r["totals"]["sample"]), ([], {"from": None, "to": None}, 0, None, "INSUFFICIENT"))


class SampleSizeTests(unittest.TestCase):

    def test_levels_and_boundaries(self):
        self.assertEqual(mo.JUDGMENT_ORDERS, INITIAL_MIN_SAMPLE_SIZE)
        expect = {0: "INSUFFICIENT", 9: "INSUFFICIENT", 10: "REFERENCE", 49: "REFERENCE", 50: "ENOUGH", 500: "ENOUGH"}
        for n, level in expect.items():
            self.assertEqual(mo.sample_level(n), level, n)
        for n in (9, 10, 49, 50):
            self.assertEqual(entry(summarize(many(n)))["sample"], mo.sample_level(n), n)

    def test_the_sample_counts_distinct_sale_orders_not_units(self):
        r = summarize([row("1", qty=100, amount=1290000)])
        self.assertEqual((entry(r)["orders"], entry(r)["sale_units"], entry(r)["sample"]), (1, 100, "INSUFFICIENT"))

    def test_refund_only_orders_do_not_count_toward_the_sample(self):
        r = summarize(many(9) + many(5, kind="REFUND"))
        self.assertEqual((entry(r)["orders"], entry(r)["sample"]), (9, "INSUFFICIENT"))

    def test_high_return_needs_at_least_a_reference_sample(self):
        small = summarize(many(9) + many(9, kind="REFUND"))
        self.assertEqual((entry(small)["return_rate"], entry(small)["high_return"]), ("1.0000", False))
        ten = summarize(many(10) + many(1, kind="REFUND"))
        self.assertEqual((entry(ten)["return_rate"], entry(ten)["high_return"]), ("0.1000", True))      # 기준(10%)과 같으면 표시
        below = summarize(many(11) + many(1, kind="REFUND"))
        self.assertEqual((entry(below)["return_rate"], entry(below)["high_return"]), ("0.0909", False))

    def test_the_overall_sample_level_uses_all_distinct_sale_orders(self):
        r = summarize(many(30, "9001") + many(30, "9002"))
        self.assertEqual((r["totals"]["orders"], r["totals"]["sample"]), (60, "ENOUGH"))
        self.assertEqual({e["sample"] for e in r["items"]}, {"REFERENCE"})   # 옵션별로는 각각 30건 → 참고용


class IntegrityTests(unittest.TestCase):

    def test_the_same_line_is_counted_once_but_another_recognition_date_is_a_different_line(self):
        r = summarize([row("1"), row("1"), row("1", date="2026-10-11")])
        self.assertEqual((entry(r)["sale_units"], r["duplicates"]), (2, 1))

    def test_refund_signs_are_unknown_so_absolute_values_are_used_and_nothing_is_subtracted(self):
        positive = summarize([row("1"), row("1", kind="REFUND", qty=1, amount=12900)])
        negative = summarize([row("1"), row("1", kind="REFUND", qty=-1, amount=-12900)])
        for r in (positive, negative):
            a = entry(r)
            self.assertEqual((a["refund_units"], a["refund_amount"], a["sale_units"], a["sale_amount"]), (1, "12900", 1, "12900"))
        self.assertNotIn("net", json.dumps(positive))   # 순매출을 만들지 않는다

    def test_a_refund_without_a_sale_is_flagged_and_has_no_rate(self):
        a = entry(summarize([row("9", kind="REFUND", qty=2)]))
        self.assertEqual((a["refund_without_sale"], a["return_rate"], a["sale_units"], a["avg_unit_price"], a["fee_ratio"]), (True, None, 0, None, None))

    def test_unreadable_lines_are_skipped_and_counted(self):
        bad = [
            {}, {"order_id": "1"}, row("2", kind="EXCHANGE"), row("3", amount="abc", fee=0, settlement=0), row("4", amount=float("nan"), fee=0, settlement=0),
            row("5", amount=float("inf"), fee=0, settlement=0),
            {**row("6"), "quantity": "x"}, {**row("7"), "quantity": None}, {**row("8"), "vendor_item_id": None},
        ]
        r = summarize(bad + [row("10")])
        self.assertEqual((r["skipped"], r["totals"]["orders"]), (len(bad), 1))
        self.assertEqual(entry(r)["sale_units"], 1)

    def test_a_missing_amount_counts_as_zero_not_as_a_guess(self):
        r = summarize([{**row("1"), "sale_amount": None, "service_fee": None, "settlement_amount": None}])
        a = entry(r)
        self.assertEqual((a["sale_amount"], a["avg_unit_price"], a["fee_ratio"]), ("0", "0", None))

    def test_the_documented_limits_are_fixed_numbers(self):
        # 시험이 상수를 따라 바뀌어 상한 변경을 놓치지 않도록 값을 직접 고정한다
        self.assertEqual((mo.MAX_ITEMS_SHOWN, mo.REFERENCE_ORDERS, mo.JUDGMENT_ORDERS, str(mo.HIGH_RETURN_RATE)), (50, 10, 50, "0.10"))

    def test_only_the_top_items_are_shown_and_the_rest_are_counted(self):
        r = summarize([row(str(n), item=str(1000 + n)) for n in range(mo.MAX_ITEMS_SHOWN + 7)])
        self.assertEqual((len(r["items"]), r["omitted"], r["totals"]["items"]), (mo.MAX_ITEMS_SHOWN, 7, mo.MAX_ITEMS_SHOWN + 7))

    def test_the_first_name_is_kept_and_clipped(self):
        r = summarize([row("1", name=None), row("2", name="가" * 100), row("3", name="나")])
        self.assertEqual(entry(r)["name"], "가" * 60)


class SourceTests(unittest.TestCase):

    def test_the_module_is_pure_and_judges_nothing(self):
        for forbidden in (r"sqlalchemy", r"\brequests\b", r"urllib", r"socket", r"subprocess", r"\bopen\(", r"call_llm", r"\bfloat\(", r"\bprint\("):
            self.assertIsNone(re.search(forbidden, SOURCE), forbidden)
        for word in ("적중했", "효과가 있었", "성공했", "개선됐"):
            self.assertNotIn(word, " ".join(mo.NOTES))

    def test_the_fixed_notes_state_the_limits(self):
        text = " ".join(summarize([])["notes"])
        for phrase in ("배송 완료 7일", "노출·클릭·전환 데이터는 쿠팡 공식 API에 없어서", "판정하지 않아요"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
