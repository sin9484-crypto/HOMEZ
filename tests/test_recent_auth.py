"""
=========================================================
Homez OS

File : tests/test_recent_auth.py

2026-08-04 V6 Gate 1A: "최근 인증(recent-auth)" 토큰 발급/소비 및
POST /auth/recent-auth 엔드포인트 검증. 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.recent_auth import (
    RECENT_AUTH_MAX_FAILED_ATTEMPTS,
    consume_recent_auth_token,
    is_recent_auth_locked_out,
    issue_recent_auth_token,
    record_recent_auth_failure,
    reset_recent_auth_state_for_tests,
)
from app.core.security import hash_password
from app.database.base import Base
from app.domains.auth.router import issue_recent_auth
from app.domains.auth.schema import RecentAuthRequest
from app.domains.role.model import Role
from app.domains.user.model import User

STRONG_PASSWORD = "Str0ng!Passw0rd"


class RecentAuthTokenUnitTestCase(unittest.TestCase):

    def tearDown(self):
        reset_recent_auth_state_for_tests()

    def test_issue_then_consume_succeeds_once(self):

        token, _expires_at = issue_recent_auth_token(user_id=42)

        self.assertTrue(consume_recent_auth_token(token, user_id=42))

    def test_token_is_single_use(self):

        token, _ = issue_recent_auth_token(user_id=1)

        self.assertTrue(consume_recent_auth_token(token, user_id=1))
        self.assertFalse(consume_recent_auth_token(token, user_id=1))

    def test_token_rejected_for_different_user(self):

        token, _ = issue_recent_auth_token(user_id=1)

        self.assertFalse(consume_recent_auth_token(token, user_id=2))

    def test_empty_or_none_token_rejected(self):

        self.assertFalse(consume_recent_auth_token(None, user_id=1))
        self.assertFalse(consume_recent_auth_token("", user_id=1))

    def test_concurrent_consume_exactly_one_thread_succeeds(self):
        """
        동시 회사명 변경 요청의 원자성 — 같은 recent-auth 토큰을 여러
        스레드가 동시에 소비하려 해도 정확히 하나만 성공해야 한다
        (실제 threading.Lock 기반 구현을 실제 스레드로 검증한다 —
        Mock으로 흉내내지 않는다).
        """

        token, _ = issue_recent_auth_token(user_id=7)

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            ok = consume_recent_auth_token(token, user_id=7)
            with results_lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 9)

    def test_process_restart_invalidates_all_tokens(self):
        """
        메모리 기반 저장소는 단일 프로세스 전용이다 — "재시작"을 실제로
        일으킬 수는 없으므로, 프로세스 재시작과 동일한 효과(전역 상태
        초기화)를 내는 reset 함수 호출 후 이전에 발급된 토큰이 더 이상
        유효하지 않음을 확인한다.
        """

        token, _ = issue_recent_auth_token(user_id=1)

        reset_recent_auth_state_for_tests()

        self.assertFalse(consume_recent_auth_token(token, user_id=1))

    def test_one_users_lockout_does_not_affect_another_user(self):

        for _ in range(RECENT_AUTH_MAX_FAILED_ATTEMPTS):
            record_recent_auth_failure(user_id=100)

        self.assertTrue(is_recent_auth_locked_out(100))
        self.assertFalse(is_recent_auth_locked_out(200))

        # 잠기지 않은 사용자는 정상적으로 토큰을 계속 발급받을 수 있다.
        token, _ = issue_recent_auth_token(user_id=200)
        self.assertTrue(consume_recent_auth_token(token, user_id=200))

    def test_expired_token_rejected(self):

        import app.core.recent_auth as recent_auth_module

        token, _ = issue_recent_auth_token(user_id=1)
        key = recent_auth_module._hash_token(token)
        # 시간 흐름을 기다리지 않고 만료 시각만 과거로 앞당긴다.
        recent_auth_module._tokens[key]["expires_at"] = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        )

        self.assertFalse(consume_recent_auth_token(token, user_id=1))


class RecentAuthEndpointTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(bind=self.engine, tables=[Role.__table__, User.__table__])

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.role = Role(name="Administrator", code="ADMIN")
        self.db.add(self.role)
        self.db.flush()

        self.user = User(
            username="ru1", email="ru1@example.com",
            password_hash=hash_password(STRONG_PASSWORD), role_id=self.role.id,
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        reset_recent_auth_state_for_tests()

    def test_correct_password_issues_token(self):

        result = issue_recent_auth(
            RecentAuthRequest(current_password=STRONG_PASSWORD),
            current_user=self.user, db=self.db,
        )

        self.assertTrue(result.recent_auth_token)
        # 발급된 토큰은 실제로 본인에게 1회 소비 가능해야 한다.
        self.assertTrue(consume_recent_auth_token(result.recent_auth_token, self.user.id))

    def test_wrong_password_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            issue_recent_auth(
                RecentAuthRequest(current_password="WrongPassword!1"),
                current_user=self.user, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_password_carries_recent_auth_failed_code_not_a_logout_code(self):
        """
        2026-08-04 CTO 재보완: 이 401은 콘솔 JS의 skipAuthHandling에만
        기대지 않고, 헤더 계약 자체로 "허용된 로그아웃 코드가 아님"이
        성립해야 한다 — RECENT_AUTH_FAILED가 콘솔의 ALLOWED_LOGOUT_CODES
        (SESSION_EXPIRED/SESSION_REVOKED/ACCOUNT_DISABLED)에 없다는 것을
        고정한다.
        """

        with self.assertRaises(HTTPException) as ctx:
            issue_recent_auth(
                RecentAuthRequest(current_password="WrongPassword!1"),
                current_user=self.user, db=self.db,
            )

        code = ctx.exception.headers.get("X-Auth-Error-Code")
        self.assertEqual(code, "RECENT_AUTH_FAILED")
        self.assertNotIn(code, {"SESSION_EXPIRED", "SESSION_REVOKED", "ACCOUNT_DISABLED"})

    def test_repeated_wrong_password_locks_out(self):

        for _ in range(RECENT_AUTH_MAX_FAILED_ATTEMPTS):
            with self.assertRaises(HTTPException):
                issue_recent_auth(
                    RecentAuthRequest(current_password="WrongPassword!1"),
                    current_user=self.user, db=self.db,
                )

        self.assertTrue(is_recent_auth_locked_out(self.user.id))

        with self.assertRaises(HTTPException) as ctx:
            issue_recent_auth(
                RecentAuthRequest(current_password=STRONG_PASSWORD),  # 맞는 비밀번호라도
                current_user=self.user, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
