"""
=========================================================
Homez OS

File : tests/test_store_connection_production_adapter.py

Gate 3(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5) — 쿠팡 실제 서명
Adapter contract test. 실제 네트워크를 전혀 호출하지 않는다(가짜
CoupangHttpTransport로 상태코드/예외만 주입) — "이 Gate에서는 실제
사용자 Credential을 사용하지 않는다"는 요구사항을 그대로 지킨다.

검증 대상:
1) 서명 생성이 공식 문서(developers.coupang.com, 2026-08-01 확인)와
   일치하는 형식/순서로 결정론적으로 나온다.
2) 200 성공, 400/401/403/412 즉시 실패, 429/5xx 재시도 후 성공/실패,
   timeout, 일반 URLError가 각각 올바르게 정규화된다.
3) 재시도는 429/5xx/timeout에서만 일어나고, 나머지는 즉시 중단한다.
4) Secret 원문이 예외 메시지·정규화된 오류 요약 어디에도 없다.
5) 소스 테스트용 fixture 레지스트리는 계속 Fake를 유지하고, 공식
   frozen 런타임용 production 레지스트리는 별도로 존재한다.
6) (2026-08-01 CTO 2차 지적 반영) 엔드포인트가 api/v6이고 searchType/
   status 쿼리를 포함하며, 서명에 쓰인 query와 실제 요청 URL의
   query가 byte-for-byte 동일하다.
7) 3xx 리다이렉트가 오면(다른 host 포함) Authorization을 재전송하지
   않고 즉시 실패한다.
=========================================================
"""

import inspect
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.domains.store_connection.adapters import ADAPTERS_BY_MARKETPLACE_CODE
from app.domains.store_connection.adapters.coupang_production import (
    BASE_URL,
    CoupangHttpTransport,
    CoupangProductionAdapter,
    _BlockRedirectHandler,
)
from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
    build_signing_message,
    compute_signature,
    format_signed_date,
)
from app.domains.store_connection.constants import ConnectionErrorCode

SECRET_KEY_VALUE = "PROD_ADAPTER_TEST_SECRET_KEY_xx99"
ACCESS_KEY_VALUE = "PROD_ADAPTER_TEST_ACCESS_KEY_yy88"

VALID_FIELDS = {
    "vendor_id": "A00999111", "access_key": ACCESS_KEY_VALUE,
    "secret_key": SECRET_KEY_VALUE,
}


class _FakeTransport(CoupangHttpTransport):
    """
    스크립트된 (status_or_exception, ...) 시퀀스를 순서대로 반환한다
    — 실제 urllib 호출을 하지 않는다.
    """

    def __init__(self, script):

        self.script = list(script)
        self.calls = []

    def request(self, url, headers, timeout):

        self.calls.append((url, headers, timeout))
        outcome = self.script.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        status = outcome
        return status, b"{}"


class CoupangSigningTestCase(unittest.TestCase):

    def test_signed_date_format_matches_official_yyMMddTHHmmssZ(self):

        now = datetime(2026, 8, 1, 12, 5, 30, tzinfo=timezone.utc)
        self.assertEqual(format_signed_date(now), "260801T120530Z")

    def test_message_is_datetime_method_path_query_no_separators(self):

        message = build_signing_message(
            "260801T120530Z", "get", "/v2/x", "?a=1",
        )
        self.assertEqual(message, "260801T120530ZGET/v2/x?a=1")

    def test_message_with_no_query_omits_it_cleanly(self):

        message = build_signing_message("260801T120530Z", "GET", "/v2/x")
        self.assertEqual(message, "260801T120530ZGET/v2/x")

    def test_signature_is_deterministic_hmac_sha256_hexdigest(self):

        sig1 = compute_signature("secret", "message")
        sig2 = compute_signature("secret", "message")
        self.assertEqual(sig1, sig2)
        self.assertEqual(len(sig1), 64)
        self.assertRegex(sig1, r"^[0-9a-f]{64}$")

    def test_different_secret_produces_different_signature(self):

        sig_a = compute_signature("secret-a", "message")
        sig_b = compute_signature("secret-b", "message")
        self.assertNotEqual(sig_a, sig_b)

    def test_authorization_header_matches_official_cea_format(self):

        now = datetime(2026, 8, 1, 12, 5, 30, tzinfo=timezone.utc)
        header = build_authorization_header(
            "AK123", "SK456", "GET", "/v2/x", "", now=now,
        )
        self.assertTrue(header.startswith(
            "CEA algorithm=HmacSHA256, access-key=AK123, "
            "signed-date=260801T120530Z, signature=",
        ))
        signature_part = header.split("signature=")[1]
        self.assertRegex(signature_part, r"^[0-9a-f]{64}$")

    def test_authorization_header_never_contains_secret_key_value(self):

        header = build_authorization_header(
            "AK123", SECRET_KEY_VALUE, "GET", "/v2/x", "",
        )
        self.assertNotIn(SECRET_KEY_VALUE, header)


class CoupangProductionAdapterTestCase(unittest.TestCase):

    def test_success_200_returns_success_true(self):

        transport = _FakeTransport([200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertTrue(result.success)
        self.assertIsNone(result.error_code)
        self.assertEqual(len(transport.calls), 1)

    def test_401_fails_immediately_without_retry(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 401, "unauthorized", {}, None),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.UNAUTHORIZED)
        self.assertEqual(len(transport.calls), 1)

    def test_403_fails_immediately_without_retry(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 403, "forbidden", {}, None),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.FORBIDDEN)
        self.assertEqual(len(transport.calls), 1)

    def test_429_retries_then_succeeds(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 429, "rate limited", {}, None),
            200,
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.coupang_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertTrue(result.success)
        self.assertEqual(len(transport.calls), 2)

    def test_429_exhausts_retries_and_fails(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 429, "rate limited", {}, None),
            urllib.error.HTTPError("url", 429, "rate limited", {}, None),
            urllib.error.HTTPError("url", 429, "rate limited", {}, None),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.coupang_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.RATE_LIMITED)
        self.assertEqual(len(transport.calls), 3)

    def test_5xx_retries_then_succeeds(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 503, "unavailable", {}, None),
            200,
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.coupang_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertTrue(result.success)

    def test_timeout_is_normalized_and_retried(self):

        transport = _FakeTransport([TimeoutError(), 200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.coupang_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertTrue(result.success)

    def test_generic_url_error_normalizes_to_platform_error(self):

        transport = _FakeTransport([
            urllib.error.URLError("connection refused"),
            urllib.error.URLError("connection refused"),
            urllib.error.URLError("connection refused"),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.coupang_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.PLATFORM_ERROR)

    def test_error_summary_never_contains_secret(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 401, "unauthorized", {}, None),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertNotIn(SECRET_KEY_VALUE, result.error_summary)
        self.assertNotIn(ACCESS_KEY_VALUE, result.error_summary)

    def test_request_headers_never_expose_secret_key_value(self):

        transport = _FakeTransport([200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        _url, headers, _timeout = transport.calls[0]
        self.assertNotIn(SECRET_KEY_VALUE, str(headers))

    def test_missing_credential_fields_raises_value_error(self):

        adapter = CoupangProductionAdapter(transport=_FakeTransport([200]))

        with self.assertRaises(ValueError):
            adapter.verify_connection({"vendor_id": "A001"})

    def test_400_fails_immediately_without_retry(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 400, "bad request", {}, None),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.BAD_REQUEST)
        self.assertEqual(len(transport.calls), 1)

    def test_412_fails_immediately_without_retry(self):

        transport = _FakeTransport([
            urllib.error.HTTPError("url", 412, "precondition failed", {}, None),
        ])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_code, ConnectionErrorCode.PRECONDITION_FAILED,
        )
        self.assertEqual(len(transport.calls), 1)

    def test_400_401_403_412_are_all_distinct_error_codes(self):
        """
        2026-08-01 CTO 2차 지적: "400/401/403/412/429/5xx/timeout 분리".
        네 개의 4xx 코드가 서로 다른 ConnectionErrorCode로 정규화되어
        하나의 뭉뚱그려진 코드로 섞이지 않는지 확인한다.
        """

        codes = {}
        for status in (400, 401, 403, 412):
            transport = _FakeTransport([
                urllib.error.HTTPError("url", status, "err", {}, None),
            ])
            adapter = CoupangProductionAdapter(transport=transport, timeout=1)
            result = adapter.verify_connection(VALID_FIELDS)
            codes[status] = result.error_code

        self.assertEqual(len(set(codes.values())), 4, codes)


class CoupangProductionEndpointTestCase(unittest.TestCase):
    """
    2026-08-01 CTO 2차 지적 반영 — 엔드포인트 버전(v4→v6)과 쿼리
    파라미터(searchType/status) 오류 수정 검증.
    """

    def test_request_uses_api_v6_path_not_v4(self):

        transport = _FakeTransport([200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        url, _headers, _timeout = transport.calls[0]
        self.assertIn("/v2/providers/openapi/apis/api/v6/vendors/", url)
        self.assertNotIn("/api/v4/", url)

    def test_request_url_starts_with_official_base_url(self):

        transport = _FakeTransport([200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        url, _headers, _timeout = transport.calls[0]
        self.assertTrue(url.startswith(BASE_URL))

    def test_request_query_includes_search_type_and_status(self):

        transport = _FakeTransport([200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        url, _headers, _timeout = transport.calls[0]
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query.get("searchType"), ["timeFrame"])
        self.assertEqual(query.get("status"), ["UC"])
        self.assertIn("createdAtFrom", query)
        self.assertIn("createdAtTo", query)

    def test_signed_query_and_request_url_query_are_byte_for_byte_identical(
        self,
    ):
        """
        서명 메시지에 들어간 query와 실제 요청 URL의 query가 정말로
        같은 문자열인지, 독립적으로 서명을 재계산해서 증명한다 —
        Authorization 헤더의 signature를 그대로 재현할 수 있어야
        "같은 query로 서명했다"는 증거가 된다.
        """

        transport = _FakeTransport([200])
        adapter = CoupangProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        url, headers, _timeout = transport.calls[0]
        parsed = urlparse(url)
        request_query = parsed.query
        path = parsed.path

        authorization = headers["Authorization"]
        signed_date = authorization.split("signed-date=")[1].split(",")[0]
        signature_from_header = authorization.split("signature=")[1]

        expected_message = build_signing_message(
            signed_date, "GET", path, request_query,
        )
        expected_signature = compute_signature(
            SECRET_KEY_VALUE, expected_message,
        )

        self.assertEqual(signature_from_header, expected_signature)


class CoupangRedirectBlockedTestCase(unittest.TestCase):
    """
    2026-08-01 CTO 2차 지적: "다른 host redirect 시 Authorization
    전달 차단". _BlockRedirectHandler는 host가 같든 다르든 모든
    리다이렉트를 즉시 차단한다(실제 공식 문서에 이 API가 리다이렉트를
    응답한다는 근거가 없으므로, 어떤 리다이렉트도 신뢰하지 않는다).
    """

    def test_redirect_to_different_host_raises_http_error(self):

        handler = _BlockRedirectHandler()
        req = urllib.request.Request(
            f"{BASE_URL}/v2/providers/openapi/apis/api/v6/vendors/A1/returnRequests",
            headers={"Authorization": "CEA algorithm=HmacSHA256, ..."},
        )

        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(
                req, None, 302, "Found", {},
                "https://evil.example.com/steal",
            )

    def test_redirect_to_same_host_also_raises_http_error(self):
        """
        같은 host라도 이 API는 리다이렉트를 문서화한 적이 없으므로
        신뢰하지 않는다 — "다른 host만" 막는 것이 아니라 전부 막는다
        (더 안전한 fail-closed 선택).
        """

        handler = _BlockRedirectHandler()
        req = urllib.request.Request(
            f"{BASE_URL}/v2/providers/openapi/apis/api/v6/vendors/A1/returnRequests",
            headers={"Authorization": "CEA algorithm=HmacSHA256, ..."},
        )

        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(
                req, None, 301, "Moved", {},
                f"{BASE_URL}/some/other/path",
            )

    def test_all_common_redirect_codes_are_blocked(self):

        handler = _BlockRedirectHandler()
        req = urllib.request.Request(f"{BASE_URL}/x")

        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(urllib.error.HTTPError):
                handler.redirect_request(
                    req, None, code, "Redirect", {},
                    "https://attacker.example.com/",
                )


class ProductionAdapterRuntimeRegistryTestCase(unittest.TestCase):
    """
    Source/test fixture와 공식 frozen 런타임의 production adapter를
    명시적으로 분리한다. 테스트 레지스트리가 외부 호출로 바뀌지 않는
    동시에, 설치본이 Fake 성공을 보고하지 않는 계약을 검증한다.
    """

    def test_registry_returns_fixture_adapter_not_production_adapter(self):

        adapter = ADAPTERS_BY_MARKETPLACE_CODE["COUPANG"]
        self.assertNotIsInstance(adapter, CoupangProductionAdapter)
        self.assertEqual(type(adapter).__name__, "CoupangConnectionAdapter")

    def test_adapters_init_module_defines_separate_production_registry(self):

        import app.domains.store_connection.adapters as adapters_pkg

        source = inspect.getsource(adapters_pkg)
        self.assertIn("PRODUCTION_ADAPTERS_BY_MARKETPLACE_CODE", source)
        self.assertIn("CoupangProductionAdapter", source)

    def test_service_module_does_not_import_production_adapter(self):

        import app.domains.store_connection.service as service_mod

        source = inspect.getsource(service_mod)
        self.assertNotIn("coupang_production", source)


if __name__ == "__main__":
    unittest.main()
