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

    # ------------------------------------------------------------------
    # 2026-09-17 개인 베타 실데이터 검증 Phase 6 후속 — 실제 쿠팡 API가
    # 정확히 24시간 구간 요청을 400으로 거부한 것을 재현하고 고정한다.
    # 공식 문서: "An error occurs when searching for more than 24 hours"
    # 라고만 적혀 있었지만, 실제 서버는 "24시간과 같은" 구간도 거부했다
    # (경계값이 문서보다 엄격함 — 실제 응답으로 확정, 추측 아님).
    # ------------------------------------------------------------------

    def test_exactly_24_hour_window_is_rejected(self):
        transport = QueueTransport()
        provider = self.provider(transport)
        result = self.collect(
            provider,
            created_at_from=datetime(2026, 8, 31, 0, 0, tzinfo=KST),
            created_at_to=datetime(2026, 9, 1, 0, 0, tzinfo=KST),  # 정확히 24시간
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "TIME_RANGE_TOO_LARGE")
        self.assertEqual(transport.calls, [])  # 서버까지 가지 않고 클라이언트에서 차단

    def test_window_just_under_24_hours_is_allowed(self):
        transport = QueueTransport(response(payload={"code": 200, "data": []}))
        provider = self.provider(transport)
        result = self.collect(
            provider,
            created_at_from=datetime(2026, 8, 31, 0, 0, tzinfo=KST),
            created_at_to=datetime(2026, 8, 31, 23, 59, tzinfo=KST),  # 23시간 59분
        )
        self.assertTrue(result.success)
        self.assertEqual(len(transport.calls), 1)

    def test_reversed_time_range_is_rejected(self):
        transport = QueueTransport()
        provider = self.provider(transport)
        result = self.collect(
            provider,
            created_at_from=datetime(2026, 8, 31, 1, 0, tzinfo=KST),
            created_at_to=datetime(2026, 8, 31, 0, 0, tzinfo=KST),  # 종료가 시작보다 빠름
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "INVALID_TIME_RANGE")
        self.assertEqual(transport.calls, [])

    def test_equal_time_range_is_rejected(self):
        """시작=종료(구간 길이 0)도 역전과 동일하게 거부한다."""

        transport = QueueTransport()
        provider = self.provider(transport)
        same = datetime(2026, 8, 31, 0, 0, tzinfo=KST)
        result = self.collect(provider, created_at_from=same, created_at_to=same)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "INVALID_TIME_RANGE")
        self.assertEqual(transport.calls, [])

    def test_kst_offset_plus_sign_is_percent_encoded_exactly_once(self):
        """공식 문서 예시(`%2B09:00`)의 핵심 — `+`는 반드시 인코딩
        돼야 하고(안 하면 URL에서 공백으로 오해될 수 있음), 두 번
        인코딩돼 `%252B`가 되면 안 된다."""

        transport = QueueTransport(response(payload={"code": 200, "data": []}))
        result = self.collect(self.provider(transport))
        url = transport.calls[0][0]
        self.assertIn("%2B09", url)
        self.assertNotIn("%252B", url)
        self.assertNotIn("++", url)

    def test_first_page_omits_next_token_and_second_page_forwards_it(self):
        transport = QueueTransport(
            response(payload={"code": 200, "data": [], "nextToken": "N1"}),
            response(payload={"code": 200, "data": []}),
        )
        result = self.collect(self.provider(transport))
        self.assertTrue(result.success)
        self.assertNotIn("nextToken=", transport.calls[0][0])
        self.assertIn("nextToken=N1", transport.calls[1][0])

    def test_signed_query_and_sent_query_are_identical(self):
        """서명(Authorization 헤더 계산)과 실제 전송 URL이 같은
        query 문자열에서 나왔는지 확인한다 — 서명 대상과 실제 전송이
        어긋나면 실제 서버에서 401이 난다."""

        transport = QueueTransport(response(payload={"code": 200, "data": []}))
        self.collect(self.provider(transport))
        url, headers, _ = transport.calls[0]
        sent_query = url.split("?", 1)[1]
        # Authorization 헤더의 signature는 (signed_date+method+path+query)의
        # HMAC이다 — 같은 query로 직접 재계산해 두 값이 일치하는지 확인한다.
        import re

        from app.domains.store_connection.adapters.coupang_signing import (
            build_authorization_header,
        )

        m = re.search(r"signed-date=([^,]+)", headers["Authorization"])
        signed_date = m.group(1)
        expected = build_authorization_header(
            "AK", "SK", "GET", "/v2/providers/openapi/apis/api/v5/vendors/A1/ordersheets",
            sent_query, now=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )
        # signed-date는 now_factory가 고정돼 있으므로 헤더 전체가 그대로 일치해야 한다.
        self.assertEqual(headers["Authorization"], expected)

    def test_http_400_preserves_safe_provider_error_detail(self):
        """실패 응답의 code/message만 안전하게 남기는지 확인한다 —
        Authorization 헤더나 자격증명은 절대 이 필드에 들어가지
        않는다(애초에 응답 바디에 없으므로 구조적으로 불가능)."""

        transport = QueueTransport(response(
            400, payload={
                "code": "ERROR",
                "message": "endTime-startTime range should be less than 1 day",
            },
        ))
        result = self.collect(self.provider(transport))
        self.assertEqual(result.error_code, "BAD_REQUEST")
        self.assertIsNotNone(result.provider_error_detail)
        self.assertIn("less than 1 day", result.provider_error_detail)
        self.assertNotIn("SK", result.provider_error_detail)

    def test_non_json_error_body_does_not_crash(self):
        bad_response = response(400)
        bad_response.json.side_effect = ValueError("not json")
        transport = QueueTransport(bad_response)
        result = self.collect(self.provider(transport))
        self.assertEqual(result.error_code, "BAD_REQUEST")
        self.assertIsNone(result.provider_error_detail)

    def test_no_automatic_retry_after_failure(self):
        """실패 응답 1개만 큐에 넣는다 — 재시도하면 큐가 비어
        IndexError가 나서 테스트 자체가 실패한다(재시도가 없다는
        증거)."""

        transport = QueueTransport(response(500))
        result = self.collect(self.provider(transport))
        self.assertEqual(result.error_code, "PLATFORM_ERROR")
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
