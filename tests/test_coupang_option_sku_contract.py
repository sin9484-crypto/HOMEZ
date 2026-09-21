"""
=========================================================
Homez OS

File : tests/test_coupang_option_sku_contract.py

2026-09-21 옵션 등록·연결 완성 — 쿠팡에 등록하는 옵션의 `externalVendorSku`는
이후 주문 수집에서 `channel_sku`로 돌아와 HOMEZ SKU·공급처 옵션 연결의 조인
키가 된다(app/domains/order/coupang_normalizer.py: 주문 품목의
`externalVendorSkuCode`를 strip한 값, 비어 있으면 vendorItemId로 대체).
따라서 등록 시점에 빈 값·공백이 붙은 값·150자 초과 값을 막아야 한다.
스키마 주석은 "빈 값 불허(min_length=1), 최대 150자"라고 적고 있었지만 실제
필드에는 제약이 없었다(수정 전 재현: '' / '   ' 모두 통과).

실제 쿠팡·온채널 호출 없음.
=========================================================
"""

import unittest

from pydantic import ValidationError

from app.domains.marketplace_listing.coupang_submission_contract import (
    validate_coupang_submission_contract,
)
from app.domains.marketplace_listing.required_fields_schemas import (
    CoupangSellerFulfilledItem,
)
from app.domains.order.coupang_normalizer import normalize_coupang_order_item
from tests.test_coupang_submission_contract import DRAFT
from tests.test_coupang_submission_contract import NOTICE_ATTRIBUTES
from tests.test_coupang_submission_contract import _valid_required_fields


class RegisteredSkuMustBeAJoinKeyTestCase(unittest.TestCase):

    def test_blank_and_whitespace_only_sku_are_rejected(self):
        for value in ("", "   ", "\t"):
            with self.subTest(sku=repr(value)):
                with self.assertRaises(ValidationError):
                    CoupangSellerFulfilledItem(itemName="옵션", externalVendorSku=value)

    def test_padded_sku_is_rejected_because_order_side_strips_it(self):
        """주문 정규화가 externalVendorSkuCode를 strip하므로 공백이 붙은 SKU로
        등록하면 주문의 channel_sku와 영원히 일치하지 않는다."""

        for value in (" SKU-1", "SKU-1 ", " SKU-1 "):
            with self.subTest(sku=repr(value)):
                with self.assertRaises(ValidationError):
                    CoupangSellerFulfilledItem(itemName="옵션", externalVendorSku=value)

    def test_sku_longer_than_150_chars_is_rejected(self):
        CoupangSellerFulfilledItem(itemName="옵션", externalVendorSku="A" * 150)
        with self.assertRaises(ValidationError):
            CoupangSellerFulfilledItem(itemName="옵션", externalVendorSku="A" * 151)

    def test_registered_sku_round_trips_to_the_order_channel_sku(self):
        registered = CoupangSellerFulfilledItem(
            itemName="샴푸 200ml", externalVendorSku="HOMEZ-SH-200",
        ).externalVendorSku
        order_item = normalize_coupang_order_item({
            "sequenceNo": "1", "vendorItemId": 9001,
            "externalVendorSkuCode": registered,
            "vendorItemName": "샴푸 200ml", "shippingCount": 1,
            "salesPrice": {"currencyCode": "KRW", "units": 7000, "nanos": 0},
            "orderPrice": {"currencyCode": "KRW", "units": 7000, "nanos": 0},
        })
        self.assertEqual(order_item.channel_sku, registered)


class LegacyDraftWithBlankSkuIsCaughtByContractTestCase(unittest.TestCase):
    """스키마를 거치지 않은 옛 초안 JSON에도 등록 전 계약 검증이 SKU를 요구한다."""

    def _blocking_codes(self, items):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(items=items),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        return result.blocking_codes

    def test_missing_blank_or_padded_sku_blocks_submission(self):
        for items in (
            [{"itemName": "기본"}],
            [{"itemName": "기본", "externalVendorSku": ""}],
            [{"itemName": "기본", "externalVendorSku": "   "}],
            [{"itemName": "기본", "externalVendorSku": " SKU-1"}],
        ):
            with self.subTest(items=items):
                self.assertIn("SKU_REQUIRED", self._blocking_codes(items))

    def test_valid_sku_does_not_add_the_issue(self):
        self.assertNotIn(
            "SKU_REQUIRED",
            self._blocking_codes([{"itemName": "기본", "externalVendorSku": "SKU-1"}]),
        )


if __name__ == "__main__":
    unittest.main()
