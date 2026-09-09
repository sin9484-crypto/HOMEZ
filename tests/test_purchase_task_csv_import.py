"""
=========================================================
Homez OS

File : tests/test_purchase_task_csv_import.py

Gate PT-1(2026-08-22) 검증 — CSV 가져오기 파싱(순수 함수, DB 없음).
=========================================================
"""

import unittest

from app.domains.purchase_task.csv_import import decode_csv_bytes
from app.domains.purchase_task.csv_import import parse_csv_rows
from app.domains.purchase_task.csv_import import preview_csv


class CsvImportTestCase(unittest.TestCase):

    def test_preview_reports_missing_columns(self):

        raw = "a,b\n1,2\n".encode("utf-8")
        preview = preview_csv(raw)
        self.assertFalse(preview.column_mapping_ok)
        self.assertIn("purchase_task_id", preview.missing_columns)

    def test_valid_rows_parse_successfully(self):

        raw = (
            "purchase_task_id,shopping_mall_code,external_order_number,"
            "actual_amount,actual_shipping_fee\n"
            "1,NAVER_SHOPPING,N-1,13000,3000\n"
            "2,ELEVENST,E-1,15000,\n"
        ).encode("utf-8")
        results = parse_csv_rows(raw)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results))
        self.assertEqual(results[0].data["actual_amount"], 13000.0)
        self.assertIsNone(results[1].data["actual_shipping_fee"])

    def test_row_level_partial_failure_is_reported(self):

        raw = (
            "purchase_task_id,shopping_mall_code,external_order_number,"
            "actual_amount\n"
            "1,NAVER_SHOPPING,N-1,13000\n"
            ",ELEVENST,E-1,15000\n"
            "3,GMARKET,G-1,notanumber\n"
        ).encode("utf-8")
        results = parse_csv_rows(raw)
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertFalse(results[2].success)

    def test_duplicate_rows_in_same_file_rejected(self):

        raw = (
            "purchase_task_id,shopping_mall_code,external_order_number,"
            "actual_amount\n"
            "1,NAVER_SHOPPING,N-1,13000\n"
            "2,NAVER_SHOPPING,N-1,13000\n"
        ).encode("utf-8")
        results = parse_csv_rows(raw)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertIn("중복", results[1].error)

    def test_formula_injection_neutralized(self):

        raw = (
            "purchase_task_id,shopping_mall_code,external_order_number,"
            "actual_amount,memo\n"
            "1,NAVER_SHOPPING,=cmd|'/c calc',13000,+HYPERLINK(\"evil\")\n"
        ).encode("utf-8")
        results = parse_csv_rows(raw)
        self.assertTrue(results[0].success)
        self.assertTrue(
            results[0].data["external_order_number"].startswith("'"),
        )
        self.assertTrue(results[0].data["memo"].startswith("'"))

    def test_encoding_fallback_to_cp949(self):

        raw = "purchase_task_id,shopping_mall_code,external_order_number,actual_amount\n".encode("cp949")
        text = decode_csv_bytes(raw)
        self.assertIn("purchase_task_id", text)

    def test_missing_required_columns_raises(self):

        raw = "foo,bar\n1,2\n".encode("utf-8")
        with self.assertRaises(ValueError):
            parse_csv_rows(raw)


if __name__ == "__main__":
    unittest.main()
