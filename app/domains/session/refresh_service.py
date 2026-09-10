"""
=========================================================
Homez OS

File : app/domains/session/refresh_service.py

2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token Rotation + 재사용
탐지. 감사에서 확인된 Critical 결함(로그아웃/비밀번호변경/계정
비활성화 이후에도 이미 발급된 Refresh Token으로 계속 새 Access
Token을 받을 수 있었다 — app/core/token.py::revoke_refresh_token()이
빈 스텁이었고 AuthService.refresh()가 서버 세션 상태를 전혀 확인하지
않았다)를 수정하는 핵심 모듈이다.

계약:
  - Refresh Token 원문은 이 모듈도, DB도 저장하지 않는다 — sha256
    해시만 저장한다(app/core/security.py::sha256 재사용).
  - 발급마다 새 jti를 갖는다(rotation). 이전 jti는 소비(consumed_at)
    표시되고 다시는 성공하지 못한다.
  - 이미 소비된 jti가 다시 제시되면(재사용 = 탈취 의심) 그 family
    전체와 연결된 access 세션까지 즉시 폐기한다.
  - `auth_sessions`/`refresh_token_families`/`refresh_tokens` 셋 중
    하나라도 스키마가 아직 없으면(이 Migration 미적용 환경)
    SCHEMA_NOT_READY를 반환한다 — 호출자(AuthService)가 기존 동작
    (JWT 자체 만료에만 의존)으로 안전하게 폴백한다. 스키마가 없다고
    앱이 죽거나 로그인이 막히지 않는다.
  - `now`는 항상 주입 가능하다(테스트가 실제 30분을 기다리지 않고
    짧은 TTL로 만료 시나리오를 검증할 수 있도록).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.config import settings

from app.core.security import create_access_token
from app.core.security import sha256
from app.core.token import create_refresh_token
from app.core.token import decode_refresh_token
from app.core.token import verify_refresh_token
from app.domains.session.repository import RefreshTokenRepository
from app.domains.session.repository import SessionRepository
from app.domains.session.service import SessionStatus
from app.domains.session.service import get_session_status


class RefreshOutcome(str, Enum):

    SUCCESS = "SUCCESS"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    SESSION_REVOKED = "SESSION_REVOKED"
    REUSE_DETECTED = "REUSE_DETECTED"
    SCHEMA_NOT_READY = "SCHEMA_NOT_READY"
    # 2026-09-09 Phase 2 — 연결된 access 세션이 "마지막 사용 후 3시간"
    # 규정을 넘겼다. 자동 갱신(scheduleAutoRefresh)이 실제 사용자
    # 활동과 무관하게 타이머로 계속 새 Access Token을 받아가면 3시간
    # 규정이 무의미해지므로, 이 검사가 없으면 SESSION_REVOKED와 달리
    # 조용히 뚫린다 — SESSION_REVOKED로 뭉뚱그리지 않고 별도 값으로
    # 둬서 원인을 감사에서 구분할 수 있게 한다(클라이언트에는 둘 다
    # 결국 재로그인 요구로 동일하게 나간다).
    IDLE_TIMEOUT = "IDLE_TIMEOUT"


@dataclass(frozen=True)
class RefreshRotationResult:

    outcome: RefreshOutcome
    user_id: int | None = None
    access_token: str | None = None
    refresh_token: str | None = None


def _utcnow() -> datetime:

    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_schema_ready(db: Session) -> bool:

    inspector = inspect(db.get_bind())
    tables = set(inspector.get_table_names())
    return {
        "auth_sessions", "refresh_token_families", "refresh_tokens",
    }.issubset(tables)


def issue_family(
    db: Session,
    *,
    user_id: int,
    username: str,
    access_session_jti: str,
    now: datetime | None = None,
) -> str | None:
    """
    로그인 성공 시 호출한다. 새 Refresh Token 계보(family)를 만들고
    첫 Refresh Token을 발급한다. 스키마가 아직 없으면(마이그레이션
    미적용) family_id 없는 레거시 방식 토큰을 그대로 발급한다(None
    반환 — 호출자는 이 경우 fallback 발급 경로를 쓴다).
    """

    if not is_schema_ready(db):
        return None

    now = now or _utcnow()
    repo = RefreshTokenRepository(db)

    family_id = sha256(f"family:{user_id}:{access_session_jti}:{now.isoformat()}")
    repo.create_family_no_commit(
        user_id=user_id, family_id=family_id,
        access_session_jti=access_session_jti, now=now,
    )

    raw_token = create_refresh_token(
        data={"sub": str(user_id), "username": username, "family_id": family_id},
    )
    payload = decode_refresh_token(raw_token)

    repo.create_token_no_commit(
        family_id=family_id, jti=payload["jti"], token_hash=sha256(raw_token),
        issued_at=now,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc).replace(tzinfo=None),
    )

    return raw_token


def rotate(
    db: Session,
    *,
    raw_refresh_token: str,
    now: datetime | None = None,
) -> RefreshRotationResult:
    """
    Refresh Token 1회 소비 → 검증 → (성공 시) 새 Access/Refresh Token
    쌍 발급. 이 함수 자신은 커밋하지 않는다 — 호출자가 커밋한다(감사
    로그 기록 등과 같은 트랜잭션 경계에 있을 수 있으므로).
    """

    if not is_schema_ready(db):
        return RefreshRotationResult(outcome=RefreshOutcome.SCHEMA_NOT_READY)

    if not verify_refresh_token(raw_refresh_token):
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)

    try:
        payload = decode_refresh_token(raw_refresh_token)
    except Exception:  # noqa: BLE001 — verify_refresh_token()이 이미 서명·만료를
        # 확인했으므로 여기서 또 실패할 일은 사실상 없지만, 방어적으로
        # 잡는다(형식 오류를 500이 아니라 INVALID로 처리).
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)

    family_id = payload.get("family_id")
    jti = payload.get("jti")
    subject = payload.get("sub")
    username = payload.get("username")

    if not family_id or not jti or subject is None:
        # family_id가 없다 = 이 스키마 도입 이전에 발급된 레거시
        # 토큰(또는 위조 시도). 추적할 계보가 없으므로 신뢰하지 않는다
        # (fail-closed — "일단 통과시키고 다음부터 추적"하지 않는다).
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)

    now = now or _utcnow()
    token_repo = RefreshTokenRepository(db)
    session_repo = SessionRepository(db)

    family = token_repo.get_family_by_id(family_id)
    if family is None or family.user_id != user_id:
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)
    if family.status != "ACTIVE":
        return RefreshRotationResult(outcome=RefreshOutcome.SESSION_REVOKED)

    access_status = get_session_status(db, family.access_session_jti)
    if access_status in (SessionStatus.REVOKED, SessionStatus.NOT_FOUND):
        return RefreshRotationResult(outcome=RefreshOutcome.SESSION_REVOKED)
    if access_status == SessionStatus.EXPIRED:
        return RefreshRotationResult(outcome=RefreshOutcome.SESSION_REVOKED)
    if access_status == SessionStatus.IDLE_TIMEOUT:
        # 타이머 기반 자동 갱신이 "마지막 사용 후 3시간" 규정을
        # 우회하지 못하도록, family와 access 세션을 여기서 명시적으로
        # 폐기한다(REUSE_DETECTED와 동일하게 "의심되면 세션 전체를
        # 막는다"는 아니지만, 재로그인 없이는 더 이상 이 family로
        # 갱신할 수 없어야 하므로 폐기 자체는 동일하게 한다).
        token_repo.revoke_family_no_commit(
            family_id, "session_idle_timeout", now,
        )
        session_repo.revoke_by_jti(
            family.access_session_jti, "session_idle_timeout", now,
        )
        return RefreshRotationResult(outcome=RefreshOutcome.IDLE_TIMEOUT)

    token_row = token_repo.get_token_by_jti(jti)
    if token_row is None or token_row.family_id != family_id:
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)
    if token_row.token_hash != sha256(raw_refresh_token):
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)

    if token_row.consumed_at is not None:
        # 재사용 탐지 — 이미 회전(rotate)되어 소비된 토큰이 다시
        # 제시됐다. 탈취 후 재사용 가능성이 있으므로 이 family 전체와
        # 연결된 access 세션까지 즉시 폐기한다("의심되면 그 세션
        # 전체를 막는다").
        token_repo.revoke_family_no_commit(
            family_id, "refresh_token_reuse_detected", now,
        )
        session_repo.revoke_by_jti(
            family.access_session_jti, "refresh_token_reuse_detected", now,
        )
        return RefreshRotationResult(outcome=RefreshOutcome.REUSE_DETECTED)

    if now >= token_row.expires_at:
        return RefreshRotationResult(outcome=RefreshOutcome.EXPIRED)

    consumed = token_repo.consume_token_no_commit(jti, now)
    if consumed == 0:
        # 동시 refresh 요청 중 이 요청이 경쟁에서 졌다(다른 요청이
        # 그 사이 먼저 소비했다) — 이건 재사용이 아니라 단순 타이밍
        # 손실이다. 이긴 요청이 이미 새 토큰 쌍을 발급했으므로 이
        # 요청은 조용히 거부한다.
        return RefreshRotationResult(outcome=RefreshOutcome.INVALID)

    new_access_token = create_access_token(
        data={"sub": str(user_id), "username": username},
    )
    new_refresh_token = create_refresh_token(
        data={"sub": str(user_id), "username": username, "family_id": family_id},
    )
    new_payload = decode_refresh_token(new_refresh_token)
    token_repo.create_token_no_commit(
        family_id=family_id, jti=new_payload["jti"],
        token_hash=sha256(new_refresh_token), issued_at=now,
        expires_at=datetime.fromtimestamp(
            new_payload["exp"], tz=timezone.utc,
        ).replace(tzinfo=None),
    )

    # 사전 발견 결함 수정(위 SessionRepository.extend_expiry docstring
    # 참고) — 이 회전이 실제로 성공했으므로, 연결된 access 세션의
    # expires_at도 함께 앞으로 민다. 이걸 하지 않으면 로그인 후
    # ACCESS_TOKEN_EXPIRE_MINUTES가 지난 시점부터는 이후의 모든 Refresh
    # 요청이 access_status==EXPIRED로 거부된다(30일 Refresh Token의
    # 존재 의미가 없어짐). last_seen_at/issued_at은 건드리지 않는다 —
    # 그 두 값은 "진짜 마지막 사용 시각" 판정(Phase 2 IDLE_TIMEOUT)
    # 전용이고, 타이머 기반 회전 자체는 사용자 활동이 아니다.
    session_repo.extend_expiry(
        family.access_session_jti,
        now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return RefreshRotationResult(
        outcome=RefreshOutcome.SUCCESS, user_id=user_id,
        access_token=new_access_token, refresh_token=new_refresh_token,
    )


__all__ = [
    "RefreshOutcome",
    "RefreshRotationResult",
    "is_schema_ready",
    "issue_family",
    "rotate",
]
