"""
=========================================================
Homez OS

File : tests/test_store_connection_idempotency_fingerprint.py

2026-08-01 CTO 2차 재심사 대응 — idempotency 요청 본문 fingerprint.

기존 결함: create()가 같은 (company_id, creation_idempotency_key)의
기존 행을 발견하면 지금 들어온 요청 내용과 전혀 비교하지 않고 그대로
반환했다(service.py 196~206행, 수정 전). 클라이언트가 이미 성공한
idempotency key를 다른 marketplace_code/seller_identifier/
display_name/Credential로 재사용해도 오류 없이 "성공"으로 위장된
엉뚱한 기존 행을 돌려받는 조용한 데이터 불일치였다.

이 파일은 실제 임시 SQLite DB + InMemoryCredentialStore로 Service
경계를 직접 검증한다(Mock만으로 끝내지 않음).
=========================================================
"""

import os
import tempfile
import threading
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.store_connection.model import StoreConnection
from app.domains.store_connection.schema import (
    StoreConnectionCreateRequest,
    StoreConnectionVerifyRequest,
)
from app.domains.store_connection.service import StoreConnectionService
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

SECRET_ACCESS_KEY = "FPRINT_TEST_ACCESS_KEY_aaaa7777"
SECRET_SECRET_KEY = "FPRINT_TEST_SECRET_KEY_bbbb6666"

VALID_COUPANG_FIELDS = {
    "vendor_id": "A00777666", "access_key": SECRET_ACCESS_KEY,
    "secret_key": SECRET_SECRET_KEY,
}
VALID_NAVER_FIELDS = {
    "client_id": "naver-client-1", "client_secret": "naver-secret-1",
    "account_id": "naver-account-1", "account_type": "NAVER",
}


class StoreConnectionIdempotencyFingerprintTestCase(unittest.TestCase):

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
            name="핑거프린트 테스트 회사", business_number="333-33-33333",
            ceo="검증", phone="02-333-3333", email="fp@example.com",
            address="서울",
        )
        self.company_b = Company(
            name="핑거프린트 테스트 회사B", business_number="444-44-44444",
            ceo="검증B", phone="02-444-4444", email="fp-b@example.com",
            address="서울",
        )
        self.db.add_all([self.company, self.company_b])
        self.db.commit()

        self.store = InMemoryCredentialStore()
        self.service = StoreConnectionService(self.db, self.store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _valid_token(
        self, company_id, marketplace_code, seller_identifier, fields,
    ):

        resp = self.service.verify_new(
            StoreConnectionVerifyRequest(
                marketplace_code=marketplace_code,
                seller_identifier=seller_identifier, credential_fields=fields,
            ),
            company_id,
        )
        return resp.verification_token

    def _create(
        self, company_id, marketplace_code, seller_identifier, display_name,
        fields, idempotency_key, token=None, created_by=1,
    ):

        if token is None:
            token = self._valid_token(
                company_id, marketplace_code, seller_identifier, fields,
            )

        return self.service.create(
            StoreConnectionCreateRequest(
                marketplace_code=marketplace_code, display_name=display_name,
                seller_identifier=seller_identifier, credential_fields=fields,
                verification_token=token, idempotency_key=idempotency_key,
            ),
            company_id=company_id, created_by=created_by,
        )

    # ----------------------------------------------------
    # 1) 완전히 같은 요청 재호출 → 기존 row
    # ----------------------------------------------------

    def test_identical_replay_returns_existing_row(self):

        key = _next_key("replay")
        conn1, dup1 = self._create(
            self.company.id, "COUPANG", "seller-1", "연결1",
            VALID_COUPANG_FIELDS, key,
        )
        conn2, dup2 = self._create(
            self.company.id, "COUPANG", "seller-1", "연결1",
            VALID_COUPANG_FIELDS, key,
        )

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(conn1.id, conn2.id)

    # ----------------------------------------------------
    # 2) 다른 marketplace → 409
    # ----------------------------------------------------

    def test_different_marketplace_same_key_returns_409(self):

        key = _next_key("diff-marketplace")
        self._create(
            self.company.id, "COUPANG", "seller-2", "연결2",
            VALID_COUPANG_FIELDS, key,
        )

        with self.assertRaises(ConflictException) as ctx:
            self._create(
                self.company.id, "NAVER_SMARTSTORE", "seller-2", "연결2",
                VALID_NAVER_FIELDS, key, token="not-checked-before-409",
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ----------------------------------------------------
    # 3) 다른 seller_identifier → 409
    # ----------------------------------------------------

    def test_different_seller_identifier_same_key_returns_409(self):

        key = _next_key("diff-seller")
        self._create(
            self.company.id, "COUPANG", "seller-3a", "연결3",
            VALID_COUPANG_FIELDS, key,
        )

        with self.assertRaises(ConflictException) as ctx:
            self._create(
                self.company.id, "COUPANG", "seller-3b", "연결3",
                VALID_COUPANG_FIELDS, key, token="not-checked-before-409",
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ----------------------------------------------------
    # 4) 다른 display_name → 409
    # ----------------------------------------------------

    def test_different_display_name_same_key_returns_409(self):

        key = _next_key("diff-name")
        self._create(
            self.company.id, "COUPANG", "seller-4", "연결4-A",
            VALID_COUPANG_FIELDS, key,
        )

        with self.assertRaises(ConflictException) as ctx:
            self._create(
                self.company.id, "COUPANG", "seller-4", "연결4-B",
                VALID_COUPANG_FIELDS, key, token="not-checked-before-409",
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ----------------------------------------------------
    # 5) 다른 Credential → 409
    # ----------------------------------------------------

    def test_different_credential_same_key_returns_409(self):

        key = _next_key("diff-cred")
        self._create(
            self.company.id, "COUPANG", "seller-5", "연결5",
            VALID_COUPANG_FIELDS, key,
        )

        changed_fields = dict(VALID_COUPANG_FIELDS)
        changed_fields["access_key"] = "DIFFERENT_ACCESS_KEY_zzzz0000"

        with self.assertRaises(ConflictException) as ctx:
            self._create(
                self.company.id, "COUPANG", "seller-5", "연결5",
                changed_fields, key, token="not-checked-before-409",
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ----------------------------------------------------
    # 6) 다른 회사는 같은 key 사용 가능
    # ----------------------------------------------------

    def test_different_company_can_reuse_same_key(self):

        key = "literal-shared-key-across-companies"

        conn_a, dup_a = self._create(
            self.company.id, "COUPANG", "seller-6a", "연결6A",
            VALID_COUPANG_FIELDS, key,
        )
        conn_b, dup_b = self._create(
            self.company_b.id, "COUPANG", "seller-6b", "연결6B",
            VALID_COUPANG_FIELDS, key,
        )

        self.assertFalse(dup_a)
        self.assertFalse(dup_b)
        self.assertNotEqual(conn_a.id, conn_b.id)
        self.assertEqual(conn_a.company_id, self.company.id)
        self.assertEqual(conn_b.company_id, self.company_b.id)

    # ----------------------------------------------------
    # 7) 동시 동일 요청 → 정확히 1건 생성
    # ----------------------------------------------------

    def test_concurrent_identical_request_creates_exactly_one(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )
        SessionLocal2 = sessionmaker(bind=engine2)

        try:
            key = "concurrent-identical-key"
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.store)

                token = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier="race-identical-seller",
                        credential_fields=VALID_COUPANG_FIELDS,
                    ),
                    self.company.id,
                ).verification_token

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    conn, _dup = service.create(
                        StoreConnectionCreateRequest(
                            marketplace_code="COUPANG",
                            display_name="동시 동일 요청",
                            seller_identifier="race-identical-seller",
                            credential_fields=VALID_COUPANG_FIELDS,
                            verification_token=token, idempotency_key=key,
                        ),
                        company_id=self.company.id, created_by=1,
                    )
                    results[name] = ("ok", conn.id)
                except Exception as e:  # noqa: BLE001
                    results[name] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("a",))
            t2 = threading.Thread(target=worker, args=("b",))
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            # 완전히 동일한 요청이므로, 성공한 쪽은 모두 같은 connection을
            # 가리켜야 한다(경쟁에서 진 쪽도 fingerprint가 일치하면 409가
            # 아니라 winner를 그대로 돌려받는다 — SQLite 잠금 타이밍에
            # 따라 드물게 둘 다 성공하지 못할 수도 있으므로, 기존
            # test_store_connection_concurrency.py와 동일하게 "성공한
            # id들이 서로 다르면 안 된다"만 엄격히 검증한다).
            ids = {v[1] for v in results.values() if v[0] == "ok"}
            self.assertEqual(len(ids), 1, results)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(StoreConnection)
                    .filter(
                        StoreConnection.company_id == self.company.id,
                        StoreConnection.creation_idempotency_key == key,
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 8) 동시 다른 요청 + 같은 key → 1건 성공, 패자는 409
    # ----------------------------------------------------

    def test_concurrent_different_request_same_key_loser_gets_409(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )
        SessionLocal2 = sessionmaker(bind=engine2)

        try:
            key = "concurrent-different-key"
            results = {}
            barrier = threading.Barrier(2)

            def worker(name, seller_identifier):

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.store)

                token = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier=seller_identifier,
                        credential_fields=VALID_COUPANG_FIELDS,
                    ),
                    self.company.id,
                ).verification_token

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    conn, _dup = service.create(
                        StoreConnectionCreateRequest(
                            marketplace_code="COUPANG",
                            display_name=f"동시 다른 요청 {name}",
                            seller_identifier=seller_identifier,
                            credential_fields=VALID_COUPANG_FIELDS,
                            verification_token=token, idempotency_key=key,
                        ),
                        company_id=self.company.id, created_by=1,
                    )
                    results[name] = ("ok", conn.id)
                except ConflictException:
                    results[name] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results[name] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(
                target=worker, args=("a", "race-diff-seller-a"),
            )
            t2 = threading.Thread(
                target=worker, args=("b", "race-diff-seller-b"),
            )
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            outcomes = [v[0] for v in results.values()]
            self.assertEqual(outcomes.count("ok"), 1, results)
            self.assertEqual(outcomes.count("conflict"), 1, results)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(StoreConnection)
                    .filter(
                        StoreConnection.company_id == self.company.id,
                        StoreConnection.creation_idempotency_key == key,
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 9) DB·응답·로그·audit_log에 Secret 원문 없음
    # ----------------------------------------------------

    def test_conflict_path_never_leaks_secret(self):

        key = _next_key("secret-leak")
        self._create(
            self.company.id, "COUPANG", "seller-9", "연결9",
            VALID_COUPANG_FIELDS, key,
        )

        changed_fields = dict(VALID_COUPANG_FIELDS)
        changed_fields["access_key"] = "OTHER_SECRET_zzzz1234"

        with self.assertRaises(ConflictException) as ctx:
            self._create(
                self.company.id, "COUPANG", "seller-9", "연결9",
                changed_fields, key, token="not-checked-before-409",
            )

        message = str(ctx.exception.detail)
        self.assertNotIn(SECRET_ACCESS_KEY, message)
        self.assertNotIn(SECRET_SECRET_KEY, message)
        self.assertNotIn("OTHER_SECRET_zzzz1234", message)

        self.db.commit()

        with open(self.db_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(SECRET_ACCESS_KEY.encode(), raw)
        self.assertNotIn(SECRET_SECRET_KEY.encode(), raw)
        self.assertNotIn(b"OTHER_SECRET_zzzz1234", raw)

        rows = self.db.execute(
            __import__("sqlalchemy").text("SELECT description FROM audit_logs"),
        ).fetchall()
        for (description,) in rows:
            self.assertNotIn(SECRET_ACCESS_KEY, description or "")
            self.assertNotIn(SECRET_SECRET_KEY, description or "")
            self.assertNotIn("OTHER_SECRET_zzzz1234", description or "")


if __name__ == "__main__":
    unittest.main()
