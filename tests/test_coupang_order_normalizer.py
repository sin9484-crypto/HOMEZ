from __future__ import annotations

import unittest
from decimal import Decimal

from app.domains.order.coupang_normalizer import (
    CoupangOrderNormalizationError,
    normalize_coupang_order,
    normalize_money,
)


def money(units=1000, nanos=0, currency="KRW"):
    return {"currencyCode": currency, "units": units, "nanos": nanos}


def item(**overrides):
    value = {
        "sequenceNo": "001",
        "vendorItemId": 123,
        "externalVendorSkuCode": "SKU-1",
        "vendorItemName": "합성 상품",
        "shippingCount": 3,
        "holdCountForCancel": 1,
        "cancelCount": 1,
        "salesPrice": money(1000, 500_000_000),
        "orderPrice": money(3001, 500_000_000),
        "discountPrice": money(100),
        "sellerProductId": 456,
    }
    value.update(overrides)
    return value


def order(**overrides):
    value = {
        "shipmentBoxId": 11,
        "orderId": 22,
        "orderedAt": "2026-08-31T10:00:00+09:00",
        "status": "ACCEPT",
        "orderer": {"name": "합성주문자"},
        "receiver": {
            "name": "합성수취인",
            "safeNumber": "+82-10-0000-0000",
            "addr1": "합성시 합성구",
            "addr2": "1층",
            "postCode": "00000",
        },
        "orderItems": [item()],
    }
    value.update(overrides)
    return value


class CoupangOrderNormalizerTest(unittest.TestCase):
    def test_preserves_order_and_fulfillment_ids_separately(self):
        result = normalize_coupang_order(order())
        self.assertEqual(result.channel_order_id, "22")
        self.assertEqual(result.channel_fulfillment_id, "11")
        self.assertEqual(result.items[0].channel_item_id, "001")

    def test_available_quantity_excludes_hold_and_cancel(self):
        result = normalize_coupang_order(order())
        self.assertEqual(result.items[0].fulfillable_quantity, 1)
        self.assertTrue(result.items[0].fulfillable)

    def test_zero_available_quantity_is_preserved_but_not_fulfillable(self):
        result = normalize_coupang_order(
            order(orderItems=[item(shippingCount=2, holdCountForCancel=1, cancelCount=1)]),
        )
        self.assertEqual(result.items[0].fulfillable_quantity, 0)
        self.assertFalse(result.items[0].fulfillable)

    def test_decimal_units_and_nanos_are_exact(self):
        result = normalize_money(money(10, 125_000_000), "MONEY_INVALID")
        self.assertEqual(result.amount, Decimal("10.125"))

    def test_external_sku_falls_back_to_vendor_item_id(self):
        result = normalize_coupang_order(
            order(orderItems=[item(externalVendorSkuCode="")]),
        )
        self.assertEqual(result.items[0].channel_sku, "123")

    def test_missing_status_fails_closed(self):
        raw = order()
        del raw["status"]
        with self.assertRaises(CoupangOrderNormalizationError):
            normalize_coupang_order(raw)

    def test_empty_status_fails_closed(self):
        with self.assertRaises(CoupangOrderNormalizationError):
            normalize_coupang_order(order(status=""))

    def test_multiple_order_items_are_all_normalized(self):
        result = normalize_coupang_order(order(orderItems=[
            item(sequenceNo="001", vendorItemId=1),
            item(sequenceNo="002", vendorItemId=2),
        ]))
        self.assertEqual(len(result.items), 2)
        self.assertEqual(
            [i.channel_item_id for i in result.items], ["001", "002"],
        )

    def test_negative_or_inconsistent_quantity_fails_closed(self):
        for changed in (
            {"shippingCount": -1},
            {"shippingCount": 1, "holdCountForCancel": 2},
            {"cancelCount": 1.5},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(CoupangOrderNormalizationError):
                    normalize_coupang_order(order(orderItems=[item(**changed)]))

    def test_unsupported_currency_and_invalid_nanos_fail_closed(self):
        for value in (money(currency="USD"), money(nanos=1_000_000_000)):
            with self.subTest(value=value):
                with self.assertRaises(CoupangOrderNormalizationError):
                    normalize_money(value, "MONEY_INVALID")

    def test_missing_required_fields_do_not_echo_personal_data(self):
        source = order(receiver={"name": "PRIVATE-NAME"})
        with self.assertRaises(CoupangOrderNormalizationError) as caught:
            normalize_coupang_order(source)
        self.assertNotIn("PRIVATE-NAME", str(caught.exception))

    def test_datetime_requires_timezone(self):
        with self.assertRaisesRegex(
            CoupangOrderNormalizationError, "ORDERED_AT_TIMEZONE_REQUIRED",
        ):
            normalize_coupang_order(order(orderedAt="2026-08-31T10:00:00"))


if __name__ == "__main__":
    unittest.main()
