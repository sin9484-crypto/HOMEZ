"""
=========================================================
Homez OS

File : tests/test_v6_gate1_hardening.py

2026-08-03: HOMEZ V6 Gate 1 보완 사항 검증 — 운영 기본값 DEBUG=False,
CORS가 X-Auth-Error-Code만 명시적으로 노출하는지. 실제 homez.db는
사용하지 않는다.
=========================================================
"""

import unittest

from app.core.config import Settings


class DebugDefaultTestCase(unittest.TestCase):

    def test_debug_defaults_to_false(self):
        """
        .env/환경변수로 덮어쓰지 않은 순수 코드 기본값은 반드시 False여야
        한다 — True면 app/main.py의 FastAPI(debug=settings.DEBUG)가
        처리되지 않은 예외의 전체 스택 트레이스를 HTTP 응답에 그대로
        노출한다.
        """

        field = Settings.model_fields["DEBUG"]
        self.assertFalse(field.default)

    def test_app_main_wires_debug_flag_from_settings(self):

        import app.main as main_module

        self.assertIs(main_module.app.debug, main_module.settings.DEBUG)


class CorsExposeHeadersTestCase(unittest.TestCase):

    def test_x_auth_error_code_is_exposed_and_nothing_else(self):
        """
        app/core/auth.py 등이 401/403에 실어 보내는 X-Auth-Error-Code와
        (2026-08-07 Gate G부터) app/main.py의 Migration 제한 모드
        미들웨어가 423에 실어 보내는 X-Migration-Restricted-Code를
        브라우저 JS가 cross-origin에서도 읽을 수 있어야 한다(CORS
        expose_headers 미설정 시 브라우저가 응답 헤더를 숨긴다). 그 외
        헤더는 노출 목록에 없어야 한다(요구사항: "필요한 오류 코드
        헤더만 노출").
        """

        import app.main as main_module
        from starlette.middleware.cors import CORSMiddleware

        cors_entry = next(
            (
                m for m in main_module.app.user_middleware
                if m.cls is CORSMiddleware
            ),
            None,
        )
        self.assertIsNotNone(cors_entry, "CORSMiddleware가 등록돼 있지 않습니다.")

        expose_headers = cors_entry.kwargs.get("expose_headers")
        self.assertEqual(
            list(expose_headers),
            ["X-Auth-Error-Code", "X-Migration-Restricted-Code"],
        )


class CorsOriginRestrictionTestCase(unittest.TestCase):
    """
    2026-08-04 V6 Gate 1D: allow_origins=["*"] + allow_credentials=True
    조합(스펙상 이례적, CTO 반려 사유)을 제거하고 loopback(임의 포트)
    만 정규식으로 허용하는지 검증한다.
    """

    def _cors_kwargs(self):

        import app.main as main_module
        from starlette.middleware.cors import CORSMiddleware

        cors_entry = next(
            m for m in main_module.app.user_middleware
            if m.cls is CORSMiddleware
        )
        return cors_entry.kwargs

    def test_no_wildcard_allow_origins(self):

        kwargs = self._cors_kwargs()
        self.assertNotIn("*", kwargs.get("allow_origins", []))

    def test_loopback_origin_regex_matches_any_port(self):

        import re

        kwargs = self._cors_kwargs()
        pattern = kwargs.get("allow_origin_regex")
        self.assertIsNotNone(pattern)
        compiled = re.compile(pattern)

        for origin in (
            "http://127.0.0.1:61626",
            "http://127.0.0.1:18765",
            "http://localhost:3000",
            "https://127.0.0.1",
        ):
            self.assertIsNotNone(compiled.fullmatch(origin), f"{origin} should match")

    def test_loopback_origin_regex_rejects_external_and_null(self):

        import re

        kwargs = self._cors_kwargs()
        pattern = kwargs.get("allow_origin_regex")
        compiled = re.compile(pattern)

        for origin in (
            "https://evil.example.com",
            "null",
            "http://127.0.0.1.evil.com",
            "http://127.0.0.1:8080@evil.com",
        ):
            self.assertIsNone(compiled.fullmatch(origin), f"{origin} should NOT match")


if __name__ == "__main__":
    unittest.main()
