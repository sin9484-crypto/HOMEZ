"""
=========================================================
Homez OS

File : tests/test_price_monitoring_adapter.py

2026-09-06 — price_monitoring Adapter 검증. HttpPriceStockAdapter는
가짜 http_get으로, HeadlessBrowserPriceStockAdapter는 가짜 브라우저/
페이지 객체로 대체한다 — 실제 네트워크·실제 Chromium 기동은 이
파일에서 절대 하지 않는다(수동 실행 확인은 별도로 완료함, docs/
HOMEZ_PROJECT_STATE.md 기록 참고).
=========================================================
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.domains.price_monitoring.adapter import FakePriceStockAdapter
from app.domains.price_monitoring.adapter import HeadlessBrowserPriceStockAdapter
from app.domains.price_monitoring.adapter import HttpPriceStockAdapter


class _Watch:
    def __init__(self, target_url="https://example.test/product",
                 price_extract_regex=None, stock_keyword=None):
        self.target_url = target_url
        self.price_extract_regex = price_extract_regex
        self.stock_keyword = stock_keyword


class _FakeHttpResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakePriceStockAdapterTest(unittest.TestCase):

    def test_returns_fixed_deterministic_result(self):
        adapter = FakePriceStockAdapter(fixed_price=Decimal("5000"), in_stock=False)
        result = adapter.check(_Watch())
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.price, Decimal("5000"))
        self.assertFalse(result.in_stock)


class HttpPriceStockAdapterTest(unittest.TestCase):

    def test_successful_price_extraction(self):
        adapter = HttpPriceStockAdapter(
            http_get=lambda url, headers, timeout: _FakeHttpResponse(
                200, "<span>판매가 19,900원</span>",
            ),
        )
        watch = _Watch(price_extract_regex=r"판매가 ([0-9,]+)원")
        result = adapter.check(watch)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.price, Decimal("19900"))

    def test_stock_keyword_detection(self):
        adapter = HttpPriceStockAdapter(
            http_get=lambda url, headers, timeout: _FakeHttpResponse(
                200, "<div>품절된 상품입니다</div>",
            ),
        )
        watch = _Watch(stock_keyword="품절")
        result = adapter.check(watch)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertFalse(result.in_stock)

    def test_403_is_failed_without_retry(self):
        calls = []

        def fake_get(url, headers, timeout):
            calls.append(1)
            return _FakeHttpResponse(403, "")

        adapter = HttpPriceStockAdapter(http_get=fake_get)
        result = adapter.check(_Watch())
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "HTTP_403")
        self.assertEqual(len(calls), 1)  # 재시도하지 않음

    def test_network_exception_is_failed_not_raised(self):
        def raising_get(url, headers, timeout):
            raise ConnectionError("접속 실패")

        adapter = HttpPriceStockAdapter(http_get=raising_get)
        result = adapter.check(_Watch())
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "NETWORK_ERROR")

    def test_price_pattern_not_found_is_failed(self):
        adapter = HttpPriceStockAdapter(
            http_get=lambda url, headers, timeout: _FakeHttpResponse(
                200, "가격 정보 없음",
            ),
        )
        watch = _Watch(price_extract_regex=r"판매가 ([0-9,]+)원")
        result = adapter.check(watch)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "PRICE_PATTERN_NOT_FOUND")


class _FakePage:
    def __init__(self, text="", raise_on_goto=None):
        self._text = text
        self._raise_on_goto = raise_on_goto
        self.closed = False

    def goto(self, url, wait_until, timeout):
        if self._raise_on_goto:
            raise self._raise_on_goto

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, selector):
        return self._text

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakePage):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class HeadlessBrowserPriceStockAdapterTest(unittest.TestCase):
    """실제 Playwright는 절대 기동하지 않는다 — __enter__를 우회해
    가짜 브라우저를 직접 주입한다."""

    def _adapter_with_fake_browser(self, page: _FakePage) -> HeadlessBrowserPriceStockAdapter:
        adapter = HeadlessBrowserPriceStockAdapter()
        adapter._browser = _FakeBrowser(page)
        return adapter

    def test_successful_extraction_after_rendering(self):
        page = _FakePage(text="판매가 129,000원 재고있음")
        adapter = self._adapter_with_fake_browser(page)
        watch = _Watch(price_extract_regex=r"판매가 ([0-9,]+)원")

        result = adapter.check(watch)

        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.price, Decimal("129000"))
        self.assertTrue(page.closed)

    def test_navigation_timeout_is_failed_not_raised(self):
        page = _FakePage(raise_on_goto=TimeoutError("30000ms exceeded"))
        adapter = self._adapter_with_fake_browser(page)

        result = adapter.check(_Watch())

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "NAVIGATION_ERROR")
        self.assertTrue(page.closed)  # 실패해도 page는 정리한다

    def test_reuses_same_browser_across_multiple_checks(self):
        page1 = _FakePage(text="1,000원")
        page2 = _FakePage(text="2,000원")
        browser = _FakeBrowser(page1)
        adapter = HeadlessBrowserPriceStockAdapter()
        adapter._browser = browser

        # new_page()가 매번 같은 page를 반환하도록 바꿔서 브라우저
        # 인스턴스가 재사용되는지(매 check마다 새 __enter__가 아닌지)
        # 확인한다.
        pages = [page1, page2]
        browser.new_page = lambda: pages.pop(0)

        r1 = adapter.check(_Watch(price_extract_regex=r"([0-9,]+)원"))
        r2 = adapter.check(_Watch(price_extract_regex=r"([0-9,]+)원"))

        self.assertEqual(r1.price, Decimal("1000"))
        self.assertEqual(r2.price, Decimal("2000"))
        self.assertFalse(browser.closed)  # 브라우저 자체는 아직 안 닫힘


if __name__ == "__main__":
    unittest.main()
