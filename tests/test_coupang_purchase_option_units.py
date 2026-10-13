"""
=========================================================
Homez OS

File : tests/test_coupang_purchase_option_units.py

구매옵션 값의 단위(2026-10-11). 쿠팡은 숫자 속성의 `attributeValueName`에 단위가 붙기를
요구한다(공식 예시 "1개", 2026-08-29 라이브 재현 "40" 거부·"40개" 통과). 백산수 재등록(제출 #10)이
"유효하지 않은 구매 옵션 값 혹은 단위가 존재합니다"로 거절된 원인은 숫자만 저장·전송한 것이었다.
순수 숫자일 때만 카테고리 메타데이터의 기본 단위를 붙이고, 그 밖에는 값을 건드리지 않는다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.coupang_live_payload import (
    _purchase_option_value, build_coupang_live_payload,
)
from tests.test_coupang_live_submission import (
    _base_live_required_fields, _live_channel_policy_attributes,
)

DEFINITIONS = [
    {"attribute_type_name": "총 수량", "data_type": "NUMBER", "basic_unit": "개", "usable_units": ["개", "박스", "세트"]},
    {"attribute_type_name": "개당 용량", "data_type": "NUMBER", "basic_unit": "ml", "usable_units": ["L", "ml"]},
    {"attribute_type_name": "개당 중량", "data_type": "NUMBER", "basic_unit": "g", "usable_units": ["g", "kg"]},
    {"attribute_type_name": "용기 타입", "data_type": "STRING", "basic_unit": "없음", "usable_units": []},
    {"attribute_type_name": "단위 없는 수", "data_type": "NUMBER", "basic_unit": "", "usable_units": []},
    {"attribute_type_name": "이상한 기본단위", "data_type": "NUMBER", "basic_unit": "tea", "usable_units": ["개"]},
]


class PurchaseOptionValueTests(unittest.TestCase):

    def test_pure_numbers_get_the_basic_unit(self):
        self.assertEqual(_purchase_option_value("총 수량", "20", DEFINITIONS), "20개")
        self.assertEqual(_purchase_option_value("개당 용량", "500", DEFINITIONS), "500ml")
        self.assertEqual(_purchase_option_value("개당 중량", "1.5", DEFINITIONS), "1.5g")
        self.assertEqual(_purchase_option_value("총 수량", 20, DEFINITIONS), "20개")

    def test_values_that_already_have_a_unit_are_untouched(self):
        self.assertEqual(_purchase_option_value("총 수량", "20개", DEFINITIONS), "20개")
        self.assertEqual(_purchase_option_value("개당 용량", "0.5L", DEFINITIONS), "0.5L")
        self.assertEqual(_purchase_option_value("총 수량", "2박스", DEFINITIONS), "2박스")

    def test_nothing_is_guessed_when_the_contract_is_unclear(self):
        self.assertEqual(_purchase_option_value("용기 타입", "500", DEFINITIONS), "500")          # 숫자 속성이 아님
        self.assertEqual(_purchase_option_value("단위 없는 수", "7", DEFINITIONS), "7")            # 기본 단위 없음
        self.assertEqual(_purchase_option_value("이상한 기본단위", "3", DEFINITIONS), "3")         # 기본 단위가 사용 가능 목록에 없음
        self.assertEqual(_purchase_option_value("정의 없는 속성", "9", DEFINITIONS), "9")          # 정의 없음
        self.assertEqual(_purchase_option_value("총 수량", "20", []), "20")                       # 스냅샷 없음
        self.assertEqual(_purchase_option_value("용기 타입", "플라스틱병", DEFINITIONS), "플라스틱병")


class PayloadAttributesUnitTests(unittest.TestCase):

    def _build(self, purchase_options, **extra):
        policy = _live_channel_policy_attributes(
            purchase_options=purchase_options, purchase_option_field_definitions=DEFINITIONS,
        )
        return build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=_base_live_required_fields(**extra),
            channel_policy_attributes=policy,
        )

    def test_numeric_only_options_are_sent_with_units(self):
        payload, blockers = self._build({"총 수량": "20", "개당 용량": "500"})
        self.assertEqual(blockers, [])
        attrs = {a["attributeTypeName"]: a["attributeValueName"] for a in payload["items"][0]["attributes"]}
        self.assertEqual(attrs["총 수량"], "20개")
        self.assertEqual(attrs["개당 용량"], "500ml")

    def test_options_with_units_are_unchanged_and_uid_still_added(self):
        payload, _ = self._build({"총 수량": "20개", "개당 용량": "500ml"}, globalTradeItemNumber="8801043022781")
        attrs = [(a["attributeTypeName"], a["attributeValueName"]) for a in payload["items"][0]["attributes"]]
        self.assertEqual(attrs, [("총 수량", "20개"), ("개당 용량", "500ml"), ("Global Trade Item Number", "8801043022781")])

    def test_per_item_option_attributes_also_get_units(self):
        required = _base_live_required_fields()
        required["items"] = [{"itemName": "20개입", "externalVendorSku": "SKU-1", "optionAttributes": {"총 수량": "20", "개당 용량": "500"}}]
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"}, required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(
                purchase_option_field_definitions=DEFINITIONS,
            ),
        )
        self.assertEqual(blockers, [])
        attrs = {a["attributeTypeName"]: a["attributeValueName"] for a in payload["items"][0]["attributes"]}
        self.assertEqual((attrs["총 수량"], attrs["개당 용량"]), ("20개", "500ml"))


if __name__ == "__main__":
    unittest.main()
