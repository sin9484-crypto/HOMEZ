"""
=========================================================
Homez OS

File : tests/test_diagnostics_export.py

Gate Y-5(2026-08-12) — 진단 내보내기 검증. 핵심 요구사항: 실제
비밀정보(SECRET_KEY 등)가 절대 결과에 포함되지 않는다는 것을
증명한다. 전부 임시 SQLite 파일/로그 파일만 사용한다.
=========================================================
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.guard import admin_guard
from app.domains.diagnostics import router as diagnostics_router_module
from app.domains.diagnostics.redaction import KNOWN_SECRET_SETTINGS_FIELDS
from app.domains.diagnostics.redaction import REDACTED_PLACEHOLDER
from app.domains.diagnostics.redaction import known_secret_values
from app.domains.diagnostics.redaction import redact_text
from app.domains.diagnostics.service import build_diagnostics_bundle


def _fake_settings(**overrides) -> SimpleNamespace:

    base = {name: f"realvalue-{name.lower()}-0123456789" for name in KNOWN_SECRET_SETTINGS_FIELDS}
    base.update(overrides)

    return SimpleNamespace(**base)


class RedactionTestCase(unittest.TestCase):

    def test_known_secret_values_extracted_from_settings(self):

        settings = _fake_settings()
        values = known_secret_values(settings)

        self.assertEqual(len(values), len(KNOWN_SECRET_SETTINGS_FIELDS))
        self.assertIn("realvalue-secret_key-0123456789", values)

    def test_short_default_values_excluded(self):

        settings = _fake_settings(SECRET_KEY="dev")
        values = known_secret_values(settings)

        self.assertNotIn("dev", values)

    def test_redact_text_removes_known_secret_verbatim(self):

        settings = _fake_settings()
        secret_value = settings.SECRET_KEY

        text = f"boot ok, SECRET_KEY={secret_value}, continuing"
        redacted = redact_text(text, known_values=known_secret_values(settings))

        self.assertNotIn(secret_value, redacted)
        self.assertIn(REDACTED_PLACEHOLDER, redacted)

    def test_redact_text_catches_generic_key_value_patterns(self):

        text = 'login attempt: password="hunter2-very-secret" failed'
        redacted = redact_text(text)

        self.assertNotIn("hunter2-very-secret", redacted)
        self.assertIn(REDACTED_PLACEHOLDER, redacted)

    def test_redact_text_does_not_mangle_unrelated_text(self):

        text = "정상적으로 서버가 시작되었습니다. 포트: 8000"
        redacted = redact_text(text)

        self.assertEqual(text, redacted)

    def test_empty_text_returns_empty(self):

        self.assertEqual(redact_text(""), "")
        self.assertIsNone(redact_text(None))


class DiagnosticsBundleTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_diag_test_"))
        self.db_path = self.tmp_dir / "app.db"
        self.migrations_dir = self.tmp_dir / "migrations"
        self.migrations_dir.mkdir()

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_bundle_contains_expected_top_level_keys(self):

        bundle = build_diagnostics_bundle(
            db_path=self.db_path,
            migrations_dir=self.migrations_dir,
            route_count=42,
        )

        for key in (
            "generated_at", "app_version", "os", "database",
            "migrations", "route_count", "log_tail",
        ):
            self.assertIn(key, bundle)

        self.assertEqual(bundle["route_count"], 42)
        self.assertTrue(bundle["database"]["exists"])
        self.assertEqual(
            bundle["database"]["integrity_check_result"], "ok",
        )

    def test_missing_db_reported_without_crash(self):

        bundle = build_diagnostics_bundle(
            db_path=self.tmp_dir / "does_not_exist.db",
            migrations_dir=self.migrations_dir,
            route_count=1,
        )

        self.assertFalse(bundle["database"]["exists"])
        self.assertIsNone(bundle["database"]["integrity_check_result"])
        self.assertEqual(bundle["migrations"]["status"], "db_not_found")

    def test_log_tail_is_none_when_no_log_path_given(self):

        bundle = build_diagnostics_bundle(
            db_path=self.db_path,
            migrations_dir=self.migrations_dir,
            route_count=1,
            log_path=None,
        )

        self.assertIsNone(bundle["log_tail"])

    def test_log_tail_limited_to_requested_lines(self):

        log_path = self.tmp_dir / "app.log"
        log_path.write_text(
            "\n".join(f"line {i}" for i in range(500)),
            encoding="utf-8",
        )

        bundle = build_diagnostics_bundle(
            db_path=self.db_path,
            migrations_dir=self.migrations_dir,
            route_count=1,
            log_path=log_path,
            log_tail_lines=10,
        )

        tail_lines = bundle["log_tail"].splitlines()
        self.assertEqual(len(tail_lines), 10)
        self.assertIn("line 499", bundle["log_tail"])
        self.assertNotIn("line 0\n", bundle["log_tail"])

    # ------------------------------------------------
    # 핵심: 실제 비밀정보가 로그에 있어도 결과에 남지 않는다
    # ------------------------------------------------

    def test_secrets_in_log_are_never_exposed_in_bundle(self):

        settings = _fake_settings()
        real_secret = settings.JWT_SECRET_KEY

        log_path = self.tmp_dir / "app.log"
        log_path.write_text(
            "\n".join(
                [
                    "서버 시작",
                    f"디버그: JWT_SECRET_KEY={real_secret}",
                    'password: "user-typed-this-password-123"',
                    f"Authorization: Bearer {real_secret}",
                    "정상 종료",
                ],
            ),
            encoding="utf-8",
        )

        bundle = build_diagnostics_bundle(
            db_path=self.db_path,
            migrations_dir=self.migrations_dir,
            route_count=1,
            log_path=log_path,
            settings=settings,
        )

        serialized = str(bundle)

        self.assertNotIn(real_secret, serialized)
        self.assertNotIn("user-typed-this-password-123", serialized)
        self.assertIn(REDACTED_PLACEHOLDER, bundle["log_tail"])

    def test_all_nine_known_secret_fields_individually_redacted(self):
        """
        9개 필드 각각을 개별적으로(다른 필드는 짧은 기본값으로 둔 채)
        로그에 심어, 전부 빠짐없이 지워지는지 하나씩 확인한다 —
        하나라도 놓치면 이 테스트가 그 필드명을 특정해 실패한다.
        """

        for field_name in KNOWN_SECRET_SETTINGS_FIELDS:
            with self.subTest(field=field_name):

                unique_secret = f"{field_name}-unique-secret-value-999"
                settings = SimpleNamespace(
                    **{
                        name: (
                            unique_secret if name == field_name
                            else "short"
                        )
                        for name in KNOWN_SECRET_SETTINGS_FIELDS
                    },
                )

                log_path = self.tmp_dir / f"log_{field_name}.log"
                log_path.write_text(
                    f"value leaked: {unique_secret}",
                    encoding="utf-8",
                )

                bundle = build_diagnostics_bundle(
                    db_path=self.db_path,
                    migrations_dir=self.migrations_dir,
                    route_count=1,
                    log_path=log_path,
                    settings=settings,
                )

                self.assertNotIn(unique_secret, bundle["log_tail"])


# ----------------------------------------------------
# Router 계약
# ----------------------------------------------------


class DiagnosticsRouterContractTestCase(unittest.TestCase):

    def test_export_route_requires_admin_guard(self):
        # 2026-08-30 후속 지시로 /db-identity 라우트가 추가돼 더 이상
        # "정확히 1개"가 아니다 — 이 테스트의 진짜 의도(admin_guard
        # 없이는 어떤 진단 라우트도 없어야 한다)는 그대로 유지하면서,
        # 라우트 개수가 앞으로 더 늘어도 깨지지 않게 "전부"로 바꾼다.
        routes = diagnostics_router_module.router.routes
        self.assertGreaterEqual(len(routes), 1)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                admin_guard, calls,
                f"{route.path}가 admin_guard를 요구하지 않습니다.",
            )

    def test_response_schemas_have_no_secret_looking_fields(self):

        from app.domains.diagnostics.schema import DbIdentityResponse
        from app.domains.diagnostics.schema import DiagnosticsBundleResponse

        forbidden_substrings = ("secret", "password", "token", "api_key")

        for schema_cls in (DiagnosticsBundleResponse, DbIdentityResponse):
            field_names = set(schema_cls.model_fields.keys())
            for name in field_names:
                lowered = name.lower()
                for forbidden in forbidden_substrings:
                    self.assertNotIn(
                        forbidden,
                        lowered,
                        f"{schema_cls.__name__}.{name!r}가 비밀정보를 "
                        "암시하는 이름을 가지고 있습니다.",
                    )


if __name__ == "__main__":
    unittest.main()
