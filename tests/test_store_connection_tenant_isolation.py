"""
=========================================================
Homez OS

File : tests/test_store_connection_tenant_isolation.py

2026-08-01 CTO 재심사(PHASE_1_REJECTED_FOR_REMEDIATION) 대응 —
회사 간(테넌트 간) 격리 전용 테스트. Mock만으로 끝내지 않고 임시
SQLite DB + InMemoryCredentialStore로 실제 Service/Router 경계를
검증한다(httpx가 설치되어 있지 않아 FastAPI TestClient를 쓸 수
없다는 이 코드베이스의 기존 제약과 동일하게, tests/
test_desktop_auth_session.py와 같은 패턴으로 라우터 엔드포인트
함수를 ASGI 계층 없이 직접 호출한다).

검증 대상:
1) 회사 A 관리자가 회사 B 연결을 GET/verify/rotate/disable/delete
   하면 전부 404(NotFoundException) — 403이나 상세 메시지로 존재를
   알려주지 않는다.
2) 위 실패 시 회사 B의 DB row와 Credential Store가 전혀 변경되지
   않는다.
3) 회사 A에서 발급된 verification_token을 회사 B의 create/rotate에
   사용하면 거부된다.
4) 같은 idempotency_key를 회사 A/B가 써도 서로의 응답이 섞이지 않는다
   (동시성 버전은 tests/test_store_connection_concurrency.py).
5) 실패한 요청의 응답·예외 메시지에 Secret 원문이 없다.
=========================================================
"""

import os
import tempfile
import types
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException, NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.store_connection.model import StoreConnection
from app.domains.store_connection.router import (
    create_connection,
    delete_credential,
    disable_connection,
    get_connection,
    rotate_credential,
    verify_existing_connection,
    verify_new_connection,
)
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
    issue_token,
    verify_token,
)
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

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

SECRET_ACCESS_KEY = "TENANT_TEST_ACCESS_KEY_qqqq1111"
SECRET_SECRET_KEY = "TENANT_TEST_SECRET_KEY_wwww2222"

VALID_COUPANG_FIELDS = {
    "vendor_id": "A00999888", "access_key": SECRET_ACCESS_KEY,
    "secret_key": SECRET_SECRET_KEY,
}


def _fake_user(company_id: int, user_id: int = 1):
    """
    router.py 엔드포인트 함수가 실제로 참조하는 속성(company_id, id)만
    가진 가벼운 대역 — tests/test_desktop_auth_session.py의 _FakeClient/
    _FakeRequest와 동일한 패턴.
    """

    return types.SimpleNamespace(company_id=company_id, id=user_id)


class StoreConnectionTenantIsolationTestCase(unittest.TestCase):

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

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111", ceo="에이",
            phone="02-111-1111", email="a@example.com", address="서울 A",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222", ceo="비",
            phone="02-222-2222", email="b@example.com", address="서울 B",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.store = InMemoryCredentialStore()
        self.service = StoreConnectionService(self.db, self.store)

        self.user_a = _fake_user(self.company_a.id, user_id=101)
        self.user_b = _fake_user(self.company_b.id, user_id=201)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_company_b_connection(self):
        """회사 B 소유의 정상 연결을 하나 만들어 둔다(공격 대상)."""

        verify_resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="victim-seller",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company_b.id,
        )
        connection, _dup = self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="회사 B 연결",
                seller_identifier="victim-seller",
                credential_fields=VALID_COUPANG_FIELDS,
                verification_token=verify_resp.verification_token,
                idempotency_key=_next_key("victim-create"),
            ),
            company_id=self.company_b.id, created_by=201,
        )

        return connection

    def _snapshot(self, connection_id: int, credential_reference: str):

        row = (
            self.db.query(StoreConnection)
            .filter(StoreConnection.id == connection_id)
            .first()
        )
        return {
            "connection_status": row.connection_status,
            "credential_version": row.credential_version,
            "credential_reference": row.credential_reference,
            "masked_credential_hint": row.masked_credential_hint,
            "updated_at": row.updated_at,
            "credential_exists": self.store.exists(credential_reference),
            "credential_payload": self.store.read(credential_reference),
        }

    # ----------------------------------------------------
    # 1) GET — 회사 A가 회사 B 연결을 조회하면 404
    # ----------------------------------------------------

    def test_router_get_connection_cross_company_returns_404_not_403(self):

        victim = self._create_company_b_connection()
        before = self._snapshot(victim.id, victim.credential_reference)

        with self.assertRaises(NotFoundException) as ctx:
            get_connection(
                victim.id, current_user=self.user_a, service=self.service,
            )

        self.assertEqual(ctx.exception.status_code, 404)
        # 403이나 "다른 회사 소유"류의 상세 메시지로 존재를 알려주지 않는다
        # — 진짜로 없는 id를 조회했을 때와 완전히 같은 메시지여야 한다.
        not_found_message = str(ctx.exception.detail)
        with self.assertRaises(NotFoundException) as ctx2:
            get_connection(
                999999, current_user=self.user_a, service=self.service,
            )
        self.assertEqual(str(ctx2.exception.detail), not_found_message)

        after = self._snapshot(victim.id, victim.credential_reference)
        self.assertEqual(before, after)

    # ----------------------------------------------------
    # 2) verify — 회사 A가 회사 B 연결을 재검증하면 404, 아무것도 안 변함
    # ----------------------------------------------------

    def test_router_verify_existing_cross_company_returns_404_and_no_side_effect(
        self,
    ):

        victim = self._create_company_b_connection()
        before = self._snapshot(victim.id, victim.credential_reference)

        with self.assertRaises(NotFoundException) as ctx:
            verify_existing_connection(
                victim.id, current_user=self.user_a, service=self.service,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after = self._snapshot(victim.id, victim.credential_reference)
        self.assertEqual(
            before, after,
            "회사 A의 실패한 verify 시도가 회사 B의 행이나 Credential을 "
            "건드렸습니다.",
        )

    # ----------------------------------------------------
    # 3) rotate — 회사 A가 회사 B Credential을 교체 시도하면 404, 불변
    # ----------------------------------------------------

    def test_router_rotate_credential_cross_company_returns_404_and_no_side_effect(
        self,
    ):

        victim = self._create_company_b_connection()
        before = self._snapshot(victim.id, victim.credential_reference)

        # 공격자(회사 A)가 자기 회사 이름으로 정상적으로 발급받은
        # verification_token을 그대로 재사용하려는 상황까지 재현한다
        # (token 자체도 회사 A 소속이므로 이중으로 막혀야 한다).
        attacker_verify = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="attacker-seller",
                credential_fields={
                    "vendor_id": "A00000000", "access_key": "attacker-ak",
                    "secret_key": "attacker-sk",
                },
            ),
            self.company_a.id,
        )

        with self.assertRaises(NotFoundException) as ctx:
            rotate_credential(
                victim.id,
                StoreConnectionRotateCredentialRequest(
                    credential_fields={
                        "vendor_id": "A00000000", "access_key": "attacker-ak",
                        "secret_key": "attacker-sk",
                    },
                    verification_token=attacker_verify.verification_token,
                    idempotency_key=_next_key("attack-rotate"),
                ),
                current_user=self.user_a, service=self.service,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after = self._snapshot(victim.id, victim.credential_reference)
        self.assertEqual(
            before, after,
            "회사 A의 실패한 rotate 시도가 회사 B의 Credential을 "
            "건드렸습니다.",
        )

    # ----------------------------------------------------
    # 4) disable — 회사 A가 회사 B 연결을 비활성화 시도하면 404, 불변
    # ----------------------------------------------------

    def test_router_disable_cross_company_returns_404_and_no_side_effect(self):

        victim = self._create_company_b_connection()
        before = self._snapshot(victim.id, victim.credential_reference)

        with self.assertRaises(NotFoundException) as ctx:
            disable_connection(
                victim.id,
                StoreConnectionDisableRequest(idempotency_key=_next_key("dis")),
                current_user=self.user_a, service=self.service,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after = self._snapshot(victim.id, victim.credential_reference)
        self.assertEqual(before, after)
        self.assertNotEqual(after["connection_status"], "DISABLED")

    # ----------------------------------------------------
    # 5) delete credential — 회사 A가 회사 B Credential을 삭제 시도하면
    #    404, Credential Store도 전혀 건드리지 않음
    # ----------------------------------------------------

    def test_router_delete_credential_cross_company_returns_404_and_credential_survives(
        self,
    ):

        victim = self._create_company_b_connection()
        before = self._snapshot(victim.id, victim.credential_reference)

        with self.assertRaises(NotFoundException) as ctx:
            delete_credential(
                victim.id,
                StoreConnectionDeleteCredentialRequest(
                    confirm=True, idempotency_key=_next_key("del"),
                ),
                current_user=self.user_a, service=self.service,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        after = self._snapshot(victim.id, victim.credential_reference)
        self.assertEqual(before, after)
        self.assertTrue(self.store.exists(victim.credential_reference))

    # ----------------------------------------------------
    # 6) 회사 A의 verification_token을 회사 B의 create()에 사용 → 거부
    # ----------------------------------------------------

    def test_token_issued_for_company_a_rejected_for_company_b_create(self):

        token_resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="cross-token-seller",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            create_connection(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="탈취 시도 연결",
                    seller_identifier="cross-token-seller",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=token_resp.verification_token,
                    idempotency_key=_next_key("cross-token-create"),
                ),
                current_user=self.user_b, service=self.service,
            )

        # 회사 B에 어떤 연결도 생성되지 않았어야 한다.
        remaining = self.service.list_for_company(self.company_b.id)
        self.assertEqual(remaining, [])

    def test_token_company_binding_enforced_at_verify_token_level(self):
        """verify_token() 자체가 company_id 불일치를 직접 거부하는지 단위 확인."""

        token, _expires = issue_token(
            self.company_a.id, "COUPANG", "s1", VALID_COUPANG_FIELDS,
        )

        with self.assertRaises(InvalidVerificationTokenError):
            verify_token(
                token, self.company_b.id, "COUPANG", "s1", VALID_COUPANG_FIELDS,
            )

    # ----------------------------------------------------
    # 7) 같은 idempotency_key를 회사 A/B가 사용해도 교차 응답 없음
    # ----------------------------------------------------

    def test_same_idempotency_key_used_by_both_companies_stays_isolated(self):

        shared_key = "same-literal-key-both-companies"

        verify_a = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="seller-a",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company_a.id,
        )
        conn_a = create_connection(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="회사 A 연결",
                seller_identifier="seller-a",
                credential_fields=VALID_COUPANG_FIELDS,
                verification_token=verify_a.verification_token,
                idempotency_key=shared_key,
            ),
            current_user=self.user_a, service=self.service,
        )

        verify_b = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="seller-b",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company_b.id,
        )
        conn_b = create_connection(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="회사 B 연결",
                seller_identifier="seller-b",
                credential_fields=VALID_COUPANG_FIELDS,
                verification_token=verify_b.verification_token,
                idempotency_key=shared_key,
            ),
            current_user=self.user_b, service=self.service,
        )

        self.assertNotEqual(conn_a.id, conn_b.id)
        self.assertEqual(conn_a.company_id, self.company_a.id)
        self.assertEqual(conn_b.company_id, self.company_b.id)

        # 재호출(같은 회사, 같은 key)은 여전히 idempotent해야 한다 —
        # 회사별로 격리됐을 뿐 idempotency 자체가 깨지면 안 된다.
        replay_a, dup_replay_a = self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="회사 A 연결",
                seller_identifier="seller-a",
                credential_fields=VALID_COUPANG_FIELDS,
                verification_token=verify_a.verification_token,
                idempotency_key=shared_key,
            ),
            company_id=self.company_a.id, created_by=101,
        )
        self.assertTrue(dup_replay_a)
        self.assertEqual(replay_a.id, conn_a.id)

    # ----------------------------------------------------
    # 8) 실패 응답/예외에 Secret 원문 없음
    # ----------------------------------------------------

    def test_cross_company_failure_paths_never_leak_secret(self):

        victim = self._create_company_b_connection()

        for attempt in (
            lambda: get_connection(
                victim.id, current_user=self.user_a, service=self.service,
            ),
            lambda: verify_existing_connection(
                victim.id, current_user=self.user_a, service=self.service,
            ),
            lambda: disable_connection(
                victim.id,
                StoreConnectionDisableRequest(idempotency_key=_next_key("leak")),
                current_user=self.user_a, service=self.service,
            ),
        ):
            with self.assertRaises(NotFoundException) as ctx:
                attempt()
            message = str(ctx.exception.detail)
            self.assertNotIn(SECRET_ACCESS_KEY, message)
            self.assertNotIn(SECRET_SECRET_KEY, message)


if __name__ == "__main__":
    unittest.main()
