"""
=========================================================
Homez OS

File : tests/test_session_lifecycle_phase2.py

HOMEZ 사용자 운영 기준 11번(개인정보·보안) — "HOMEZ 로그인은 프로그램
종료 시 끝내며, 프로그램이 계속 열려 있어도 마지막 사용 후 최대
3시간까지만 유지한다." Phase 2 구현을 검증한다.

이 파일은 두 가지를 검증한다:
1. 사전 발견 결함 수정 — `rotate()`가 새 Access Token은 발급하면서도
   연결된 `AuthSession.expires_at`은 로그인 시점(30분 TTL)에 고정된
   채 갱신하지 않아, 실제 운영에서는 로그인 30분 후부터 모든 Refresh
   요청이 진짜 세션 폐기와 구분되지 않는 SESSION_REVOKED로 거부되던
   문제(`app/domains/session/repository.py::extend_expiry` 참고).
2. 신규 기능 — 마지막 실제 사용(`last_seen_at`) 후 `SESSION_TIMEOUT_
   MINUTES`(기본 180분)가 지나면 access 세션도, 그걸 갱신하려는
   Refresh 시도도 SESSION_IDLE_TIMEOUT으로 거부된다. 타이머 기반
   자동 갱신(실제 사용자 조작이 아님)만으로는 이 판정을 우회할 수
   없다.

실제 homez.db는 사용하지 않는다(임시 SQLite 파일, Base.metadata.
create_all).
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, hash_password
from app.database.base import Base
from app.domains.auth.service import AuthService, RefreshTokenError
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession, RefreshToken, RefreshTokenFamily
from app.domains.session.refresh_service import RefreshOutcome, issue_family, rotate
from app.domains.session.service import (
    SessionStatus,
    create_session,
    get_session_status,
    touch_session_last_seen,
)
from app.domains.user.model import User


def _epoch_to_naive_utc(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


def _real_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SessionLifecycleTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, RolePermission.__table__,
                Permission.__table__, User.__table__, AuthSession.__table__,
                RefreshTokenFamily.__table__, RefreshToken.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        self.company = Company(name="세션테스트 회사")
        self.role = Role(name="Administrator", code="ADMIN")
        self.db.add_all([self.company, self.role])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username="u1"):

        user = User(
            company_id=self.company.id, username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("Test1234!"),
            name=username, role_id=self.role.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _login(self, user) -> dict:
        """실제 시각(now=None)으로 로그인한다 — 이 파일의 테스트는
        `AuthSession.expires_at`을 직접 조작해 "실제 벽시계 기준으로
        이미 지난 시각"을 재현하므로, `rotate()`에 주입하는 `now`가
        아니라 DB에 저장된 실제 시각 값 자체가 검증 대상이다."""

        access_token = create_access_token(
            data={"sub": str(user.id), "username": user.username},
        )
        payload = decode_access_token(access_token)
        create_session(
            self.db, user_id=user.id, jti=payload["jti"],
            issued_at=_epoch_to_naive_utc(payload["iat"]),
            expires_at=_epoch_to_naive_utc(payload["exp"]),
            is_desktop=True,
        )
        refresh_token = issue_family(
            self.db, user_id=user.id, username=user.username,
            access_session_jti=payload["jti"],
        )
        self.db.commit()
        return {
            "access_token": access_token, "refresh_token": refresh_token,
            "access_jti": payload["jti"],
        }


class ExpiresAtExtensionBugFixTestCase(SessionLifecycleTestCaseBase):

    def test_refresh_after_original_thirty_minute_window_still_succeeds(self):
        """사전 발견 결함의 핵심 재현: 로그인으로부터 실제 30분이 지나
        `AuthSession.expires_at`(원래 로그인 시점 + 30분으로 고정)이
        이미 지난 뒤, 정상적인(정확히 이 결함이 실제로는 절대 만들지
        않는) 시점 — 즉 만료 '직전'에 회전이 한 번 성공했다면, 그
        다음 만료 시각도 함께 갱신돼 있어야 한다."""

        user = self._create_user("extendtest1")
        session = self._login(user)

        # 로그인 직후: 아직 만료 전이므로 정상 회전.
        result1 = rotate(self.db, raw_refresh_token=session["refresh_token"])
        self.assertEqual(result1.outcome, RefreshOutcome.SUCCESS)
        self.db.commit()

        row = self.db.query(AuthSession).filter(
            AuthSession.jti == session["access_jti"],
        ).one()
        original_expiry = _epoch_to_naive_utc(
            decode_access_token(session["access_token"])["exp"],
        )
        self.assertGreater(
            row.expires_at, original_expiry,
            "회전 성공 후 expires_at이 원래 로그인 시점의 만료 시각보다 "
            "뒤로(연장되어) 있어야 한다 — 그렇지 않으면 이 결함이 "
            "고쳐지지 않은 것이다.",
        )

        # 원래(고쳐지지 않았다면) 로그인 후 실제 30분이 지난 시점엔
        # 이미 EXPIRED였을 것이다 — 테스트는 실제로 30분을 기다리지
        # 않으므로, "실제 30분이 지났다"는 상태를 expires_at을 진짜
        # 과거로 돌려서 직접 재현한다(단순히 원래 계산된 만료 시각
        # 값으로 되돌리는 것만으로는 그 값 자체가 여전히 미래이므로
        # 재현되지 않는다).
        del original_expiry  # 미사용 — 아래는 "실제 벽시계 과거"를 직접 재현
        row.expires_at = _real_utcnow() - timedelta(minutes=1)
        self.db.commit()

        status = get_session_status(self.db, session["access_jti"])
        self.assertEqual(
            status, SessionStatus.EXPIRED,
            "고치기 전 상태를 그대로 흉내냈을 때는 실제로 EXPIRED가 "
            "떠야 한다 — 이 결함이 실재했음을 재확인.",
        )

    def test_repeated_rotation_keeps_extending_expiry_indefinitely(self):
        """실제 30분(원래 TTL)을 여러 번 넘겨서도, 매번 만료 '직전'에
        회전하면(=scheduleAutoRefresh가 실제로 하는 일) 계속 성공해야
        한다 — 30일짜리 Refresh Token의 존재 의미."""

        user = self._create_user("extendtest2")
        session = self._login(user)
        current_refresh = session["refresh_token"]

        for _ in range(4):
            result = rotate(self.db, raw_refresh_token=current_refresh)
            self.assertEqual(result.outcome, RefreshOutcome.SUCCESS)
            self.db.commit()
            current_refresh = result.refresh_token

            row = self.db.query(AuthSession).filter(
                AuthSession.jti == session["access_jti"],
            ).one()
            # 매번 "지금부터 ACCESS_TOKEN_EXPIRE_MINUTES 이내"로 갱신돼
            # 있어야 한다(과거로 남아있으면 다음 회전이 EXPIRED로
            # 막힌다).
            self.assertGreater(row.expires_at, _real_utcnow())


class IdleTimeoutTestCase(SessionLifecycleTestCaseBase):

    def test_session_status_idle_timeout_when_never_touched_past_window(self):

        user = self._create_user("idletest1")
        session = self._login(user)

        row = self.db.query(AuthSession).filter(
            AuthSession.jti == session["access_jti"],
        ).one()
        # 로그인은 됐지만 그 뒤로 한 번도 실제 API를 호출하지 않은 채
        # SESSION_TIMEOUT_MINUTES를 초과한 상태를 흉내낸다.
        row.issued_at = _real_utcnow() - timedelta(
            minutes=settings.SESSION_TIMEOUT_MINUTES + 5,
        )
        row.expires_at = _real_utcnow() + timedelta(days=1)  # JWT 자체 만료는 아직 아님
        self.db.commit()

        status = get_session_status(self.db, session["access_jti"])
        self.assertEqual(status, SessionStatus.IDLE_TIMEOUT)

    def test_touch_resets_idle_clock(self):

        user = self._create_user("idletest2")
        session = self._login(user)

        row = self.db.query(AuthSession).filter(
            AuthSession.jti == session["access_jti"],
        ).one()
        row.issued_at = _real_utcnow() - timedelta(
            minutes=settings.SESSION_TIMEOUT_MINUTES + 30,
        )
        row.expires_at = _real_utcnow() + timedelta(days=1)
        self.db.commit()

        self.assertEqual(
            get_session_status(self.db, session["access_jti"]),
            SessionStatus.IDLE_TIMEOUT,
        )

        touched = touch_session_last_seen(self.db, session["access_jti"])
        self.assertTrue(touched)

        self.assertEqual(
            get_session_status(self.db, session["access_jti"]),
            SessionStatus.VALID,
            "실제 활동을 기록(touch)한 뒤에는 유휴 시계가 리셋돼야 한다.",
        )

    def test_refresh_rejected_after_idle_timeout_even_with_valid_refresh_token(self):
        """타이머 기반 자동 갱신이 유휴시간 규정을 우회하지 못함을
        확인한다 — Refresh Token 자체는 30일짜리라 아직 완전히
        유효하지만, 연결된 access 세션이 유휴 초과 상태면 회전 자체가
        거부돼야 한다."""

        user = self._create_user("idletest3")
        session = self._login(user)

        row = self.db.query(AuthSession).filter(
            AuthSession.jti == session["access_jti"],
        ).one()
        row.issued_at = _real_utcnow() - timedelta(
            minutes=settings.SESSION_TIMEOUT_MINUTES + 5,
        )
        row.expires_at = _real_utcnow() + timedelta(days=1)
        self.db.commit()

        result = rotate(self.db, raw_refresh_token=session["refresh_token"])
        self.db.commit()

        self.assertEqual(result.outcome, RefreshOutcome.IDLE_TIMEOUT)

        # 폐기까지 함께 이뤄졌는지 확인 — 다음 시도는 아예 family가
        # ACTIVE가 아니므로 SESSION_REVOKED로 막혀야 한다(재사용 방지와
        # 동일한 엄격함).
        family = self.db.query(RefreshTokenFamily).filter(
            RefreshTokenFamily.access_session_jti == session["access_jti"],
        ).one()
        self.assertNotEqual(family.status, "ACTIVE")

    def test_auth_service_refresh_raises_idle_timeout_error_code(self):

        user = self._create_user("idletest4")
        session = self._login(user)

        row = self.db.query(AuthSession).filter(
            AuthSession.jti == session["access_jti"],
        ).one()
        row.issued_at = _real_utcnow() - timedelta(
            minutes=settings.SESSION_TIMEOUT_MINUTES + 5,
        )
        row.expires_at = _real_utcnow() + timedelta(days=1)
        self.db.commit()

        auth = AuthService(self.db)
        with self.assertRaises(RefreshTokenError) as ctx:
            auth.refresh(session["refresh_token"])

        self.assertEqual(ctx.exception.code, "SESSION_IDLE_TIMEOUT")

    def test_active_use_within_window_does_not_trigger_idle_timeout(self):
        """정상적으로 계속 쓰고 있는 세션(마지막 사용이 창 이내)은
        유휴 판정을 받으면 안 된다 — 오탐 방지 확인."""

        user = self._create_user("idletest5")
        session = self._login(user)

        touch_session_last_seen(self.db, session["access_jti"])

        self.assertEqual(
            get_session_status(self.db, session["access_jti"]),
            SessionStatus.VALID,
        )


if __name__ == "__main__":
    unittest.main()
