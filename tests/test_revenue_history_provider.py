"""
=========================================================
Homez OS

File : tests/test_revenue_history_provider.py

2026-10-18 — 마케팅 AI M4: 쿠팡 매출내역 읽기(`read_revenue_history`)를 고정한다. 가짜 전송, 쿠팡 호출 0, 쓰기 0.

  - 요청: GET 한 가지, 기간은 어제까지·최대 31일(KST), 첫 쪽은 빈 token, 이후 nextToken, 쪽당 50건. 쪽 사이에 쉬고 최대 10쪽에서 멈추며 넘으면 truncated로 알린다.
  - 해석: 주문을 옵션 단위 줄로 펼친다. SALE/REFUND만, 옵션 ID·수량이 없으면 건너뛴다. 고객·주소·연락처 필드는 응답에 있어도 읽지 않는다.
  - 모르는 모양은 추측하지 않는다: 데이터가 목록이 아니거나, 쪽 토큰이 되풀이되거나, HTTP가 200이 아니면 실패로 돌려주고 그때까지 읽은 줄은 보존한다.
  - 읽기 허용 연결: `ChannelActionService`의 배송 이후 읽기 목록에 `revenue`가 있고 `AFTER_SALES_CHECK` 허용을 거친다.
=========================================================
"""

import re
import unittest
from datetime import datetime
from datetime import timezone
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

from app.domains.purchase_task import coupang_after_sales_provider as mod
from app.domains.purchase_task.coupang_after_sales_provider import _AfterSalesMixin

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/purchase_task/coupang_after_sales_provider.py").read_text(encoding="utf-8")
ACTIONS = (ROOT / "app/domains/purchase_task/channel_action_service.py").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 18, 16, 0, 0, tzinfo=timezone.utc)   # 한국 시간으로는 2026-10-19 01:00 → 어제 = 2026-10-18


class FakeResponse:
    def __init__(self, status=200, body=None, raw=None):
        self.status_code = status
        self._body = body
        self._raw = raw

    def json(self):
        if self._raw is not None:
            raise ValueError("not json")
        return self._body


class FakeProvider(_AfterSalesMixin):
    vendor_id = "A00012345"

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def _now(self):
        return NOW

    def _send(self, method, path, *, query=""):
        self.sent.append((method, path, query))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def order(order_id="1001", sale_type="SALE", items=None, **extra):
    base = {"orderId": order_id, "saleType": sale_type, "saleDate": "2026-10-10", "recognitionDate": "2026-10-17",
            "items": items if items is not None else [item()]}
    base.update(extra)
    return base


def item(vendor_item_id="9001", quantity=2, **extra):
    base = {"vendorItemId": vendor_item_id, "productName": "바디워시 상품", "vendorItemName": "바디워시 3개입", "quantity": quantity,
            "salePrice": 25800, "saleAmount": 25800, "serviceFee": 2786, "serviceFeeVat": 278, "settlementAmount": 22736}
    base.update(extra)
    return base


def page(orders, has_next=False, next_token=None):
    return FakeResponse(body={"code": 200, "message": "OK", "data": orders, "hasNext": has_next, "nextToken": next_token})


class PatchedSleep(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self._orig = mod._sleep
        mod._sleep = self.sleeps.append
        self.addCleanup(lambda: setattr(mod, "_sleep", self._orig))


class RequestTests(PatchedSleep):

    def query(self, provider, index=0):
        method, path, raw = provider.sent[index]
        return method, path, {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def test_one_get_with_the_documented_parameters_and_yesterday_as_the_end(self):
        provider = FakeProvider([page([order()])])
        result = provider.read_revenue_history(days=30)
        method, path, q = self.query(provider)
        self.assertEqual((method, path), ("GET", "/v2/providers/openapi/apis/api/v1/revenue-history"))
        self.assertEqual(q, {"vendorId": "A00012345", "recognitionDateFrom": "2026-09-19", "recognitionDateTo": "2026-10-18", "token": "", "maxPerPage": "50"})
        self.assertEqual((result.ok, result.calls, result.truncated, len(result.rows)), (True, 1, False, 1))

    def test_the_range_is_days_inclusive_and_at_most_31(self):
        provider = FakeProvider([page([]), page([])])
        provider.read_revenue_history(days=1)
        self.assertEqual(self.query(provider)[2]["recognitionDateFrom"], self.query(provider)[2]["recognitionDateTo"])
        provider.read_revenue_history(days=31)
        q = self.query(provider, 1)[2]
        self.assertEqual((q["recognitionDateFrom"], q["recognitionDateTo"]), ("2026-09-18", "2026-10-18"))

    def test_bad_ranges_never_reach_the_network(self):
        provider = FakeProvider([])
        for days in (0, 32, -1, True, "7", 7.0, None):
            result = provider.read_revenue_history(days=days)
            self.assertFalse(result.ok, days)
        self.assertEqual(provider.sent, [])

    def test_following_pages_use_the_next_token_and_pause_between_calls(self):
        provider = FakeProvider([
            page([order("1")], has_next=True, next_token="T1"), page([order("2")], has_next=True, next_token="T2"), page([order("3")]),
        ])
        result = provider.read_revenue_history(days=30)
        self.assertEqual([self.query(provider, i)[2]["token"] for i in range(3)], ["", "T1", "T2"])
        self.assertEqual((result.ok, result.calls, [r["order_id"] for r in result.rows]), (True, 3, ["1", "2", "3"]))
        self.assertEqual(self.sleeps, [mod.REVENUE_PAGE_PAUSE_SECONDS, mod.REVENUE_PAGE_PAUSE_SECONDS])
        self.assertLessEqual(1 / mod.REVENUE_PAGE_PAUSE_SECONDS, 5)   # 초당 5회 한도 아래

    def test_it_stops_at_the_page_cap_and_says_the_data_is_cut(self):
        pages = [page([order(str(n))], has_next=True, next_token=f"T{n}") for n in range(mod.MAX_REVENUE_PAGES + 3)]
        provider = FakeProvider(pages)
        result = provider.read_revenue_history(days=30)
        self.assertEqual((result.ok, result.calls, result.truncated, len(result.rows)), (True, mod.MAX_REVENUE_PAGES, True, mod.MAX_REVENUE_PAGES))
        self.assertEqual(len(provider.sent), mod.MAX_REVENUE_PAGES)

    def test_a_repeated_page_token_is_a_failure_that_keeps_what_was_read(self):
        for tokens in (["T1", "T1"], ["T1", "T2", "T1"]):
            provider = FakeProvider([page([order(str(i))], has_next=True, next_token=t) for i, t in enumerate(tokens)] + [page([])])
            result = provider.read_revenue_history(days=30)
            self.assertFalse(result.ok, tokens)
            self.assertIn("되풀이", result.detail)
            self.assertGreaterEqual(len(result.rows), 1)

    def test_has_next_without_a_token_ends_the_read_and_says_the_data_is_cut(self):
        provider = FakeProvider([page([order()], has_next=True, next_token=None)])
        result = provider.read_revenue_history(days=30)
        self.assertEqual((result.ok, result.calls, result.truncated), (True, 1, True))


class ParsingTests(PatchedSleep):

    def read(self, data):
        return FakeProvider([page(data)]).read_revenue_history(days=30)

    def test_an_order_is_flattened_into_one_row_per_option_with_money_as_numbers(self):
        result = self.read([order("1001", items=[item("9001", 2), item("9002", 1, saleAmount=10000, serviceFee=1000)])])
        self.assertEqual([(r["order_id"], r["vendor_item_id"], r["quantity"]) for r in result.rows], [("1001", "9001", 2), ("1001", "9002", 1)])
        first = result.rows[0]
        self.assertEqual((first["sale_type"], first["sale_amount"], first["service_fee"], first["settlement_amount"], first["recognition_date"]), ("SALE", 25800.0, 2786.0, 22736.0, "2026-10-17"))
        self.assertEqual(first["name"], "바디워시 3개입")

    def test_refunds_are_kept_and_other_types_and_incomplete_rows_are_skipped(self):
        result = self.read([
            order("1", "REFUND"), order("2", "sale"), order("3", "EXCHANGE"), order("4", None), {"saleType": "SALE", "items": [item()]},
            order("5", items=[item(vendor_item_id=None), item(quantity=None), item(quantity="x"), "bad", item("9100", 1)]), "oops", None,
        ])
        self.assertEqual([(r["order_id"], r["sale_type"]) for r in result.rows], [("1", "REFUND"), ("2", "SALE"), ("5", "SALE")])
        self.assertEqual(result.rows[-1]["vendor_item_id"], "9100")

    def test_an_empty_list_is_a_successful_empty_read(self):
        result = self.read([])
        self.assertEqual((result.ok, result.rows, result.calls), (True, (), 1))

    def test_customer_address_and_contact_fields_are_never_copied(self):
        secret_order = order("1", buyer={"name": "홍길동", "phone": "010-1234-5678"}, receiver="서울시 어딘가", email="a@b.co", deliveryFee={"amount": 3000})
        secret_order["items"] = [item(buyerName="홍길동", receiverPhone="010-9999-0000", memo="문 앞에 놔 주세요")]
        row = self.read([secret_order]).rows[0]
        self.assertEqual(set(row), {"order_id", "sale_type", "sale_date", "recognition_date", "vendor_item_id", "name", "quantity", "sale_price", "sale_amount",
                                    "service_fee", "service_fee_vat", "settlement_amount"})
        text = repr(row)
        for secret in ("홍길동", "010-", "서울시", "a@b.co", "문 앞"):
            self.assertNotIn(secret, text)

    def test_a_long_option_name_is_clipped(self):
        row = self.read([order(items=[item(vendorItemName="가" * 200)])]).rows[0]
        self.assertEqual(len(row["name"]), 60)


class FailureTests(PatchedSleep):

    def test_unreadable_shapes_are_failures_not_guesses(self):
        for response in (FakeResponse(raw="<html>"), FakeResponse(body=[1, 2]), FakeResponse(body={"data": {"x": 1}}), FakeResponse(body={"data": "x"}), FakeResponse(body={})):
            result = FakeProvider([response]).read_revenue_history(days=30)
            self.assertFalse(result.ok)
            self.assertIn("해석 불가", result.detail)
            self.assertEqual(result.rows, ())

    def test_a_non_200_response_is_a_failure_with_the_status(self):
        result = FakeProvider([FakeResponse(429, {"code": "TOO_MANY", "message": "너무 많은 요청"})]).read_revenue_history(days=30)
        self.assertEqual((result.ok, result.http_status), (False, 429))
        self.assertIn("TOO_MANY", result.detail)

    def test_a_failure_on_a_later_page_keeps_the_rows_already_read(self):
        provider = FakeProvider([page([order("1")], has_next=True, next_token="T1"), FakeResponse(500, {"code": "E", "message": "서버 오류"})])
        result = provider.read_revenue_history(days=30)
        self.assertEqual((result.ok, result.calls, [r["order_id"] for r in result.rows]), (False, 2, ["1"]))

    def test_network_errors_are_failures(self):
        import requests

        for error, expect in ((requests.Timeout(), "시간 초과"), (requests.ConnectionError(), "통신 오류")):
            result = FakeProvider([error]).read_revenue_history(days=30)
            self.assertFalse(result.ok)
            self.assertIn(expect, result.detail)


class WiringTests(unittest.TestCase):

    def test_it_is_part_of_the_after_sales_read_list_and_the_real_provider(self):
        self.assertIn('"revenue": "read_revenue_history"', ACTIONS)
        from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider

        self.assertTrue(callable(getattr(CoupangChannelControlProvider, "read_revenue_history", None)))
        self.assertIn("ExtPurpose.AFTER_SALES_CHECK", ACTIONS[ACTIONS.index("def read_after_sales"):ACTIONS.index("def register_invoice")])

    def test_the_reader_only_does_get_and_has_no_write_call(self):
        reader = SOURCE[SOURCE.index("def read_revenue_history"):SOURCE.index("# ---- 공통 ----")]
        self.assertNotIn('"POST"', reader)
        self.assertNotIn('"PUT"', reader)
        self.assertNotIn('"DELETE"', reader)
        self.assertNotIn("requests.post", reader)
        self.assertIsNone(re.search(r"\bprint\(", reader))

    def test_the_documentation_header_names_the_endpoint_and_the_limits(self):
        for phrase in ("revenue-history", "최대 31일", "초당 5회", "첫 실호출"):
            self.assertIn(phrase, SOURCE)


if __name__ == "__main__":
    unittest.main()
