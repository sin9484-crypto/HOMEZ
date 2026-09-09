"""
=========================================================
Homez OS

File : tests/test_store_connection_security.py

판매채널 연결 — 보안 감사:
1) 로그인 ID·비밀번호 필드가 어디에도 없음(쿠팡/네이버 공식 API
   자격증명 필드만 존재)
2) Secret이 DB에 평문 저장되지 않음(credential_reference만 존재)
3) Secret이 API 응답에 없음
4) Secret이 audit_log에 없음
5) Secret이 예외/traceback에 노출되지 않음
6) Adapter가 네트워크 라이브러리를 import하지 않음
7) console.js가 Secret을 localStorage/sessionStorage에 저장하지 않음
8) 권한 없는 사용자가 자격증명을 변경할 수 없음(admin_guard 강제 확인)
=========================================================
"""

import inspect
import os
import sqlite3
import tempfile
import traceback
import unittest

from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ServiceUnavailableException
from app.core.guard import admin_guard
from app.core.windows_credential_store import (
    AlwaysFailingCredentialStore,
    InMemoryCredentialStore,
)
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.store_connection.model import StoreConnection
from app.domains.store_connection.schema import (
    StoreConnectionCreateRequest,
    StoreConnectionResponse,
    StoreConnectionVerifyRequest,
    StoreConnectionVerifyResponse,
)
from app.domains.store_connection.service import StoreConnectionService

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
    "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)

SECRET_ACCESS_KEY = "REAL_LOOKING_ACCESS_KEY_zzzz9999"
SECRET_SECRET_KEY = "REAL_LOOKING_SECRET_KEY_yyyy8888"

VALID_COUPANG_FIELDS = {
    "vendor_id": "A00123456", "access_key": SECRET_ACCESS_KEY,
    "secret_key": SECRET_SECRET_KEY,
}


class NoLoginFieldsTestCase(unittest.TestCase):
    """1) 로그인 ID·비밀번호 필드가 어디에도 없다."""

    def test_credential_fields_schemas_have_no_login_fields(self):

        from app.domains.store_connection.schema import (
            CoupangCredentialFields,
            NaverCredentialFields,
        )

        forbidden_substrings = ("login", "password", "아이디", "비밀번호")

        for schema_cls in (CoupangCredentialFields, NaverCredentialFields):
            for field_name in schema_cls.model_fields:
                lowered = field_name.lower()
                for forbidden in forbidden_substrings:
                    self.assertNotIn(
                        forbidden.lower(), lowered,
                        f"{schema_cls.__name__}.{field_name}가 로그인 관련 "
                        "필드처럼 보입니다.",
                    )

    def test_coupang_fields_are_exactly_vendor_access_secret(self):

        from app.domains.store_connection.schema import CoupangCredentialFields

        self.assertEqual(
            set(CoupangCredentialFields.model_fields.keys()),
            {"vendor_id", "access_key", "secret_key"},
        )

    def test_naver_fields_are_exactly_client_credentials(self):

        from app.domains.store_connection.schema import NaverCredentialFields

        self.assertEqual(
            set(NaverCredentialFields.model_fields.keys()),
            {"client_id", "client_secret", "account_id", "account_type"},
        )

    def test_response_schemas_have_no_secret_or_token_fields(self):

        forbidden = ("secret", "access_token", "authorization", "password")

        for schema_cls in (StoreConnectionResponse, StoreConnectionVerifyResponse):
            for field_name in schema_cls.model_fields:
                lowered = field_name.lower()
                for term in forbidden:
                    self.assertNotIn(
                        term, lowered,
                        f"{schema_cls.__name__}.{field_name}에 민감한 이름이 "
                        "있습니다.",
                    )


class DatabaseNoPlaintextSecretTestCase(unittest.TestCase):
    """2) Secret이 DB에 평문 저장되지 않는다 — credential_reference만 존재."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, StoreConnection.__table__],
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company = Company(
            name="테스트회사", business_number="123-45-67890", ceo="홍길동",
            phone="02-000-0000", email="a@b.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()

        self.credential_store = InMemoryCredentialStore()
        self.service = StoreConnectionService(self.db, self.credential_store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_store_connection_table_has_no_secret_columns(self):

        columns = {c.name for c in StoreConnection.__table__.columns}
        forbidden = {
            "access_key", "secret_key", "client_secret", "access_token",
            "password",
        }
        self.assertEqual(columns & forbidden, set())
        self.assertIn("credential_reference", columns)

    def test_raw_sqlite_file_never_contains_secret_bytes(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )
        self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="쿠팡",
                seller_identifier="s1", credential_fields=VALID_COUPANG_FIELDS,
                verification_token=resp.verification_token,
                idempotency_key="sec-test-1",
            ),
            company_id=self.company.id, created_by=1,
        )

        self.db.commit()

        with open(self.db_path, "rb") as f:
            raw = f.read()

        self.assertNotIn(SECRET_ACCESS_KEY.encode(), raw)
        self.assertNotIn(SECRET_SECRET_KEY.encode(), raw)

    def test_audit_log_description_never_contains_secret(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )
        self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="쿠팡",
                seller_identifier="s1", credential_fields=VALID_COUPANG_FIELDS,
                verification_token=resp.verification_token,
                idempotency_key="sec-test-2",
            ),
            company_id=self.company.id, created_by=1,
        )

        rows = self.db.execute(
            __import__("sqlalchemy").text("SELECT description FROM audit_logs"),
        ).fetchall()

        self.assertTrue(len(rows) > 0)
        for (description,) in rows:
            self.assertNotIn(SECRET_ACCESS_KEY, description or "")
            self.assertNotIn(SECRET_SECRET_KEY, description or "")

    def test_api_response_schema_never_exposes_secret(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )
        connection, _dup = self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="쿠팡",
                seller_identifier="s1", credential_fields=VALID_COUPANG_FIELDS,
                verification_token=resp.verification_token,
                idempotency_key="sec-test-3",
            ),
            company_id=self.company.id, created_by=1,
        )

        response_model = StoreConnectionResponse.model_validate(connection)
        serialized = response_model.model_dump_json()

        self.assertNotIn(SECRET_ACCESS_KEY, serialized)
        self.assertNotIn(SECRET_SECRET_KEY, serialized)

    def test_exception_messages_never_contain_secret_on_store_failure(self):

        failing_store = AlwaysFailingCredentialStore()
        service = StoreConnectionService(self.db, failing_store)

        resp = service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s2",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        try:
            service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="쿠팡",
                    seller_identifier="s2", credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=resp.verification_token,
                    idempotency_key="sec-test-4",
                ),
                company_id=self.company.id, created_by=1,
            )
            self.fail("should have raised")
        except ServiceUnavailableException:
            tb_text = traceback.format_exc()
            self.assertNotIn(SECRET_ACCESS_KEY, tb_text)
            self.assertNotIn(SECRET_SECRET_KEY, tb_text)


class AdapterNoNetworkLibraryTestCase(unittest.TestCase):
    """6) fixture Adapter가 실제 네트워크 라이브러리를 import하지 않는다."""

    def test_coupang_adapter_source_has_no_network_imports(self):

        import app.domains.store_connection.adapters.coupang as mod

        source = inspect.getsource(mod)
        for forbidden in ("import requests", "import httpx", "urllib.request", "import socket"):
            self.assertNotIn(forbidden, source)

    def test_naver_adapter_source_has_no_network_imports(self):

        import app.domains.store_connection.adapters.naver as mod

        source = inspect.getsource(mod)
        for forbidden in ("import requests", "import httpx", "urllib.request", "import socket"):
            self.assertNotIn(forbidden, source)


class ConsoleJsNoClientSideSecretPersistenceTestCase(unittest.TestCase):
    """
    7) console.js가 Secret 값을 localStorage/sessionStorage에 저장하지
    않는지 정적으로 확인한다(토큰 저장용 localStorage.setItem(TOKEN_KEY,...)
    는 세션 토큰이지 판매채널 Secret이 아니므로 별개다 — 이 테스트는
    scWizard/credential_fields 관련 코드 블록에 storage 호출이 없는지만
    확인한다).
    """

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.source = f.read()

    def test_no_localstorage_or_sessionstorage_call_within_sc_wizard_block(self):

        start = self.source.index("let scWizard = null;")
        end = self.source.index("function initStoreConnectionWizard")
        wizard_block = self.source[start:end]

        self.assertNotIn("localStorage", wizard_block)
        self.assertNotIn("sessionStorage", wizard_block)

    def test_secret_inputs_have_autocomplete_off(self):

        self.assertIn('autocomplete="off"', self.source)

    def test_wizard_close_clears_input_dom_values(self):

        self.assertIn("scCloseWizard", self.source)
        idx = self.source.index("function scCloseWizard")
        snippet = self.source[idx:idx + 500]
        self.assertIn('i.value = ""', snippet)


class AdminGuardEnforcedTestCase(unittest.TestCase):
    """8) 모든 store-connections 엔드포인트가 admin_guard로 보호된다."""

    def test_all_store_connection_routes_require_admin_guard(self):

        from app.domains.store_connection.router import router

        self.assertGreaterEqual(len(router.routes), 8)

        for route in router.routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                admin_guard, calls,
                f"{route.path}이(가) admin_guard 없이 노출되어 있습니다.",
            )


if __name__ == "__main__":
    unittest.main()
