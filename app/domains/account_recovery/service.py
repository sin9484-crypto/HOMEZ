"""
=========================================================
Homez OS

File : app/domains/account_recovery/service.py

계정 복구 핵심 로직 — 아이디 찾기 / 로그아웃 상태 복구 코드 재설정 /
이메일 재설정 링크 / SUPER_ADMIN 발급 재설정. 이 파일의 모든
공개 함수는 다음 원칙을 지킨다:

  - 계정 존재 여부를 응답 코드·메시지·타이밍으로 구분해 드러내지
    않는다(SUPER_ADMIN 전용 함수 제외 — 이미 인증된 관리자에게는
    회사 범위 내에서 사용자 존재 여부를 알려줘도 안전하다).
  - 원문(이메일 자체는 예외 — 요청 파라미터이므로 함수 호출 스코프
    안에서는 다뤄야 한다, 그러나 audit_log에는 남기지 않는다)/
    비밀번호/복구 코드/토큰 원문을 audit_log나 로그에 남기지 않는다.
  - 성공 시 항상 같은 Transaction 안에서 비밀번호 변경 + 전체 세션
    폐기 + 감사 로그를 함께 commit한다.
=========================================================
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum

import secrets

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.config import settings
from app.core.password_policy import validate_password_policy
from app.core.security import hash_password
from app.core.security import sha256
from app.core.security import verify_password
from app.domains.account_recovery.model import PasswordResetToken
from app.domains.account_recovery.model import RecoveryCode
from app.domains.account_recovery.providers import PasswordResetDeliveryProvider
from app.domains.session.service import revoke_all_sessions_for_user
from app.domains.user.model import User

RECOVERY_CODE_COUNT = 10
RESET_TOKEN_TTL_MINUTES = 15

FORGOT_ID_WINDOW_SECONDS = 900
FORGOT_ID_MAX_REQUESTS = 5

RESET_REQUEST_WINDOW_SECONDS = 900
RESET_REQUEST_MAX_REQUESTS = 5

RECOVERY_CODE_MAX_FAILED = 5
RECOVERY_CODE_LOCKOUT_SECONDS = 900

RESET_TOKEN_MAX_FAILED = 8
RESET_TOKEN_LOCKOUT_SECONDS = 900


# --------------------------------------------------
# 프로세스 전역 rate limit / lockout
#
# 이 Desktop 앱은 항상 단일 프로세스로 실행되므로(app/core/setup_nonce.py
# 와 동일한 전제), DB 컬럼 대신 메모리 상태로 충분하다 — 프로세스가
# 재시작되면 제한이 초기화되지만, 그 시점엔 어차피 새 세션이므로
# 안전 하한선(fail-open이 아니라 단순 초기화)일 뿐이다.
# --------------------------------------------------

_rl_lock = threading.Lock()
_rl_windows: dict[str, list[datetime]] = {}
_lockouts: dict[str, dict] = {}


def _now() -> datetime:
    """
    naive UTC를 반환한다(app/domains/session/service.py::_utcnow()와
    동일한 관례) — SQLite는 tz-aware datetime을 저장·재조회할 때
    tzinfo를 보존하지 않으므로, aware datetime을 쓰면 새 세션에서
    다시 읽은 값과 비교할 때 "can't compare offset-naive and
    offset-aware datetimes"로 깨진다. 처음부터 naive로 통일해 이
    불일치 자체를 만들지 않는다.
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _within_rate_limit(key: str, window_seconds: int, max_requests: int) -> bool:

    now = _now()

    with _rl_lock:
        timestamps = _rl_windows.setdefault(key, [])
        cutoff = now - timedelta(seconds=window_seconds)
        timestamps[:] = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= max_requests:
            return False

        timestamps.append(now)
        return True


def _is_locked_out(key: str) -> bool:

    with _rl_lock:
        entry = _lockouts.get(key)

        if entry is None:
            return False

        if _now() >= entry["locked_until"]:
            del _lockouts[key]
            return False

        return True


def _record_failed_attempt(key: str, max_failed: int, lockout_seconds: int) -> None:

    with _rl_lock:
        entry = _lockouts.setdefault(key, {"failed": 0, "locked_until": _now()})
        entry["failed"] += 1

        if entry["failed"] >= max_failed:
            entry["locked_until"] = _now() + timedelta(seconds=lockout_seconds)


def _clear_lockout(key: str) -> None:

    with _rl_lock:
        _lockouts.pop(key, None)


def reset_rate_limit_state_for_tests() -> None:
    """테스트 전용 — 모듈 전역 rate limit/lockout 상태를 초기화한다."""

    with _rl_lock:
        _rl_windows.clear()
        _lockouts.clear()


# --------------------------------------------------
# 마스킹
# --------------------------------------------------

def _mask_login_id(login_id: str) -> str:

    if "@" in login_id:
        local, _, domain = login_id.partition("@")
        return f"{_mask_segment(local)}@{domain}"

    return _mask_segment(login_id)


def _mask_segment(value: str) -> str:

    if len(value) <= 2:
        return value[0] + "*" * max(len(value) - 1, 1)

    return value[0] + "*" * (len(value) - 2) + value[-1]


# --------------------------------------------------
# 새 비밀번호 검증 공통 헬퍼
# --------------------------------------------------

def _validate_new_password(new_password: str, new_password_confirmation: str) -> None:

    if new_password != new_password_confirmation:
        raise ValueError("PASSWORD_CONFIRMATION_MISMATCH")

    policy_errors = validate_password_policy(new_password, settings)

    if policy_errors:
        raise ValueError("PASSWORD_POLICY_VIOLATION")


# --------------------------------------------------
# 1. 아이디 찾기
# --------------------------------------------------

class ForgotIdOutcome(str, Enum):

    GENERIC = "GENERIC"
    EMAIL_NOT_CONFIGURED = "EMAIL_NOT_CONFIGURED"


def request_forgot_id(
    db: Session,
    *,
    email: str,
    provider: PasswordResetDeliveryProvider,
) -> ForgotIdOutcome:
    """
    계정 존재 여부와 무관하게 항상 같은 흐름 — rate limit 초과 시에도
    호출자에게는 GENERIC과 동일하게 보이도록 반환값을 그대로 둔다
    (감사 로그로만 구분).
    """

    normalized_email = email.strip().lower()
    rate_key = f"forgot_id:{normalized_email}"

    if not _within_rate_limit(rate_key, FORGOT_ID_WINDOW_SECONDS, FORGOT_ID_MAX_REQUESTS):
        write_audit_log(
            db, user_id=None, action="FORGOT_ID_RATE_LIMITED",
            entity="users", entity_id="-",
            description="Forgot-ID request rate limit exceeded.",
        )
        db.commit()
        return ForgotIdOutcome.GENERIC

    if not provider.is_configured:
        write_audit_log(
            db, user_id=None, action="FORGOT_ID_REQUESTED",
            entity="users", entity_id="-",
            description="Forgot-ID requested while no email provider is configured.",
        )
        db.commit()
        return ForgotIdOutcome.EMAIL_NOT_CONFIGURED

    user = db.query(User).filter(User.email == normalized_email).first()

    if user is not None and user.is_active:
        try:
            provider.send_forgot_id_email(
                to_email=normalized_email,
                masked_login_id=_mask_login_id(user.username),
            )
        except Exception:
            pass

    write_audit_log(
        db, user_id=None, action="FORGOT_ID_REQUESTED",
        entity="users", entity_id="-",
        description="Forgot-ID request processed.",
    )
    db.commit()

    return ForgotIdOutcome.GENERIC


# --------------------------------------------------
# 2. 로그인 상태 — 복구 코드 묶음 생성
# --------------------------------------------------

def generate_recovery_codes(db: Session, *, user: User) -> list[str]:
    """
    현재 로그인된 사용자 본인의 복구 코드 묶음을 새로 생성한다. 기존
    미사용 코드는 전부 폐기(revoked_at)된다. 원문은 이 호출의 반환값
    에만 담기고 DB에는 해시만 저장된다 — 호출자(라우터)는 응답 후
    다시 조회할 수 없다.
    """

    now = _now()

    existing = (
        db.query(RecoveryCode)
        .filter(
            RecoveryCode.user_id == user.id,
            RecoveryCode.used_at.is_(None),
            RecoveryCode.revoked_at.is_(None),
        )
        .all()
    )

    for row in existing:
        row.revoked_at = now
        db.add(row)

    raw_codes: list[str] = []

    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_urlsafe(10).replace("-", "").replace("_", "")[:16]
        raw_codes.append(raw)

        db.add(
            RecoveryCode(
                user_id=user.id,
                company_id=user.company_id,
                code_hash=hash_password(raw),
                created_at=now,
                expires_at=None,
                used_at=None,
                revoked_at=None,
            ),
        )

    write_audit_log(
        db, user_id=user.id, action="RECOVERY_CODES_REGENERATED",
        entity="users", entity_id=str(user.id),
        description=f"User(id={user.id}) regenerated recovery codes "
        f"({len(existing)} previous unused code(s) revoked).",
        company_id=user.company_id,
    )
    db.commit()

    return raw_codes


# --------------------------------------------------
# 3. 로그아웃 상태 — 복구 코드로 비밀번호 재설정
# --------------------------------------------------

@dataclass
class RecoverySubmitResult:

    success: bool


def consume_recovery_code(
    db: Session,
    *,
    email: str,
    code: str,
    new_password: str,
    new_password_confirmation: str,
) -> RecoverySubmitResult:
    """
    항상 GENERIC 실패/성공 신호만 반환한다(계정 존재·코드 일치 여부를
    구분해 드러내지 않는다) — 단, 새 비밀번호 정책 위반은 호출자가
    입력을 수정할 수 있어야 하므로 별도 예외로 구분한다(계정 존재
    여부와 무관한 순수 입력 형식 오류이므로 열거 공격에 쓰이지 않는다).
    """

    normalized_email = email.strip().lower()

    _validate_new_password(new_password, new_password_confirmation)

    user = db.query(User).filter(User.email == normalized_email).first()

    if user is None or not user.is_active:
        # 존재하지 않는 계정도 동일한 지연/락아웃 계열 경로를 태워
        # 타이밍으로 계정 존재 여부가 드러나지 않게 한다. 어떤 이메일인지는
        # 남기지 않고 시도 자체만 감사 로그에 남긴다.
        _record_failed_attempt(
            f"recovery_code:{normalized_email}",
            RECOVERY_CODE_MAX_FAILED, RECOVERY_CODE_LOCKOUT_SECONDS,
        )
        write_audit_log(
            db, user_id=None, action="RECOVERY_CODE_FAILED",
            entity="users", entity_id="-",
            description="Recovery code submission for an unknown or inactive account.",
        )
        db.commit()
        return RecoverySubmitResult(success=False)

    lockout_key = f"recovery_code:user:{user.id}"

    if _is_locked_out(lockout_key):
        return RecoverySubmitResult(success=False)

    candidates = (
        db.query(RecoveryCode)
        .filter(
            RecoveryCode.user_id == user.id,
            RecoveryCode.used_at.is_(None),
            RecoveryCode.revoked_at.is_(None),
        )
        .all()
    )

    matched: RecoveryCode | None = None

    for candidate in candidates:
        if verify_password(code, candidate.code_hash):
            matched = candidate
            break

    if matched is None:
        _record_failed_attempt(lockout_key, RECOVERY_CODE_MAX_FAILED, RECOVERY_CODE_LOCKOUT_SECONDS)
        write_audit_log(
            db, user_id=user.id, action="RECOVERY_CODE_FAILED",
            entity="users", entity_id=str(user.id),
            description="Recovery code submission did not match any active code.",
            company_id=user.company_id,
        )
        db.commit()
        return RecoverySubmitResult(success=False)

    now = _now()

    # matched는 SELECT 시점 스냅샷이라 두 요청이 같은 코드를 동시에
    # "미사용"으로 봤을 수 있다 — 여기서 조건부 UPDATE(rowcount 체크)로
    # 원자적으로 claim해야 정확히 1건만 성공한다(app/domains/media_asset
    # /job_queue_service.py::_execute_job()의 PENDING→RUNNING 패턴과
    # 동일한 원칙).
    claim = db.execute(
        text(
            "UPDATE recovery_codes SET used_at = :used_at "
            "WHERE id = :id AND used_at IS NULL AND revoked_at IS NULL",
        ),
        {"used_at": now, "id": matched.id},
    )

    if claim.rowcount != 1:
        _record_failed_attempt(lockout_key, RECOVERY_CODE_MAX_FAILED, RECOVERY_CODE_LOCKOUT_SECONDS)
        db.rollback()
        return RecoverySubmitResult(success=False)

    user.password_hash = hash_password(new_password)
    db.add(user)

    revoke_all_sessions_for_user(db, user.id, reason="recovery_code_reset")

    write_audit_log(
        db, user_id=user.id, action="PASSWORD_RESET_VIA_RECOVERY_CODE",
        entity="users", entity_id=str(user.id),
        description="Password reset using a one-time recovery code; all sessions revoked.",
        company_id=user.company_id,
    )
    db.commit()

    _clear_lockout(lockout_key)

    return RecoverySubmitResult(success=True)


# --------------------------------------------------
# 4. 이메일 재설정 링크 — 요청
# --------------------------------------------------

def _issue_reset_token(db: Session, *, user: User, issued_by: str) -> str:
    """미사용 토큰 전부 폐기 후 새 토큰 1건을 발급, 원문을 반환한다."""

    now = _now()

    existing = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
        .all()
    )

    for row in existing:
        row.revoked_at = now
        db.add(row)

    raw_token = secrets.token_urlsafe(32)

    db.add(
        PasswordResetToken(
            user_id=user.id,
            company_id=user.company_id,
            token_hash=sha256(raw_token),
            issued_by=issued_by,
            created_at=now,
            expires_at=now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
            used_at=None,
            revoked_at=None,
        ),
    )

    return raw_token


def request_password_reset_email(
    db: Session,
    *,
    email: str,
    provider: PasswordResetDeliveryProvider,
    build_reset_link: Callable[[str], str],
) -> ForgotIdOutcome:
    """
    항상 GENERIC(계정 존재 비노출) 또는 EMAIL_NOT_CONFIGURED만 반환.
    `build_reset_link(raw_token)`은 라우터가 넘기는 순수 함수 — 이
    서비스 계층은 URL 스킴/호스트를 알 필요가 없다.
    """

    normalized_email = email.strip().lower()
    rate_key = f"reset_email:{normalized_email}"

    if not _within_rate_limit(rate_key, RESET_REQUEST_WINDOW_SECONDS, RESET_REQUEST_MAX_REQUESTS):
        write_audit_log(
            db, user_id=None, action="PASSWORD_RESET_EMAIL_RATE_LIMITED",
            entity="users", entity_id="-",
            description="Password reset email request rate limit exceeded.",
        )
        db.commit()
        return ForgotIdOutcome.GENERIC

    if not provider.is_configured:
        write_audit_log(
            db, user_id=None, action="PASSWORD_RESET_EMAIL_REQUESTED",
            entity="users", entity_id="-",
            description="Password reset email requested while no provider is configured.",
        )
        db.commit()
        return ForgotIdOutcome.EMAIL_NOT_CONFIGURED

    user = db.query(User).filter(User.email == normalized_email).first()

    if user is not None and user.is_active:
        raw_token = _issue_reset_token(db, user=user, issued_by="EMAIL")
        db.commit()

        try:
            provider.send_password_reset_email(
                to_email=normalized_email,
                reset_link=build_reset_link(raw_token),
            )
        except Exception:
            pass
    else:
        db.commit()

    write_audit_log(
        db, user_id=None, action="PASSWORD_RESET_EMAIL_REQUESTED",
        entity="users", entity_id="-",
        description="Password reset email request processed.",
    )
    db.commit()

    return ForgotIdOutcome.GENERIC


# --------------------------------------------------
# 5. 재설정 토큰 소비(이메일 링크·SUPER_ADMIN 발급 공용)
# --------------------------------------------------

def consume_reset_token(
    db: Session,
    *,
    token: str,
    new_password: str,
    new_password_confirmation: str,
) -> RecoverySubmitResult:

    _validate_new_password(new_password, new_password_confirmation)

    token_hash = sha256(token)
    lockout_key = f"reset_token:{token_hash}"

    if _is_locked_out(lockout_key):
        return RecoverySubmitResult(success=False)

    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .first()
    )

    now = _now()

    if (
        row is None
        or row.used_at is not None
        or row.revoked_at is not None
        or row.expires_at <= now
    ):
        _record_failed_attempt(lockout_key, RESET_TOKEN_MAX_FAILED, RESET_TOKEN_LOCKOUT_SECONDS)
        return RecoverySubmitResult(success=False)

    user = db.query(User).filter(User.id == row.user_id).first()

    if user is None or not user.is_active:
        _record_failed_attempt(lockout_key, RESET_TOKEN_MAX_FAILED, RESET_TOKEN_LOCKOUT_SECONDS)
        return RecoverySubmitResult(success=False)

    # row는 SELECT 시점 스냅샷 — 조건부 UPDATE(rowcount 체크)로 원자적
    # claim해야 동시 요청 중 정확히 1건만 성공한다.
    claim = db.execute(
        text(
            "UPDATE password_reset_tokens SET used_at = :used_at "
            "WHERE id = :id AND used_at IS NULL AND revoked_at IS NULL",
        ),
        {"used_at": now, "id": row.id},
    )

    if claim.rowcount != 1:
        _record_failed_attempt(lockout_key, RESET_TOKEN_MAX_FAILED, RESET_TOKEN_LOCKOUT_SECONDS)
        db.rollback()
        return RecoverySubmitResult(success=False)

    user.password_hash = hash_password(new_password)
    db.add(user)

    revoke_all_sessions_for_user(db, user.id, reason="password_reset_token")

    write_audit_log(
        db, user_id=user.id, action="PASSWORD_RESET_VIA_TOKEN",
        entity="users", entity_id=str(user.id),
        description=f"Password reset via {row.issued_by} token; all sessions revoked.",
        company_id=user.company_id,
    )
    db.commit()

    _clear_lockout(lockout_key)

    return RecoverySubmitResult(success=True)


# --------------------------------------------------
# 6. SUPER_ADMIN — 다른 사용자 재설정 시작
# --------------------------------------------------

class SuperAdminResetError(str, Enum):

    USER_NOT_FOUND = "USER_NOT_FOUND"
    CROSS_COMPANY = "CROSS_COMPANY"
    TARGET_INACTIVE = "TARGET_INACTIVE"


def super_admin_initiate_reset(
    db: Session,
    *,
    target_user_id: int,
    requesting_admin: User,
) -> str:
    """
    SUPER_ADMIN이 같은 회사 사용자를 위해 1회용 재설정 토큰을 발급
    한다. 관리자는 이 원문 토큰을 화면에서 1회만 보고, 사용자에게
    직접 전달해야 한다(이메일 발송이 아니다) — 관리자는 이후 이 토큰
    으로 새 비밀번호가 무엇이 되는지 알 수 없다(사용자가 직접 입력).
    타 회사 사용자는 존재 여부를 노출하지 않기 위해 USER_NOT_FOUND와
    동일하게 취급한다(호출자가 이미 인증된 SUPER_ADMIN이라 해도, 다른
    회사 정보를 아예 열거할 수 없게 하기 위함).
    """

    target = db.query(User).filter(User.id == target_user_id).first()

    if target is None:
        raise LookupError(SuperAdminResetError.USER_NOT_FOUND.value)

    if (
        requesting_admin.company_id is None
        or target.company_id != requesting_admin.company_id
    ):
        raise LookupError(SuperAdminResetError.USER_NOT_FOUND.value)

    if not target.is_active:
        raise LookupError(SuperAdminResetError.TARGET_INACTIVE.value)

    raw_token = _issue_reset_token(db, user=target, issued_by="SUPER_ADMIN")

    write_audit_log(
        db, user_id=requesting_admin.id, action="SUPER_ADMIN_INITIATED_PASSWORD_RESET",
        entity="users", entity_id=str(target.id),
        description=f"SUPER_ADMIN(id={requesting_admin.id}) issued a password reset "
        f"token for user(id={target.id}).",
        company_id=target.company_id,
    )
    db.commit()

    return raw_token


__all__ = [
    "RECOVERY_CODE_COUNT",
    "RESET_TOKEN_TTL_MINUTES",
    "ForgotIdOutcome",
    "RecoverySubmitResult",
    "SuperAdminResetError",
    "request_forgot_id",
    "generate_recovery_codes",
    "consume_recovery_code",
    "request_password_reset_email",
    "consume_reset_token",
    "super_admin_initiate_reset",
    "reset_rate_limit_state_for_tests",
]
