"""
=========================================================
Homez OS

File : tests/test_coupang_product_lookup.py

쿠팡 등록 확인(상품 목록 조회) — 2026-10-06.

결과불명·실패 제출의 "쿠팡에 상품이 있는가"를 WING 화면 없이 HOMEZ에서 확인하는
읽기 전용 기능을 고정한다. 핵심 원칙: 이상이 하나라도 있으면 "없음"이라고 결론 내리지
않는다(UNKNOWN). 쿠팡에는 합성 응답만 쓰고 실제 네트워크는 쓰지 않는다.
=========================================================
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

from app.domains.marketplace_listing.coupang_live_provider import (
    CREATE_PRODUCT_PATH, BASE_URL, CoupangLiveProductProvider,
    ProductListResult, RegisteredProductSummary,
)
from app.domains.marketplace_listing.listing_wizard_live_service import (
    evaluate_registration_lookup, safe_list_products,
)
from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)

FIXED_NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
CREDENTIAL = {"access_key": "AK-TEST", "secret_key": "SK-TEST", "vendor_id": "A00000001"}
NAME = "백산수 생수 500ml x 20PET"


class _Response:
    def __init__(self, status_code=200, body=None, invalid_json=False):
        self.status_code = status_code
        self._body = body
        self._invalid = invalid_json
        self.text = ""

    def json(self):
        if self._invalid:
            raise ValueError("invalid")
        return self._body


class _Session:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "headers": headers})
        if self.exc:
            raise self.exc
        return self.response

    def post(self, *a, **k):  # pragma: no cover - 목록 조회가 POST를 쓰면 안 된다
        raise AssertionError("목록 조회는 POST를 쓰지 않는다")


def _provider(session):
    return CoupangLiveProductProvider(
        CREDENTIAL, session=session, now_factory=lambda: FIXED_NOW,
    )


def _row(pid=1234567890, name=NAME[:20], status="APPROVED"):
    return {"sellerProductId": pid, "sellerProductName": name, "statusName": status,
            "displayCategoryCode": 58759, "vendorUserId": "should-not-be-copied"}


class ProviderListProductsTests(unittest.TestCase):

    def test_one_get_with_signed_query_and_no_pagination_follow(self):
        session = _Session(_Response(200, {"code": "200", "data": [_row()], "nextToken": ""}))
        result = _provider(session).list_products(NAME[:20], max_per_page=500)
        self.assertEqual(len(session.calls), 1)
        query = urlencode(
            [("vendorId", "A00000001"), ("maxPerPage", "100"), ("sellerProductName", NAME[:20])],
            quote_via=quote,
        )
        call = session.calls[0]
        self.assertEqual(call["url"], f"{BASE_URL}{CREATE_PRODUCT_PATH}?{query}")
        self.assertEqual(
            call["headers"]["Authorization"],
            build_authorization_header(
                "AK-TEST", "SK-TEST", "GET", CREATE_PRODUCT_PATH, query, now=FIXED_NOW,
            ),
        )
        self.assertEqual(result.outcome, "FOUND")
        self.assertEqual(result.products[0].seller_product_id, "1234567890")
        self.assertEqual(result.products[0].status_name, "APPROVED")
        self.assertEqual(result.products[0].display_category_code, "58759")

    def test_only_allowlisted_fields_are_carried(self):
        result = _provider(_Session(_Response(200, {"data": [_row()]}))).list_products(NAME)
        self.assertEqual(
            set(vars(result.products[0])),
            {"seller_product_id", "seller_product_name", "status_name", "display_category_code"},
        )

    def test_empty_list_without_next_token_is_not_found(self):
        result = _provider(_Session(_Response(200, {"data": [], "nextToken": ""}))).list_products(NAME)
        self.assertEqual(result.outcome, "NOT_FOUND")

    def test_empty_list_with_next_token_is_unknown_not_absent(self):
        result = _provider(_Session(_Response(200, {"data": [], "nextToken": "abc"}))).list_products(NAME)
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertTrue(result.has_more)

    def test_errors_and_bad_shapes_are_unknown_never_not_found(self):
        cases = [
            _Session(_Response(401, {"code": "ERROR", "message": "denied"})),
            _Session(_Response(500, {})),
            _Session(_Response(200, None, invalid_json=True)),
            _Session(_Response(200, {"data": "not-a-list"})),
            _Session(_Response(200, {"data": ["not-a-dict"]})),
            _Session(_Response(200, {"data": [{"sellerProductId": "x"}]})),
            _Session(exc=requests.Timeout()),
            _Session(exc=requests.ConnectionError()),
        ]
        for session in cases:
            with self.subTest(case=type(session.response).__name__ if session.response else repr(session.exc)):
                result = _provider(session).list_products(NAME)
                self.assertEqual(result.outcome, "UNKNOWN")
                self.assertEqual(len(session.calls), 1)  # 자동 재시도 없음


class EvaluateRegistrationLookupTests(unittest.TestCase):

    def _result(self, products=(), outcome=None, has_more=False, **kw):
        return ProductListResult(
            outcome=outcome or ("FOUND" if products else "NOT_FOUND"),
            products=tuple(products), has_more=has_more, **kw,
        )

    def _p(self, pid, name):
        return RegisteredProductSummary(seller_product_id=pid, seller_product_name=name, status_name="APPROVED")

    def test_found_by_first_20_characters_and_reference_flag(self):
        out = evaluate_registration_lookup(
            NAME, self._result([self._p("111", NAME[:20]), self._p("222", "다른 상품")]), "111",
        )
        self.assertEqual(out["outcome"], "FOUND")
        self.assertEqual([m["seller_product_id"] for m in out["matches"]], ["111"])
        self.assertTrue(out["matches"][0]["is_recorded_reference"])

    def test_same_name_twice_returns_both_without_choosing(self):
        out = evaluate_registration_lookup(
            NAME, self._result([self._p("111", NAME[:20]), self._p("222", NAME[:20])]),
        )
        self.assertEqual(len(out["matches"]), 2)

    def test_listed_but_other_names_is_not_found_only_when_no_more_pages(self):
        listed = self._result([self._p("999", "삼색 부직포 주방행주")])
        self.assertEqual(evaluate_registration_lookup(NAME, listed)["outcome"], "NOT_FOUND")
        more = self._result([self._p("999", "삼색 부직포 주방행주")], has_more=True)
        self.assertEqual(evaluate_registration_lookup(NAME, more)["outcome"], "UNKNOWN")

    def test_provider_unknown_passes_through(self):
        out = evaluate_registration_lookup(
            NAME, ProductListResult(outcome="UNKNOWN", error_code="TIMEOUT", error_summary="시간 초과"),
        )
        self.assertEqual(out["outcome"], "UNKNOWN")
        self.assertEqual(out["error_code"], "TIMEOUT")


class SafeListProductsTests(unittest.TestCase):
    """제공자가 처리하지 못한 예외는 500이 아니라 확인불가(UNKNOWN)여야 한다."""

    class _Boom:
        def __init__(self, exc):
            self.exc, self.calls = exc, 0

        def list_products(self, seller_product_name=None, max_per_page=20):
            self.calls += 1
            raise self.exc

    def test_unexpected_exceptions_become_unknown_never_not_found(self):
        for exc in (RuntimeError("범위 한정 가드: GET 예산 소진"), ValueError("x"), KeyError("k")):
            with self.subTest(exc=type(exc).__name__):
                provider = self._Boom(exc)
                result = safe_list_products(provider, NAME)
                self.assertEqual(result.outcome, "UNKNOWN")
                self.assertEqual(result.error_code, "LOOKUP_FAILED")
                self.assertEqual(provider.calls, 1)  # 재시도 없음
                evaluated = evaluate_registration_lookup(NAME, result)
                self.assertEqual(evaluated["outcome"], "UNKNOWN")
                self.assertNotIn("예산", evaluated["error_summary"])  # 예외 원문 미노출

    def test_normal_result_passes_through_untouched(self):
        ok = ProductListResult(outcome="NOT_FOUND")

        class _Ok:
            def list_products(self, seller_product_name=None, max_per_page=20):
                return ok

        self.assertIs(safe_list_products(_Ok(), NAME), ok)

    def test_name_is_cut_to_20_characters(self):
        seen = []

        class _Spy:
            def list_products(self, seller_product_name=None, max_per_page=20):
                seen.append(seller_product_name)
                return ProductListResult(outcome="NOT_FOUND")

        safe_list_products(_Spy(), NAME)
        self.assertEqual(seen, [NAME[:20]])


class WiringTests(unittest.TestCase):

    def test_router_and_screen_are_wired(self):
        root = Path(__file__).resolve().parents[1] / "app"
        router = (root / "domains" / "marketplace_listing" / "listing_wizard_router.py").read_text(encoding="utf-8")
        self.assertIn("/submissions/{submission_id}/registration-lookup", router)
        self.assertIn("service.lookup_registration(", router)
        console = (root / "web" / "console.js").read_text(encoding="utf-8")
        self.assertIn("lw-regcheck-btn", console)
        self.assertIn("/registration-lookup`", console)


if __name__ == "__main__":
    unittest.main()
