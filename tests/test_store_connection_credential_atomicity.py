"""
=========================================================
Homez OS

File : tests/test_store_connection_credential_atomicity.py

2026-08-04 V6 Gate 2D: Credential Store(2단계 쓰기) 원자성 검증 —
SelectiveFailureCredentialStore(app/core/windows_credential_store.py)로
실제 실패 지점을 주입해, 코드 읽기로만 "이미 맞다"고 주장하지 않고
직접 증명한다. 실제 Windows Credential Manager는 사용하지 않는다.
=========================================================
"""

import logging
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import (
    CredentialNotFoundError,
    SelectiveFailureCredentialStore,
)
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
    reset_verification_token_jti_state_for_tests,
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


class CredentialAtomicityTestCase(unittest.TestCase):

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

        self.store = SelectiveFailureCredentialStore()
        self.service = StoreConnectionService(self.db, self.store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        reset_verification_token_jti_state_for_tests()

    # --------------------------------------------------
    # helpers
    # --------------------------------------------------

    def _verify_and_create(self, seller_identifier="s1", idem_prefix="create"):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier=seller_identifier,
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )
        conn, _dup = self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code="COUPANG", display_name="연결",
                seller_identifier=seller_identifier,
                credential_fields=VALID_COUPANG_FIELDS,
                verification_token=resp.verification_token,
                idempotency_key=_next_key(idem_prefix),
            ),
            company_id=self.company.id, created_by=1,
        )

        return conn

    def _connection_count(self) -> int:

        return self.db.query(StoreConnection).count()

    def _break_commit(self):
        """
        self.db.commit()이 이번 한 번만 예외를 던지도록 만든다 — DB 쓰기
        단계 실패(디스크 오류, 락, 제약 위반 등 어떤 원인이든)를
        시뮬레이션한다.
        """

        original_commit = self.db.commit
        called = {"n": 0}

        def _failing_commit():
            called["n"] += 1
            if called["n"] == 1:
                raise RuntimeError("시뮬레이션된 DB 커밋 실패")
            return original_commit()

        self.db.commit = _failing_commit

    # --------------------------------------------------
    # 1) 최초 저장 성공
    # --------------------------------------------------

    def test_initial_save_succeeds_and_activates_credential(self):

        conn = self._verify_and_create()

        self.assertEqual(conn.connection_status, "CONNECTED")
        self.assertIsNotNone(conn.credential_reference)
        self.assertTrue(self.store.exists(conn.credential_reference))
        self.assertEqual(self.store.call_counts["save"], 1)

    # --------------------------------------------------
    # 2) Credential 저장 실패 시 DB 행 생성 안 됨
    # --------------------------------------------------

    def test_credential_save_failure_blocks_db_row_creation(self):

        self.store.fail_next("save", times=1)

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-savefail",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        with self.assertRaises(ServiceUnavailableException):
            self.service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="연결",
                    seller_identifier="s-savefail",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=resp.verification_token,
                    idempotency_key=_next_key("savefail"),
                ),
                company_id=self.company.id, created_by=1,
            )

        self.assertEqual(self._connection_count(), 0)
        # save()가 실패했으므로 그 뒤 어떤 delete() 보상도 필요 없다.
        self.assertEqual(self.store.call_counts["delete"], 0)

    # --------------------------------------------------
    # 3) DB 실패 시 새로 저장한 Credential 정리(보상 삭제)
    # --------------------------------------------------

    def test_db_failure_after_credential_save_triggers_compensating_delete(self):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-dbfail",
                credential_fields=VALID_COUPANG_FIELDS,
            ),
            self.company.id,
        )

        self._break_commit()

        with self.assertRaises(RuntimeError):
            self.service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="연결",
                    seller_identifier="s-dbfail",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=resp.verification_token,
                    idempotency_key=_next_key("dbfail"),
                ),
                company_id=self.company.id, created_by=1,
            )

        self.db.rollback()

        # DB에는 아무 행도 남지 않는다.
        fresh = self.SessionLocal()
        try:
            self.assertEqual(fresh.query(StoreConnection).count(), 0)
        finally:
            fresh.close()

        # 방금 저장했던 새 Credential은 보상 삭제되어 더 이상 존재하지 않는다.
        self.assertEqual(self.store.call_counts["save"], 1)
        self.assertEqual(self.store.call_counts["delete"], 1)
        self.assertEqual(len(self.store._data), 0)

    # --------------------------------------------------
    # 4) rotate 성공 후 새 Credential 활성화 + 이전 Credential 정리
    # --------------------------------------------------

    def test_rotate_success_activates_new_credential_and_cleans_up_old(self):

        conn = self._verify_and_create(seller_identifier="s-rotate-ok")
        old_target = conn.credential_reference

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-rotate-ok",
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "new-access",
                    "secret_key": "new-secret",
                },
            ),
            self.company.id,
        )

        updated = self.service.rotate_credential(
            conn.id, self.company.id,
            StoreConnectionRotateCredentialRequest(
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "new-access",
                    "secret_key": "new-secret",
                },
                verification_token=resp.verification_token,
                idempotency_key=_next_key("rotate-ok"),
            ),
        )

        new_target = updated.credential_reference

        self.assertNotEqual(old_target, new_target)
        self.assertTrue(self.store.exists(new_target))
        self.assertFalse(self.store.exists(old_target))

    # --------------------------------------------------
    # 5) rotate DB 실패 시 기존 Credential 유지(새 Credential만 보상 삭제)
    # --------------------------------------------------

    def test_rotate_db_failure_preserves_old_credential(self):

        conn = self._verify_and_create(seller_identifier="s-rotate-dbfail")
        old_target = conn.credential_reference

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-rotate-dbfail",
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "new-access-2",
                    "secret_key": "new-secret-2",
                },
            ),
            self.company.id,
        )

        self._break_commit()

        with self.assertRaises(RuntimeError):
            self.service.rotate_credential(
                conn.id, self.company.id,
                StoreConnectionRotateCredentialRequest(
                    credential_fields={
                        "vendor_id": "A00123456", "access_key": "new-access-2",
                        "secret_key": "new-secret-2",
                    },
                    verification_token=resp.verification_token,
                    idempotency_key=_next_key("rotate-dbfail"),
                ),
            )

        self.db.rollback()

        # 기존 Credential은 그대로 남아 있다(삭제되지 않았다).
        self.assertTrue(self.store.exists(old_target))
        self.assertEqual(
            self.store.read(old_target)["access_key"], "fake-access",
        )

        # DB 상 연결도 여전히 이전 credential_reference를 가리킨다.
        fresh = self.SessionLocal()
        try:
            reloaded = fresh.query(StoreConnection).filter_by(id=conn.id).one()
            self.assertEqual(reloaded.credential_reference, old_target)
        finally:
            fresh.close()

    # --------------------------------------------------
    # 6) 이전 Credential 삭제 실패 시 orphan 상태를 명시적으로 기록
    # --------------------------------------------------

    def test_old_credential_cleanup_failure_after_rotate_is_logged_not_hidden(self):

        conn = self._verify_and_create(seller_identifier="s-rotate-orphan")
        old_target = conn.credential_reference

        self.store.fail_target("delete", old_target)

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-rotate-orphan",
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "new-access-3",
                    "secret_key": "new-secret-3",
                },
            ),
            self.company.id,
        )

        with self.assertLogs("homez", level="WARNING") as captured:
            updated = self.service.rotate_credential(
                conn.id, self.company.id,
                StoreConnectionRotateCredentialRequest(
                    credential_fields={
                        "vendor_id": "A00123456", "access_key": "new-access-3",
                        "secret_key": "new-secret-3",
                    },
                    verification_token=resp.verification_token,
                    idempotency_key=_next_key("rotate-orphan"),
                ),
            )

        # rotate 자체는 성공(신규 Credential이 활성화된다) — 이전 Credential
        # 정리 실패가 rotate 성공 여부를 가리지 않는다.
        self.assertEqual(updated.connection_status, "CONNECTED")
        self.assertNotEqual(updated.credential_reference, old_target)

        # 그러나 이전 Credential은 정리 실패로 저장소에 orphan 상태로 남는다.
        self.assertTrue(self.store.exists(old_target))

        # 그 사실이 명시적으로 로그에 기록된다(조용히 삼켜지지 않는다).
        self.assertTrue(
            any("정리에 실패" in msg for msg in captured.output),
            captured.output,
        )

    # --------------------------------------------------
    # 7) disable은 Credential을 임의 삭제하지 않는다
    # --------------------------------------------------

    def test_disable_never_deletes_credential(self):

        conn = self._verify_and_create(seller_identifier="s-disable")
        target = conn.credential_reference

        self.service.disable(
            conn.id, self.company.id,
            StoreConnectionDisableRequest(idempotency_key=_next_key("disable")),
        )

        self.assertEqual(self.store.call_counts["delete"], 0)
        self.assertTrue(self.store.exists(target))

    # --------------------------------------------------
    # 8) delete는 DB 상태와 Credential Store 실패를 숨기지 않는다
    # --------------------------------------------------

    def test_delete_credential_logs_store_failure_but_still_clears_db_reference(self):

        conn = self._verify_and_create(seller_identifier="s-delete-storefail")
        target = conn.credential_reference

        self.store.fail_target("delete", target)

        with self.assertLogs("homez", level="ERROR") as captured:
            updated = self.service.delete_credential(
                conn.id, self.company.id,
                StoreConnectionDeleteCredentialRequest(
                    confirm=True, idempotency_key=_next_key("delete-storefail"),
                ),
            )

        # DB는 자격증명 참조를 지웠다고 정직하게 반영한다.
        self.assertIsNone(updated.credential_reference)

        # 하지만 실제 저장소에는 여전히(정리 실패로) 남아 있고, 이 사실이
        # 로그에 명시적으로 기록된다 — 숨기지 않는다.
        self.assertTrue(self.store.exists(target))
        self.assertTrue(
            any("실제 보안 저장소 삭제에 실패" in msg for msg in captured.output),
            captured.output,
        )

    def test_delete_credential_succeeds_and_removes_from_store(self):

        conn = self._verify_and_create(seller_identifier="s-delete-ok")
        target = conn.credential_reference

        updated = self.service.delete_credential(
            conn.id, self.company.id,
            StoreConnectionDeleteCredentialRequest(
                confirm=True, idempotency_key=_next_key("delete-ok"),
            ),
        )

        self.assertIsNone(updated.credential_reference)
        self.assertFalse(self.store.exists(target))

        with self.assertRaises(CredentialNotFoundError):
            self.store.read(target)

    # --------------------------------------------------
    # 9) Secret read/save/delete 호출 횟수 검증
    # --------------------------------------------------

    def test_call_counts_are_exact_across_full_lifecycle(self):

        conn = self._verify_and_create(seller_identifier="s-callcount")

        self.assertEqual(self.store.call_counts["save"], 1)
        self.assertEqual(self.store.call_counts["read"], 0)
        self.assertEqual(self.store.call_counts["delete"], 0)

        # verify_existing은 저장된 Credential을 정확히 1번만 읽는다.
        self.service.verify_existing(conn.id, self.company.id)
        self.assertEqual(self.store.call_counts["read"], 1)

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-callcount",
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "new-access-4",
                    "secret_key": "new-secret-4",
                },
            ),
            self.company.id,
        )
        self.service.rotate_credential(
            conn.id, self.company.id,
            StoreConnectionRotateCredentialRequest(
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "new-access-4",
                    "secret_key": "new-secret-4",
                },
                verification_token=resp.verification_token,
                idempotency_key=_next_key("callcount-rotate"),
            ),
        )
        # rotate: 새 Credential save 1회 + 이전 Credential delete 1회.
        self.assertEqual(self.store.call_counts["save"], 2)
        self.assertEqual(self.store.call_counts["delete"], 1)

        self.service.delete_credential(
            conn.id, self.company.id,
            StoreConnectionDeleteCredentialRequest(
                confirm=True, idempotency_key=_next_key("callcount-delete"),
            ),
        )
        self.assertEqual(self.store.call_counts["delete"], 2)

    # --------------------------------------------------
    # 10) API 응답과 로그에 Secret이 없다
    # --------------------------------------------------

    def test_secret_values_never_appear_in_response_or_logs(self):

        secret_marker = "super-secret-value-xyz"

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code="COUPANG", seller_identifier="s-secretcheck",
                credential_fields={
                    "vendor_id": "A00123456", "access_key": "access-secretcheck",
                    "secret_key": secret_marker,
                },
            ),
            self.company.id,
        )

        self.assertNotIn(secret_marker, resp.verification_token or "")
        self.assertNotIn(secret_marker, resp.masked_credential_hint or "")

        root_logger = logging.getLogger("homez")
        stream_capture = []

        class _ListHandler(logging.Handler):
            def emit(self, record):
                stream_capture.append(self.format(record))

        list_handler = _ListHandler()
        root_logger.addHandler(list_handler)
        try:
            conn, _dup = self.service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="연결",
                    seller_identifier="s-secretcheck",
                    credential_fields={
                        "vendor_id": "A00123456", "access_key": "access-secretcheck",
                        "secret_key": secret_marker,
                    },
                    verification_token=resp.verification_token,
                    idempotency_key=_next_key("secretcheck"),
                ),
                company_id=self.company.id, created_by=1,
            )
        finally:
            root_logger.removeHandler(list_handler)

        self.assertNotIn(secret_marker, conn.credential_reference or "")
        for line in stream_capture:
            self.assertNotIn(secret_marker, line)


if __name__ == "__main__":
    unittest.main()
