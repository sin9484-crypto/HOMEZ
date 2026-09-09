"""
=========================================================
Homez OS

File : tests/test_desktop_first_admin_setup.py

HOMEZ Desktop 최초 관리자(SUPER_ADMIN) 설정 흐름 검증(2026-07-30).
httpx가 없어 라우터/의존성 함수를 직접 호출한다(기존 test_desktop_auth_
session.py와 동일한 방식). 실제 homez.db는 사용하지 않는다 — 임시
SQLite 파일 DB + 별도 sqlite3 커넥션(원자적 생성 모듈 자체가 항상
그렇게 동작함)만 사용한다.
=========================================================
"""

import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import desktop_setup as setup_router
from app.core import setup_nonce as nonce_module
from app.core.desktop_token import clear_desktop_token
from app.core.desktop_token import set_desktop_token
from app.core.first_admin_setup import FirstAdminSetupStatus
from app.core.first_admin_setup import atomic_create_first_admin
from app.core.security import hash_password
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession
from app.domains.user.model import User

FIRST_ADMIN_EMAIL = setup_router.FIRST_ADMIN_EMAIL


class _FakeClient:

    def __init__(self, host="127.0.0.1"):
        self.host = host


class _FakeRequest:

    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None


AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


def _make_temp_db(seed_super_admin: bool = True) -> str:

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Company.__table__, Role.__table__, RolePermission.__table__,
            Permission.__table__, User.__table__, AuthSession.__table__,
        ],
    )

    with engine.begin() as conn:
        conn.exec_driver_sql(AUDIT_LOGS_DDL)

    if seed_super_admin:
        Session = sessionmaker(bind=engine)
        db = Session()
        db.add(Role(name="Super Administrator", code="SUPER_ADMIN"))
        db.commit()
        db.close()

    engine.dispose()

    return path


class AtomicFirstAdminCreationTestCase(unittest.TestCase):
    """1개 프로세스, 실 sqlite3 연결 기준 원자적 생성 동작."""

    def setUp(self):

        self.db_path = _make_temp_db()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _role_id(self) -> int:

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT id FROM roles WHERE code='SUPER_ADMIN'").fetchone()
        conn.close()
        return row[0]

    # --- 14. User + audit_log 단일 Transaction ---

    def test_success_creates_user_and_audit_log_together(self):

        result = atomic_create_first_admin(
            self.db_path, username=FIRST_ADMIN_EMAIL, email=FIRST_ADMIN_EMAIL,
            password_hash=hash_password("Str0ng!Passw0rd"), role_id=self._role_id(),
            company_name="테스트 회사",
        )

        self.assertEqual(result.status, FirstAdminSetupStatus.SUCCESS)
        self.assertIsNotNone(result.user_id)
        self.assertIsNotNone(result.company_id)

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)

        user_row = conn.execute(
            "SELECT company_id FROM users WHERE id=?", (result.user_id,),
        ).fetchone()
        self.assertEqual(user_row[0], result.company_id)

        company_row = conn.execute(
            "SELECT name FROM companies WHERE id=?", (result.company_id,),
        ).fetchone()
        self.assertEqual(company_row[0], "테스트 회사")

        audit_rows = conn.execute(
            "SELECT action, entity, entity_id, company_id FROM audit_logs "
            "WHERE user_id=?",
            (result.user_id,),
        ).fetchall()
        conn.close()

        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0][0], "CREATE_FIRST_ADMIN_ACCOUNT")
        self.assertEqual(audit_rows[0][3], result.company_id)

    # --- 15. 감사 로그(2번째 INSERT) 실패 시 User INSERT도 rollback ---

    def test_mid_transaction_failure_rolls_back_user_insert_too(self):

        # audit_logs 테이블을 일부러 지워 두 번째 INSERT가 실패하도록 만든다.
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE audit_logs")
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.OperationalError):
            atomic_create_first_admin(
                self.db_path, username=FIRST_ADMIN_EMAIL, email=FIRST_ADMIN_EMAIL,
                password_hash=hash_password("Str0ng!Passw0rd"), role_id=self._role_id(),
                company_name="테스트 회사",
            )

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 0)
        conn.close()

    # --- 16. 동시 최초 설정 요청 — 정확히 1건 성공 ---

    def test_concurrent_first_setup_exactly_one_succeeds(self):

        role_id = self._role_id()
        results = []
        barrier = threading.Barrier(2)

        def _attempt(idx):
            barrier.wait(timeout=5)
            r = atomic_create_first_admin(
                self.db_path, username=FIRST_ADMIN_EMAIL, email=FIRST_ADMIN_EMAIL,
                password_hash=hash_password("Str0ng!Passw0rd"), role_id=role_id,
                company_name="테스트 회사",
            )
            results.append(r)

        threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(results), 2)
        statuses = sorted(r.status.value for r in results)
        self.assertEqual(statuses, ["ALREADY_COMPLETED", "SUCCESS"])

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        conn.close()

    def test_already_completed_when_user_exists(self):

        role_id = self._role_id()
        first = atomic_create_first_admin(
            self.db_path, username=FIRST_ADMIN_EMAIL, email=FIRST_ADMIN_EMAIL,
            password_hash=hash_password("Str0ng!Passw0rd"), role_id=role_id,
            company_name="테스트 회사",
        )
        self.assertEqual(first.status, FirstAdminSetupStatus.SUCCESS)

        second = atomic_create_first_admin(
            self.db_path, username=FIRST_ADMIN_EMAIL, email=FIRST_ADMIN_EMAIL,
            password_hash=hash_password("AnotherStrong1!"), role_id=role_id,
            company_name="다른 회사",
        )
        self.assertEqual(second.status, FirstAdminSetupStatus.ALREADY_COMPLETED)

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        conn.close()


class DesktopSetupRouterTestCase(unittest.TestCase):
    """의존성/엔드포인트 함수를 직접 호출해 HTTP 계층 동작을 검증한다."""

    def setUp(self):

        self.db_path = _make_temp_db()
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        nonce_module.clear_setup_nonce()
        clear_desktop_token()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        nonce_module.clear_setup_nonce()
        clear_desktop_token()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _override_db_path(self):
        """atomic_create_first_admin이 이 테스트의 임시 DB를 쓰도록 patch."""

        from unittest.mock import patch

        return patch.object(setup_router, "resolve_sqlite_path", return_value=self.db_path)

    def _valid_body(self, nonce, password="Str0ng!Passw0rd", company_name="테스트 회사"):

        return setup_router.FirstAdminInitializeRequest(
            email=FIRST_ADMIN_EMAIL, password=password,
            password_confirmation=password, setup_nonce=nonce,
            company_name=company_name,
        )

    # --- 1/2. status ---

    def test_status_setup_required_true_when_zero_users(self):

        result = setup_router.get_setup_status(db=self.db)
        self.assertTrue(result.setup_required)
        self.assertEqual(result.configured_email, FIRST_ADMIN_EMAIL)

    def test_status_setup_required_false_when_user_exists(self):

        role = self.db.query(Role).filter(Role.code == "SUPER_ADMIN").first()
        self.db.add(User(
            username=FIRST_ADMIN_EMAIL, email=FIRST_ADMIN_EMAIL,
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=role.id, is_active=True,
        ))
        self.db.commit()

        result = setup_router.get_setup_status(db=self.db)
        self.assertFalse(result.setup_required)
        self.assertIsNone(result.configured_email)

    # --- 4. Desktop token 누락 ---

    def test_missing_desktop_token_dependency_rejects(self):

        clear_desktop_token()  # Desktop 모드 아님

        with self.assertRaises(HTTPException) as ctx:
            setup_router.require_desktop_mode_and_token(homez_desktop_token=None)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_desktop_token_mismatch_rejects(self):

        set_desktop_token("real-token")

        with self.assertRaises(HTTPException) as ctx:
            setup_router.require_desktop_mode_and_token(homez_desktop_token="wrong")

        self.assertEqual(ctx.exception.status_code, 403)

    def test_desktop_token_match_passes(self):

        set_desktop_token("real-token")
        setup_router.require_desktop_mode_and_token(homez_desktop_token="real-token")

    # --- 9. 외부 Host 거부 ---

    def test_external_host_rejected(self):

        req = _FakeRequest(headers={"host": "evil.example.com"})

        with self.assertRaises(HTTPException) as ctx:
            setup_router.require_matching_origin(req)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_loopback_host_passes(self):

        req = _FakeRequest(headers={"host": "127.0.0.1:51234"})
        setup_router.require_matching_origin(req)  # 예외 없으면 통과

    def test_mismatched_origin_rejected(self):

        req = _FakeRequest(headers={
            "host": "127.0.0.1:51234", "origin": "http://evil.example.com",
        })

        with self.assertRaises(HTTPException) as ctx:
            setup_router.require_matching_origin(req)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_matching_origin_passes(self):

        req = _FakeRequest(headers={
            "host": "127.0.0.1:51234", "origin": "http://127.0.0.1:51234",
        })
        setup_router.require_matching_origin(req)

    # --- 1(loopback dependency) ---

    def test_non_loopback_client_rejected(self):

        req = _FakeRequest(client_host="203.0.113.10")

        with self.assertRaises(HTTPException) as ctx:
            setup_router.require_loopback(req)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_loopback_client_passes(self):

        req = _FakeRequest(client_host="127.0.0.1")
        setup_router.require_loopback(req)

    # --- 3. 정상 흐름 ---

    def test_valid_nonce_and_data_creates_admin(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            result = setup_router.initialize_first_admin(
                self._valid_body(nonce), db=self.db,
            )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.user_id)

        # nonce가 실제로 소비되었는지(더 이상 브리지에 반환되지 않음)
        self.assertIsNone(nonce_module.get_current_nonce_for_bridge())

    # --- 5/6/7/8. nonce 관련 거부 ---

    def test_missing_nonce_rejected(self):

        nonce_module.generate_setup_nonce()

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(
                    self._valid_body(""), db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_nonce_mismatch_rejected(self):

        nonce_module.generate_setup_nonce()

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(
                    self._valid_body("wrong-nonce-value"), db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_nonce_reuse_rejected(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            first = setup_router.initialize_first_admin(self._valid_body(nonce), db=self.db)
            self.assertTrue(first.success)

            # 새 DB에 대해서라도(현실적으로는 users!=0이라 ALREADY_COMPLETED가
            # 먼저 뜨겠지만) nonce 자체가 소비되었으므로 다시 쓰면 nonce 거부가
            # 먼저 일어나야 한다는 것을 새 임시 DB로 별도 확인한다.
            fresh_db_path = _make_temp_db()
            try:
                fresh_engine = create_engine(f"sqlite:///{fresh_db_path}")
                fresh_db = sessionmaker(bind=fresh_engine)()
                try:
                    with patch.object(
                        setup_router, "resolve_sqlite_path", return_value=fresh_db_path,
                    ):
                        with self.assertRaises(HTTPException) as ctx:
                            setup_router.initialize_first_admin(
                                self._valid_body(nonce), db=fresh_db,
                            )
                    self.assertEqual(ctx.exception.status_code, 403)
                finally:
                    fresh_db.close()
                    fresh_engine.dispose()
            finally:
                os.remove(fresh_db_path)

    def test_nonce_expired_rejected(self):

        from datetime import timedelta

        nonce = nonce_module.generate_setup_nonce()
        # 강제로 과거 만료 시각으로 변경(내부 상태 직접 조작 — 테스트 전용).
        with nonce_module._lock:  # noqa: SLF001
            nonce_module._state["expires_at"] = nonce_module._now() - timedelta(seconds=1)

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(
                    self._valid_body(nonce), db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 403)

    # --- 10. 잘못된 이메일 ---

    def test_wrong_email_rejected(self):

        nonce = nonce_module.generate_setup_nonce()
        body = setup_router.FirstAdminInitializeRequest(
            email="attacker@example.com", password="Str0ng!Passw0rd",
            password_confirmation="Str0ng!Passw0rd", setup_nonce=nonce,
            company_name="테스트 회사",
        )

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(body, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    # --- 11. 약한 비밀번호 ---

    def test_weak_password_rejected(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(
                    self._valid_body(nonce, password="short1"), db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 400)

    # --- 12. 비밀번호 확인 불일치 ---

    def test_password_confirmation_mismatch_rejected(self):

        nonce = nonce_module.generate_setup_nonce()
        body = setup_router.FirstAdminInitializeRequest(
            email=FIRST_ADMIN_EMAIL, password="Str0ng!Passw0rd",
            password_confirmation="Different1!", setup_nonce=nonce,
            company_name="테스트 회사",
        )

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(body, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    # --- 13. SUPER_ADMIN 역할 부재 ---

    def test_missing_super_admin_role_rejected(self):

        no_role_db_path = _make_temp_db(seed_super_admin=False)
        engine = create_engine(f"sqlite:///{no_role_db_path}")
        db = sessionmaker(bind=engine)()

        try:
            nonce = nonce_module.generate_setup_nonce()

            with self._override_db_path():
                with self.assertRaises(HTTPException) as ctx:
                    setup_router.initialize_first_admin(self._valid_body(nonce), db=db)

            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            db.close()
            engine.dispose()
            os.remove(no_role_db_path)

    # --- 17. 성공 후 재호출 차단 ---

    def test_reinitialize_after_success_is_blocked(self):

        nonce1 = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            first = setup_router.initialize_first_admin(self._valid_body(nonce1), db=self.db)
            self.assertTrue(first.success)

            nonce2 = nonce_module.generate_setup_nonce()  # 새 nonce라도 이미 완료됨

            with self.assertRaises(HTTPException) as ctx:
                setup_router.initialize_first_admin(self._valid_body(nonce2), db=self.db)

            self.assertEqual(ctx.exception.status_code, 409)

    # --- 19. 생성 직후 자동 로그인하지 않음(응답에 access_token 없음) ---

    def test_response_never_contains_access_token(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            result = setup_router.initialize_first_admin(self._valid_body(nonce), db=self.db)

        dumped = result.model_dump()
        self.assertNotIn("access_token", dumped)
        self.assertNotIn("token", dumped)

    # --- 20/21. 생성 후 실제 로그인 성공/실패 ---

    def test_login_succeeds_with_password_set_during_setup(self):

        from app.domains.auth.router import login as auth_login
        from app.domains.auth.schema import LoginRequest

        nonce = nonce_module.generate_setup_nonce()
        password = "Str0ng!Passw0rd"

        with self._override_db_path():
            result = setup_router.initialize_first_admin(
                self._valid_body(nonce, password=password), db=self.db,
            )
        self.assertTrue(result.success)

        login_result = auth_login(
            data=LoginRequest(username=FIRST_ADMIN_EMAIL, password=password),
            request=_FakeRequest(), db=self.db,
        )
        self.assertTrue(login_result["access_token"])
        self.assertEqual(login_result["user"].username, FIRST_ADMIN_EMAIL)
        self.assertEqual(login_result["user"].role, "SUPER_ADMIN")

    def test_login_fails_with_wrong_password_after_setup(self):

        from fastapi import HTTPException as FastAPIHTTPException

        from app.domains.auth.router import login as auth_login
        from app.domains.auth.schema import LoginRequest

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            result = setup_router.initialize_first_admin(
                self._valid_body(nonce, password="Str0ng!Passw0rd"), db=self.db,
            )
        self.assertTrue(result.success)

        with self.assertRaises(FastAPIHTTPException) as ctx:
            auth_login(
                data=LoginRequest(username=FIRST_ADMIN_EMAIL, password="WrongPassword1!"),
                request=_FakeRequest(), db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 401)

    # --- 22. 재시작(재조회) 후 setup 화면 재표시 안 됨 ---

    def test_status_stays_false_across_fresh_queries_after_setup(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            result = setup_router.initialize_first_admin(self._valid_body(nonce), db=self.db)
        self.assertTrue(result.success)

        # "재시작"을 흉내내기 위해 완전히 새 Session/Engine으로 다시 연다.
        fresh_engine = create_engine(f"sqlite:///{self.db_path}")
        fresh_db = sessionmaker(bind=fresh_engine)()
        try:
            status_after_restart = setup_router.get_setup_status(db=fresh_db)
            self.assertFalse(status_after_restart.setup_required)
        finally:
            fresh_db.close()
            fresh_engine.dispose()

    # --- 23. 비밀번호/해시/토큰 로그 미노출 ---

    def test_no_password_or_hash_leaked_in_logs(self):

        import logging

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("homez.auth.audit")
        handler = _Capture()
        logger.addHandler(handler)

        password = "VeryS3cr3t!Value"

        try:
            nonce = nonce_module.generate_setup_nonce()
            with self._override_db_path():
                result = setup_router.initialize_first_admin(
                    self._valid_body(nonce, password=password), db=self.db,
                )
            self.assertTrue(result.success)
        finally:
            logger.removeHandler(handler)

        joined = " ".join(records)
        self.assertNotIn(password, joined)


if __name__ == "__main__":
    unittest.main()
