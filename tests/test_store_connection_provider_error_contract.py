"""
=========================================================
Homez OS

File : tests/test_store_connection_provider_error_contract.py

2026-08-04 V6 Gate 2C: 중앙 Provider 오류 분류·재시도 정책·backoff·
Secret 제거 헬퍼 검증. 실제 homez.db·외부 API는 사용하지 않는다.
=========================================================
"""

import unittest

from app.domains.store_connection.constants import ConnectionErrorCode
from app.domains.store_connection.provider_error_contract import (
    ProviderErrorCategory,
    classify_connection_error_code,
    compute_backoff_seconds,
    is_retryable_connection_error_code,
    normalize_http_status_to_connection_error_code,
    parse_retry_after_header,
    redact_secrets,
)


class ClassificationTestCase(unittest.TestCase):

    def test_credential_errors_classified_as_invalid_credential(self):

        for code in (
            ConnectionErrorCode.UNAUTHORIZED, ConnectionErrorCode.SECRET_MISMATCH,
            ConnectionErrorCode.CREDENTIAL_EXPIRED,
        ):
            self.assertEqual(
                classify_connection_error_code(code),
                ProviderErrorCategory.INVALID_CREDENTIAL,
            )

    def test_forbidden_and_ip_not_allowed_classified_as_permission_denied(self):

        self.assertEqual(
            classify_connection_error_code(ConnectionErrorCode.FORBIDDEN),
            ProviderErrorCategory.PERMISSION_DENIED,
        )
        self.assertEqual(
            classify_connection_error_code(ConnectionErrorCode.IP_NOT_ALLOWED),
            ProviderErrorCategory.PERMISSION_DENIED,
        )

    def test_rate_limited_platform_error_timeout_categories(self):

        self.assertEqual(
            classify_connection_error_code(ConnectionErrorCode.RATE_LIMITED),
            ProviderErrorCategory.RATE_LIMITED,
        )
        self.assertEqual(
            classify_connection_error_code(ConnectionErrorCode.PLATFORM_ERROR),
            ProviderErrorCategory.PROVIDER_UNAVAILABLE,
        )
        self.assertEqual(
            classify_connection_error_code(ConnectionErrorCode.TIMEOUT),
            ProviderErrorCategory.TIMEOUT,
        )

    def test_unknown_code_classified_as_invalid_response_not_retryable(self):

        self.assertEqual(
            classify_connection_error_code("SOMETHING_NEVER_SEEN"),
            ProviderErrorCategory.INVALID_RESPONSE,
        )
        self.assertFalse(is_retryable_connection_error_code("SOMETHING_NEVER_SEEN"))

    def test_credential_errors_never_retryable(self):
        """Credential 오류에는 자동 무한 재시도를 금지한다."""

        for code in (
            ConnectionErrorCode.UNAUTHORIZED, ConnectionErrorCode.SECRET_MISMATCH,
            ConnectionErrorCode.FORBIDDEN, ConnectionErrorCode.IP_NOT_ALLOWED,
        ):
            self.assertFalse(is_retryable_connection_error_code(code), code)

    def test_rate_limited_platform_error_timeout_are_retryable(self):

        for code in (
            ConnectionErrorCode.RATE_LIMITED, ConnectionErrorCode.PLATFORM_ERROR,
            ConnectionErrorCode.TIMEOUT,
        ):
            self.assertTrue(is_retryable_connection_error_code(code), code)


class HttpStatusMappingTestCase(unittest.TestCase):

    def test_known_statuses_map_correctly(self):

        cases = {
            400: ConnectionErrorCode.BAD_REQUEST,
            401: ConnectionErrorCode.UNAUTHORIZED,
            403: ConnectionErrorCode.FORBIDDEN,
            412: ConnectionErrorCode.PRECONDITION_FAILED,
            429: ConnectionErrorCode.RATE_LIMITED,
            500: ConnectionErrorCode.PLATFORM_ERROR,
            503: ConnectionErrorCode.PLATFORM_ERROR,
            599: ConnectionErrorCode.PLATFORM_ERROR,
        }
        for status, expected in cases.items():
            self.assertEqual(
                normalize_http_status_to_connection_error_code(status), expected, status,
            )

    def test_unmapped_status_returns_unknown(self):

        self.assertEqual(
            normalize_http_status_to_connection_error_code(418), ConnectionErrorCode.UNKNOWN,
        )


class BackoffTestCase(unittest.TestCase):

    def test_exponential_backoff_without_retry_after(self):

        self.assertEqual(compute_backoff_seconds(1, 1.0), 1.0)
        self.assertEqual(compute_backoff_seconds(2, 1.0), 2.0)
        self.assertEqual(compute_backoff_seconds(3, 1.0), 4.0)

    def test_retry_after_takes_precedence_over_exponential(self):

        self.assertEqual(compute_backoff_seconds(3, 1.0, retry_after_seconds=30.0), 30.0)

    def test_negative_retry_after_ignored(self):

        self.assertEqual(compute_backoff_seconds(2, 1.0, retry_after_seconds=-5.0), 2.0)


class RetryAfterHeaderParsingTestCase(unittest.TestCase):

    def test_parses_integer_seconds(self):

        self.assertEqual(parse_retry_after_header("30"), 30.0)

    def test_none_or_empty_returns_none(self):

        self.assertIsNone(parse_retry_after_header(None))
        self.assertIsNone(parse_retry_after_header(""))

    def test_http_date_format_ignored_not_guessed(self):
        """
        HTTP-date 형식(예: "Wed, 21 Oct 2026 07:28:00 GMT")은 초 단위로
        추측하지 않고 그대로 무시한다(exponential backoff로 안전하게
        폴백) — 값이 애매하면 추측하지 않는다는 이 도메인의 원칙과 같다.
        """

        self.assertIsNone(parse_retry_after_header("Wed, 21 Oct 2026 07:28:00 GMT"))

    def test_negative_value_ignored(self):

        self.assertIsNone(parse_retry_after_header("-5"))


class RedactSecretsTestCase(unittest.TestCase):

    def test_redacts_all_provided_secrets(self):

        text = "error calling with secret=abc123 and token=xyz789"
        result = redact_secrets(text, ["abc123", "xyz789"])

        self.assertNotIn("abc123", result)
        self.assertNotIn("xyz789", result)
        self.assertIn("[REDACTED]", result)

    def test_empty_secret_list_returns_text_unchanged(self):

        text = "no secrets here"
        self.assertEqual(redact_secrets(text, []), text)

    def test_falsy_secret_values_skipped_safely(self):

        text = "hello world"
        self.assertEqual(redact_secrets(text, ["", None, "world"]), "hello [REDACTED]")


if __name__ == "__main__":
    unittest.main()
