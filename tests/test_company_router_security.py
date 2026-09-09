"""
=========================================================
Homez OS

File : tests/test_company_router_security.py

app/domains/company/router.py Critical 결함 수정 검증(2026-08-02) —
인증 없이 누구나 회사 정보를 조회·수정·삭제할 수 있던 결함. 이제
로그인 + SUPER_ADMIN + 자기 회사 격리가 강제되는지 라우터 함수를
직접 호출해 검증한다(httpx 미설치로 TestClient 불가 — 기존 관례와
동일). 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.guard import SuperAdminGuard
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.core.security import hash_password
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.company.router import delete_company
from app.domains.company.router import get_companies
from app.domains.company.router import get_company
from app.domains.company.router import update_company
from app.domains.company.schema import CompanyUpdate
from app.domains.role.model import Role
from app.domains.user.model import User

STRONG_PASSWORD = "Str0ng!Passw0rd"


class CompanyRouterSecurityTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, Role.__table__, User.__table__],
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), ip_address VARCHAR(50))",
            )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.super_admin_role, self.viewer_role])
        self.db.flush()

        self.company_a = Company(name="Company A", active=True)
        self.company_b = Company(name="Company B", active=True)
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        reset_recent_auth_state_for_tests()

    def _create_user(self, username, role_id, company_id):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(STRONG_PASSWORD), role_id=role_id,
            company_id=company_id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    # --------------------------------------------------
    # 인증/권한
    # --------------------------------------------------

    def test_regular_user_rejected_by_guard(self):

        viewer = self._create_user("viewer_co", self.viewer_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            SuperAdminGuard(current_user=viewer)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_super_admin_list_returns_only_own_company(self):

        admin_a = self._create_user("admin_co_a", self.super_admin_role.id, self.company_a.id)

        result = get_companies(current_user=admin_a, db=self.db)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, self.company_a.id)

    # --------------------------------------------------
    # company_id 격리 — 다른 회사는 404
    # --------------------------------------------------

    def test_get_other_company_returns_404_not_403(self):

        admin_a = self._create_user("admin_get_a", self.super_admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            get_company(self.company_b.id, current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_own_company_succeeds(self):

        admin_a = self._create_user("admin_get_own", self.super_admin_role.id, self.company_a.id)

        result = get_company(self.company_a.id, current_user=admin_a, db=self.db)

        self.assertEqual(result.id, self.company_a.id)

    def test_get_nonexistent_company_returns_404(self):

        admin_a = self._create_user("admin_get_none", self.super_admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            get_company(999999, current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_update_other_company_returns_404_and_no_change(self):

        admin_a = self._create_user("admin_upd_a", self.super_admin_role.id, self.company_a.id)
        original_name = self.company_b.name

        with self.assertRaises(HTTPException) as ctx:
            update_company(
                self.company_b.id, CompanyUpdate(name="침입 시도"),
                current_user=admin_a, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 404)

        self.db.refresh(self.company_b)
        self.assertEqual(self.company_b.name, original_name)

    def test_update_own_company_succeeds_with_recent_auth_token(self):

        admin_a = self._create_user("admin_upd_own", self.super_admin_role.id, self.company_a.id)
        token, _ = issue_recent_auth_token(admin_a.id)

        result = update_company(
            self.company_a.id, CompanyUpdate(name="새 이름"),
            current_user=admin_a, db=self.db, recent_auth_token=token,
        )

        self.assertEqual(result.name, "새 이름")

    def test_update_non_name_fields_does_not_require_recent_auth(self):
        """이름을 바꾸지 않는 일반 필드 수정에는 recent-auth를 요구하지 않는다."""

        admin_a = self._create_user("admin_upd_other_field", self.super_admin_role.id, self.company_a.id)

        result = update_company(
            self.company_a.id, CompanyUpdate(ceo="새 대표"),
            current_user=admin_a, db=self.db, recent_auth_token=None,
        )

        self.assertEqual(result.ceo, "새 대표")

    def test_update_company_name_without_recent_auth_token_rejected(self):

        admin_a = self._create_user("admin_upd_no_token", self.super_admin_role.id, self.company_a.id)
        original_name = self.company_a.name

        with self.assertRaises(HTTPException) as ctx:
            update_company(
                self.company_a.id, CompanyUpdate(name="토큰 없이 시도"),
                current_user=admin_a, db=self.db, recent_auth_token=None,
            )

        self.assertEqual(ctx.exception.status_code, 401)
        self.db.refresh(self.company_a)
        self.assertEqual(self.company_a.name, original_name)

    def test_update_company_name_with_wrong_users_token_rejected(self):

        admin_a = self._create_user("admin_upd_wrong_user", self.super_admin_role.id, self.company_a.id)
        other_admin = self._create_user("admin_other_owner", self.super_admin_role.id, self.company_a.id)
        token, _ = issue_recent_auth_token(other_admin.id)

        with self.assertRaises(HTTPException) as ctx:
            update_company(
                self.company_a.id, CompanyUpdate(name="남의 토큰"),
                current_user=admin_a, db=self.db, recent_auth_token=token,
            )

        self.assertEqual(ctx.exception.status_code, 401)

    def test_recent_auth_token_is_single_use(self):

        admin_a = self._create_user("admin_upd_reuse", self.super_admin_role.id, self.company_a.id)
        token, _ = issue_recent_auth_token(admin_a.id)

        update_company(
            self.company_a.id, CompanyUpdate(name="첫 변경"),
            current_user=admin_a, db=self.db, recent_auth_token=token,
        )

        with self.assertRaises(HTTPException) as ctx:
            update_company(
                self.company_a.id, CompanyUpdate(name="재사용 시도"),
                current_user=admin_a, db=self.db, recent_auth_token=token,
            )

        self.assertEqual(ctx.exception.status_code, 401)
        self.db.refresh(self.company_a)
        self.assertEqual(self.company_a.name, "첫 변경")

    def test_company_name_change_audit_log_has_no_name_values(self):

        from sqlalchemy import text as sa_text

        admin_a = self._create_user("admin_upd_audit", self.super_admin_role.id, self.company_a.id)
        old_name = self.company_a.name

        token, _ = issue_recent_auth_token(admin_a.id)
        update_company(
            self.company_a.id, CompanyUpdate(name="민감할 수 있는 새 회사명"),
            current_user=admin_a, db=self.db, recent_auth_token=token,
        )

        row = self.db.execute(
            sa_text("SELECT action, description FROM audit_logs WHERE action = 'COMPANY_NAME_UPDATED'"),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertNotIn("민감할 수 있는 새 회사명", row[1])
        self.assertNotIn(old_name, row[1])

    def test_delete_other_company_returns_404(self):

        admin_a = self._create_user("admin_del_a", self.super_admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            delete_company(self.company_b.id, current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

        still_exists = self.db.query(Company).filter(Company.id == self.company_b.id).first()
        self.assertIsNotNone(still_exists)

    # --------------------------------------------------
    # 마지막 운영 회사 / 연결된 회사 삭제 방지
    # --------------------------------------------------

    def test_delete_own_company_blocked_when_users_linked(self):

        admin_a = self._create_user("admin_del_own", self.super_admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            delete_company(self.company_a.id, current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

        still_exists = self.db.query(Company).filter(Company.id == self.company_a.id).first()
        self.assertIsNotNone(still_exists)

    def test_delete_blocked_when_only_one_company_even_without_users(self):
        """
        연결된 사용자가 없어도, 시스템에 회사가 1개뿐이면(마지막 운영
        회사) 삭제를 막아야 한다 — company_b를 지워 1개만 남긴 뒤
        재확인한다(단, company_b 자체는 admin_a의 회사가 아니므로 이
        시나리오는 admin_a가 company_a 하나만 남기고 나머지를 지운
        상황을 흉내내되, 실제로는 다른 회사 삭제가 이미 격리로 막혀
        있으므로 대신 직접 두 번째 회사 행을 지워 "회사가 1개만
        남은" 데이터 상태를 만든 뒤, 그 유일한 회사에 연결된 사용자가
        없는 상태로 삭제를 시도한다.
        """

        # company_a에는 사용자가 없다(아직 아무도 생성하지 않음) —
        # company_b만 제거해 "회사 1개, 사용자 0명" 상태를 만든다.
        self.db.query(Company).filter(Company.id == self.company_b.id).delete()
        self.db.commit()

        admin_a = self._create_user("admin_last_co", self.super_admin_role.id, self.company_a.id)
        # admin_a 본인이 company_a에 연결돼 있으므로 "연결된 사용자
        # 없음" 조건은 만족하지 않지만, "마지막 회사" 조건이 먼저
        # 걸려야 한다.

        with self.assertRaises(HTTPException) as ctx:
            delete_company(self.company_a.id, current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("마지막", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
