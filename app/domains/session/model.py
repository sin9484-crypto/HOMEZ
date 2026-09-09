"""
=========================================================
Homez OS

File : app/domains/session/model.py

HOMEZ Desktop 로그인 흐름 — 서버 측 세션(Access Token 발급 단위) 모델

기존 JWT 인증은 상태 없이(stateless) 발급되어, 자연 만료 전에는
로그아웃이나 강제 무효화가 불가능했다(2026-07-30 발견 — `/auth/logout`이
"Refresh Token 저장소 추가 후 구현" 상태의 stub이었다). 이 테이블은
발급된 access token(jti 기준)마다 서버 측에서 취소 가능한 상태 하나를
보관해, 다음을 실제로 강제한다:

  - 로그아웃 시 서버 세션 폐기(revoked_at 기록)
  - 로그아웃 후 같은 토큰 재사용 차단(revoked_at이 있으면 이후 모든
    인증 검사에서 거부)
  - 세션 만료(expires_at — JWT 자체 만료와 별개로 서버가 다시 확인)

FK 없음(다른 Domain과 동일한 설계 원칙 — user_id는 논리 참조 컬럼).
=========================================================
"""

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class AuthSession(Base):
    """발급된 access token 1건에 대응하는 서버 측 세션 상태."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    jti: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    revoked_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    is_desktop: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    def __repr__(self) -> str:

        return f"<AuthSession(id={self.id}, user_id={self.user_id}, revoked={self.revoked_at is not None})>"


class RefreshTokenFamily(Base):
    """
    2026-08-30 V7 후속 안정화 Phase 2 — 로그인 1회(access_session)에
    대응하는 Refresh Token 계보 하나. `access_session_jti`로
    `AuthSession.jti`와 1:1로 묶여, 그 access 세션이 폐기되면 이
    family도 함께 폐기된다(session/service.py::revoke_session()/
    revoke_all_sessions_for_user()가 단일 지점에서 둘 다 처리).

    실제 Refresh Token 원문은 어디에도 저장하지 않는다 — 발급마다
    `RefreshToken` 행(해시만 저장)이 새로 생기고, 이 테이블은 그
    계보 전체의 생사(ACTIVE/REVOKED)만 갖는다. FK 없음(다른 Domain과
    동일 설계 원칙).
    """

    __tablename__ = "refresh_token_families"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    family_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )

    access_session_jti: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE",
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    revoked_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    def __repr__(self) -> str:

        return (
            f"<RefreshTokenFamily(id={self.id}, user_id={self.user_id}, "
            f"status={self.status})>"
        )


class RefreshToken(Base):
    """
    Refresh Token Rotation의 한 단계(발급 1건). 원문은 저장하지 않고
    sha256 해시만 저장한다 — DB가 유출돼도 원문 토큰을 복원할 수
    없다. `consumed_at`이 NULL이면 아직 사용되지 않은(=현재 유효한)
    토큰이고, 한 번 소비(rotate)되면 그 시점에 채워진다.

    재사용 탐지: 이미 `consumed_at`이 있는 토큰이 다시 제시되면
    (rotate 시도가 조건부 UPDATE ... WHERE consumed_at IS NULL로
    rowcount=0을 받으면서 이 상태를 관측하면) 탈취 재사용으로 간주해
    같은 family_id 전체를 폐기한다. 이 UPDATE 자체가 동시 요청 중
    정확히 하나만 성공시키는 single-flight 소비 지점이기도 하다.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    family_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )

    jti: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True,
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    def __repr__(self) -> str:

        return (
            f"<RefreshToken(id={self.id}, family_id={self.family_id}, "
            f"consumed={self.consumed_at is not None})>"
        )


__all__ = ["AuthSession", "RefreshTokenFamily", "RefreshToken"]
