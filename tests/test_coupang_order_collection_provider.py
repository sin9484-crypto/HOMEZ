from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import requests

from app.domains.order.adapters.coupang_collection import (
    CoupangOrderCollectionProvider,
)


KST = timezone(timedelta(hours=9))


def response(status=200, payload=None, headers=None):
    item = Mock(spec=requests.Response)
    item.status_code = status
    item.headers = headers or {}
    item.json.return_value = payload
    return item


class QueueTransport:
    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def get(self, url, *, headers, timeout):
        self.calls.append((url, headers, timeout))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CoupangOrderCollectionProviderTest(unittest.TestCase):
    def provider(self, transport, max_pages=100):
        return CoupangOrderCollectionProvider(
            {"access_key": "AK", "secret_key": "SK", "vendor_id": "A1"},
            transport=transport, max_pages=max_pages,
            now_factory=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
        )

    def collect(self, provider, **overrides):
        values = {
            "created_at_from": datetime(2026, 8, 31, 0, 0, tzinfo=KST),
            "created_at_to": datetime(2026, 8, 31, 1, 0, tzinfo=KST),
            "status": "ACCEPT",
        }
        values.update(overrides)
        return provider.collect(**values)

    def test_empty_success(self):
        transport = QueueTransport(response(payload={"code": 200, "data": []}))
        result = self.collect(self.provider(transport))
        self.assertTrue(result.success)
        self.assertEqual(result.pages[0].orders, ())
        self.assertIn("/api/v5/vendors/A1/ordersheets?", transport.calls[0][0])
        self.assertIn("searchType=timeFrame", transport.calls[0][0])

    def test_multiple_pages(self):
        transport = QueueTransport(
            response(payload={"code": 200, "data": [{"orderId": 1}], "nextToken": "N1"}),
            response(payload={"code": 200, "data": [{"orderId": 2}]}),
        )
        result = self.collect(self.provider(transport))
        self.assertTrue(result.success)
        self.assertEqual(len(result.pages), 2)
        self.assertIn("nextToken=N1", transport.calls[1][0])

    def test_repeated_next_token_fails_closed(self):
        transport = QueueTransport(
            response(payload={"code": 200, "data": [], "nextToken": "N1"}),
            response(payload={"code": 200, "data": [], "nextToken": "N1"}),
        )
        result = self.collect(self.provider(transport))
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "PAGINATION_LOOP")

    def test_page_limit(self):
        transport = QueueTransport(
            response(payload={"code": 200, "data": [], "nextToken": "N1"}),
        )
        result = self.collect(self.provider(transport, max_pages=1))
        self.assertEqual(result.error_code, "PAGE_LIMIT_EXCEEDED")

    def test_rate_limit_preserves_retry_after(self):
        transport = QueueTransport(response(429, headers={"Retry-After": "17"}))
        result = self.collect(self.provider(transport))
        self.assertEqual(result.error_code, "RATE_LIMITED")
        self.assertEqual(result.retry_after_seconds, 17)

    def test_authentication_statuses_are_distinct(self):
        for status, code in ((401, "UNAUTHORIZED"), (403, "FORBIDDEN")):
            with self.subTest(status=status):
                result = self.collect(self.provider(QueueTransport(response(status))))
                self.assertEqual(result.error_code, code)

    def test_precondition_failure_is_distinct(self):
        result = self.collect(self.provider(QueueTransport(response(412))))
        self.assertEqual(result.error_code, "PRECONDITION_FAILED")

    def test_vendor_id_is_path_encoded_and_raw_orders_are_not_in_repr(self):
        transport = QueueTransport(response(payload={
            "code": 200, "data": [{"private": "DO-NOT-LOG"}],
        }))
        provider = CoupangOrderCollectionProvider(
            {"access_key": "AK", "secret_key": "SK", "vendor_id": "A/1?x"},
            transport=transport,
            now_factory=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
        )
        result = self.collect(provider)
        self.assertIn("/vendors/A%2F1%3Fx/ordersheets", transport.calls[0][0])
        self.assertNotIn("DO-NOT-LOG", repr(result))

    def test_timeout_and_network_error_are_distinct(self):
        timeout = self.collect(self.provider(QueueTransport(requests.Timeout())))
        network = self.collect(self.provider(QueueTransport(requests.ConnectionError())))
        self.assertEqual(timeout.error_code, "TIMEOUT")
        self.assertEqual(network.error_code, "NETWORK_ERROR")

    def test_malformed_response_is_not_logged_or_returned(self):
        result = self.collect(self.provider(QueueTransport(response(payload={"secret": "PII"}))))
        self.assertEqual(result.error_code, "INVALID_RESPONSE")
        self.assertNotIn("PII", repr(result))

    def test_time_range_and_status_validation_happen_before_transport(self):
        transport = QueueTransport()
        provider = self.provider(transport)
        too_large = self.collect(
            provider,
            created_at_to=datetime(2026, 9, 1, 0, 1, tzinfo=KST),
        )
        invalid_status = self.collect(provider, status="CANCELLED")
        naive = self.collect(
            provider, created_at_from=datetime(2026, 8, 31, 0, 0),
        )
        self.assertEqual(too_large.error_code, "TIME_RANGE_TOO_LARGE")
        self.assertEqual(invalid_status.error_code, "INVALID_STATUS")
        self.assertEqual(naive.error_code, "TIMEZONE_REQUIRED")
        self.assertEqual(transport.calls, [])

    def test_only_get_is_exposed_and_redirect_is_blocked(self):
        transport = QueueTransport(response(302))
        result = self.collect(self.provider(transport))
        self.assertEqual(result.error_code, "REDIRECT_BLOCKED")
        self.assertFalse(hasattr(self.provider(transport), "acknowledge"))


if __name__ == "__main__":
    unittest.main()
