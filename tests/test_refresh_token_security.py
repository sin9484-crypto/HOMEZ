"""
=========================================================
Homez OS

File : tests/test_refresh_token_security.py

2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token Rotation + 재사용
탐지 필수 테스트 14개(요구사항 목록 그대로). 실제 30분 sleep은 하지
않는다 — `rotate()`/`issue_family()`의 `now` 주입으로 시간 경과를
시뮬레이션한다. 실제 homez.db는 사용하지 않는다(임시 SQLite 파일).
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import create_access_token, decode_access_token
from app.core.security import hash_password, sha256
from app.core.token import create_refresh_token, decode_refresh_token
from app.database.base import Base
from app.domains.auth.service import AuthService, RefreshTokenError
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession, RefreshToken, RefreshTokenFamily
from app.domains.session.refresh_service import RefreshOutcome, issue_family, rotate
from app.domains.session.repository import RefreshTokenRepository, SessionRepository
from app.domains.session.service import create_session, revoke_all_sessions_for_user, revoke_session
from app.domains.user.model import User


def _epoch_to_naive_utc(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


class RefreshTokenSecurityTestCase(unittest.TestCase):

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

        self.company_a = Company(name="회사 A")
        self.company_b = Company(name="회사 B")
        self.role = Role(name="Administrator", code="ADMIN")
        self.db.add_all([self.company_a, self.company_b, self.role])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username, company, is_active=True):

        user = User(
            company_id=company.id, username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("Test1234!"),
            name=username, role_id=self.role.id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _login(self, user, *, now=None) -> dict:
        """실제 AuthService.login()의 access 세션 생성 + family 발급
        부분만 재사용한다(비밀번호 검증 자체는 이 테스트의 관심사가
        아니므로 직접 조립한다)."""

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
            access_session_jti=payload["jti"], now=now,
        )
        self.db.commit()
        return {
            "access_token": access_token, "refresh_token": refresh_token,
            "access_jti": payload["jti"],
        }

    # 1) 정상 Refresh 성공
    def test_normal_refresh_succeeds_and_rotates(self):

        user = self._create_user("u1", self.company_a)
        session = self._login(user)

        result = AuthService(self.db).refresh(session["refresh_token"])

        self.assertIsNotNone(result["access_token"])
        self.assertIsNotNone(result["refresh_token"])
        self.assertNotEqual(result["refresh_token"], session["refresh_token"])

    # 2) 만료된 Refresh 차단
    def test_expired_refresh_token_is_blocked(self):

        user = self._create_user("u2", self.company_a)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session = self._login(user, now=now)

        # DB에 저장된 만료 시각을 직접 과거로 앞당겨, 실제 30일을
        # 기다리지 않고 "만료됨" 상태를 결정론적으로 재현한다.
        family = self.db.query(RefreshTokenFamily).filter(
            RefreshTokenFamily.user_id == user.id,
        ).first()
        token_row = self.db.query(RefreshToken).filter(
            RefreshToken.family_id == family.family_id,
        ).first()
        token_row.expires_at = now - timedelta(minutes=1)
        self.db.commit()

        result = rotate(self.db, raw_refresh_token=session["refresh_token"], now=now)
        self.assertEqual(result.outcome, RefreshOutcome.EXPIRED)

    # 3) 로그아웃 후 Refresh 차단
    def test_refresh_blocked_after_logout(self):

        user = self._create_user("u3", self.company_a)
        session = self._login(user)

        revoke_session(self.db, session["access_jti"], reason="user_logout")

        with self.assertRaises(RefreshTokenError) as ctx:
            AuthService(self.db).refresh(session["refresh_token"])

        self.assertEqual(ctx.exception.code, "SESSION_REVOKED")

    # 4) 폐기된 Access 세션에 연결된 Refresh 차단(로그아웃 경유가 아닌
    #    직접 세션 만료로 도달 — SessionStatus.EXPIRED 경로)
    def test_refresh_blocked_when_linked_access_session_expired(self):

        user = self._create_user("u4", self.company_a)
        session = self._login(user)

        repo = SessionRepository(self.db)
        row = repo.get_by_jti(session["access_jti"])
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        self.db.commit()

        with self.assertRaises(RefreshTokenError) as ctx:
            AuthService(self.db).refresh(session["refresh_token"])

        self.assertEqual(ctx.exception.code, "SESSION_REVOKED")

    # 5) 비활성 사용자 차단
    def test_refresh_blocked_for_inactive_user(self):

        user = self._create_user("u5", self.company_a)
        session = self._login(user)

        user.is_active = False
        self.db.commit()

        with self.assertRaises(PermissionError):
            AuthService(self.db).refresh(session["refresh_token"])

    # 6) 비밀번호 변경 후 기존 Refresh 차단
    def test_refresh_blocked_after_password_change_revokes_all_sessions(self):

        user = self._create_user("u6", self.company_a)
        session = self._login(user)

        revoke_all_sessions_for_user(self.db, user.id, reason="password_changed")

        with self.assertRaises(RefreshTokenError) as ctx:
            AuthService(self.db).refresh(session["refresh_token"])

        self.assertEqual(ctx.exception.code, "SESSION_REVOKED")

    # 7) Rotation 후 이전 토큰 재사용 차단
    def test_rotated_token_cannot_be_reused(self):

        user = self._create_user("u7", self.company_a)
        session = self._login(user)

        first = AuthService(self.db).refresh(session["refresh_token"])
        self.assertIsNotNone(first["refresh_token"])

        with self.assertRaises(RefreshTokenError) as ctx:
            AuthService(self.db).refresh(session["refresh_token"])

        self.assertEqual(ctx.exception.code, "SESSION_REVOKED")

    # 8) 재사용 탐지 시 family 전체 폐기(새로 발급받은 최신 토큰도 무효화됨)
    def test_reuse_detection_revokes_entire_family(self):

        user = self._create_user("u8", self.company_a)
        session = self._login(user)

        second = AuthService(self.db).refresh(session["refresh_token"])

        # 이전(이미 소비된) 토큰 재사용 시도 -> family 전체 폐기.
        with self.assertRaises(RefreshTokenError):
            AuthService(self.db).refresh(session["refresh_token"])

        # family가 폐기됐으므로, 재사용 시도 *이전*에 정상 발급받았던
        # 최신 토큰(second)도 더 이상 쓸 수 없어야 한다.
        with self.assertRaises(RefreshTokenError) as ctx:
            AuthService(self.db).refresh(second["refresh_token"])

        self.assertEqual(ctx.exception.code, "SESSION_REVOKED")

        # 연결된 access 세션도 함께 폐기됐는지 직접 확인.
        repo = SessionRepository(self.db)
        access_row = repo.get_by_jti(session["access_jti"])
        self.assertIsNotNone(access_row.revoked_at)

    # 9) 동시 Refresh 요청 중 1건만 성공(같은 jti에 대한 원자적 소비 경쟁)
    def test_concurrent_refresh_only_one_succeeds(self):

        user = self._create_user("u9", self.company_a)
        session = self._login(user)

        results = []
        lock = threading.Lock()

        def attempt():
            local_db = self.SessionLocal()
            try:
                result = AuthService(local_db).refresh(session["refresh_token"])
                with lock:
                    results.append(("ok", result))
            except RefreshTokenError as exc:
                with lock:
                    results.append(("error", exc.code))
            except ValueError:
                # 경쟁에서 진 요청 — rotate()가 단순 무효로 처리한다
                # (탈취 재사용과 구분되는, 순수 타이밍 손실).
                with lock:
                    results.append(("lost_race", None))
            finally:
                local_db.close()

        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(results), 5, results)
        successes = [r for r in results if r[0] == "ok"]
        # 지는 쪽은 타이밍에 따라 두 가지 안전한 결과 중 하나로
        # 갈린다 — 둘 다 정확히 같은 만큼 안전하다(권한을 절대 내주지
        # 않는다), 어느 쪽이 나올지만 non-deterministic이다:
        #   (a) "lost_race" — 자신이 읽었을 땐 consumed_at이 아직
        #       비어 있었지만, 원자적 UPDATE 시점엔 이미 이긴 쪽이
        #       가져간 뒤라 rowcount=0으로 조용히 무효 처리된다.
        #   (b) "error"/SESSION_REVOKED — 이긴 쪽의 쓰기가 이 요청의
        #       읽기보다 먼저 보여서, 이미 소비된 토큰이 다시
        #       제시된 것으로 곧바로 재사용 탐지 경로를 탄다(family
        #       전체 폐기까지 실행됨 — 방어가 더 강하게 작동한 것).
        losers = [r for r in results if r[0] in ("lost_race", "error")]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(losers), 4, results)

    # 10) 사용자 A 토큰으로 사용자 B 접근 차단(다른 사용자의 family_id를
    #     자기 토큰의 subject와 짜맞춰도 통과할 수 없다)
    def test_user_a_token_cannot_impersonate_user_b(self):

        user_a = self._create_user("u10a", self.company_a)
        user_b = self._create_user("u10b", self.company_a)
        session_a = self._login(user_a)
        session_b = self._login(user_b)

        family_b = decode_refresh_token(session_b["refresh_token"])["family_id"]

        # A의 subject(sub)를 그대로 두고 B의 family_id만 이어붙인
        # 위조 토큰 — family.user_id와 sub가 어긋나 거부돼야 한다.
        forged = create_refresh_token(
            data={"sub": str(user_a.id), "username": user_a.username, "family_id": family_b},
        )

        result = rotate(self.db, raw_refresh_token=forged)
        self.assertEqual(result.outcome, RefreshOutcome.INVALID)

    # 11) 회사 A/B 격리 — 서로 다른 회사 사용자의 Refresh가 서로의
    #     인증 결과에 전혀 영향을 주지 않는다.
    def test_company_isolation_between_refresh_sessions(self):

        user_a = self._create_user("u11a", self.company_a)
        user_b = self._create_user("u11b", self.company_b)
        session_a = self._login(user_a)
        session_b = self._login(user_b)

        result_a = AuthService(self.db).refresh(session_a["refresh_token"])
        result_b = AuthService(self.db).refresh(session_b["refresh_token"])

        self.assertEqual(result_a["user"].company_id, self.company_a.id)
        self.assertEqual(result_b["user"].company_id, self.company_b.id)
        self.assertNotEqual(result_a["access_token"], result_b["access_token"])

    # 12) Token 원문이 로그(감사 사유 문자열 등)에 절대 노출되지 않는다.
    def test_raw_token_text_never_persisted_in_audit_fields(self):

        user = self._create_user("u12", self.company_a)
        session = self._login(user)

        AuthService(self.db).refresh(session["refresh_token"])
        with self.assertRaises(RefreshTokenError):
            AuthService(self.db).refresh(session["refresh_token"])  # 재사용 -> 폐기 사유 기록

        raw = session["refresh_token"]

        family_row = self.db.query(RefreshTokenFamily).filter(
            RefreshTokenFamily.user_id == user.id,
        ).first()
        self.assertIsNotNone(family_row.revoked_reason)
        self.assertNotIn(raw, family_row.revoked_reason)

        session_row = SessionRepository(self.db).get_by_jti(session["access_jti"])
        self.assertNotIn(raw, session_row.revoked_reason or "")

        # 저장된 것은 원문이 아니라 해시뿐이어야 한다.
        token_rows = self.db.query(RefreshToken).filter(
            RefreshToken.family_id == family_row.family_id,
        ).all()
        for row in token_rows:
            self.assertNotEqual(row.token_hash, raw)
            self.assertEqual(len(row.token_hash), 64)  # sha256 hex

    # 13) Desktop 재시작 후 정상 복원(Credential Manager 저장소 계약).
    def test_desktop_credential_store_round_trips_access_and_refresh_tokens(self):

        from app.core.desktop_console_session_store import DesktopConsoleSessionStore
        from app.core.windows_credential_store import InMemoryCredentialStore

        store = DesktopConsoleSessionStore(InMemoryCredentialStore())
        store.save(
            user_id=42, access_token="access.jwt.value",
            expires_at_epoch=int(datetime.now(timezone.utc).timestamp()) + 3600,
            refresh_token="refresh.jwt.value",
            refresh_expires_at_epoch=int(datetime.now(timezone.utc).timestamp()) + 86400,
        )

        restored = store.load()

        self.assertIsNotNone(restored)
        self.assertEqual(restored["user_id"], 42)
        self.assertEqual(restored["access_token"], "access.jwt.value")
        self.assertEqual(restored["refresh_token"], "refresh.jwt.value")

    # 14) 짧은 TTL로 30분 초과 연속 작업 흐름 검증(실제 sleep 없음).
    def test_rotation_survives_simulated_thirty_minute_session(self):

        user = self._create_user("u14", self.company_a)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session = self._login(user, now=now)

        current_refresh = session["refresh_token"]
        # 5분 간격으로 6회(=30분)에 걸쳐 연속 rotation이 전부 성공해야
        # 한다(각 access token은 짧게 만료되지만 refresh는 30일 TTL
        # 안에 있으므로 계속 유효).
        for minute in range(5, 35, 5):
            simulated_now = now + timedelta(minutes=minute)
            result = rotate(
                self.db, raw_refresh_token=current_refresh, now=simulated_now,
            )
            self.assertEqual(result.outcome, RefreshOutcome.SUCCESS, minute)
            self.db.commit()
            current_refresh = result.refresh_token


if __name__ == "__main__":
    unittest.main()
