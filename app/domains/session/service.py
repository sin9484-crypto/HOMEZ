"""
=========================================================
Homez OS

File : app/domains/session/service.py

AuthSession Service — 세션 생성/검증/폐기의 순수 로직.
DB 스키마(auth_sessions 테이블)가 아직 적용되지 않은 환경(예: 실
homez.db)에서도 앱 전체가 죽지 않도록, 테이블 부재 시 "세션 확인
불가"로 안전하게 처리한다(다른 Domain의 fail-closed 패턴과 다르게,
이 경우는 세션 계층 자체가 아직 없다는 뜻이므로 상위 호출자가 기존
JWT 자체 만료 검증에만 의존하도록 None을 반환한다 — 아래
`get_session_status` docstring 참고).
=========================================================
"""

from datetime import datetime
from datetime import timezone
from enum import Enum

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.domains.session.model import AuthSession
from app.domains.session.repository import RefreshTokenRepository
from app.domains.session.repository import SessionRepository


class SessionStatus(str, Enum):

    VALID = "VALID"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    NOT_FOUND = "NOT_FOUND"
    SCHEMA_NOT_READY = "SCHEMA_NOT_READY"


def _utcnow() -> datetime:

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _schema_ready(db: Session) -> bool:

    inspector = inspect(db.get_bind())
    return "auth_sessions" in inspector.get_table_names()


def create_session(
    db: Session,
    *,
    user_id: int,
    jti: str,
    issued_at: datetime,
    expires_at: datetime,
    is_desktop: bool = False,
) -> AuthSession | None:
    """
    로그인 성공 시 호출한다. `auth_sessions` 테이블이 아직 적용되지
    않은 환경(V4 진입 Gate 이전의 실 homez.db 등)에서는 세션 기록 없이
    None을 반환한다 — 이 경우 서버 측 강제 로그아웃/재사용 차단은
    적용되지 않고 기존 JWT 자연 만료에만 의존한다(아래 최종 보고의
    "잔여 위험"에 명시).
    """

    if not _schema_ready(db):
        return None

    repository = SessionRepository(db)

    return repository.create_no_commit(
        user_id=user_id,
        jti=jti,
        issued_at=issued_at,
        expires_at=expires_at,
        is_desktop=is_desktop,
    )


def get_session_status(db: Session, jti: str) -> SessionStatus:

    if not _schema_ready(db):
        return SessionStatus.SCHEMA_NOT_READY

    repository = SessionRepository(db)
    session = repository.get_by_jti(jti)

    if session is None:
        return SessionStatus.NOT_FOUND

    if session.revoked_at is not None:
        return SessionStatus.REVOKED

    if session.expires_at <= _utcnow():
        return SessionStatus.EXPIRED

    return SessionStatus.VALID


def _refresh_schema_ready(db: Session) -> bool:

    inspector = inspect(db.get_bind())
    tables = set(inspector.get_table_names())
    return {"refresh_token_families", "refresh_tokens"}.issubset(tables)


def revoke_session(db: Session, jti: str, reason: str) -> bool:
    """
    로그아웃 시 호출한다. 이미 취소된 세션이면 False(멱등).

    2026-08-30 V7 후속 안정화 Phase 2 — 이 access 세션에 연결된
    Refresh Token family도 같은 트랜잭션에서 함께 폐기한다(단일
    지점 — 이 함수를 호출하는 7곳 전부가 자동으로 Refresh Token까지
    막는다, 각 호출부를 개별 수정할 필요가 없다). Refresh 스키마가
    아직 없는 환경에서는 이 부분만 조용히 건너뛴다(access 세션
    폐기는 그대로 진행된다 — 기존 동작 유지).
    """

    if not _schema_ready(db):
        return False

    repository = SessionRepository(db)
    rowcount = repository.revoke_by_jti(jti, reason, _utcnow())

    if _refresh_schema_ready(db):
        RefreshTokenRepository(db).revoke_families_by_access_session_jti_no_commit(
            jti, reason, _utcnow(),
        )

    db.commit()

    return rowcount > 0


def revoke_all_sessions_for_user(db: Session, user_id: int, reason: str) -> int:
    """
    비밀번호 변경/계정 비활성화/"다른 세션 모두 폐기" 액션에서
    호출한다. `auth_sessions` 스키마가 아직 없는 환경에서는 0을
    반환한다(안전한 폴백 — 상위 호출자가 실패로 취급하지 않는다).

    2026-08-30 V7 후속 안정화 Phase 2 — 이 사용자의 모든 Refresh
    Token family도 같은 트랜잭션에서 함께 폐기한다(revoke_session()과
    동일한 단일 지점 원칙 — 비밀번호 변경/계정 정지/역할 재배정/복구
    코드 재설정 등 기존 7개 호출부를 개별 수정하지 않는다).
    """

    if not _schema_ready(db):
        return 0

    repository = SessionRepository(db)
    rowcount = repository.revoke_all_for_user(user_id, reason, _utcnow())

    if _refresh_schema_ready(db):
        RefreshTokenRepository(db).revoke_families_for_user_no_commit(
            user_id, reason, _utcnow(),
        )

    db.commit()

    return rowcount


def get_last_login_at(db: Session, user_id: int) -> datetime | None:
    """
    Gate T(2026-08-10) — 사용자 관리 화면의 "최근 로그인" 표시용.
    `auth_sessions` 스키마가 아직 없는 환경에서는 None(표시할 수
    없음)을 반환한다 — 0이나 임의 값으로 위장하지 않는다.
    """

    if not _schema_ready(db):
        return None

    latest = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user_id)
        .order_by(AuthSession.issued_at.desc())
        .first()
    )

    return latest.issued_at if latest is not None else None


def count_active_sessions(db: Session, user_id: int) -> int | None:
    """
    Gate T(2026-08-10) — "활성 세션 수" 표시용. 스키마 부재 시 None을
    반환한다(0으로 위장하지 않는다).
    """

    if not _schema_ready(db):
        return None

    now = _utcnow()

    return (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .count()
    )


__all__ = [
    "SessionStatus",
    "create_session",
    "get_session_status",
    "revoke_session",
    "revoke_all_sessions_for_user",
    "get_last_login_at",
    "count_active_sessions",
]
