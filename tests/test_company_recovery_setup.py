"""
=========================================================
Homez OS

File : tests/test_company_recovery_setup.py

HOMEZ Desktop 회사 초기 설정 "복구" 흐름 검증(2026-08-02, CTO 보안
보완 Gate R2) — companies=0이고 활성 SUPER_ADMIN 정확히 1명의
company_id가 NULL인 상태에서만 허용되는 1회성 복구 경로.

실제 homez.db는 사용하지 않는다 — 임시 SQLite 파일 DB + 별도 sqlite3
커넥션(원자적 복구 모듈 자체가 항상 그렇게 동작함)만 사용한다.
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

from app.core import company_recovery_setup as recovery_router
from app.core import setup_nonce as nonce_module
from app.core.desktop_token import clear_desktop_token
from app.core.desktop_token import set_desktop_token
from app.core.security import hash_password
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class _FakeClient:

    def __init__(self, host="127.0.0.1"):
        self.host = host


class _FakeRequest:

    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None


def _make_temp_db() -> str:

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Company.__table__, Role.__table__, RolePermission.__table__,
            Permission.__table__, User.__table__,
        ],
    )

    with engine.begin() as conn:
        conn.exec_driver_sql(AUDIT_LOGS_DDL)

    engine.dispose()

    return path


def _seed_role(db_path: str, code: str = "SUPER_ADMIN") -> int:

    # V7 Live Gate 4 재작업 — Role 모델에 `active`(NOT NULL, DB 레벨
    # DEFAULT 없음) 매핑이 추가되면서 Base.metadata.create_all()로 만든
    # 이 테스트 전용 roles 테이블도 그 컬럼을 갖게 됐다. 이 헬퍼는 ORM이
    # 아니라 raw sqlite3 드라이버로 직접 INSERT하므로 SQLAlchemy의
    # Python 쪽 default=True가 적용되지 않는다 — 명시적으로 채운다.
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO roles (name, code, active) VALUES (?, ?, 1)",
        (code, code),
    )
    role_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return role_id


def _seed_admin_without_company(
    db_path: str, role_id: int, email: str = "sin9484@gmail.com",
    active: int = 1,
) -> int:

    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO users "
        "(company_id, role_id, username, email, password, name, phone, active) "
        "VALUES (NULL, ?, ?, ?, ?, NULL, NULL, ?)",
        (role_id, email, email, hash_password("Str0ng!Passw0rd"), active),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return user_id


class AtomicCompanyRecoveryTestCase(unittest.TestCase):
    """1개 프로세스, 실 sqlite3 연결 기준 원자적 복구 동작."""

    def setUp(self):

        self.db_path = _make_temp_db()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_success_creates_company_links_admin_and_audit_log_atomically(self):

        role_id = _seed_role(self.db_path)
        admin_id = _seed_admin_without_company(self.db_path, role_id)

        result = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="에브리홈즈",
        )

        self.assertEqual(result.status, recovery_router.CompanyRecoveryStatus.SUCCESS)
        self.assertIsNotNone(result.company_id)
        self.assertEqual(result.user_id, admin_id)

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)

        user_row = conn.execute(
            "SELECT company_id FROM users WHERE id=?", (admin_id,),
        ).fetchone()
        self.assertEqual(user_row[0], result.company_id)

        company_row = conn.execute(
            "SELECT name FROM companies WHERE id=?", (result.company_id,),
        ).fetchone()
        self.assertEqual(company_row[0], "에브리홈즈")

        audit_rows = conn.execute(
            "SELECT action, entity, entity_id, company_id, user_id "
            "FROM audit_logs WHERE user_id=?",
            (admin_id,),
        ).fetchall()
        conn.close()

        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0][0], "RECOVER_COMPANY_AND_LINK_ADMIN")
        self.assertEqual(audit_rows[0][3], result.company_id)

    def test_not_eligible_when_company_already_exists(self):

        role_id = _seed_role(self.db_path)
        _seed_admin_without_company(self.db_path, role_id)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO companies (name, active) VALUES ('기존 회사', 1)",
        )
        conn.commit()
        conn.close()

        result = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="새 회사",
        )

        self.assertEqual(
            result.status, recovery_router.CompanyRecoveryStatus.NOT_ELIGIBLE,
        )

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)
        conn.close()

    def test_not_eligible_when_no_unlinked_super_admin(self):

        # SUPER_ADMIN 자체가 없는 상태.
        result = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="새 회사",
        )

        self.assertEqual(
            result.status, recovery_router.CompanyRecoveryStatus.NOT_ELIGIBLE,
        )

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 0)
        conn.close()

    def test_not_eligible_when_admin_already_linked(self):

        role_id = _seed_role(self.db_path)
        admin_id = _seed_admin_without_company(self.db_path, role_id)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE users SET company_id = 999 WHERE id = ?", (admin_id,),
        )
        conn.commit()
        conn.close()

        result = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="새 회사",
        )

        self.assertEqual(
            result.status, recovery_router.CompanyRecoveryStatus.NOT_ELIGIBLE,
        )

    def test_not_eligible_when_multiple_unlinked_super_admins(self):

        role_id = _seed_role(self.db_path)
        _seed_admin_without_company(self.db_path, role_id, email="a@test.com")
        _seed_admin_without_company(self.db_path, role_id, email="b@test.com")

        result = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="새 회사",
        )

        # 모호한 상태 — 자동으로 하나를 고르지 않는다.
        self.assertEqual(
            result.status, recovery_router.CompanyRecoveryStatus.NOT_ELIGIBLE,
        )

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 0)
        conn.close()

    def test_not_eligible_when_admin_inactive(self):

        role_id = _seed_role(self.db_path)
        _seed_admin_without_company(self.db_path, role_id, active=0)

        result = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="새 회사",
        )

        self.assertEqual(
            result.status, recovery_router.CompanyRecoveryStatus.NOT_ELIGIBLE,
        )

    def test_mid_transaction_failure_rolls_back_company_insert_too(self):

        role_id = _seed_role(self.db_path)
        _seed_admin_without_company(self.db_path, role_id)

        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE audit_logs")
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.OperationalError):
            recovery_router.atomic_recover_company_and_link_admin(
                self.db_path, company_name="새 회사",
            )

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 0)
        user_row = conn.execute("SELECT company_id FROM users").fetchone()
        conn.close()

        self.assertIsNone(user_row[0])

    def test_concurrent_recovery_requests_exactly_one_succeeds(self):

        role_id = _seed_role(self.db_path)
        _seed_admin_without_company(self.db_path, role_id)

        results = []
        barrier = threading.Barrier(2)

        def _attempt():
            barrier.wait(timeout=5)
            r = recovery_router.atomic_recover_company_and_link_admin(
                self.db_path, company_name="경쟁 회사",
            )
            results.append(r)

        threads = [threading.Thread(target=_attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(results), 2)
        statuses = sorted(r.status.value for r in results)
        self.assertEqual(statuses, ["NOT_ELIGIBLE", "SUCCESS"])

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)
        conn.close()

    def test_reinitialize_after_success_is_not_eligible(self):

        role_id = _seed_role(self.db_path)
        _seed_admin_without_company(self.db_path, role_id)

        first = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="첫 회사",
        )
        self.assertEqual(first.status, recovery_router.CompanyRecoveryStatus.SUCCESS)

        second = recovery_router.atomic_recover_company_and_link_admin(
            self.db_path, company_name="두 번째 회사",
        )
        self.assertEqual(
            second.status, recovery_router.CompanyRecoveryStatus.NOT_ELIGIBLE,
        )

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)
        conn.close()


class CompanyRecoveryRouterTestCase(unittest.TestCase):
    """의존성/엔드포인트 함수를 직접 호출해 HTTP 계층 동작을 검증한다."""

    def setUp(self):

        self.db_path = _make_temp_db()
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.role_id = _seed_role(self.db_path)
        self.admin_id = _seed_admin_without_company(self.db_path, self.role_id)

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

        return patch.object(
            recovery_router, "resolve_sqlite_path", return_value=self.db_path,
        )

    def _valid_body(self, nonce, company_name="에브리홈즈"):

        return recovery_router.CompanyRecoveryInitializeRequest(
            company_name=company_name, setup_nonce=nonce,
        )

    def test_status_recovery_required_true_when_eligible(self):

        result = recovery_router.get_company_recovery_status(db=self.db)
        self.assertTrue(result.recovery_required)
        self.assertEqual(result.admin_email, "sin9484@gmail.com")

    def test_status_recovery_required_false_when_company_exists(self):

        self.db.add(Company(name="기존 회사"))
        self.db.commit()

        result = recovery_router.get_company_recovery_status(db=self.db)
        self.assertFalse(result.recovery_required)
        self.assertIsNone(result.admin_email)

    def test_status_recovery_required_false_when_no_eligible_admin(self):

        fresh_path = _make_temp_db()
        try:
            engine = create_engine(f"sqlite:///{fresh_path}")
            db = sessionmaker(bind=engine)()
            try:
                result = recovery_router.get_company_recovery_status(db=db)
                self.assertFalse(result.recovery_required)
            finally:
                db.close()
                engine.dispose()
        finally:
            os.remove(fresh_path)

    def test_missing_desktop_token_dependency_rejects(self):

        clear_desktop_token()

        with self.assertRaises(HTTPException) as ctx:
            recovery_router.require_desktop_mode_and_token(homez_desktop_token=None)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_external_host_rejected(self):

        req = _FakeRequest(headers={"host": "evil.example.com"})

        with self.assertRaises(HTTPException) as ctx:
            recovery_router.require_matching_origin(req)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_non_loopback_client_rejected(self):

        req = _FakeRequest(client_host="203.0.113.10")

        with self.assertRaises(HTTPException) as ctx:
            recovery_router.require_loopback(req)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_valid_nonce_and_data_recovers_company(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            result = recovery_router.initialize_company_recovery(
                self._valid_body(nonce), db=self.db,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.user_id, self.admin_id)
        self.assertIsNone(nonce_module.get_current_nonce_for_bridge())

    def test_missing_nonce_rejected(self):

        nonce_module.generate_setup_nonce()

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                recovery_router.initialize_company_recovery(
                    self._valid_body(""), db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 403)

    def test_nonce_reuse_rejected(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            first = recovery_router.initialize_company_recovery(
                self._valid_body(nonce), db=self.db,
            )
            self.assertTrue(first.success)

            fresh_db_path = _make_temp_db()
            try:
                role_id = _seed_role(fresh_db_path)
                _seed_admin_without_company(fresh_db_path, role_id)

                fresh_engine = create_engine(f"sqlite:///{fresh_db_path}")
                fresh_db = sessionmaker(bind=fresh_engine)()
                try:
                    with patch.object(
                        recovery_router, "resolve_sqlite_path",
                        return_value=fresh_db_path,
                    ):
                        with self.assertRaises(HTTPException) as ctx:
                            recovery_router.initialize_company_recovery(
                                self._valid_body(nonce), db=fresh_db,
                            )
                    self.assertEqual(ctx.exception.status_code, 403)
                finally:
                    fresh_db.close()
                    fresh_engine.dispose()
            finally:
                os.remove(fresh_db_path)

    def test_reinitialize_after_success_is_blocked(self):

        nonce1 = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            first = recovery_router.initialize_company_recovery(
                self._valid_body(nonce1), db=self.db,
            )
            self.assertTrue(first.success)

            nonce2 = nonce_module.generate_setup_nonce()

            with self.assertRaises(HTTPException) as ctx:
                recovery_router.initialize_company_recovery(
                    self._valid_body(nonce2), db=self.db,
                )

            self.assertEqual(ctx.exception.status_code, 409)

    def test_empty_company_name_rejected(self):

        nonce = nonce_module.generate_setup_nonce()

        with self._override_db_path():
            with self.assertRaises(HTTPException) as ctx:
                recovery_router.initialize_company_recovery(
                    self._valid_body(nonce, company_name="   "), db=self.db,
                )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_request_schema_has_no_client_controlled_identifiers(self):
        """
        요청 바디에는 company_name/setup_nonce만 존재한다 — 클라이언트가
        company_id/role_id/user_id를 지정할 방법 자체가 없다(대상 관리자와
        생성될 Company id는 전부 서버가 원자적 조회로 결정한다).
        """

        fields = set(
            recovery_router.CompanyRecoveryInitializeRequest.model_fields.keys(),
        )
        self.assertEqual(fields, {"company_name", "setup_nonce"})


if __name__ == "__main__":
    unittest.main()
