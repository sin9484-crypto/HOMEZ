"""
=========================================================
Homez OS

File : tests/test_coupang_channel_control_provider.py

2026-10-05 — 쿠팡 판매중지·주문 취소 Provider의 HTTP 계약과 오류 분류 검증(Fake HTTP,
외부 호출 없음). 요청 모양은 공식 명세(판매중지 PUT …/sales/stop 본문 없음, 주문 취소 POST
…/orders/{orderId}/cancel 본문 CANERR/CCTTER)를 기준으로 하고, 오류 분류는
"요청이 적용되지 않았음이 확실한가"를 기준으로 한다.
=========================================================
"""

import json
import unittest

import requests

from app.domains.purchase_task.coupang_channel_control_provider import (
    CallKind, CoupangChannelControlProvider,
)

CRED = {
    "access_key": "AK-TEST", "secret_key": "SK-TEST", "vendor_id": "A00012345",
    "wing_user_id": "wing-user",
}


def _response(status, body=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = (
        json.dumps(body).encode("utf-8") if body is not None else b"not json"
    )
    response.headers.update(headers or {})
    return response


class FakeTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json_body})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider(*outcomes, credential=None):
    transport = FakeTransport(*outcomes)
    return CoupangChannelControlProvider(credential or CRED, transport=transport), transport


class RequestShapeTestCase(unittest.TestCase):

    def test_sale_stop_is_a_bodyless_put_on_the_vendor_item_with_signed_header(self):
        provider, transport = _provider(_response(200, {"code": "SUCCESS", "message": "ok"}))
        result = provider.stop_vendor_item_sale("5469001088")
        self.assertEqual(result.kind, CallKind.SUCCEEDED)
        call = transport.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertTrue(call["url"].startswith("https://api-gateway.coupang.com/"))
        self.assertTrue(call["url"].endswith(
            "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/5469001088/sales/stop"))
        self.assertIsNone(call["body"])
        self.assertTrue(call["headers"]["Authorization"].startswith("CEA algorithm=HmacSHA256"))
        self.assertNotIn("SK-TEST", json.dumps(call["headers"]))

    def test_order_cancel_posts_the_documented_body_for_one_shipment_box(self):
        provider, transport = _provider(_response(200, {
            "code": "200", "message": "Success",
            "data": {"receiptMap": {"receiptId": 1}, "orderId": 2000006593044, "failedItemIds": []},
        }))
        result = provider.cancel_order_items(
            order_id="2000006593044", vendor_item_ids=["3145181064", "3145181065"],
            receipt_counts=[1, 2],
        )
        self.assertEqual(result.kind, CallKind.SUCCEEDED)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith(
            "/v2/providers/openapi/apis/api/v5/vendors/A00012345/orders/2000006593044/cancel"))
        self.assertEqual(call["body"], {
            "orderId": 2000006593044, "vendorItemIds": [3145181064, 3145181065],
            "receiptCounts": [1, 2], "bigCancelCode": "CANERR", "middleCancelCode": "CCTTER",
            "vendorId": "A00012345", "userId": "wing-user",
        })

    def test_missing_wing_user_id_never_calls_and_is_never_guessed(self):
        credential = {k: v for k, v in CRED.items() if k != "wing_user_id"}
        provider, transport = _provider(_response(200, {}), credential=credential)
        result = provider.cancel_order_items(
            order_id="2000006593044", vendor_item_ids=["1"], receipt_counts=[1],
        )
        self.assertEqual(result.kind, CallKind.ACTION_REQUIRED)
        self.assertEqual(result.error_class, "MISSING_WING_USER_ID")
        self.assertEqual(transport.calls, [])

    def test_malformed_targets_are_rejected_before_any_call(self):
        provider, transport = _provider(_response(200, {}))
        for bad in ("", "VI-1", "12a", None):
            self.assertEqual(
                provider.stop_vendor_item_sale(bad).error_class, "INVALID_TARGET", repr(bad))
        for args in (
            dict(order_id="CPG-1", vendor_item_ids=["1"], receipt_counts=[1]),
            dict(order_id="1", vendor_item_ids=[], receipt_counts=[]),
            dict(order_id="1", vendor_item_ids=["1", "2"], receipt_counts=[1]),
            dict(order_id="1", vendor_item_ids=["1"], receipt_counts=[0]),
            dict(order_id="1", vendor_item_ids=["x"], receipt_counts=[1]),
        ):
            self.assertEqual(
                provider.cancel_order_items(**args).error_class, "INVALID_TARGET", args)
        self.assertEqual(transport.calls, [])


class ErrorClassificationTestCase(unittest.TestCase):

    def _stop(self, outcome):
        provider, _ = _provider(outcome)
        return provider.stop_vendor_item_sale("5469001088")

    def test_request_permission_and_target_errors_require_action_and_are_not_retryable(self):
        for status, expected in ((400, "BAD_REQUEST"), (401, "AUTH"), (403, "AUTH"),
                                 (404, "NOT_FOUND"), (409, "BAD_REQUEST"), (302, "REDIRECT_BLOCKED")):
            result = self._stop(_response(status, {"code": status, "message": "x"}))
            self.assertEqual(result.kind, CallKind.ACTION_REQUIRED, status)
            self.assertEqual(result.error_class, expected, status)

    def test_only_rate_limit_is_retryable_and_honours_retry_after(self):
        result = self._stop(_response(429, {"code": 429}, {"Retry-After": "120"}))
        self.assertEqual(result.kind, CallKind.RETRYABLE)
        self.assertEqual(result.retry_after_seconds, 120)
        self.assertEqual(self._stop(_response(429, {})).retry_after_seconds, None)

    def test_server_errors_timeouts_and_network_failures_are_unknown_not_failures(self):
        for outcome in (
            _response(500, {"code": 500}), _response(502, None), _response(503, {}),
            requests.Timeout("t"), requests.ConnectionError("c"),
        ):
            result = self._stop(outcome)
            self.assertEqual(result.kind, CallKind.UNKNOWN, repr(outcome))

    def test_200_with_unreadable_or_unexpected_body_is_unknown(self):
        for outcome in (_response(200, None), _response(200, {}), _response(200, {"code": "WAT"}),
                        _response(200, [1])):
            self.assertEqual(self._stop(outcome).kind, CallKind.UNKNOWN, repr(outcome))

    def test_200_with_explicit_error_code_is_a_definite_rejection(self):
        result = self._stop(_response(200, {"code": "ERROR", "message": "nope"}))
        self.assertEqual((result.kind, result.error_class), (CallKind.ACTION_REQUIRED, "REJECTED"))

    def test_cancel_partial_failure_is_not_success_and_not_retryable(self):
        provider, _ = _provider(_response(200, {
            "code": "200", "data": {"orderId": 1, "failedItemIds": [2]}}))
        result = provider.cancel_order_items(order_id="1", vendor_item_ids=["1", "2"],
                                             receipt_counts=[1, 1])
        self.assertEqual((result.kind, result.error_class),
                         (CallKind.ACTION_REQUIRED, "PARTIAL_FAILURE"))

    def test_cancel_400_status_error_requires_action(self):
        provider, _ = _provider(_response(400, {
            "code": "400", "message": "Order status is not Payment Completed",
            "data": {"failedVendorItemIds": [1]}}))
        result = provider.cancel_order_items(order_id="1", vendor_item_ids=["1"], receipt_counts=[1])
        self.assertEqual(result.kind, CallKind.ACTION_REQUIRED)
        self.assertIn("Payment Completed", result.detail)

    def test_error_detail_never_echoes_credentials(self):
        result = self._stop(_response(401, {"code": 401, "message": "bad key"}))
        for secret in ("AK-TEST", "SK-TEST", "wing-user"):
            self.assertNotIn(secret, result.detail)


class ReadReconciliationTestCase(unittest.TestCase):

    def test_on_sale_read(self):
        for on_sale in (True, False):
            provider, transport = _provider(_response(200, {
                "code": "SUCCESS", "data": {"sellerItemId": 1, "onSale": on_sale}}))
            read = provider.read_vendor_item_on_sale("5469001088")
            self.assertTrue(read.ok)
            self.assertIs(read.value, on_sale)
            self.assertEqual(transport.calls[0]["method"], "GET")
            self.assertTrue(transport.calls[0]["url"].endswith("/vendor-items/5469001088/inventories"))

    def test_unreadable_responses_are_not_values(self):
        for outcome in (_response(500, {}), _response(404, {}), _response(200, None),
                        _response(200, {"code": "SUCCESS", "data": {"onSale": "yes"}}),
                        requests.Timeout("t")):
            provider, _ = _provider(outcome)
            read = provider.read_vendor_item_on_sale("5469001088")
            self.assertFalse(read.ok, repr(outcome))
            self.assertIsNone(read.value)

    def test_order_cancel_state_requires_every_requested_item(self):
        def sheet(*items):
            return _response(200, {"code": 200, "data": {"orderItems": list(items)}})

        provider, _ = _provider(sheet(
            {"vendorItemId": 11, "shippingCount": 1, "cancelCount": 1},
            {"vendorItemId": 12, "shippingCount": 2, "cancelCount": 0, "canceled": False},
        ))
        self.assertFalse(provider.read_order_items_cancelled("642538970006401429", ["11", "12"]).value)
        provider, _ = _provider(sheet(
            {"vendorItemId": 11, "shippingCount": 1, "cancelCount": 1},
            {"vendorItemId": 12, "shippingCount": 2, "cancelCount": 2},
        ))
        self.assertTrue(provider.read_order_items_cancelled("642538970006401429", ["11", "12"]).value)
        provider, _ = _provider(sheet({"vendorItemId": 11, "canceled": True}))
        self.assertTrue(provider.read_order_items_cancelled("642538970006401429", ["11"]).value)
        # 요청 품목이 응답에 없으면 값이 아니다
        provider, _ = _provider(sheet({"vendorItemId": 99, "canceled": True}))
        read = provider.read_order_items_cancelled("642538970006401429", ["11"])
        self.assertFalse(read.ok)
        self.assertIsNone(read.value)


if __name__ == "__main__":
    unittest.main()
