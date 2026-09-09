"""
=========================================================
Homez OS

File : tests/test_role_permission_admin.py

Gate T(2026-08-10) — 역할 Permission 편집(app/domains/role_permission/
admin_service.py, admin_router.py) + account_admin.py 회사 스코프
보강 검증. 실제 homez.db는 전혀 사용하지 않는다(임시 SQLite 파일).
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import account_admin
from app.core.recent_auth import (
    issue_recent_auth_token,
    reset_recent_auth_state_for_tests,
)
from app.core.security import hash_password
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission import admin_service
from app.domains.role_permission.model import RolePermission
from app.domains.role_permission.permission_edit_nonce import (
    PermissionEditNonceStatus,
    clear_all_permission_edit_nonces,
    verify_nonce,
)
from app.domains.session.model import AuthSession
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

STRONG_PASSWORD = "Str0ng!Passw0rd"

PERMISSION_CODES = [
    "listing_wizard.view",
    "listing_wizard.create",
    "listing_wizard.edit",
    "listing_wizard.economics_view",
    "USER_VIEW",
]


class RolePermissionAdminTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 15})

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, RolePermission.__table__,
                Permission.__table__, User.__table__, AuthSession.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="Company A", active=True)
        self.company_b = Company(name="Company B", active=True)
        self.db.add_all([self.company_a, self.company_b])
        self.db.flush()

        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.super_admin_role, self.admin_role, self.viewer_role])
        self.db.flush()

        self.permissions = {}
        for code in PERMISSION_CODES:
            perm = Permission(name=code, code=code, active=True)
            self.db.add(perm)
            self.permissions[code] = perm
        self.db.commit()

        reset_recent_auth_state_for_tests()
        clear_all_permission_edit_nonces()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        reset_recent_auth_state_for_tests()
        clear_all_permission_edit_nonces()

    def _create_user(self, username, role_id, company_id, is_active=True):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(STRONG_PASSWORD), role_id=role_id,
            company_id=company_id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def _grant(self, role_id, codes):

        for code in codes:
            self.db.add(RolePermission(role_id=role_id, permission_id=self.permissions[code].id))
        self.db.commit()

    # --------------------------------------------------
    # 1. 카탈로그
    # --------------------------------------------------

    def test_catalog_groups_permissions_and_flags_risky(self):

        from app.domains.role_permission.permission_catalog import build_permission_catalog

        catalog = build_permission_catalog(self.db)
        groups = {g["group"]: g for g in catalog}

        self.assertIn("listing_wizard", groups)
        self.assertIn("user", groups)

        econ = next(
            p for p in groups["listing_wizard"]["permissions"]
            if p["code"] == "listing_wizard.economics_view"
        )
        self.assertTrue(econ["risky"])
        self.assertTrue(econ["name_ko"])
        self.assertTrue(econ["impact_ko"])

    def test_catalog_unknown_code_falls_back_safely(self):

        from app.domains.role_permission.permission_catalog import build_permission_catalog

        self.db.add(Permission(name="Mystery", code="SOME_FUTURE_CODE", active=True))
        self.db.commit()

        catalog = build_permission_catalog(self.db)
        other = next(g for g in catalog if g["group"] == "other")
        self.assertEqual(other["permissions"][0]["code"], "SOME_FUTURE_CODE")
        self.assertFalse(other["permissions"][0]["risky"])

    # --------------------------------------------------
    # 2. SUPER_ADMIN 역할은 편집 대상에서 제외
    # --------------------------------------------------

    def test_get_role_permission_codes_allows_super_admin_readonly(self):

        # 조회 자체는 허용된다 — 편집 진입점(request_permission_edit_nonce)
        # 에서만 SUPER_ADMIN 역할이 막힌다.
        codes = admin_service.get_role_permission_codes(self.db, self.super_admin_role.id)
        self.assertEqual(codes, set())

    def test_super_admin_edit_nonce_blocked(self):

        admin = self._create_user("super2", self.super_admin_role.id, self.company_a.id)

        with self.assertRaises(ValueError) as ctx:
            admin_service.request_permission_edit_nonce(
                self.db, role_id=self.super_admin_role.id, current_user=admin,
            )
        self.assertEqual(ctx.exception.args[0], admin_service.RolePermissionUpdateError.SUPER_ADMIN_NOT_EDITABLE.value)

    # --------------------------------------------------
    # 3. nonce 재발급 시 이전 nonce 무효화
    # --------------------------------------------------

    def test_reissuing_nonce_invalidates_previous(self):

        admin = self._create_user("admin1", self.admin_role.id, self.company_a.id)

        nonce1, _ = admin_service.request_permission_edit_nonce(
            self.db, role_id=self.viewer_role.id, current_user=admin,
        )
        nonce2, _ = admin_service.request_permission_edit_nonce(
            self.db, role_id=self.viewer_role.id, current_user=admin,
        )

        self.assertNotEqual(nonce1, nonce2)
        self.assertEqual(verify_nonce(self.viewer_role.id, nonce1), PermissionEditNonceStatus.MISMATCH)
        self.assertEqual(verify_nonce(self.viewer_role.id, nonce2), PermissionEditNonceStatus.VALID)

    # --------------------------------------------------
    # 4. recent-auth 없이 수정 불가
    # --------------------------------------------------

    def test_update_requires_recent_auth(self):

        admin = self._create_user("admin2", self.admin_role.id, self.company_a.id)
        nonce, _ = admin_service.request_permission_edit_nonce(
            self.db, role_id=self.viewer_role.id, current_user=admin,
        )

        with self.assertRaises(ValueError) as ctx:
            admin_service.update_role_permissions(
                self.db, role_id=self.viewer_role.id, current_user=admin,
                expected_codes=set(), new_codes={"USER_VIEW"},
                nonce=nonce, recent_auth_token=None, db_path=self.db_path,
            )
        self.assertEqual(ctx.exception.args[0], admin_service.RolePermissionUpdateError.RECENT_AUTH_REQUIRED.value)

    # --------------------------------------------------
    # 5. 알 수 없는 Permission 코드 거부(Preset 이름 신뢰 안 함)
    # --------------------------------------------------

    def test_update_rejects_unknown_permission_code(self):

        admin = self._create_user("admin3", self.admin_role.id, self.company_a.id)
        nonce, _ = admin_service.request_permission_edit_nonce(
            self.db, role_id=self.viewer_role.id, current_user=admin,
        )
        token, _ = issue_recent_auth_token(admin.id)

        with self.assertRaises(ValueError) as ctx:
            admin_service.update_role_permissions(
                self.db, role_id=self.viewer_role.id, current_user=admin,
                expected_codes=set(), new_codes={"NOT_A_REAL_CODE"},
                nonce=nonce, recent_auth_token=token, db_path=self.db_path,
            )
        self.assertEqual(ctx.exception.args[0], admin_service.RolePermissionUpdateError.UNKNOWN_PERMISSION_CODE.value)

    # --------------------------------------------------
    # 6. 정상 수정 — before/after 감사 기록
    # --------------------------------------------------

    def test_update_success_writes_audit_trail(self):

        admin = self._create_user("admin4", self.admin_role.id, self.company_a.id)
        nonce, _ = admin_service.request_permission_edit_nonce(
            self.db, role_id=self.viewer_role.id, current_user=admin,
        )
        token, _ = issue_recent_auth_token(admin.id)

        result = admin_service.update_role_permissions(
            self.db, role_id=self.viewer_role.id, current_user=admin,
            expected_codes=set(), new_codes={"listing_wizard.view"},
            nonce=nonce, recent_auth_token=token, db_path=self.db_path,
        )

        self.assertEqual(result, {"listing_wizard.view"})

        codes = admin_service.get_role_permission_codes(self.db, self.viewer_role.id)
        self.assertEqual(codes, {"listing_wizard.view"})

        with self.engine.begin() as conn:
            log_row = conn.exec_driver_sql(
                "SELECT action, description FROM audit_logs WHERE action = 'ROLE_PERMISSIONS_UPDATED'",
            ).fetchone()

        self.assertIsNotNone(log_row)
        self.assertIn("before=[]", log_row[1])
        self.assertIn("after=[listing_wizard.view]", log_row[1])

        # nonce는 소비되어 재사용 불가
        self.assertEqual(
            verify_nonce(self.viewer_role.id, nonce),
            PermissionEditNonceStatus.ALREADY_CONSUMED,
        )

    # --------------------------------------------------
    # 7. 낙관적 동시성 — expected_codes가 stale하면 409 상당 오류
    # --------------------------------------------------

    def test_update_conflict_when_expected_codes_stale(self):

        admin = self._create_user("admin5", self.admin_role.id, self.company_a.id)
        self._grant(self.viewer_role.id, ["USER_VIEW"])

        nonce, _ = admin_service.request_permission_edit_nonce(
            self.db, role_id=self.viewer_role.id, current_user=admin,
        )
        token, _ = issue_recent_auth_token(admin.id)

        with self.assertRaises(ValueError) as ctx:
            admin_service.update_role_permissions(
                self.db, role_id=self.viewer_role.id, current_user=admin,
                expected_codes=set(),  # stale — 실제로는 USER_VIEW가 이미 있음
                new_codes={"listing_wizard.view"},
                nonce=nonce, recent_auth_token=token, db_path=self.db_path,
            )
        self.assertEqual(ctx.exception.args[0], admin_service.RolePermissionUpdateError.VERSION_CONFLICT.value)

        # 실패한 시도는 아무것도 바꾸지 않는다
        self.assertEqual(admin_service.get_role_permission_codes(self.db, self.viewer_role.id), {"USER_VIEW"})

    # --------------------------------------------------
    # 8. 동시 변경 경쟁 — 정확히 하나만 성공
    # --------------------------------------------------

    def test_concurrent_updates_exactly_one_succeeds(self):

        # role_id는 스레드 시작 "전"에 정수로 캡처한다. self.viewer_role은
        # 메인 스레드의 self.db Session에 바인딩된 ORM 객체라, setUp()의
        # 여러 commit() 이후 expire된 상태에서 워커 스레드가 이 속성에
        # 접근하면 self.db/self.engine에 대해 크로스스레드로 암묵적
        # SELECT가 발생해 SQLAlchemy Session 내부 상태가 손상된다
        # (ObjectDeletedError/sqlite3.InterfaceError로 관측됨 — 실측
        # 재현: tests.test_marketplace_listing_status_sync의 동일 결함과
        # 같은 근본 원인). admin_service._atomic_update_role_permissions()
        # 자체는 이 결함과 무관하다.
        role_id = self.viewer_role.id

        results = []
        errors = []
        barrier = threading.Barrier(2)

        def attempt():
            barrier.wait()
            try:
                r = admin_service._atomic_update_role_permissions(
                    self.db_path, role_id=role_id, current_user_id=1,
                    expected_codes=set(), new_codes={"USER_VIEW"},
                )
                results.append(r)
            except ValueError as exc:
                errors.append(exc.args[0])

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0], admin_service.RolePermissionUpdateError.VERSION_CONFLICT.value)


class AccountAdminCompanyScopeTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 15})

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, RolePermission.__table__,
                Permission.__table__, User.__table__, AuthSession.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="Company A", active=True)
        self.company_b = Company(name="Company B", active=True)
        self.db.add_all([self.company_a, self.company_b])
        self.db.flush()

        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.super_admin_role, self.admin_role, self.viewer_role])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username, role_id, company_id, is_active=True):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(STRONG_PASSWORD), role_id=role_id,
            company_id=company_id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def test_list_users_scoped_to_own_company(self):

        admin_a = self._create_user("admina", self.super_admin_role.id, self.company_a.id)
        self._create_user("usera", self.viewer_role.id, self.company_a.id)
        self._create_user("userb", self.viewer_role.id, self.company_b.id)

        result = account_admin.list_users(current_user=admin_a, db=self.db)

        usernames = {u.username for u in result}
        self.assertEqual(usernames, {"admina", "usera"})

    def test_get_user_detail_cross_company_is_404(self):

        admin_a = self._create_user("adminx", self.super_admin_role.id, self.company_a.id)
        user_b = self._create_user("useryy", self.viewer_role.id, self.company_b.id)

        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            account_admin.get_user_detail(user_id=user_b.id, current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_update_active_status_cross_company_is_404(self):

        admin_a = self._create_user("adminz", self.super_admin_role.id, self.company_a.id)
        user_b = self._create_user("userzz", self.viewer_role.id, self.company_b.id)

        from fastapi import HTTPException
        from app.core.account_admin import AdminUserActiveUpdateRequest

        with self.assertRaises(HTTPException) as ctx:
            account_admin.update_user_active_status(
                user_id=user_b.id, data=AdminUserActiveUpdateRequest(active=False),
                current_user=admin_a, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 404)

    def test_create_user_inherits_creator_company_id(self):

        admin_a = self._create_user("admincreate", self.super_admin_role.id, self.company_a.id)

        from app.core.account_admin import AdminUserCreateRequest

        result = account_admin.create_user(
            data=AdminUserCreateRequest(
                username="newbie", email="newbie@example.com",
                password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
                role_code="VIEWER",
            ),
            current_user=admin_a, db=self.db,
        )

        self.assertEqual(result.company_id, self.company_a.id)

    def test_only_admin_in_company_cannot_deactivate_self(self):

        # 회사에 관리자가 1명뿐인 상황(super_a 자신)에서 그 사람이
        # 스스로를 비활성화하려는 시도는 self-action 차단으로 막힌다 —
        # "회사의 마지막 관리자" 보호가 실질적으로 필요한 유일한 경로가
        # 이미 다른 검사로 막혀 있음을 확인한다(admin_service 설계
        # 노트 및 account_admin.py 상단 주석 참고).
        super_a = self._create_user("supersolo", self.super_admin_role.id, self.company_a.id)

        from fastapi import HTTPException
        from app.core.account_admin import AdminUserActiveUpdateRequest

        with self.assertRaises(HTTPException) as ctx:
            account_admin.update_user_active_status(
                user_id=super_a.id, data=AdminUserActiveUpdateRequest(active=False),
                current_user=super_a, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("본인", ctx.exception.detail)

    def test_deactivating_one_of_two_company_admins_leaves_other_intact(self):

        # company_a에 SUPER_ADMIN 1명 + ADMIN 1명 — ADMIN을 비활성화해도
        # SUPER_ADMIN이 남아 회사가 무관리자 상태가 되지 않으므로 허용된다.
        super_a = self._create_user("superduo", self.super_admin_role.id, self.company_a.id)
        admin_a = self._create_user("adminduo", self.admin_role.id, self.company_a.id)

        from app.core.account_admin import AdminUserActiveUpdateRequest

        result = account_admin.update_user_active_status(
            user_id=admin_a.id, data=AdminUserActiveUpdateRequest(active=False),
            current_user=super_a, db=self.db,
        )

        self.assertFalse(result.is_active)


class AuditLogTenantIsolationTestCase(RolePermissionAdminTestCase):
    """
    Gate 1(2026-08-15, V6.5 최종 계약 감사) — `GET /admin/audit-logs`가
    회사 필터 없이 `audit_logs` 테이블 전체를 반환하던 결함(Critical,
    테넌트 격리 우회)의 회귀 테스트. 원인은 두 갈래였다:

      1. `app/core/audit_db.py::write_audit_log()`가 `company_id`를
         항상 NULL로 고정 INSERT했다 — 어느 회사 이벤트인지 기록
         자체가 안 됐다.
      2. `list_audit_logs()`(app/domains/role_permission/admin_router.py)
         가 회사로 필터링하지 않았다.

    이 테스트는 (1) `write_audit_log()`가 전달받은 `company_id`를
    실제로 저장하는지, (2) `list_audit_logs()`가 호출자 자신의 회사
    행 + company_id가 NULL인 전역/익명 행만 반환하고 다른 회사의 행은
    절대 반환하지 않는지를 검증한다. `RolePermissionAdminTestCase`를
    상속해 동일한 Company A/B + Role/Permission 시딩(setUp)을 그대로
    재사용한다.
    """

    def test_write_audit_log_persists_explicit_company_id(self):

        from app.core.audit_db import write_audit_log

        user_a = self._create_user("audituser_a", self.admin_role.id, self.company_a.id)

        write_audit_log(
            self.db, user_id=user_a.id, action="TEST_ACTION",
            entity="users", entity_id=str(user_a.id),
            description="test", company_id=self.company_a.id,
        )
        self.db.commit()

        with self.engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT company_id FROM audit_logs WHERE action = 'TEST_ACTION'",
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], self.company_a.id)

    def test_write_audit_log_defaults_to_null_when_company_unknown(self):

        from app.core.audit_db import write_audit_log

        write_audit_log(
            self.db, user_id=None, action="ANONYMOUS_ACTION",
            entity="users", entity_id="-",
            description="no identifiable company",
        )
        self.db.commit()

        with self.engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT company_id FROM audit_logs WHERE action = 'ANONYMOUS_ACTION'",
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertIsNone(row[0])

    def test_list_audit_logs_excludes_other_companys_entries(self):

        from app.core.audit_db import write_audit_log
        from app.domains.role_permission.admin_router import list_audit_logs

        super_a = self._create_user("auditsuper_a", self.super_admin_role.id, self.company_a.id)
        super_b = self._create_user("auditsuper_b", self.super_admin_role.id, self.company_b.id)

        write_audit_log(
            self.db, user_id=super_a.id, action="COMPANY_A_ONLY_EVENT",
            entity="users", entity_id=str(super_a.id),
            description="company A secret event", company_id=self.company_a.id,
        )
        write_audit_log(
            self.db, user_id=super_b.id, action="COMPANY_B_ONLY_EVENT",
            entity="users", entity_id=str(super_b.id),
            description="company B secret event", company_id=self.company_b.id,
        )
        self.db.commit()

        entries_a = list_audit_logs(current_user=super_a, db=self.db)
        actions_a = {e.action for e in entries_a}

        self.assertIn("COMPANY_A_ONLY_EVENT", actions_a)
        self.assertNotIn("COMPANY_B_ONLY_EVENT", actions_a)

        entries_b = list_audit_logs(current_user=super_b, db=self.db)
        actions_b = {e.action for e in entries_b}

        self.assertIn("COMPANY_B_ONLY_EVENT", actions_b)
        self.assertNotIn("COMPANY_A_ONLY_EVENT", actions_b)

    def test_list_audit_logs_includes_global_null_company_entries(self):
        """
        role_permissions 편집 이력처럼 원래부터 회사 무관 전역 설정인
        행(company_id NULL)은 계속 모든 회사의 SUPER_ADMIN에게 보여야
        한다 — 위 배타적 격리 테스트와 대비되는 의도적 예외.
        """

        from app.core.audit_db import write_audit_log
        from app.domains.role_permission.admin_router import list_audit_logs

        super_a = self._create_user("auditsuper_global", self.super_admin_role.id, self.company_a.id)

        write_audit_log(
            self.db, user_id=super_a.id, action="ROLE_PERMISSIONS_UPDATED",
            entity="role_permissions", entity_id=str(self.viewer_role.id),
            description="before=[] after=[USER_VIEW]",
        )
        self.db.commit()

        entries = list_audit_logs(current_user=super_a, db=self.db)
        actions = {e.action for e in entries}

        self.assertIn("ROLE_PERMISSIONS_UPDATED", actions)


if __name__ == "__main__":
    unittest.main()
