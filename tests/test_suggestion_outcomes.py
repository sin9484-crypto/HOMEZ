"""
=========================================================
Homez OS

File : tests/test_suggestion_outcomes.py

2026-10-23 — 마케팅 AI M4-full: 채택한 제안과 쿠팡 옵션 성과(제안일 전·후)를 연결하는 순수 함수(`suggestion_outcomes.evaluate`)와 Migration 초안을 고정한다. DB·네트워크·AI 0.

  - 판정하지 않는다: 항상 `causal: False`, "적중/실패" 표현 없음. 표본이 작으면 INSUFFICIENT(판단 불가). 채택하지 않았거나 연결·날짜·기간이 없으면 평가하지 않고 이유를 단다.
  - 전·후 기간은 읽은 매출내역 기간을 제안일에서 가른 것이며 제안일 당일은 '후'다. 같은 옵션 제안이 여럿이면 겹침 경고.
  - (2026-10-23 같은 날 후속) Migration 초안은 실제 Migration(`migrations/20261023_00_…`)과 Model로 옮겼고 그 검증은 `test_suggestion_record_service.py`에 있다.
=========================================================
"""

import json
import unittest

from app.domains.ai_learning import suggestion_outcomes as so



def sale(order, day, vendor="V1", qty=1, kind="SALE"):
    return {"vendor_item_id": vendor, "order_id": order, "sale_type": kind, "quantity": qty, "sale_amount": "10000", "service_fee": "1000",
            "settlement_amount": "9000", "recognition_date": day}


def month_rows(vendor="V1", before_orders=12, after_orders=12, refunds_after=0):
    rows = [sale(f"b{i}", f"2026-09-{(i % 14) + 1:02d}", vendor) for i in range(before_orders)]          # 9/1~9/14
    rows += [sale(f"a{i}", f"2026-09-{(i % 14) + 15:02d}", vendor) for i in range(after_orders)]          # 9/15~9/28
    rows += [sale(f"a{i}", "2026-09-20", vendor, kind="REFUND") for i in range(refunds_after)]
    return rows


def record(**overrides):
    base = {"id": 1, "kind": "PRODUCT_NAME", "vendor_item_id": "V1", "suggested_at": "2026-09-15", "adopted": True, "suggested_value": "새 상품명"}
    return {**base, **overrides}


class ObservedTests(unittest.TestCase):

    def test_adopted_suggestion_shows_before_and_after_without_judging(self):
        result = so.evaluate([record()], month_rows(before_orders=12, after_orders=24, refunds_after=3))
        entry = result["results"][0]
        self.assertEqual((entry["status"], entry["reason"], entry["causal"]), ("OBSERVED", None, False))
        self.assertEqual((entry["before"]["days"], entry["after"]["days"]), (14, 14))      # 9/1~9/14, 9/15~9/28
        self.assertEqual((entry["before"]["orders"], entry["after"]["orders"]), (12, 24))
        self.assertEqual((entry["before"]["sample"], entry["after"]["sample"]), ("REFERENCE", "REFERENCE"))
        self.assertEqual((entry["before"]["units_per_day"], entry["after"]["units_per_day"]), ("0.86", "1.71"))
        self.assertEqual((entry["after"]["refund_units"], entry["after"]["return_rate"]), (3, "0.1250"))
        self.assertEqual(entry["before"]["return_rate"], "0.0000")      # 이전 기간에는 판매는 있고 환불은 없다
        self.assertEqual(result["counts"], {"OBSERVED": 1, "INSUFFICIENT": 0, "NOT_EVALUATED": 0})
        text = json.dumps(result, ensure_ascii=False)
        for word in ("적중", "성공", "효과가 있", "개선됐"):
            self.assertNotIn(word, text)
        self.assertEqual(result["period"], {"from": "2026-09-01", "to": "2026-09-28"})

    def test_the_suggestion_day_belongs_to_the_after_side(self):
        rows = [sale("x1", "2026-09-14"), sale("x2", "2026-09-15"), sale("x3", "2026-09-01"), sale("x4", "2026-09-20")]
        entry = so.evaluate([record()], rows)["results"][0]
        self.assertEqual((entry["before"]["sale_units"], entry["after"]["sale_units"]), (2, 2))

    def test_only_the_linked_option_is_counted(self):
        rows = month_rows("V1", 12, 12) + month_rows("V2", 40, 40)
        entry = so.evaluate([record()], rows)["results"][0]
        self.assertEqual((entry["before"]["orders"], entry["after"]["orders"]), (12, 12))

    def test_a_small_sample_on_either_side_is_insufficient_not_a_comparison(self):
        for before, after in ((3, 30), (30, 3), (0, 0)):
            entry = so.evaluate([record()], month_rows(before_orders=before, after_orders=after) + [sale("pad1", "2026-09-01", "V9"), sale("pad2", "2026-09-28", "V9")])["results"][0]
            self.assertEqual((entry["status"], entry["reason"], entry["causal"]), ("INSUFFICIENT", "SAMPLE_TOO_SMALL", False), (before, after))
        # 경계: 정확히 10건이면 참고 표본이 된다
        entry = so.evaluate([record()], month_rows(before_orders=10, after_orders=10))["results"][0]
        self.assertEqual(entry["status"], "OBSERVED")
        entry = so.evaluate([record()], month_rows(before_orders=9, after_orders=10) + [sale("pad", "2026-09-01", "V9")])["results"][0]
        self.assertEqual(entry["status"], "INSUFFICIENT")


class NotEvaluatedTests(unittest.TestCase):

    def reason(self, rec, rows=None):
        entry = so.evaluate([rec], month_rows() if rows is None else rows)["results"][0]
        self.assertEqual(entry["status"], "NOT_EVALUATED")
        return entry["reason"]

    def test_each_reason_is_named(self):
        self.assertEqual(self.reason(record(kind="DISCOUNT")), "UNKNOWN_KIND")
        for adopted in (False, None, "true", 1, 0):
            self.assertEqual(self.reason(record(adopted=adopted)), "NOT_ADOPTED", adopted)       # 참(True)만 채택이다
        for vendor in (None, "", "  "):
            self.assertEqual(self.reason(record(vendor_item_id=vendor)), "NOT_LINKED", vendor)
        for at in (None, "", "어제", "2026-13-40", 20260915):
            self.assertEqual(self.reason(record(suggested_at=at)), "SUGGESTED_AT_UNREADABLE", at)
        self.assertEqual(self.reason(record(), rows=[]), "NO_REVENUE_ROWS")
        self.assertEqual(self.reason(record(), rows=[{"vendor_item_id": "V1", "order_id": "o", "sale_type": "SALE", "quantity": 1, "recognition_date": "날짜아님"}]), "NO_REVENUE_ROWS")

    def test_a_suggestion_at_or_outside_the_data_period_has_no_before_or_after(self):
        for at in ("2026-09-01", "2026-08-20", "2026-09-27", "2026-12-01"):       # 기본 자료의 기간은 9/1~9/26
            entry = so.evaluate([record(suggested_at=at)], month_rows())["results"][0]
            self.assertEqual((entry["status"], entry["reason"]), ("NOT_EVALUATED", "NO_BEFORE_OR_AFTER_PERIOD"), at)
            self.assertEqual(entry["window"], {"from": "2026-09-01", "to": "2026-09-26"})

    def test_the_last_data_day_still_has_an_after_period_of_one_day(self):
        entry = so.evaluate([record(suggested_at="2026-09-26")], month_rows())["results"][0]
        self.assertEqual((entry["status"], entry["after"]["days"]), ("INSUFFICIENT", 1))

    def test_datetime_values_are_accepted(self):
        from datetime import date, datetime

        for at in (datetime(2026, 9, 15, 13, 30), date(2026, 9, 15), "2026-09-15T13:30:00"):
            self.assertEqual(so.evaluate([record(suggested_at=at)], month_rows())["results"][0]["status"], "OBSERVED", at)


class OverlapAndLimitTests(unittest.TestCase):

    def test_two_suggestions_for_the_same_option_warn_about_overlap(self):
        results = so.evaluate([record(id=1, suggested_at="2026-09-10"), record(id=2, kind="PRICE", suggested_at="2026-09-20")], month_rows())["results"]
        self.assertEqual([e["warnings"] for e in results], [["OVERLAPPING_SUGGESTIONS"]] * 2)
        single = so.evaluate([record(id=1), record(id=3, vendor_item_id="V2")], month_rows())["results"]
        self.assertTrue(all("warnings" not in e for e in single))

    def test_not_evaluated_records_do_not_trigger_the_overlap_warning(self):
        results = so.evaluate([record(id=1), record(id=2, adopted=False)], month_rows())["results"]
        self.assertTrue(all("warnings" not in e for e in results))

    def test_records_beyond_the_limit_are_not_evaluated_and_counted(self):
        result = so.evaluate([record(id=i) for i in range(so.MAX_RECORDS + 5)], month_rows())
        self.assertEqual((len(result["results"]), result["omitted_records"]), (so.MAX_RECORDS, 5))

    def test_the_record_limit_is_exactly_two_hundred(self):
        self.assertEqual(so.MAX_RECORDS, 200)
        exact = so.evaluate([record(id=i) for i in range(200)], month_rows())
        self.assertEqual((len(exact["results"]), exact["omitted_records"]), (200, 0))
        over = so.evaluate([record(id=i) for i in range(201)], month_rows())
        self.assertEqual((len(over["results"]), over["omitted_records"]), (200, 1))

    def test_a_long_suggested_value_is_clipped_in_the_output(self):
        entry = so.evaluate([record(suggested_value="가" * 500)], month_rows())["results"][0]
        self.assertEqual(len(entry["suggested_value"]), 120)
        self.assertIsNone(so.evaluate([record(suggested_value="")], month_rows())["results"][0]["suggested_value"])

    def test_empty_inputs_are_safe(self):
        for records, rows in (([], []), (None, None), ([], month_rows())):
            result = so.evaluate(records, rows)
            self.assertEqual(result["results"], [])
        self.assertEqual(so.evaluate([], [])["period"], {"from": None, "to": None})

    def test_it_does_not_change_its_inputs(self):
        records, rows = [record()], month_rows()
        copy_records, copy_rows = json.loads(json.dumps(records)), json.loads(json.dumps(rows))
        so.evaluate(records, rows)
        self.assertEqual((records, rows), (copy_records, copy_rows))

    def test_output_is_json_friendly_and_carries_the_notes(self):
        result = so.evaluate([record()], month_rows())
        json.dumps(result, ensure_ascii=False)
        self.assertTrue(any("판정하지 않아요" in note for note in result["notes"]))


if __name__ == "__main__":
    unittest.main()
