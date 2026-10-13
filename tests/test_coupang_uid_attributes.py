"""
=========================================================
Homez OS

File : tests/test_coupang_uid_attributes.py

상품 식별번호(UID: GTIN/MPN)를 쿠팡 `attributes`로 보내는 입력 경로(2026-10-11).

근거: 백산수(브랜드 상품) 등록이 "MPN/GTIN 속성이 누락되었거나 유효하지 않습니다"로 거절됐고,
이전에 통과한 상품(무브랜드)의 상세 조회에서 attributes에 `Global Trade Item Number`,
`Manufacturer Part Number`가 빈 값 슬롯으로 존재함을 확인했다. 값이 있을 때만 각 item의
attributes에 넣고, 없으면 아무것도 만들지 않는다. GTIN은 형식(자리수·체크디지트)만 검증한다.
=========================================================
"""

import unittest

from pydantic import ValidationError

from app.domains.marketplace_listing.coupang_live_payload import (
    build_coupang_live_payload,
)
from app.domains.marketplace_listing.required_fields_schemas import (
    CoupangSellerFulfilledFields,
)
from tests.test_coupang_live_submission import (
    _base_live_required_fields, _live_channel_policy_attributes,
)

DRAFT = {"product_name": "상품", "brand": "HOMEZ"}


def _build(**overrides):
    required = _base_live_required_fields(**overrides)
    return build_coupang_live_payload(
        draft=DRAFT, required_fields=required,
        channel_policy_attributes=_live_channel_policy_attributes(),
    )


def _attr_names(payload):
    return [a["attributeTypeName"] for a in payload["items"][0]["attributes"]]


class UidAttributePayloadTests(unittest.TestCase):

    def test_values_are_added_to_item_attributes_with_hidden_exposure(self):
        payload, blockers = _build(
            globalTradeItemNumber="8801043022781", manufacturerPartNumber="MPN-ABC-1",
        )
        self.assertEqual(blockers, [])
        attributes = {a["attributeTypeName"]: a for a in payload["items"][0]["attributes"]}
        gtin = attributes["Global Trade Item Number"]
        self.assertEqual(gtin["attributeValueName"], "8801043022781")
        self.assertEqual(gtin["exposed"], "NONE")
        self.assertEqual(attributes["Manufacturer Part Number"]["attributeValueName"], "MPN-ABC-1")

    def test_without_values_nothing_is_invented(self):
        payload, blockers = _build()
        self.assertEqual(blockers, [])
        names = _attr_names(payload)
        self.assertNotIn("Global Trade Item Number", names)
        self.assertNotIn("Manufacturer Part Number", names)

    def test_blank_values_add_nothing(self):
        payload, _ = _build(globalTradeItemNumber="   ", manufacturerPartNumber="")
        self.assertNotIn("Global Trade Item Number", _attr_names(payload))

    def test_shared_attribute_list_is_not_mutated_across_builds(self):
        first, _ = _build(globalTradeItemNumber="8801043022781")
        second, _ = _build()
        self.assertIn("Global Trade Item Number", _attr_names(first))
        self.assertNotIn("Global Trade Item Number", _attr_names(second))

    def test_barcode_field_stays_independent(self):
        payload, _ = _build(barcode="8801043022781", globalTradeItemNumber="8801043022781")
        item = payload["items"][0]
        self.assertEqual(item["barcode"], "8801043022781")
        self.assertFalse(item["emptyBarcode"])
        self.assertIn("Global Trade Item Number", [a["attributeTypeName"] for a in item["attributes"]])


class UidSchemaValidationTests(unittest.TestCase):

    def _fields(self, **overrides):
        return CoupangSellerFulfilledFields(**_base_live_required_fields(**overrides))

    def test_valid_gtins_pass(self):
        for gtin in ("8801043022781", "4006381333931", "96385074", "036000291452"):
            with self.subTest(gtin=gtin):
                self.assertEqual(self._fields(globalTradeItemNumber=gtin).globalTradeItemNumber, gtin)

    def test_bad_check_digit_length_or_letters_are_rejected(self):
        for bad in ("8801043022780", "880104302278", "88010430227AB", "1234567"):
            with self.subTest(gtin=bad), self.assertRaises(ValidationError):
                self._fields(globalTradeItemNumber=bad)

    def test_blank_gtin_becomes_none(self):
        self.assertIsNone(self._fields(globalTradeItemNumber="  ").globalTradeItemNumber)

    def test_mpn_length_limit_is_30(self):
        self.assertEqual(self._fields(manufacturerPartNumber="A" * 30).manufacturerPartNumber, "A" * 30)
        with self.assertRaises(ValidationError):
            self._fields(manufacturerPartNumber="A" * 31)


if __name__ == "__main__":
    unittest.main()
