"""
=========================================================
Homez OS

File : tests/test_store_connection_naver_production_adapter.py

Gate 3(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5, CTO 2차 지적 반영) —
네이버 커머스API센터 OAuth2 Client Credentials 서명/Adapter contract
test. 실제 네트워크를 전혀 호출하지 않는다(가짜 NaverHttpTransport로
상태코드/응답 바디만 주입).

**중요**: 이 파일이 검증하는 것은 "코드가 사양대로 동작한다"는 것뿐이다
— 그 사양 자체가 1차 문서로 확인된 것이 아니라 사용자 제공 스펙 +
2차 소스 교차확인이라는 사실은 naver_signing.py/naver_production.py의
docstring과 최종 보고에 그대로 남아 있어야 한다.
=========================================================
"""

import inspect
import json
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs

import bcrypt

from app.domains.store_connection.adapters import ADAPTERS_BY_MARKETPLACE_CODE
from app.domains.store_connection.adapters.naver_production import (
    NaverHttpTransport,
    NaverProductionAdapter,
    _BlockRedirectHandler,
)
from app.domains.store_connection.adapters.naver_signing import (
    TOKEN_URL,
    compute_client_secret_sign,
    current_timestamp_ms,
)
from app.domains.store_connection.constants import ConnectionErrorCode

CLIENT_SECRET_SALT = bcrypt.gensalt().decode("ascii")
CLIENT_ID_VALUE = "PROD_NAVER_TEST_CLIENT_ID_zz11"

VALID_FIELDS = {
    "client_id": CLIENT_ID_VALUE, "client_secret": CLIENT_SECRET_SALT,
}


class _FakeTransport(NaverHttpTransport):

    def __init__(self, script):

        self.script = list(script)
        self.calls = []

    def request(self, url, data, headers, timeout):

        self.calls.append((url, data, headers, timeout))
        outcome = self.script.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        status, body = outcome
        return status, body


def _http_error(status: int, body: dict | None = None) -> urllib.error.HTTPError:

    payload = json.dumps(body or {}).encode("utf-8")
    exc = urllib.error.HTTPError("url", status, "err", {}, None)
    exc.fp = __import__("io").BytesIO(payload)
    exc.read = lambda: payload

    return exc


class NaverSigningTestCase(unittest.TestCase):

    def test_timestamp_is_millisecond_epoch_string(self):

        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts = current_timestamp_ms(now)

        self.assertTrue(ts.isdigit())
        self.assertEqual(int(ts), int(now.timestamp() * 1000))

    def test_sign_is_deterministic_for_same_inputs(self):

        ts = current_timestamp_ms()
        sign1 = compute_client_secret_sign("cid", CLIENT_SECRET_SALT, ts)
        sign2 = compute_client_secret_sign("cid", CLIENT_SECRET_SALT, ts)

        self.assertEqual(sign1, sign2)

    def test_sign_changes_with_different_timestamp(self):

        sign_a = compute_client_secret_sign("cid", CLIENT_SECRET_SALT, "1000")
        sign_b = compute_client_secret_sign("cid", CLIENT_SECRET_SALT, "2000")

        self.assertNotEqual(sign_a, sign_b)

    def test_sign_changes_with_different_client_id(self):

        sign_a = compute_client_secret_sign("cid-a", CLIENT_SECRET_SALT, "1000")
        sign_b = compute_client_secret_sign("cid-b", CLIENT_SECRET_SALT, "1000")

        self.assertNotEqual(sign_a, sign_b)

    def test_sign_is_valid_base64(self):

        import base64

        sign = compute_client_secret_sign("cid", CLIENT_SECRET_SALT, "1000")
        decoded = base64.b64decode(sign)  # raises if not valid base64

        self.assertTrue(len(decoded) > 0)

    def test_invalid_salt_format_raises_value_error_not_silently_succeed(self):

        with self.assertRaises(ValueError):
            compute_client_secret_sign(
                "cid", "not-a-valid-bcrypt-salt", "1000",
            )


class NaverProductionAdapterTestCase(unittest.TestCase):

    def test_success_200_returns_success_true(self):

        transport = _FakeTransport([(200, b'{"access_token": "x"}')])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertTrue(result.success)
        self.assertEqual(len(transport.calls), 1)

    def test_request_targets_official_token_url(self):

        transport = _FakeTransport([(200, b"{}")])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        url, _data, _headers, _timeout = transport.calls[0]
        self.assertEqual(url, TOKEN_URL)

    def test_request_body_is_form_urlencoded_with_required_fields(self):

        transport = _FakeTransport([(200, b"{}")])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        _url, data, headers, _timeout = transport.calls[0]
        self.assertEqual(
            headers["Content-Type"], "application/x-www-form-urlencoded",
        )
        parsed = parse_qs(data.decode("ascii"))
        self.assertEqual(parsed["grant_type"], ["client_credentials"])
        self.assertEqual(parsed["client_id"], [CLIENT_ID_VALUE])
        self.assertEqual(parsed["type"], ["SELF"])
        self.assertIn("timestamp", parsed)
        self.assertIn("client_secret_sign", parsed)

    def test_401_without_gw_authn_fails_immediately(self):

        transport = _FakeTransport([
            _http_error(401, {"code": "GW.WRONG_CLIENT"}),
        ])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.UNAUTHORIZED)
        self.assertEqual(len(transport.calls), 1)

    def test_401_with_gw_authn_retries_once_then_succeeds(self):

        transport = _FakeTransport([
            _http_error(401, {"code": "GW.AUTHN"}),
            (200, b'{"access_token": "x"}'),
        ])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertTrue(result.success)
        self.assertEqual(len(transport.calls), 2)

    def test_401_with_gw_authn_only_retries_once_not_infinitely(self):

        transport = _FakeTransport([
            _http_error(401, {"code": "GW.AUTHN"}),
            _http_error(401, {"code": "GW.AUTHN"}),
        ])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.UNAUTHORIZED)
        # 최초 시도 + GW.AUTHN 재발급 1회 = 정확히 2번, 무한 재시도 아님.
        self.assertEqual(len(transport.calls), 2)

    def test_429_retries_then_succeeds(self):

        transport = _FakeTransport([
            _http_error(429),
            (200, b"{}"),
        ])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.naver_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertTrue(result.success)

    def test_5xx_retries_then_fails_after_exhausting_attempts(self):

        transport = _FakeTransport([
            _http_error(503), _http_error(503), _http_error(503),
        ])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.naver_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.PLATFORM_ERROR)

    def test_timeout_is_normalized(self):

        transport = _FakeTransport([TimeoutError(), TimeoutError(), TimeoutError()])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        import app.domains.store_connection.adapters.naver_production as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None
        try:
            result = adapter.verify_connection(VALID_FIELDS)
        finally:
            mod.time.sleep = original_sleep

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.TIMEOUT)

    def test_invalid_client_secret_salt_normalizes_to_unauthorized(self):

        transport = _FakeTransport([(200, b"{}")])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection({
            "client_id": CLIENT_ID_VALUE, "client_secret": "not-bcrypt-salt",
        })

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, ConnectionErrorCode.UNAUTHORIZED)
        self.assertEqual(len(transport.calls), 0)

    def test_error_summary_never_contains_client_secret(self):

        transport = _FakeTransport([_http_error(401, {"code": "GW.WRONG"})])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        result = adapter.verify_connection(VALID_FIELDS)

        self.assertNotIn(CLIENT_SECRET_SALT, result.error_summary)

    def test_request_body_never_leaks_client_secret_raw(self):
        """
        요청 바디에는 client_secret_sign(이미 해시+Base64된 값)만
        들어가야 한다 — client_secret 원문 자체가 그대로 포함되면
        안 된다.
        """

        transport = _FakeTransport([(200, b"{}")])
        adapter = NaverProductionAdapter(transport=transport, timeout=1)

        adapter.verify_connection(VALID_FIELDS)

        _url, data, _headers, _timeout = transport.calls[0]
        self.assertNotIn(CLIENT_SECRET_SALT.encode("ascii"), data)

    def test_missing_credential_fields_raises_value_error(self):

        adapter = NaverProductionAdapter(transport=_FakeTransport([(200, b"{}")]))

        with self.assertRaises(ValueError):
            adapter.verify_connection({"client_id": "only-id"})


class NaverRedirectBlockedTestCase(unittest.TestCase):

    def test_redirect_to_different_host_raises_http_error(self):

        handler = _BlockRedirectHandler()
        req = urllib.request.Request(TOKEN_URL, method="POST")

        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(
                req, None, 302, "Found", {},
                "https://evil.example.com/steal",
            )


class NaverProductionAdapterRegistrySafetyTestCase(unittest.TestCase):

    def test_registry_returns_fixture_adapter_not_production_adapter(self):

        adapter = ADAPTERS_BY_MARKETPLACE_CODE["NAVER_SMARTSTORE"]
        self.assertNotIsInstance(adapter, NaverProductionAdapter)
        self.assertEqual(type(adapter).__name__, "NaverConnectionAdapter")

    def test_source_mode_returns_fixture_while_production_registry_is_explicit(self):

        import app.domains.store_connection.adapters as adapters_pkg

        self.assertEqual(adapters_pkg._adapter_mode(), "FAKE")
        self.assertEqual(
            type(adapters_pkg.get_adapter("NAVER_SMARTSTORE")).__name__,
            "NaverConnectionAdapter",
        )
        self.assertIsInstance(
            adapters_pkg.PRODUCTION_ADAPTERS_BY_MARKETPLACE_CODE["NAVER_SMARTSTORE"],
            NaverProductionAdapter,
        )

    def test_service_module_does_not_import_production_adapter(self):

        import app.domains.store_connection.service as service_mod

        source = inspect.getsource(service_mod)
        self.assertNotIn("naver_production", source)


if __name__ == "__main__":
    unittest.main()
