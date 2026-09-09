"""
=========================================================
Homez OS

File : tests/test_store_connection_concurrency.py

판매채널 연결 — 동시성 검증: 실스레드 + 별도 DB connection으로 검증한다.

1) 동일 idempotency_key로 동시 생성 시도 → 정확히 1개 연결만 생성
2) 동일 자연키(company/marketplace/seller)로 서로 다른 idempotency_key
   동시 생성 시도 → 정확히 1개만 성공, 나머지는 충돌로 거부
3) 동일 연결에 대한 동시 자격증명 교체(rotate) → 정확히 1개만 성공
   (낙관적 동시성 — credential_version 불일치로 나머지는 거부)
4) 경쟁 요청에서도 회사 격리 유지 — 회사 A/B가 동시에 서로 다른 연결을
   생성해도 서로의 결과가 섞이지 않는다(2026-08-01 CTO 재심사 반영)
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
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)
from app.domains.store_connection.schema import (
    StoreConnectionCreateRequest,
    StoreConnectionDeleteCredentialRequest,
    StoreConnectionDisableRequest,
    StoreConnectionRotateCredentialRequest,
    StoreConnectionVerifyRequest,
)
from app.domains.store_connection.service import StoreConnectionService

def _synchronize_before_conditional_write(service, second_barrier, method_names):
    """
    2026-08-05 CTO 재검증 지시 Gate C — 결정성 결함 수정.

    `threading.Barrier`는 "두 스레드가 이 지점에 도달했다"만 동기화할
    뿐, 그 이후 실제 바이트코드 실행이 겹친다는 보장은 없다. 특히
    `verify_existing`/`rotate_credential`처럼 "행을 읽어 expected_
    version을 계산 → 조건부 UPDATE"하는 짧은 임계구역에서는, GIL
    스케줄링이 한쪽 스레드에 충분히 긴 연속 slice를 주면 그 스레드가
    읽기부터 커밋까지 전부 끝내버린 뒤에야 다른 스레드가 자기 몫의
    읽기를 시작할 수 있다 — 이러면 두 스레드가 서로 다른
    credential_version(예: 1→2, 그다음 2→3)에 대해 각각 독립적으로
    성공하므로, "정확히 하나만 성공해야 한다"는 테스트 자체가 실제
    경쟁 상황을 재현하지 못한 채 우연히 통과하거나(대부분) 실패한다
    (드물게, 이번에 실제로 재현됨 — 15회 반복 중 1회, `test_concurrent_
    rotate_same_connection_only_one_succeeds`).

    이 헬퍼는 "행을 읽은 뒤·조건부 UPDATE 실행 직전" 지점에 두 번째
    barrier를 강제로 끼워 넣어, 두 스레드가 반드시 같은 expected_
    version을 읽은 뒤에만 조건부 UPDATE를 향해 동시에 진입하도록
    만든다 — 매번 결정적으로 실제 SQL 잠금 경쟁을 재현한다. 제품
    코드는 전혀 건드리지 않는다(서비스가 소유한 repository 인스턴스의
    바운드 메서드만 테스트 범위에서 감싼다).
    """

    for method_name in method_names:
        original = getattr(service.repository, method_name)

        def make_wrapper(original_method):

            def wrapper(*args, **kwargs):

                try:
                    second_barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                return original_method(*args, **kwargs)

            return wrapper

        setattr(service.repository, method_name, make_wrapper(original))


AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
    "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)

VALID_COUPANG_FIELDS = {
    "vendor_id": "A00123456", "access_key": "fake-access", "secret_key": "fake-secret",
}


class StoreConnectionConcurrencyTestCase(unittest.TestCase):

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

        seed_db = self.SessionLocal()
        company = Company(
            name="동시성 테스트 회사", business_number="999-99-99999", ceo="테스트",
            phone="02-000-0000", email="race@example.com", address="서울",
        )
        seed_db.add(company)
        seed_db.commit()
        self.company_id = company.id

        company_b = Company(
            name="동시성 테스트 회사B", business_number="888-88-88888", ceo="테스트B",
            phone="02-000-0001", email="race-b@example.com", address="서울",
        )
        seed_db.add(company_b)
        seed_db.commit()
        self.company_b_id = company_b.id

        seed_db.close()

        # 여러 스레드가 Secret을 저장/조회하므로 credential store도
        # 스레드 간에 공유되는 하나의 in-memory 인스턴스를 쓴다(실제
        # Windows Credential Manager도 프로세스 전역 공유 자원이므로
        # 이 공유 자체가 현실적인 동시성 조건을 재현한다).
        self.shared_credential_store = InMemoryCredentialStore()

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _threaded_engine(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )
        return engine2, sessionmaker(bind=engine2)

    # ----------------------------------------------------
    # 1) 동일 idempotency_key 동시 생성 → 정확히 1개
    # ----------------------------------------------------

    def test_concurrent_create_same_idempotency_key_creates_exactly_one(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)
            shared_key = "concurrent-create-race-key"

            def worker(name):

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)

                verify_resp = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier="race-seller-1",
                        credential_fields=VALID_COUPANG_FIELDS,
                    ),
                    self.company_id,
                )

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    conn, _dup = service.create(
                        StoreConnectionCreateRequest(
                            marketplace_code="COUPANG", display_name="레이스 연결",
                            seller_identifier="race-seller-1",
                            credential_fields=VALID_COUPANG_FIELDS,
                            verification_token=verify_resp.verification_token,
                            idempotency_key=shared_key,
                        ),
                        company_id=self.company_id, created_by=1,
                    )
                    results[name] = conn.id
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("a",))
            t2 = threading.Thread(target=worker, args=("b",))
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            ids = [v for v in results.values() if isinstance(v, int)]
            self.assertEqual(len(set(ids)), 1, results)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(StoreConnection)
                    .filter(
                        StoreConnection.company_id == self.company_id,
                        StoreConnection.creation_idempotency_key == shared_key,
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 2) 동일 자연키, 서로 다른 idempotency_key → 정확히 1개만 성공
    # ----------------------------------------------------

    def test_concurrent_create_same_natural_key_only_one_succeeds(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name, idempotency_key):

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)

                verify_resp = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier="race-seller-2",
                        credential_fields=VALID_COUPANG_FIELDS,
                    ),
                    self.company_id,
                )

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    conn, _dup = service.create(
                        StoreConnectionCreateRequest(
                            marketplace_code="COUPANG", display_name="레이스 연결2",
                            seller_identifier="race-seller-2",
                            credential_fields=VALID_COUPANG_FIELDS,
                            verification_token=verify_resp.verification_token,
                            idempotency_key=idempotency_key,
                        ),
                        company_id=self.company_id, created_by=1,
                    )
                    results[name] = ("ok", conn.id)
                except ConflictException:
                    results[name] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results[name] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("a", "natural-key-race-a"))
            t2 = threading.Thread(target=worker, args=("b", "natural-key-race-b"))
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
                        StoreConnection.company_id == self.company_id,
                        StoreConnection.marketplace_code == "COUPANG",
                        StoreConnection.seller_identifier == "race-seller-2",
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 3) 동일 연결의 동시 자격증명 교체 → 정확히 1개만 성공
    # ----------------------------------------------------

    def test_concurrent_rotate_same_connection_only_one_succeeds(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            setup_service = StoreConnectionService(setup_db, self.shared_credential_store)

            initial_verify = setup_service.verify_new(
                StoreConnectionVerifyRequest(
                    marketplace_code="COUPANG",
                    seller_identifier="race-seller-3",
                    credential_fields=VALID_COUPANG_FIELDS,
                ),
                self.company_id,
            )
            connection, _dup = setup_service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="회전 대상 연결",
                    seller_identifier="race-seller-3",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=initial_verify.verification_token,
                    idempotency_key="rotate-setup-key",
                ),
                company_id=self.company_id, created_by=1,
            )
            connection_id = connection.id
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)
            # 읽기(expected_version 계산)와 조건부 UPDATE 사이를 강제로
            # 동기화해 매번 결정적으로 실제 SQL 잠금 경쟁을 재현한다
            # (barrier 하나만으로는 GIL 스케줄링에 따라 한쪽이 통째로
            # 먼저 끝나버릴 수 있다 — 위 `_synchronize_before_
            # conditional_write` 참고).
            write_barrier = threading.Barrier(2)

            def worker(name, idempotency_key, access_key_suffix):

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier,
                    [
                        "apply_verification_success_conditional",
                        "apply_verification_failure_conditional",
                    ],
                )

                new_fields = dict(VALID_COUPANG_FIELDS)
                new_fields["access_key"] = f"rotated-{access_key_suffix}"

                verify_resp = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier="race-seller-3",
                        credential_fields=new_fields,
                    ),
                    self.company_id,
                )

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    rotated = service.rotate_credential(
                        connection_id, self.company_id,
                        StoreConnectionRotateCredentialRequest(
                            credential_fields=new_fields,
                            verification_token=verify_resp.verification_token,
                            idempotency_key=idempotency_key,
                        ),
                    )
                    results[name] = ("ok", rotated.credential_version)
                except ConflictException:
                    results[name] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results[name] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("a", "rotate-race-a", "a"))
            t2 = threading.Thread(target=worker, args=("b", "rotate-race-b", "b"))
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            outcomes = [v[0] for v in results.values()]
            self.assertEqual(outcomes.count("ok"), 1, results)
            self.assertEqual(outcomes.count("conflict"), 1, results)

            verify_db = SessionLocal2()
            try:
                final = (
                    verify_db.query(StoreConnection)
                    .filter(StoreConnection.id == connection_id)
                    .first()
                )
                # 정확히 한 번만 회전했으므로 버전은 1에서 2로만 올라간다
                # (두 번 반영되어 3이 되면 안 된다).
                self.assertEqual(final.credential_version, 2)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 3-2) 동시 verify_existing vs rotate_credential 경쟁
    #      (2026-08-04 V6 Gate 2 재감사에서 발견 — verify_existing이
    #      조건부 UPDATE의 rowcount를 확인하지 않던 결함 수정 검증)
    # ----------------------------------------------------

    def test_concurrent_verify_existing_and_rotate_only_one_succeeds(self):
        """
        같은 연결에 대해 verify_existing()과 rotate_credential()이
        동시에 실행되면, credential_version 낙관적 동시성 덕분에
        정확히 하나만 반영되고 나머지는 409(ConflictException)여야
        한다 — verify_existing이 "반영되지 않았는데도 성공처럼
        응답하는" 조용한 실패를 허용하지 않는지 확인한다.
        """

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            setup_service = StoreConnectionService(setup_db, self.shared_credential_store)

            initial_verify = setup_service.verify_new(
                StoreConnectionVerifyRequest(
                    marketplace_code="COUPANG",
                    seller_identifier="race-seller-verify-rotate",
                    credential_fields=VALID_COUPANG_FIELDS,
                ),
                self.company_id,
            )
            connection, _dup = setup_service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="검증-교체 경쟁 대상",
                    seller_identifier="race-seller-verify-rotate",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=initial_verify.verification_token,
                    idempotency_key="verify-rotate-setup-key",
                ),
                company_id=self.company_id, created_by=1,
            )
            connection_id = connection.id
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)
            # 읽기와 조건부 UPDATE 사이를 강제 동기화 — 위
            # `_synchronize_before_conditional_write` 문서 참고.
            write_barrier = threading.Barrier(2)

            def verify_worker():

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier,
                    [
                        "apply_verification_success_conditional",
                        "apply_verification_failure_conditional",
                    ],
                )
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                try:
                    resp = service.verify_existing(connection_id, self.company_id)
                    results["verify"] = ("ok", resp.success)
                except ConflictException:
                    results["verify"] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results["verify"] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            def rotate_worker():

                new_fields = dict(VALID_COUPANG_FIELDS)
                new_fields["access_key"] = "rotated-during-race"

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier,
                    [
                        "apply_verification_success_conditional",
                        "apply_verification_failure_conditional",
                    ],
                )

                verify_resp = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier="race-seller-verify-rotate",
                        credential_fields=new_fields,
                    ),
                    self.company_id,
                )
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                try:
                    rotated = service.rotate_credential(
                        connection_id, self.company_id,
                        StoreConnectionRotateCredentialRequest(
                            credential_fields=new_fields,
                            verification_token=verify_resp.verification_token,
                            idempotency_key="verify-rotate-race-key",
                        ),
                    )
                    results["rotate"] = ("ok", rotated.credential_version)
                except ConflictException:
                    results["rotate"] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results["rotate"] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=verify_worker)
            t2 = threading.Thread(target=rotate_worker)
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            outcomes = [v[0] for v in results.values()]
            # 정확히 하나만 "ok"여야 한다 — 둘 다 성공하거나 둘 다
            # 조용히 무시되면 안 된다(전자는 데이터 경쟁, 후자는 이번에
            # 고친 "반영 안 됐는데 성공 응답" 결함).
            self.assertEqual(outcomes.count("ok"), 1, results)
            self.assertEqual(outcomes.count("conflict"), 1, results)
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 3-3) 동시 verify_existing vs disable 경쟁 (2026-08-04 V6 Gate 2B)
    #      — disable_conditional이 credential_version을 함께 올리지
    #      않으면, 동시 진행 중이던 verify_existing이 방금 한 비활성화를
    #      조용히 CONNECTED로 되돌릴 수 있었다(repository.py 펜싱 수정
    #      검증).
    # ----------------------------------------------------

    def test_concurrent_verify_existing_and_disable_does_not_silently_revert_disable(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            setup_service = StoreConnectionService(setup_db, self.shared_credential_store)

            initial_verify = setup_service.verify_new(
                StoreConnectionVerifyRequest(
                    marketplace_code="COUPANG",
                    seller_identifier="race-seller-verify-disable",
                    credential_fields=VALID_COUPANG_FIELDS,
                ),
                self.company_id,
            )
            connection, _dup = setup_service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="검증-비활성화 경쟁 대상",
                    seller_identifier="race-seller-verify-disable",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=initial_verify.verification_token,
                    idempotency_key="verify-disable-setup-key",
                ),
                company_id=self.company_id, created_by=1,
            )
            connection_id = connection.id
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)
            # 읽기와 조건부 UPDATE 사이를 강제 동기화 — 위
            # `_synchronize_before_conditional_write` 문서 참고. 이
            # 테스트는 "정확히 하나만 성공"을 단언하지는 않지만, 진짜
            # 경쟁을 재현하지 못하면 disable이 verify보다 먼저 끝나버려
            # (순차 실행) 이 테스트가 검증하려는 "조용한 되돌림 방지"
            # 자체를 사실상 시험하지 못하는 거짓 통과가 될 수 있다.
            write_barrier = threading.Barrier(2)

            def verify_worker():

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier,
                    [
                        "apply_verification_success_conditional",
                        "apply_verification_failure_conditional",
                    ],
                )
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                try:
                    resp = service.verify_existing(connection_id, self.company_id)
                    results["verify"] = ("ok", resp.success)
                except ConflictException:
                    results["verify"] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results["verify"] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            def disable_worker():

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier, ["disable_conditional"],
                )
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                try:
                    disabled = service.disable(
                        connection_id, self.company_id,
                        StoreConnectionDisableRequest(idempotency_key="verify-disable-race-key"),
                    )
                    results["disable"] = ("ok", disabled.connection_status)
                except Exception as e:  # noqa: BLE001
                    results["disable"] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=verify_worker)
            t2 = threading.Thread(target=disable_worker)
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            # disable은 항상 성공해야 한다(idempotent 목표 상태 자체는
            # 항상 달성 가능). 핵심 확인 대상은 최종 DB 상태다.
            self.assertEqual(results.get("disable", (None,))[0], "ok", results)

            verify_db = SessionLocal2()
            try:
                final = (
                    verify_db.query(StoreConnection)
                    .filter(StoreConnection.id == connection_id)
                    .first()
                )
                # 결과 순서와 무관하게, 최종적으로 DISABLED가 아니면
                # verify가 그 이후 요청으로 다시 CONNECTED로 덮어썼다는
                # 뜻이므로 실패한다 — disable을 "조용히 되돌리는" 경쟁을
                # 절대 허용하지 않는다.
                self.assertEqual(final.connection_status, ConnectionStatus.DISABLED, results)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 3-4) 동시 verify_existing vs delete_credential 경쟁
    # ----------------------------------------------------

    def test_concurrent_verify_existing_and_delete_credential_does_not_resurrect_deleted_reference(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            setup_service = StoreConnectionService(setup_db, self.shared_credential_store)

            initial_verify = setup_service.verify_new(
                StoreConnectionVerifyRequest(
                    marketplace_code="COUPANG",
                    seller_identifier="race-seller-verify-delete",
                    credential_fields=VALID_COUPANG_FIELDS,
                ),
                self.company_id,
            )
            connection, _dup = setup_service.create(
                StoreConnectionCreateRequest(
                    marketplace_code="COUPANG", display_name="검증-삭제 경쟁 대상",
                    seller_identifier="race-seller-verify-delete",
                    credential_fields=VALID_COUPANG_FIELDS,
                    verification_token=initial_verify.verification_token,
                    idempotency_key="verify-delete-setup-key",
                ),
                company_id=self.company_id, created_by=1,
            )
            connection_id = connection.id
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)
            # 읽기와 조건부 UPDATE 사이를 강제 동기화 — 위
            # `_synchronize_before_conditional_write` 문서 참고.
            write_barrier = threading.Barrier(2)

            def verify_worker():

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier,
                    [
                        "apply_verification_success_conditional",
                        "apply_verification_failure_conditional",
                    ],
                )
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                try:
                    resp = service.verify_existing(connection_id, self.company_id)
                    results["verify"] = ("ok", resp.success)
                except ConflictException:
                    results["verify"] = ("conflict", None)
                except Exception as e:  # noqa: BLE001
                    results["verify"] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            def delete_worker():

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)
                _synchronize_before_conditional_write(
                    service, write_barrier, ["delete_credential_conditional"],
                )
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                try:
                    deleted = service.delete_credential(
                        connection_id, self.company_id,
                        StoreConnectionDeleteCredentialRequest(
                            confirm=True, idempotency_key="verify-delete-race-key",
                        ),
                    )
                    results["delete"] = ("ok", deleted.credential_reference)
                except Exception as e:  # noqa: BLE001
                    results["delete"] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=verify_worker)
            t2 = threading.Thread(target=delete_worker)
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            self.assertEqual(results.get("delete", (None,))[0], "ok", results)

            verify_db = SessionLocal2()
            try:
                final = (
                    verify_db.query(StoreConnection)
                    .filter(StoreConnection.id == connection_id)
                    .first()
                )
                # 삭제 이후 credential_reference는 반드시 NULL로 남아야
                # 한다 — verify_existing이 삭제 전에 읽은 옛 참조값으로
                # 되살리면(가리키는 실체가 이미 Credential Manager에서
                # 지워진 상태) 안 된다.
                self.assertIsNone(final.credential_reference, results)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 4) 경쟁 요청에서도 회사 격리 유지
    # ----------------------------------------------------

    def test_concurrent_create_different_companies_same_seller_identifier_stay_isolated(
        self,
    ):
        """
        회사 A와 회사 B가 동시에 "같은 seller_identifier, 같은
        idempotency_key 문자열"로 각자의 연결을 생성해도(우연히 같은
        클라이언트 라이브러리가 같은 패턴으로 키를 만드는 상황을
        재현), (company_id, creation_idempotency_key) 복합 UNIQUE
        덕분에 서로 다른 회사의 결과가 절대 섞이지 않아야 한다 — 둘 다
        각자 자기 회사 소유의 별도 connection을 성공적으로 생성해야
        한다(하나가 다른 하나의 idempotent 재호출로 오인되면 안 됨).
        """

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)
            shared_idempotency_key = "cross-company-same-key"

            def worker(name, company_id):

                thread_db = SessionLocal2()
                service = StoreConnectionService(thread_db, self.shared_credential_store)

                verify_resp = service.verify_new(
                    StoreConnectionVerifyRequest(
                        marketplace_code="COUPANG",
                        seller_identifier="shared-seller-name",
                        credential_fields=VALID_COUPANG_FIELDS,
                    ),
                    company_id,
                )

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    conn, dup = service.create(
                        StoreConnectionCreateRequest(
                            marketplace_code="COUPANG",
                            display_name=f"{name} 연결",
                            seller_identifier="shared-seller-name",
                            credential_fields=VALID_COUPANG_FIELDS,
                            verification_token=verify_resp.verification_token,
                            idempotency_key=shared_idempotency_key,
                        ),
                        company_id=company_id, created_by=1,
                    )
                    results[name] = (conn.id, conn.company_id, dup)
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("company-a", self.company_id))
            t2 = threading.Thread(target=worker, args=("company-b", self.company_b_id))
            t1.start()
            t2.start()
            t1.join(timeout=20)
            t2.join(timeout=20)

            # 두 회사 모두 성공해야 하고(서로 다른 UNIQUE 네임스페이스),
            # 반환된 connection.company_id는 항상 자기 자신의 회사와
            # 일치해야 한다(다른 회사 것이 섞여 반환되면 안 됨).
            self.assertEqual(results["company-a"][1], self.company_id, results)
            self.assertEqual(results["company-b"][1], self.company_b_id, results)
            self.assertNotEqual(
                results["company-a"][0], results["company-b"][0], results,
            )

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(StoreConnection)
                    .filter(
                        StoreConnection.creation_idempotency_key
                        == shared_idempotency_key,
                    )
                    .count()
                )
                # 회사별로 각각 1건씩, 총 2건 — 서로의 UNIQUE 네임스페이스를
                # 침범하지 않았다.
                self.assertEqual(count, 2)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
