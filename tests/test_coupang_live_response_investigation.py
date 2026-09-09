"""
=========================================================
Homez OS

File : tests/test_coupang_live_response_investigation.py

2026-08-30 후속 지시 — 개인정보 재노출 재발 방지용 안전 추출기
검증. 실제 값은 전혀 쓰지 않는다 — 전부 합성(가짜) fixture다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.coupang_live_response_investigation import (
    extract_safe_live_status_fields,
)


def _synthetic_full_response(**overrides):
    base = {
        "code": "SUCCESS",
        "message": "",
        "data": {
            "sellerProductId": 90000000001,
            "sellerProductName": "합성 테스트 상품 A",
            "displayCategoryCode": 80754,
            "categoryId": 1783,
            "productId": 90000000002,
            "brand": "합성무브랜드",
            "brandId": "SYN-000001",
            "statusName": "승인완료",
            "deliveryMethod": "SEQUENCIAL",
            "deliveryCompanyCode": "CJGLS",
            "deliveryChargeType": "NOT_FREE",
            "deliveryCharge": 3000,
            "deliveryChargeOnReturn": 0,
            "returnCharge": 3000,
            "returnCenterCode": "SYN-RC-0001",
            "returnChargeName": "합성반품지",
            "outboundShippingPlaceCode": 11111111,
            "vendorUserId": "synuser",
            # 아래는 allowlist에 없는 민감/자유텍스트 필드 — 합성값이라도
            # 절대 결과에 나타나면 안 된다.
            "companyContactNumber": "0201234567",
            "returnAddress": "서울특별시 합성구 가짜로 1",
            "returnAddressDetail": "999호",
            "afterServiceContactNumber": "01099998888",
            "items": [
                {
                    "itemName": "합성 옵션명",
                    "externalVendorSku": "SYN-SKU-0001",
                    "originalPrice": 10000,
                    "salePrice": 9000,
                    "maximumBuyCount": 10,
                    "unitCount": 1,
                    "barcode": "",
                    "emptyBarcode": True,
                    "emptyBarcodeReason": "합성사유",
                    "notices": [
                        {
                            "noticeCategoryDetailName": "A/S 책임자와 전화번호",
                            "content": "01077778888",
                        },
                    ],
                },
            ],
            "notices": [
                {"noticeCategoryDetailName": "제조자", "content": "합성제조사"},
            ],
        },
    }
    base.update(overrides)
    return base


class SafeLiveResponseExtractorTestCase(unittest.TestCase):

    def test_allowlisted_fields_pass_through(self):

        result = extract_safe_live_status_fields(_synthetic_full_response())

        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["sellerProductId"], 90000000001)
        self.assertEqual(result["data"]["statusName"], "승인완료")
        self.assertEqual(result["data"]["items"][0]["itemName"], "합성 옵션명")

    def test_contact_number_field_structurally_excluded(self):

        result = extract_safe_live_status_fields(_synthetic_full_response())

        self.assertNotIn("companyContactNumber", result["data"])

    def test_address_fields_structurally_excluded(self):

        result = extract_safe_live_status_fields(_synthetic_full_response())

        self.assertNotIn("returnAddress", result["data"])
        self.assertNotIn("returnAddressDetail", result["data"])
        self.assertNotIn("afterServiceContactNumber", result["data"])

    def test_notices_array_structurally_excluded_top_level_and_item_level(self):
        # 이번 사고의 정확한 원인 — notices[].content(자유 텍스트)에
        # 담긴 전화번호가 고정 키 redaction을 피했다. allowlist에는
        # "notices" 자체가 없으므로 top-level에도 item 내부에도
        # 나타나지 않아야 한다.
        result = extract_safe_live_status_fields(_synthetic_full_response())

        self.assertNotIn("notices", result["data"])
        self.assertNotIn("notices", result["data"]["items"][0])

    def test_unknown_future_field_structurally_excluded(self):

        result = extract_safe_live_status_fields(
            _synthetic_full_response(
                data={
                    **_synthetic_full_response()["data"],
                    "someBrandNewFieldNeverSeenBefore": "합성값",
                },
            ),
        )

        serialized = str(result)
        self.assertNotIn("someBrandNewFieldNeverSeenBefore", serialized)
        self.assertNotIn("합성값", serialized)

    def test_fail_closed_when_phone_shaped_value_leaks_into_allowed_field(self):
        # allowlist 설계 실수를 가정한다 — 허용된 필드(brand)에 실수로
        # 전화번호 형태 값이 들어가도, 2차 방어선이 전체 결과를
        # 버려야 한다(부분 마스킹이 아니라 전체 반환 거부).
        response = _synthetic_full_response()
        response["data"]["brand"] = "010-1234-5678"

        result = extract_safe_live_status_fields(response)

        self.assertIsNone(result)

    def test_non_dict_input_returns_none(self):

        self.assertIsNone(extract_safe_live_status_fields("not a dict"))
        self.assertIsNone(extract_safe_live_status_fields(None))

    def test_missing_data_field_returns_top_level_only(self):

        result = extract_safe_live_status_fields({"code": "SUCCESS", "message": ""})

        self.assertEqual(result, {"code": "SUCCESS", "message": ""})

    def test_non_dict_items_are_skipped_without_crashing(self):

        response = _synthetic_full_response()
        response["data"]["items"] = ["not-a-dict", 123, None]

        result = extract_safe_live_status_fields(response)

        self.assertNotIn("items", result["data"])


if __name__ == "__main__":
    unittest.main()
