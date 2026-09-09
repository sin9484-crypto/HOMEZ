"""
=========================================================
Homez OS

File : app/domains/session/repository.py

AuthSession Repository — no-commit 쓰기 패턴(Decision AI/Coupang과 동일).
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.session.model import AuthSession
from app.domains.session.model import RefreshToken
from app.domains.session.model import RefreshTokenFamily


class SessionRepository:

    def __init__(self, db: Session):
        self.db = db

    def create_no_commit(
        self,
        *,
        user_id: int,
        jti: str,
        issued_at: datetime,
        expires_at: datetime,
        is_desktop: bool = False,
    ) -> AuthSession:

        session = AuthSession(
            user_id=user_id,
            jti=jti,
            issued_at=issued_at,
            expires_at=expires_at,
            is_desktop=is_desktop,
        )
        self.db.add(session)
        self.db.flush()

        return session

    def get_by_jti(self, jti: str) -> AuthSession | None:

        return (
            self.db.query(AuthSession)
            .filter(AuthSession.jti == jti)
            .first()
        )

    def revoke_by_jti(self, jti: str, reason: str, now: datetime) -> int:
        """
        조건부 UPDATE — 이미 취소된 세션은 다시 취소하지 않는다
        (rowcount로 실제 변경 여부를 반환, 동시 로그아웃 경쟁에도 안전).
        """

        result = self.db.execute(
            update(AuthSession)
            .where(
                AuthSession.jti == jti,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason=reason),
        )
        self.db.flush()

        return result.rowcount

    def touch_last_seen(self, jti: str, now: datetime) -> None:

        self.db.execute(
            update(AuthSession)
            .where(AuthSession.jti == jti)
            .values(last_seen_at=now),
        )

    def revoke_all_for_user(self, user_id: int, reason: str, now: datetime) -> int:
        """
        특정 사용자의 활성 세션을 전부 취소한다 — 본인 비밀번호 변경,
        계정 비활성화, "다른 세션 모두 폐기" 액션에서 사용한다.
        """

        result = self.db.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason=reason),
        )
        self.db.flush()

        return result.rowcount


class RefreshTokenRepository:
    """
    Refresh Token family/rotation 전용 no-commit 쓰기 패턴.
    `SessionRepository`와 동일한 원칙 — 이 Repository 자신은 절대
    commit하지 않는다(호출자, 즉 Service가 트랜잭션 경계를 책임진다).
    """

    def __init__(self, db: Session):
        self.db = db

    def create_family_no_commit(
        self,
        *,
        user_id: int,
        family_id: str,
        access_session_jti: str,
        now: datetime,
    ) -> RefreshTokenFamily:

        family = RefreshTokenFamily(
            user_id=user_id,
            family_id=family_id,
            access_session_jti=access_session_jti,
            status="ACTIVE",
            created_at=now,
        )
        self.db.add(family)
        self.db.flush()

        return family

    def get_family_by_id(self, family_id: str) -> RefreshTokenFamily | None:

        return (
            self.db.query(RefreshTokenFamily)
            .filter(RefreshTokenFamily.family_id == family_id)
            .first()
        )

    def revoke_family_no_commit(
        self, family_id: str, reason: str, now: datetime,
    ) -> int:
        """조건부 UPDATE — 이미 폐기된 family는 다시 폐기하지 않는다."""

        result = self.db.execute(
            update(RefreshTokenFamily)
            .where(
                RefreshTokenFamily.family_id == family_id,
                RefreshTokenFamily.status == "ACTIVE",
            )
            .values(status="REVOKED", revoked_at=now, revoked_reason=reason),
        )
        self.db.flush()

        return result.rowcount

    def revoke_families_by_access_session_jti_no_commit(
        self, access_session_jti: str, reason: str, now: datetime,
    ) -> int:

        result = self.db.execute(
            update(RefreshTokenFamily)
            .where(
                RefreshTokenFamily.access_session_jti == access_session_jti,
                RefreshTokenFamily.status == "ACTIVE",
            )
            .values(status="REVOKED", revoked_at=now, revoked_reason=reason),
        )
        self.db.flush()

        return result.rowcount

    def revoke_families_for_user_no_commit(
        self, user_id: int, reason: str, now: datetime,
    ) -> int:

        result = self.db.execute(
            update(RefreshTokenFamily)
            .where(
                RefreshTokenFamily.user_id == user_id,
                RefreshTokenFamily.status == "ACTIVE",
            )
            .values(status="REVOKED", revoked_at=now, revoked_reason=reason),
        )
        self.db.flush()

        return result.rowcount

    def create_token_no_commit(
        self,
        *,
        family_id: str,
        jti: str,
        token_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> RefreshToken:

        token = RefreshToken(
            family_id=family_id,
            jti=jti,
            token_hash=token_hash,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self.db.add(token)
        self.db.flush()

        return token

    def get_token_by_jti(self, jti: str) -> RefreshToken | None:

        return (
            self.db.query(RefreshToken)
            .filter(RefreshToken.jti == jti)
            .first()
        )

    def consume_token_no_commit(self, jti: str, now: datetime) -> int:
        """
        단일 소비 지점 — 조건부 UPDATE(consumed_at IS NULL일 때만)로
        동시 요청 중 정확히 하나만 성공시킨다(single-flight). rowcount
        0이면 "이미 소비됨"(재사용 시도) 또는 "존재하지 않음"이다 —
        호출자가 `get_token_by_jti()`로 구분한다.
        """

        result = self.db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.consumed_at.is_(None),
            )
            .values(consumed_at=now),
        )
        self.db.flush()

        return result.rowcount


__all__ = ["SessionRepository", "RefreshTokenRepository"]
