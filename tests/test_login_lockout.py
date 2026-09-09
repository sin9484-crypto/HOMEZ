"""
=========================================================
Homez OS

File : tests/test_login_lockout.py

HOMEZ 사용자 운영 기준 11번(개인정보·보안) — "비밀번호 반복 오류나
이상 접근이 발견되면 로그인을 잠시 차단하고 사용자에게 알린다."
(docs/HOMEZ_USER_OPERATION_SETTINGS_AUDIT_20260909.md 11-18에서
완전 미구현으로 확인됨) 구현을 검증한다.

저장소 전체 Migration 이력(migrations/20260909_00_add_login_lockout_
columns.sql 포함)을 공식 bootstrap_environment(MigrationRunner)로
빈 임시 파일 DB에 처음부터 적용한 뒤, 그 위에서 실제
AuthService.login()을 호출한다 — Model만으로 만든 스키마가 아니라
Migration이 실제로 만든 스키마로 검증한다. 실제 homez.db는 전혀
열지 않는다.
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.database.bootstrap import bootstrap_environment
from app.domains.auth.service import AuthService
from app.domains.company.model import Company
from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User
from app.domains.user.repository import UserRepository

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class LoginLockoutTestCaseBase(unittest.TestCase):
    """전체 Migration 이력을 실제로 재생한 임시 DB 위에서 검증한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        self.assertFalse(
            result.migration_approval_required,
            "이 파일이 만든 새 Migration이 저장소의 다른 pending "
            "Migration과 순서·이름이 충돌하지 않는지 확인 — 충돌하면 "
            "여기서 승인 대기 상태가 된다.",
        )

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="잠금테스트 회사", business_number="111-11-11111",
            ceo="테스트", phone="02-000-0000",
            email="lockout@example.com", address="테스트",
        )
        self.other_company = Company(
            name="다른 회사", business_number="222-22-22222",
            ceo="다른", phone="02-111-1111",
            email="other@example.com", address="다른",
        )
        self.db.add_all([self.company, self.other_company])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if self.db_path.exists():
            self.db_path.unlink()

    def _create_user(
        self, username: str, password: str, *,
        company_id: int, role: str = "VIEWER", is_active: bool = True,
    ) -> User:

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(password), name=username,
            role_id=None, is_active=is_active, company_id=company_id,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        # role은 role_id를 통해 조회되는 계산 속성이라 role_id가
        # 필요하지만, 이 테스트는 SUPER_ADMIN 여부를 문자열로 직접
        # 확인하는 코드(AuthService._notify_account_locked)를 검증하는
        # 것이 아니라 그 코드가 죽지 않고 동작하는지가 핵심이므로,
        # role_id 없이도 통과하는 role 속성 자체를 monkeypatch하지
        # 않고 실제 Role 행을 만들어 연결한다.
        from app.domains.role.model import Role as _Role
        role_row = self.db.query(_Role).filter(_Role.code == role).first()
        if role_row is None:
            role_row = _Role(name=role, code=role, description=role)
            self.db.add(role_row)
            self.db.commit()
        user.role_id = role_row.id
        self.db.commit()
        self.db.refresh(user)
        return user

    def _fresh(self, user: User) -> User:
        """다른 세션/스레드가 커밋한 최신 값을 읽기 위해 다시 조회한다."""

        self.db.expire(user)
        return self.db.query(User).filter(User.id == user.id).one()

    def _audit_actions(self, user_id: int) -> list[str]:

        rows = self.db.execute(
            text(
                "SELECT action FROM audit_logs WHERE entity = 'users' "
                "AND entity_id = :entity_id ORDER BY id ASC",
            ),
            {"entity_id": str(user_id)},
        ).fetchall()
        return [r[0] for r in rows]

    def _dispatched_event_codes(self) -> list[str]:

        rows = self.db.execute(
            text("SELECT event_code FROM notification_email_logs ORDER BY id ASC"),
        ).fetchall()
        return [r[0] for r in rows]


class BasicLockoutBehaviorTestCase(LoginLockoutTestCaseBase):

    def test_migration_added_expected_columns(self):

        cols = {
            row[1]
            for row in self.db.execute(text("PRAGMA table_info(users)")).fetchall()
        }
        self.assertIn("failed_login_count", cols)
        self.assertIn("locked_until", cols)
        self.assertIn("last_failed_login_at", cols)

    def test_repeated_failures_lock_the_account(self):

        user = self._create_user("locktest1", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        for _ in range(settings.LOGIN_MAX_ATTEMPT):
            with self.assertRaises(ValueError):
                auth.login("locktest1", "WrongPassword!")

        locked = self._fresh(user)
        self.assertIsNotNone(locked.locked_until)
        self.assertGreater(locked.locked_until, datetime.utcnow())
        self.assertEqual(locked.failed_login_count, settings.LOGIN_MAX_ATTEMPT)

    def test_locked_account_rejects_even_correct_password(self):
        """설정 가능한 최대 실패 횟수만큼 틀리면, 그 뒤에는 맞는
        비밀번호를 넣어도 잠금 기간 동안은 거부된다 — 비밀번호
        검증 자체를 하지 않는다(무차별 대입 차단의 핵심)."""

        user = self._create_user("locktest2", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        for _ in range(settings.LOGIN_MAX_ATTEMPT):
            with self.assertRaises(ValueError):
                auth.login("locktest2", "WrongPassword!")

        with self.assertRaises(ValueError):
            auth.login("locktest2", "Correct1234!")

        # 잠기지 않았다면 위 로그인이 성공했어야 한다 — 실패했다는 것
        # 자체가 잠금이 비밀번호 검증보다 먼저 적용됐다는 증거다.
        still_locked = self._fresh(user)
        self.assertIsNotNone(still_locked.locked_until)

    def test_error_message_identical_across_unknown_bad_password_and_locked(self):
        """사용자 존재 여부 비노출 — 존재하지 않는 계정, 틀린 비밀번호,
        잠긴 계정이 전부 같은 오류 메시지를 반환해야 한다."""

        user = self._create_user("locktest3", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        with self.assertRaises(ValueError) as ctx_unknown:
            auth.login("does_not_exist_at_all", "whatever")

        with self.assertRaises(ValueError) as ctx_bad_pw:
            auth.login("locktest3", "WrongPassword!")

        for _ in range(settings.LOGIN_MAX_ATTEMPT - 1):
            with self.assertRaises(ValueError):
                auth.login("locktest3", "WrongPassword!")

        with self.assertRaises(ValueError) as ctx_locked:
            auth.login("locktest3", "Correct1234!")

        self.assertEqual(str(ctx_unknown.exception), str(ctx_bad_pw.exception))
        self.assertEqual(str(ctx_bad_pw.exception), str(ctx_locked.exception))

    def test_successful_login_resets_failure_count(self):

        user = self._create_user("locktest4", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        for _ in range(settings.LOGIN_MAX_ATTEMPT - 1):
            with self.assertRaises(ValueError):
                auth.login("locktest4", "WrongPassword!")

        pre_success = self._fresh(user)
        self.assertEqual(pre_success.failed_login_count, settings.LOGIN_MAX_ATTEMPT - 1)
        self.assertIsNone(pre_success.locked_until)

        auth.login("locktest4", "Correct1234!")

        after_success = self._fresh(user)
        self.assertEqual(after_success.failed_login_count, 0)
        self.assertIsNone(after_success.locked_until)

    def test_lock_expires_naturally_after_lock_window(self):
        """서버 재시작 후에도 유지돼야 하므로 in-memory 타이머가
        아니라 DB의 locked_until 시각으로만 판단한다 — 과거 시각으로
        직접 만들어도 즉시 풀린 것처럼 동작해야 한다(자연 만료)."""

        user = self._create_user("locktest5", "Correct1234!", company_id=self.company.id)
        user.locked_until = datetime.utcnow() - timedelta(seconds=1)
        user.failed_login_count = settings.LOGIN_MAX_ATTEMPT
        self.db.commit()

        auth = AuthService(self.db)
        result = auth.login("locktest5", "Correct1234!")

        self.assertTrue(result["access_token"])

    def test_password_never_appears_in_audit_log(self):

        user = self._create_user("locktest6", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        for _ in range(settings.LOGIN_MAX_ATTEMPT):
            with self.assertRaises(ValueError):
                auth.login("locktest6", "SuperSecretWrongPassword!")

        rows = self.db.execute(
            text("SELECT description FROM audit_logs WHERE entity = 'users'"),
        ).fetchall()
        for (description,) in rows:
            self.assertNotIn("SuperSecretWrongPassword!", description)
            self.assertNotIn("Correct1234!", description)


class AuditAndNotificationTestCase(LoginLockoutTestCaseBase):

    def test_lock_transition_writes_audit_log_and_notifies_owner(self):

        user = self._create_user("locktest7", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        for _ in range(settings.LOGIN_MAX_ATTEMPT):
            with self.assertRaises(ValueError):
                auth.login("locktest7", "WrongPassword!")

        self.assertIn("ACCOUNT_LOCKED", self._audit_actions(user.id))
        self.assertIn("LOGIN_ACCOUNT_LOCKED", self._dispatched_event_codes())

        notified_user_ids = [
            r[0] for r in self.db.execute(
                text(
                    "SELECT user_id FROM notification_email_logs "
                    "WHERE event_code = 'LOGIN_ACCOUNT_LOCKED'",
                ),
            ).fetchall()
        ]
        self.assertIn(user.id, notified_user_ids)

    def test_lock_transition_notifies_company_super_admin_not_other_company(self):
        """회사 격리 — 잠긴 계정과 같은 회사의 SUPER_ADMIN에게는
        알림이 가고, 다른 회사의 SUPER_ADMIN에게는 가지 않는다."""

        target = self._create_user(
            "locktest8", "Correct1234!", company_id=self.company.id, role="VIEWER",
        )
        same_company_admin = self._create_user(
            "sameadmin", "Correct1234!", company_id=self.company.id, role="SUPER_ADMIN",
        )
        other_company_admin = self._create_user(
            "otheradmin", "Correct1234!", company_id=self.other_company.id,
            role="SUPER_ADMIN",
        )

        auth = AuthService(self.db)
        for _ in range(settings.LOGIN_MAX_ATTEMPT):
            with self.assertRaises(ValueError):
                auth.login("locktest8", "WrongPassword!")

        notified_user_ids = {
            r[0] for r in self.db.execute(
                text(
                    "SELECT user_id FROM notification_email_logs "
                    "WHERE event_code = 'LOGIN_ACCOUNT_LOCKED'",
                ),
            ).fetchall()
        }
        self.assertIn(target.id, notified_user_ids)
        self.assertIn(same_company_admin.id, notified_user_ids)
        self.assertNotIn(other_company_admin.id, notified_user_ids)

    def test_unlock_reset_writes_audit_log(self):

        user = self._create_user("locktest9", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        for _ in range(settings.LOGIN_MAX_ATTEMPT - 1):
            with self.assertRaises(ValueError):
                auth.login("locktest9", "WrongPassword!")

        auth.login("locktest9", "Correct1234!")

        self.assertIn("ACCOUNT_LOCKOUT_RESET", self._audit_actions(user.id))

    def test_successful_login_without_prior_failures_does_not_spam_audit_log(self):

        self._create_user("locktest10", "Correct1234!", company_id=self.company.id)
        auth = AuthService(self.db)

        auth.login("locktest10", "Correct1234!")

        user = self.db.query(User).filter(User.username == "locktest10").one()
        self.assertNotIn("ACCOUNT_LOCKOUT_RESET", self._audit_actions(user.id))


class ConcurrencyTestCase(LoginLockoutTestCaseBase):

    def test_concurrent_failures_lock_exactly_once(self):
        """동시 요청 경쟁 조건 방어 — 여러 스레드가 거의 동시에
        임계치를 넘겨도 '새로 잠김' 알림·감사 기록은 정확히 한 번만
        일어나야 한다."""

        user = self._create_user(
            "racetest1", "Correct1234!", company_id=self.company.id,
        )
        # 임계치 바로 아래까지 미리 채워, 다음 스레드들이 거의 동시에
        # 임계치를 "넘기는" 순간을 만든다.
        repo = UserRepository(self.db)
        for _ in range(settings.LOGIN_MAX_ATTEMPT - 1):
            repo.increment_failed_login(user)
        self.db.commit()

        SessionLocal = sessionmaker(bind=self.engine)
        results: list[bool] = []
        lock = threading.Lock()

        def _attempt():
            thread_db = SessionLocal()
            try:
                thread_repo = UserRepository(thread_db)
                thread_user = thread_db.query(User).filter(User.id == user.id).one()
                _, newly_locked = thread_repo.increment_failed_login(thread_user)
                with lock:
                    results.append(newly_locked)
            finally:
                thread_db.close()

        threads = [threading.Thread(target=_attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(
            sum(1 for r in results if r), 1,
            f"newly_locked=True가 정확히 1번이어야 한다 — 실제: {results}",
        )

        final = self._fresh(user)
        self.assertIsNotNone(final.locked_until)


class AdminUnlockTestCase(LoginLockoutTestCaseBase):

    def test_admin_unlock_endpoint_clears_lock_and_writes_audit(self):

        from app.core import account_admin

        target = self._create_user(
            "locktest11", "Correct1234!", company_id=self.company.id, role="VIEWER",
        )
        admin = self._create_user(
            "unlockadmin", "Correct1234!", company_id=self.company.id,
            role="SUPER_ADMIN",
        )

        auth = AuthService(self.db)
        for _ in range(settings.LOGIN_MAX_ATTEMPT):
            with self.assertRaises(ValueError):
                auth.login("locktest11", "WrongPassword!")

        locked = self._fresh(target)
        self.assertIsNotNone(locked.locked_until)

        result = account_admin.unlock_user(
            user_id=target.id, current_user=admin, db=self.db,
        )

        self.assertFalse(result.is_locked)
        self.assertEqual(result.failed_login_count, 0)

        unlocked = self._fresh(target)
        self.assertIsNone(unlocked.locked_until)
        self.assertEqual(unlocked.failed_login_count, 0)
        self.assertIn("UNLOCK_USER", self._audit_actions(target.id))

        # 해제 후 정상 로그인이 다시 가능해야 한다.
        result = auth.login("locktest11", "Correct1234!")
        self.assertTrue(result["access_token"])


if __name__ == "__main__":
    unittest.main()
