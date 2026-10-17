"""
=========================================================
Homez OS

File : tests/test_product_history_provider.py

2026-10-17 — 쿠팡 상품 상태·상태 변경 이력을 읽는 제공자(`coupang_product_history_provider.py`)의 규칙을 고정한다(가짜 전송, 외부 호출 0).

  - 상태는 `statusName` 원문을 보존하고 알려진 표기만 표준 이름으로 바꾼다(관측되지 않은 표기는 UNKNOWN — 추측하지 않는다).
  - 이력은 상태·사유 문구(300자)·시각만 돌려주고 `createdBy`는 버린다. 문서에 정렬 기준이 없어 시각으로 직접 정렬한다.
  - 응답이 예상 모양이 아니거나 오류면 `ok=False`다(값으로 꾸며 내지 않는다).
  - GET만 나가고 경로가 공식 문서와 같다. 쓰기 메서드는 이 제공자에 없다.
=========================================================
"""

import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.domains.purchase_task.coupang_product_history_provider import HISTORY_PATH
from app.domains.purchase_task.coupang_product_history_provider import MAX_REASON_CHARS
from app.domains.purchase_task.coupang_product_history_provider import PRODUCT_PATH
from app.domains.purchase_task.coupang_product_history_provider import CoupangProductStatusProvider
from tests.test_coupang_invoice_upload import response

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/purchase_task/coupang_product_history_provider.py").read_text(encoding="utf-8")
CRED = {"access_key": "AK", "secret_key": "SK", "vendor_id": "A00012345"}


class Transport:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append((method, url, json_body))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def provider(outcome):
    transport = Transport(outcome)
    return CoupangProductStatusProvider(CRED, transport=transport, now_factory=lambda: datetime(2026, 10, 17, 6, 0, 0)), transport


def status_body(name="승인반려", product="레이펄스 워터리스 바디워시 본품"):
    return {"code": "SUCCESS", "message": "", "data": {"statusName": name, "sellerProductName": product, "vendorUserId": "x"}}


def history_body(rows):
    return {"code": "SUCCESS", "message": "", "data": rows}


class StatusTests(unittest.TestCase):

    def test_a_known_status_is_mapped_and_the_raw_wording_is_kept(self):
        p, t = provider(response(200, status_body("승인반려")))
        result = p.read_product_status("16417638496")
        self.assertTrue(result.ok)
        self.assertEqual((result.rows[0]["status"], result.rows[0]["raw"]), ("DENIED", "승인반려"))
        self.assertEqual(result.rows[0]["name"], "레이펄스 워터리스 바디워시 본품")
        p, _ = provider(response(200, status_body("APPROVED")))
        self.assertEqual(p.read_product_status("1").rows[0]["status"], "APPROVED")

    def test_an_unseen_wording_is_unknown_not_a_guess(self):
        p, _ = provider(response(200, status_body("처음 보는 표기")))
        row = p.read_product_status("1").rows[0]
        self.assertEqual((row["status"], row["raw"]), ("UNKNOWN", "처음 보는 표기"))

    def test_errors_and_odd_shapes_are_failures_not_values(self):
        for outcome in (response(404, {"code": 404}), response(500, {"code": 500}), response(200, {"data": []}), response(200, {"data": {}}), response(200, None)):
            p, _ = provider(outcome)
            self.assertFalse(p.read_product_status("1").ok)
        p, _ = provider(requests.ConnectionError("boom"))
        result = p.read_product_status("1")
        self.assertFalse(result.ok)
        self.assertIn("ConnectionError", result.detail)

    def test_a_bad_product_number_never_reaches_the_network(self):
        p, t = provider(response(200, status_body()))
        for bad in ("", "abc", "12 34", None, "1" * 21, "../x"):
            self.assertFalse(p.read_product_status(bad).ok)
            self.assertFalse(p.read_product_history(bad).ok)
        self.assertEqual(t.calls, [])


class HistoryTests(unittest.TestCase):

    def test_rows_are_sorted_newest_first_and_the_creator_is_dropped(self):
        rows = [
            {"status": "승인대기", "comment": "컨텐츠 확인 대기중", "createdBy": "홍길동 판매자", "createdAt": "2026-10-17T14:09:58"},
            {"status": "승인반려", "comment": "상세설명 이미지를 다운받지 못했습니다", "createdBy": "쿠팡 심사", "createdAt": "2026-10-17T14:30:00"},
        ]
        p, _ = provider(response(200, history_body(rows)))
        result = p.read_product_history("16417638496")
        self.assertTrue(result.ok)
        self.assertEqual([r["status"] for r in result.rows], ["승인반려", "승인대기"])
        for row in result.rows:
            self.assertEqual(set(row), {"status", "comment", "at"})
        self.assertNotIn("홍길동", repr(result.rows))

    def test_a_long_comment_is_cut(self):
        p, _ = provider(response(200, history_body([{"status": "승인반려", "comment": "가" * 1000, "createdAt": "2026-10-17T14:30:00"}])))
        self.assertEqual(len(p.read_product_history("1").rows[0]["comment"]), MAX_REASON_CHARS)

    def test_a_non_list_response_is_a_failure(self):
        p, _ = provider(response(200, {"code": "SUCCESS", "data": {"x": 1}}))
        self.assertFalse(p.read_product_history("1").ok)

    def test_non_dict_items_are_skipped(self):
        p, _ = provider(response(200, history_body(["x", 1, {"status": "승인완료", "comment": "", "createdAt": "2026-10-17T15:00:00"}])))
        self.assertEqual(len(p.read_product_history("1").rows), 1)


class RequestShapeTests(unittest.TestCase):

    def test_only_get_goes_out_with_the_documented_paths(self):
        p, t = provider(response(200, status_body()))
        p.read_product_status("16417638496")
        p, t2 = provider(response(200, history_body([])))
        p.read_product_history("16417638496")
        self.assertEqual([(m, urlparse(u).path) for m, u, _b in t.calls],
                         [("GET", "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/16417638496")])
        self.assertEqual([(m, urlparse(u).path) for m, u, _b in t2.calls],
                         [("GET", "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/16417638496/histories")])
        self.assertTrue(all(b is None for _m, _u, b in t.calls + t2.calls))
        self.assertTrue(PRODUCT_PATH.endswith("{seller_product_id}") and HISTORY_PATH.endswith("/histories"))

    def test_the_file_has_no_write_call(self):
        for forbidden in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', "upload", "stop_vendor", "cancel_order"):
            self.assertNotIn(forbidden, SOURCE, forbidden)


if __name__ == "__main__":
    unittest.main()
