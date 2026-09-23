"""
=========================================================
Homez OS

File : tests/test_coupang_option_identifiers_provider.py

2026-09-21 옵션 연결 — `CoupangLiveProductProvider.get_product_option_identifiers()`
계약 테스트. 공식 문서(상품 조회: GET .../seller-products/{sellerProductId})의 응답
계약을 기준으로 한다:
  - data.items[]에 sellerProductItemId·vendorItemId·itemName·externalVendorSku가 있다
  - vendorItemId는 임시저장 상태에서는 null이고 승인완료 시 값이 채워진다
  - 문서 요약이 vendorItemId를 number/string으로 엇갈려 적어 두 타입 모두 받아야 한다
생성 응답에는 sellerProductId만 있으므로 옵션 번호는 이 조회로만 얻는다.

실제 쿠팡 호출 없음(가짜 Session). 응답에 섞여 올 수 있는 연락처·주소는 결과에 담기지
않아야 한다(옵션 식별자 3개 필드만 옮겨 담는다).
=========================================================
"""

import unittest
from datetime import datetime, timezone

import requests

from app.domains.marketplace_listing.coupang_live_provider import (
    CoupangLiveProductProvider,
)
from tests.test_coupang_live_submission import _Response
from tests.test_coupang_live_submission import _Session


def _provider(response=None, error=None):
    session = _Session(response=response, error=error)
    provider = CoupangLiveProductProvider(
        {"vendor_id": "A0001", "access_key": "access", "secret_key": "secret"},
        session=session,
        now_factory=lambda: datetime(2026, 9, 21, tzinfo=timezone.utc),
    )
    return provider, session


def _body(items, *, seller_product_id=123456, status="승인완료", **extra):
    data = {"sellerProductId": seller_product_id, "statusName": status, "items": items}
    data.update(extra)
    return {"code": "SUCCESS", "message": "", "data": data}


class OptionIdentifiersContractTestCase(unittest.TestCase):

    def test_reads_ids_as_numbers_or_strings_and_keeps_sku_pairing(self):
        provider, session = _provider(_Response(body=_body([
            {"sellerProductItemId": 501, "vendorItemId": 9001, "itemName": "블랙",
             "externalVendorSku": "SKU-BLACK"},
            {"sellerProductItemId": "502", "vendorItemId": "9002", "itemName": "화이트",
             "externalVendorSku": "SKU-WHITE"},
        ])))

        result = provider.get_product_option_identifiers("123456")

        self.assertEqual(result.outcome, "FOUND")
        self.assertEqual(
            [(i.external_vendor_sku, i.vendor_item_id, i.seller_product_item_id)
             for i in result.items],
            [("SKU-BLACK", "9001", "501"), ("SKU-WHITE", "9002", "502")],
        )
        self.assertEqual(result.raw_status_name, "승인완료")

    def test_is_a_single_read_only_get_on_the_documented_path(self):
        provider, session = _provider(_Response(body=_body([])))
        provider.get_product_option_identifiers("123456")

        self.assertEqual(len(session.calls), 1)
        (url,), kwargs = session.calls[0]
        self.assertTrue(url.endswith(
            "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/123456",
        ))
        self.assertIn("Authorization", kwargs["headers"])
        # 이 메서드는 POST(생성)를 하지 않는다 — 가짜 Session은 post를 호출해도 기록한다.
        self.assertTrue(all("post" not in str(c) for c in session.calls))

    def test_unissued_option_number_stays_none_not_guessed(self):
        """임시저장·승인 전에는 vendorItemId가 null — 채우지 않고 None으로 남긴다."""

        provider, _ = _provider(_Response(body=_body([
            {"sellerProductItemId": 501, "vendorItemId": None, "externalVendorSku": "A"},
            {"sellerProductItemId": 502, "externalVendorSku": "B"},
            {"sellerProductItemId": 503, "vendorItemId": 0, "externalVendorSku": "C"},
            {"sellerProductItemId": 504, "vendorItemId": "", "externalVendorSku": "D"},
            {"sellerProductItemId": 505, "vendorItemId": True, "externalVendorSku": "E"},
            {"sellerProductItemId": 506, "vendorItemId": 12.5, "externalVendorSku": "F"},
            {"sellerProductItemId": 507, "vendorItemId": "abc", "externalVendorSku": "G"},
        ], status="임시저장")))

        result = provider.get_product_option_identifiers("123456")

        self.assertEqual(result.outcome, "FOUND")
        self.assertTrue(all(i.vendor_item_id is None for i in result.items), result.items)
        self.assertEqual(result.raw_status_name, "임시저장")

    def test_blank_sku_becomes_none_so_it_can_never_match_an_option(self):
        provider, _ = _provider(_Response(body=_body([
            {"vendorItemId": 1, "externalVendorSku": ""},
            {"vendorItemId": 2},
            {"vendorItemId": 3, "externalVendorSku": 77},
            {"vendorItemId": 4, "externalVendorSku": " SKU "},
        ])))
        result = provider.get_product_option_identifiers("123456")
        self.assertEqual(
            [i.external_vendor_sku for i in result.items], [None, None, None, " SKU "],
        )

    def test_response_for_a_different_product_is_never_used(self):
        provider, _ = _provider(_Response(body=_body(
            [{"vendorItemId": 1, "externalVendorSku": "A"}], seller_product_id=999,
        )))
        result = provider.get_product_option_identifiers("123456")
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "SELLER_PRODUCT_ID_MISMATCH")
        self.assertEqual(result.items, ())

    def test_contract_violations_are_unknown_fail_closed(self):
        cases = {
            "no data": {"code": "SUCCESS", "message": ""},
            "data not object": {"code": "SUCCESS", "data": []},
            "items missing": {"code": "SUCCESS", "data": {"sellerProductId": 123456}},
            "items not list": {"code": "SUCCESS", "data": {"items": {"a": 1}}},
            "item not object": _body(["oops"]),
            "top level not object": ["x"],
        }
        for name, body in cases.items():
            with self.subTest(name):
                provider, _ = _provider(_Response(body=body))
                result = provider.get_product_option_identifiers("123456")
                self.assertEqual(result.outcome, "UNKNOWN")
                self.assertEqual(result.items, ())

    def test_invalid_json_is_unknown(self):
        provider, _ = _provider(_Response(invalid_json=True))
        self.assertEqual(provider.get_product_option_identifiers("1").outcome, "UNKNOWN")

    def test_http_outcomes_and_no_automatic_retry(self):
        provider, session = _provider(_Response(status_code=404, body={}))
        self.assertEqual(provider.get_product_option_identifiers("1").outcome, "NOT_FOUND")

        provider, session = _provider(_Response(status_code=500, body={"message": "x"}))
        self.assertEqual(provider.get_product_option_identifiers("1").outcome, "UNKNOWN")
        self.assertEqual(len(session.calls), 1)

        provider, session = _provider(error=requests.Timeout())
        timeout = provider.get_product_option_identifiers("1")
        self.assertEqual((timeout.outcome, timeout.error_code), ("UNKNOWN", "TIMEOUT"))
        self.assertEqual(len(session.calls), 1)

        provider, session = _provider(error=requests.ConnectionError())
        self.assertEqual(
            provider.get_product_option_identifiers("1").error_code, "NETWORK_ERROR",
        )
        self.assertEqual(len(session.calls), 1)

    def test_result_carries_only_option_identifiers_never_contact_or_address(self):
        provider, _ = _provider(_Response(body=_body(
            [{"vendorItemId": 9001, "externalVendorSku": "SKU-A", "itemName": "이름",
              "salePrice": 1000}],
            returnAddress="서울시 강남구 테헤란로 1", companyContactNumber="0212345678",
            returnZipCode="06236",
        )))
        result = provider.get_product_option_identifiers("123456")
        rendered = repr(result)
        for leaked in ("테헤란로", "0212345678", "06236", "이름", "1000"):
            self.assertNotIn(leaked, rendered)
        self.assertEqual(
            set(result.items[0].__dataclass_fields__),
            {"external_vendor_sku", "vendor_item_id", "seller_product_item_id"},
        )


if __name__ == "__main__":
    unittest.main()
