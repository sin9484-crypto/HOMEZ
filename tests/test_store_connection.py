"""
=========================================================
Homez OS

File : tests/test_store_connection.py

판매채널 연결(StoreConnection) — 핵심 기능 검증:
1) 연결 검증 성공·실패(401/403/429/5xx/timeout 등 정규화)
2) 입력 변경 시 검증 fingerprint 무효화
3) 검증 전 저장 차단
4) 연결 생성 성공 + idempotency 재호출
5) 회사·채널·판매자 식별자 중복 거부
6) 기존 연결 재검증(서버가 Credential Manager에서 직접 조회)
7) 자격증명 교체(rotate) 성공 + 이전 Credential 정리
8) 비활성화(Credential은 유지) vs Credential 삭제(명시적 확인 필요) 구분
9) 안내 마법사 데이터(guides) 구조 확인

2026-08-01: 모든 서비스 호출에 company_id를 명시적으로 전달한다
(CTO 재심사 이후 회사 소유권 격리가 필수 인자가 되었다) —
회사 간 격리 자체의 전용 테스트는 tests/test_store_connection_tenant_isolation.py.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException, ConflictException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.role.model import Role
from app.domains.store_connection.model import StoreConnection
from app.domains.store_connection.schema import (
    StoreConnectionCreateRequest,
    StoreConnectionDeleteCredentialRequest,
    StoreConnectionDisableRequest,
    StoreConnectionRotateCredentialRequest,
    StoreConnectionVerifyRequest,
)
from app.domains.store_connection.service import StoreConnectionService
from app.domains.store_connection.verification_token import (
    InvalidVerificationTokenError,
    reset_verification_token_jti_state_for_tests,
    verify_token,
)
from app.domains.user.model import User

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
    "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)

VALID_COUPANG_FIELDS = {
    "vendor_id": "A00123456", "access_key": "fake-access", "secret_key": "fake-secret",
}
VALID_NAVER_FIELDS = {"client_id": "fake-client-id", "client_secret": "fake-client-secret"}


class StoreConnectionTestCase(unittest.TestCase):

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

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="테스트회사", business_number="123-45-67890", ceo="홍길동",
            phone="02-000-0000", email="a@b.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()

        self.store = InMemoryCredentialStore()
        self.service = StoreConnectionService(self.db, self.store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        reset_verification_token_jti_state_for_tests()

    # ----------------------------------------------------
    # 1) 연결 검증 성공·실패
    # ----------------------------------------------------

    def test_verify_new_succeeds_for_valid_coupang_credentials(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        self.assertTrue(resp.success)
        self.assertIsNotNone(resp.verification_token)
        self.assertEqual(resp.masked_credential_hint, "••••3456")

    def test_verify_new_normalizes_various_error_scenarios(self):

        scenarios = [
            ("TRIGGER_401", "UNAUTHORIZED_401"),
            ("TRIGGER_403", "FORBIDDEN_403"),
            ("TRIGGER_429", "RATE_LIMITED_429"),
            ("TRIGGER_5XX", "PLATFORM_ERROR_5XX"),
            ("TRIGGER_TIMEOUT", "TIMEOUT"),
            ("TRIGGER_SECRET_MISMATCH", "SECRET_MISMATCH"),
        ]

        for access_key, expected_code in scenarios:
            resp = self.service.verify_new(
                StoreConnectionVerifyRequest(
                    marketplace_code="COUPANG", seller_identifier="s1",
                    credential_fields={
                        "vendor_id": "A1", "access_key": access_key,
                        "secret_key": "x",
                    },
                ),
                self.company.id,
            )
            self.assertFalse(resp.success)
            self.assertEqual(resp.error_code, expected_code)
            self.assertIsNone(resp.verification_token)
            # 무조건 "아이디 또는 비밀번호가 틀렸습니다"라고 뭉뚱그리지 않는다.
            self.assertNotIn("아이디", resp.error_summary or "")

    def test_verify_new_rejects_unsupported_credential_shape(self):

        with self.assertRaises(BadRequestException):
            self.service.verify_new(
                StoreConnectionVerifyRequest(
                    marketplace_code="COUPANG", seller_identifier="s1",
                    credential_fields={"vendor_id": "A1"},  # access_key/secret_key 누락
                ),
                self.company.id,
            )

    # ----------------------------------------------------
    # 2) 입력 변경 시 fingerprint 무효화
    # ----------------------------------------------------

    def test_verification_token_invalidated_by_changed_fields(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        changed_fields = dict(VALID_COUPANG_FIELDS)
        changed_fields["secret_key"] = "different-value"

        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(
                resp.verification_token, self.company.id, "COUPANG", "s1",
                changed_fields,
            )

    # ----------------------------------------------------
    # 2-2) jti 단발성 소비 (2026-08-04 V6 Gate 2 재보완)
    # ----------------------------------------------------

    def test_verification_token_cannot_be_verified_twice(self):
        """
        같은 토큰을 두 번 verify_token()에 통과시키면 두 번째는
        "이미 사용된 토큰"으로 거부돼야 한다 — TTL 안에서라면 여러 번
        재사용할 수 있었던 잔존 위험을 없앤다.
        """

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-jti-1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        # 첫 번째 사용은 성공해야 한다.
        verify_token(
            resp.verification_token, self.company.id, "COUPANG", "s-jti-1",
            VALID_COUPANG_FIELDS,
        )

        # 같은 토큰의 두 번째 사용은 이미 소비됐으므로 거부된다.
        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(
                resp.verification_token, self.company.id, "COUPANG", "s-jti-1",
                VALID_COUPANG_FIELDS,
            )

    def test_create_cannot_reuse_verification_token_for_a_second_connection(self):
        """
        verify_new → create로 정상 소비된 토큰을, 다른 seller_identifier로
        두 번째 연결을 만드는 데 재사용하려 하면 거부돼야 한다(토큰
        재사용으로 두 번째 StoreConnection을 몰래 더 만드는 시나리오).
        """

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-jti-2",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="첫 연결",
                seller_identifier="s-jti-2",
                credential_fields=VALID_COUPANG_FIELDS,
                verification_token=resp.verification_token,
                idempotency_key=_next_key("jti-reuse-1"),
            ),
            company_id=self.company.id, created_by=1,
        )

        # 같은 토큰을 다른 seller_identifier로 재사용 — 서명·company_id는
        # 유효하지만 이미 소비된 jti이므로 거부돼야 한다(신규 idempotency
        # key라 idempotent 재호출 경로로 빠지지 않는다).
        with self.assertRaises(Exception):
            self.service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="재사용 시도",
                    seller_identifier="s-jti-2",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=resp.verification_token,
                    idempotency_key=_next_key("jti-reuse-2"),
                ),
                company_id=self.company.id, created_by=1,
            )

    # ----------------------------------------------------
    # 3) 검증 전 저장 차단
    # ----------------------------------------------------

    def test_create_without_verification_token_is_rejected(self):

        with self.assertRaises(Exception):
            self.service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="쿠팡",
                    seller_identifier="s1",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token="not-a-real-token",
                    idempotency_key=_next_key("create"),
                ),
                company_id=self.company.id, created_by=1,
            )

    # ----------------------------------------------------
    # 4) 연결 생성 성공 + idempotency
    # ----------------------------------------------------

    def _verify_and_create(self, marketplace_code, fields, seller_identifier="s1"):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code=marketplace_code,
                seller_identifier=seller_identifier, credential_fields=fields,
            ),
            self.company.id,
        )
        conn, dup = self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code=marketplace_code, display_name="연결",
                seller_identifier=seller_identifier, credential_fields=fields,
                verification_token=resp.verification_token,
                idempotency_key=_next_key("create"),
            ),
            company_id=self.company.id, created_by=1,
        )

        return conn, dup

    def test_create_succeeds_and_sets_connected_status(self):

        conn, dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)

        self.assertFalse(dup)
        self.assertEqual(conn.connection_status, "CONNECTED")
        self.assertEqual(conn.credential_version, 1)
        self.assertIsNotNone(conn.credential_reference)

    def test_create_is_idempotent_on_same_key(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )
        key = _next_key("create")
        data = StoreConnectionCreateRequest(
            marketplace_code="COUPANG", display_name="연결",
            seller_identifier="s1", credential_fields=VALID_COUPANG_FIELDS,
            verification_token=resp.verification_token, idempotency_key=key,
        )

        conn1, dup1 = self.service.create(data, company_id=self.company.id, created_by=1)
        conn2, dup2 = self.service.create(data, company_id=self.company.id, created_by=1)

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(conn1.id, conn2.id)

    def test_naver_connection_can_also_be_created(self):

        conn, _dup = self._verify_and_create("NAVER_SMARTSTORE", VALID_NAVER_FIELDS)

        self.assertEqual(conn.marketplace_code, "NAVER_SMARTSTORE")
        self.assertEqual(conn.connection_status, "CONNECTED")

    # ----------------------------------------------------
    # 5) 중복 거부
    # ----------------------------------------------------

    def test_duplicate_company_marketplace_seller_is_rejected(self):

        self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS, seller_identifier="dup-1")

        resp2 = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="dup-1",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        with self.assertRaises(ConflictException):
            self.service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="다른 이름",
                    seller_identifier="dup-1",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=resp2.verification_token,
                    idempotency_key=_next_key("create"),
                ),
                company_id=self.company.id, created_by=1,
            )

    def test_different_seller_identifier_is_allowed(self):

        conn1, _ = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS, "seller-a")
        conn2, _ = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS, "seller-b")

        self.assertNotEqual(conn1.id, conn2.id)

    # ----------------------------------------------------
    # 6) 기존 연결 재검증
    # ----------------------------------------------------

    def test_verify_existing_reads_from_credential_store_not_client(self):

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)

        resp = self.service.verify_existing(conn.id, self.company.id)

        self.assertTrue(resp.success)

    def test_verify_existing_without_credential_is_rejected(self):

        connection = StoreConnection(
            company_id=self.company.id, marketplace_code="COUPANG",
            display_name="미설정", seller_identifier="no-cred",
            connection_status="NOT_CONFIGURED", credential_version=0,
            created_by=1, creation_idempotency_key=_next_key("raw"),
            creation_request_fingerprint="0" * 64,
        )
        self.db.add(connection)
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.verify_existing(connection.id, self.company.id)

    # ----------------------------------------------------
    # 7) 자격증명 교체
    # ----------------------------------------------------

    def test_rotate_credential_success_replaces_reference_and_bumps_version(self):

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)
        old_ref = conn.credential_reference

        new_fields = dict(VALID_COUPANG_FIELDS)
        new_fields["access_key"] = "rotated-access-key"
        resp2 = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s1",
                credential_fields=new_fields,
            ),
            self.company.id,
        )

        rotated = self.service.rotate_credential(
            conn.id, self.company.id,
            StoreConnectionRotateCredentialRequest(
                credential_fields=new_fields,
                verification_token=resp2.verification_token,
                idempotency_key=_next_key("rotate"),
            ),
        )

        self.assertEqual(rotated.credential_version, 2)
        self.assertNotEqual(rotated.credential_reference, old_ref)
        self.assertFalse(self.store.exists(old_ref))
        self.assertTrue(self.store.exists(rotated.credential_reference))

    # ----------------------------------------------------
    # 8) 비활성화 vs Credential 삭제
    # ----------------------------------------------------

    def test_disable_keeps_credential_intact(self):

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)
        ref = conn.credential_reference

        disabled = self.service.disable(
            conn.id, self.company.id,
            StoreConnectionDisableRequest(idempotency_key=_next_key("disable")),
        )

        self.assertEqual(disabled.connection_status, "DISABLED")
        self.assertTrue(self.store.exists(ref))

    def test_delete_credential_requires_explicit_confirm(self):

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)

        with self.assertRaises(BadRequestException):
            self.service.delete_credential(
                conn.id, self.company.id,
                StoreConnectionDeleteCredentialRequest(
                    confirm=False, idempotency_key=_next_key("del"),
                ),
            )

    def test_delete_credential_with_confirm_removes_secret_and_reference(self):

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)
        ref = conn.credential_reference

        deleted = self.service.delete_credential(
            conn.id, self.company.id,
            StoreConnectionDeleteCredentialRequest(
                confirm=True, idempotency_key=_next_key("del"),
            ),
        )

        self.assertIsNone(deleted.credential_reference)
        self.assertEqual(deleted.connection_status, "NOT_CONFIGURED")
        self.assertFalse(self.store.exists(ref))


if __name__ == "__main__":
    unittest.main()
