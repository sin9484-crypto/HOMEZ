"""Coupang read-only logistics lookup and cache security contracts."""

import unittest

from app.domains.marketplace_listing.coupang_logistics_provider import (
    CoupangLogisticsProvider,
    LogisticsLocation,
    cache_locations,
    get_cached_location,
)


class CoupangLogisticsProviderTest(unittest.TestCase):
    def _provider(self):
        return CoupangLogisticsProvider({
            "vendor_id": "VENDOR", "access_key": "ACCESS", "secret_key": "SECRET",
        })

    def test_outbound_paginates_and_normalizes(self):
        provider = self._provider()
        responses = iter([
            {"content": [{"outboundShippingPlaceCode": "OUT-1", "shippingPlaceName": "출고지 A", "usable": True}], "pagination": {"totalPages": 2}},
            {"content": [{"outboundShippingPlaceCode": "OUT-2", "shippingPlaceName": "출고지 B", "usable": False}], "pagination": {"totalPages": 2}},
        ])
        provider._request = lambda _path, _params: next(responses)
        items = provider.list_outbound_shipping_places()
        self.assertEqual([item.code for item in items], ["OUT-1", "OUT-2"])
        self.assertTrue(items[0].usable)
        self.assertFalse(items[1].usable)

    def test_return_public_response_excludes_private_address_and_contact(self):
        provider = self._provider()
        provider._request = lambda _path, _params: {"data": {
            "content": [{
                "returnCenterCode": "RET-1", "returnChargeName": "반품지",
                "companyContactNumber": "02-1234-5678", "returnZipCode": "01234",
                "returnAddress": "비공개 주소", "returnAddressDetail": "상세", "usable": True,
            }],
            "pagination": {"totalPages": 1},
        }}
        item = provider.list_return_shipping_centers()[0]
        public = item.public_dict("return_center_code")
        self.assertEqual(public, {
            "return_center_code": "RET-1", "shipping_place_name": "반품지", "usable": True,
        })
        self.assertNotIn("companyContactNumber", public)
        self.assertNotIn("returnAddress", public)

    def test_return_supports_official_data_array_response(self):
        provider = self._provider()
        provider._request = lambda _path, _params: {
            "code": 200,
            "message": "SUCCESS",
            "data": [{
                "returnCenterCode": "RET-OFFICIAL-1",
                "shippingPlaceName": "공식 반품지",
                "companyContactNumber": "02-1234-5678",
                "returnAddress": "비공개 주소",
                "usable": True,
            }],
        }

        item = provider.list_return_shipping_centers()[0]

        self.assertEqual(item.code, "RET-OFFICIAL-1")
        self.assertEqual(item.name, "공식 반품지")
        self.assertNotIn("returnAddress", item.public_dict("return_center_code"))

    def test_cache_isolated_by_company_and_wizard(self):
        item = LogisticsLocation(code="RET-X", name="반품지", usable=True)
        cache_locations(10, 20, "return", [item])
        self.assertIsNotNone(get_cached_location(10, 20, "return", "RET-X"))
        self.assertIsNone(get_cached_location(11, 20, "return", "RET-X"))
        self.assertIsNone(get_cached_location(10, 21, "return", "RET-X"))
        self.assertIsNone(get_cached_location(10, 20, "outbound", "RET-X"))


if __name__ == "__main__":
    unittest.main()
