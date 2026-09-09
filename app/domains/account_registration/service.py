"""
=========================================================
Homez OS

File : app/domains/account_registration/service.py

회원가입 + 승인 워크플로 핵심 로직.

가입(register) 자체는 User + UserRegistrationRequest + InvitationCode
사용횟수 증가를 하나의 Transaction으로 묶어야 하므로, 이 파일의
다른 함수들(승인/거절/정지/재활성/역할변경/초대 발급)과 달리 raw
sqlite3 BEGIN IMMEDIATE를 쓴다 — app/core/first_admin_setup.py /
app/core/company_recovery_setup.py와 동일한 "다중 테이블 원자적 생성"
패턴 재사용이다.

승인/거절/정지 등은 이미 존재하는 행 하나를 조건부 UPDATE로 원자적
claim하는 패턴(app/domains/account_recovery/service.py와 동일 원칙)
이라 일반 SQLAlchemy Session으로 충분하다.
=========================================================
"""

from __future__ import annotations

import base64
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.config import settings
from app.core.password_policy import validate_password_policy
from app.core.permission_check import can_assign_role_code
from app.core.permission_check import MINIMUM_APPROVAL_ROLE_CODE
from app.core.security import create_hmac_signature
from app.core.security import hash_password
from app.core.security import sha256
from app.core.security import verify_hmac_signature
from app.domains.account_registration.constants import (
    ALLOWED_TRANSITIONS,
    INVITATION_CODE_DEFAULT_MAX_USES,
    INVITATION_CODE_DEFAULT_TTL_SECONDS,
    INVITATION_VERIFY_LOCKOUT_SECONDS,
    INVITATION_VERIFY_MAX_FAILED,
    REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS,
    REGISTRATION_RATE_LIMIT_WINDOW_SECONDS,
    STATUS_CHECK_TOKEN_TTL_SECONDS,
    RegistrationStatus,
)
from app.domains.account_registration.model import InvitationCode
from app.domains.account_registration.model import UserRegistrationRequest
from app.domains.role.model import Role
from app.domains.session.service import revoke_all_sessions_for_user
from app.domains.user.model import User

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_BUSY_TIMEOUT_MS = 15000


def resolve_sqlite_path(database_url: str | None = None) -> str:

    url = database_url or settings.DATABASE_URL
    prefix = "sqlite:///"

    if not url.startswith(prefix):
        raise ValueError(
            "회원가입 원자적 생성은 SQLite DATABASE_URL만 지원합니다.",
        )

    raw_path = url[len(prefix):]

    if os.path.isabs(raw_path):
        return raw_path

    return os.path.normpath(os.path.join(REPO_ROOT, raw_path))


# --------------------------------------------------
# 프로세스 전역 rate limit(account_recovery/service.py와 동일 설계 원칙)
# --------------------------------------------------

_rl_lock = threading.Lock()
_rl_windows: dict[str, list[datetime]] = {}
_lockouts: dict[str, dict] = {}


def _now() -> datetime:

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


def reset_rate_limit_state_for_tests() -> None:

    with _rl_lock:
        _rl_windows.clear()
        _lockouts.clear()


# --------------------------------------------------
# 마스킹
# --------------------------------------------------

def _mask_email(email: str) -> str:

    local, _, domain = email.partition("@")

    if not domain:
        return email[0] + "*" * max(len(email) - 1, 1)

    if len(local) <= 2:
        masked_local = local[0] + "*" * max(len(local) - 1, 1)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]

    return f"{masked_local}@{domain}"


# --------------------------------------------------
# 신청 상태 확인 토큰 — DB에 저장하지 않는 stateless HMAC 토큰.
# request_id와 만료 시각만 담고, 회사/역할/관리자 정보는 없다.
# --------------------------------------------------

def issue_status_check_token(request_id: int) -> str:

    expires_epoch = int(time.time()) + STATUS_CHECK_TOKEN_TTL_SECONDS
    payload = f"{request_id}.{expires_epoch}"
    signature = create_hmac_signature(payload, settings.SECRET_KEY)
    raw = f"{payload}.{signature}"

    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def verify_status_check_token(token: str) -> int | None:

    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        request_id_str, expires_str, signature = raw.split(".")
    except Exception:
        return None

    payload = f"{request_id_str}.{expires_str}"

    if not verify_hmac_signature(payload, signature, settings.SECRET_KEY):
        return None

    try:
        if int(expires_str) < int(time.time()):
            return None
        return int(request_id_str)
    except ValueError:
        return None


# --------------------------------------------------
# 공개 가입 — 원자적 생성(raw sqlite3)
# --------------------------------------------------

class RegisterOutcome(str, Enum):

    SUCCESS = "SUCCESS"
    INVALID_INVITATION = "INVALID_INVITATION"
    DUPLICATE_ACCOUNT = "DUPLICATE_ACCOUNT"
    # 초대 코드 없이 가입할 때만 발생 — 자동 배정 가능한 활성 회사가
    # 정확히 1개가 아닌 경우(0개 또는 2개 이상)의 안전한 거부.
    # INVALID_INVITATION과 별개 값이지만, 라우터는 둘 다 동일한 일반
    # 실패 메시지로 응답해 원인을 클라이언트에 노출하지 않는다.
    COMPANY_UNAVAILABLE = "COMPANY_UNAVAILABLE"


@dataclass(frozen=True)
class RegisterResult:

    outcome: RegisterOutcome
    request_id: int | None = None


def atomic_register_user(
    db_path: str,
    *,
    email: str,
    username: str,
    display_name: str,
    password_hash: str,
    invitation_code_raw: str | None = None,
) -> RegisterResult:

    now = _now().isoformat(sep=" ")

    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)

    try:
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("BEGIN IMMEDIATE")

        try:
            invitation_id: int | None = None
            company_id: int

            if invitation_code_raw is not None:

                invitation_hash = sha256(invitation_code_raw)

                invitation_row = conn.execute(
                    "SELECT id, company_id, max_uses, used_count, expires_at, revoked_at "
                    "FROM invitation_codes WHERE code_hash = ?",
                    (invitation_hash,),
                ).fetchone()

                if invitation_row is None:
                    conn.execute("ROLLBACK")
                    return RegisterResult(outcome=RegisterOutcome.INVALID_INVITATION)

                (
                    invitation_id, company_id, max_uses, used_count,
                    expires_at, revoked_at,
                ) = invitation_row

                if (
                    revoked_at is not None
                    or used_count >= max_uses
                    or str(expires_at) < now
                ):
                    conn.execute("ROLLBACK")
                    return RegisterResult(outcome=RegisterOutcome.INVALID_INVITATION)

            else:

                # 초대 코드 없이 가입 — HOMEZ는 단일회사 설치이므로(
                # app/core/company_recovery_setup.py의 companies==0 전제와
                # 회사 전환/다중생성 UI 부재로 확인됨), 활성 회사가 정확히
                # 1개일 때만 그 회사로 자동 배정한다. 0개 또는 2개 이상이면
                # 임의로 고르지 않고 안전하게 거부한다.
                active_companies = conn.execute(
                    "SELECT id FROM companies WHERE active = 1",
                ).fetchall()

                if len(active_companies) != 1:
                    conn.execute("ROLLBACK")
                    return RegisterResult(outcome=RegisterOutcome.COMPANY_UNAVAILABLE)

                company_id = active_companies[0][0]

            existing = conn.execute(
                # users의 실제 컬럼명은 "active"다(Python 속성명
                # is_active는 mapped_column("active", ...)로 매핑된
                # 별칭일 뿐 — app/domains/user/model.py 참고).
                "SELECT id, active FROM users WHERE email = ? OR username = ?",
                (email, username),
            ).fetchone()

            resubmit_user_id: int | None = None

            if existing is not None:

                existing_user_id, existing_is_active = existing

                if existing_is_active:
                    conn.execute("ROLLBACK")
                    return RegisterResult(outcome=RegisterOutcome.DUPLICATE_ACCOUNT)

                latest_request = conn.execute(
                    "SELECT status FROM user_registration_requests "
                    "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (existing_user_id,),
                ).fetchone()

                if latest_request is None or latest_request[0] != RegistrationStatus.REJECTED.value:
                    conn.execute("ROLLBACK")
                    return RegisterResult(outcome=RegisterOutcome.DUPLICATE_ACCOUNT)

                resubmit_user_id = existing_user_id

            if resubmit_user_id is not None:

                conn.execute(
                    "UPDATE users SET email = ?, username = ?, password = ?, name = ?, "
                    "company_id = ?, role_id = NULL, active = 0 WHERE id = ?",
                    (email, username, password_hash, display_name, company_id, resubmit_user_id),
                )
                user_id = resubmit_user_id

            else:

                user_cursor = conn.execute(
                    "INSERT INTO users "
                    "(company_id, role_id, username, email, password, name, phone, active) "
                    "VALUES (?, NULL, ?, ?, ?, ?, NULL, 0)",
                    (company_id, username, email, password_hash, display_name),
                )
                user_id = user_cursor.lastrowid

            request_cursor = conn.execute(
                "INSERT INTO user_registration_requests "
                "(company_id, user_id, status, requested_at, decided_at, "
                "decided_by_user_id, rejection_reason_code, granted_role_id, invitation_code_id) "
                "VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)",
                (company_id, user_id, RegistrationStatus.PENDING_APPROVAL.value, now, invitation_id),
            )
            request_id = request_cursor.lastrowid

            if invitation_id is not None:

                claim = conn.execute(
                    "UPDATE invitation_codes SET used_count = used_count + 1 "
                    "WHERE id = ? AND used_count < max_uses AND revoked_at IS NULL",
                    (invitation_id,),
                )

                if claim.rowcount != 1:
                    conn.execute("ROLLBACK")
                    return RegisterResult(outcome=RegisterOutcome.INVALID_INVITATION)

            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, description, ip_address) "
                "VALUES (?, NULL, ?, ?, ?, ?, NULL)",
                (
                    company_id, "REGISTRATION_REQUESTED", "user_registration_requests",
                    str(request_id), "New self-registration submitted; pending approval.",
                ),
            )

            conn.execute("COMMIT")

            return RegisterResult(outcome=RegisterOutcome.SUCCESS, request_id=request_id)

        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return RegisterResult(outcome=RegisterOutcome.DUPLICATE_ACCOUNT)

        except Exception:
            conn.execute("ROLLBACK")
            raise

    finally:
        conn.close()


def register(
    db_path: str,
    *,
    email: str,
    display_name: str,
    password: str,
    password_confirmation: str,
    invitation_code: str | None = None,
) -> RegisterResult:

    normalized_email = email.strip().lower()
    normalized_username = normalized_email  # 이 시스템은 로그인 ID로 이메일을 쓴다(기존 관례)

    # 공백만 있는 코드는 "제공되지 않음"으로 취급한다(schema.py에서도
    # 정규화하지만, 이 함수가 스키마 계층을 거치지 않고 직접 호출될 수도
    # 있으므로 여기서도 방어적으로 다시 정규화한다).
    normalized_invitation_code = (invitation_code or "").strip() or None

    rate_key = f"register:{normalized_email}"

    if not _within_rate_limit(rate_key, REGISTRATION_RATE_LIMIT_WINDOW_SECONDS, REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS):
        return RegisterResult(outcome=RegisterOutcome.INVALID_INVITATION)

    if password != password_confirmation:
        raise ValueError("PASSWORD_CONFIRMATION_MISMATCH")

    policy_errors = validate_password_policy(password, settings)

    if policy_errors:
        raise ValueError("PASSWORD_POLICY_VIOLATION")

    invitation_lockout_key = None

    if normalized_invitation_code is not None:

        invitation_lockout_key = f"invitation_verify:{sha256(normalized_invitation_code)}"

        if _is_locked_out(invitation_lockout_key):
            return RegisterResult(outcome=RegisterOutcome.INVALID_INVITATION)

    result = atomic_register_user(
        db_path,
        email=normalized_email,
        username=normalized_username,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        invitation_code_raw=normalized_invitation_code,
    )

    if result.outcome == RegisterOutcome.INVALID_INVITATION and invitation_lockout_key is not None:
        _record_failed_attempt(invitation_lockout_key, INVITATION_VERIFY_MAX_FAILED, INVITATION_VERIFY_LOCKOUT_SECONDS)

    return result


# --------------------------------------------------
# 관리자 — 목록/승인/거절/정지/재활성/역할변경
# --------------------------------------------------

def _own_company_request_or_404(db: Session, request_id: int, current_user: User) -> UserRegistrationRequest:

    row = db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == request_id).first()

    if row is None or row.company_id != current_user.company_id:
        raise LookupError("NOT_FOUND")

    return row


def list_pending_requests(db: Session, *, current_user: User) -> list[UserRegistrationRequest]:

    return (
        db.query(UserRegistrationRequest)
        .filter(
            UserRegistrationRequest.company_id == current_user.company_id,
            UserRegistrationRequest.status == RegistrationStatus.PENDING_APPROVAL.value,
        )
        .order_by(UserRegistrationRequest.requested_at.asc())
        .all()
    )


def _count_active_super_admins(db: Session) -> int:

    rows = db.query(User).filter(User.is_active.is_(True)).all()

    return sum(1 for u in rows if (u.role or "").strip().upper() == "SUPER_ADMIN")


class RegistrationActionError(str, Enum):

    NOT_FOUND = "NOT_FOUND"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    ROLE_TOO_HIGH = "ROLE_TOO_HIGH"
    UNKNOWN_ROLE = "UNKNOWN_ROLE"
    SELF_ACTION_FORBIDDEN = "SELF_ACTION_FORBIDDEN"
    LAST_SUPER_ADMIN = "LAST_SUPER_ADMIN"


def approve_request(
    db: Session, *, request_id: int, current_user: User, role_code: str | None,
) -> UserRegistrationRequest:

    row = _own_company_request_or_404(db, request_id, current_user)

    if row.user_id == current_user.id:
        raise ValueError(RegistrationActionError.SELF_ACTION_FORBIDDEN.value)

    target_role_code = (role_code or MINIMUM_APPROVAL_ROLE_CODE).upper()

    if not can_assign_role_code(current_user, target_role_code):
        raise ValueError(RegistrationActionError.ROLE_TOO_HIGH.value)

    role = db.query(Role).filter(Role.code == target_role_code).first()

    if role is None:
        raise ValueError(RegistrationActionError.UNKNOWN_ROLE.value)

    # 이 초대가 발급 시점에 설정된 상한(max_role_code)보다 높은 역할로
    # 승인되지 않도록 재확인한다.
    if row.invitation_code_id is not None:
        invitation = db.query(InvitationCode).filter(InvitationCode.id == row.invitation_code_id).first()
        if invitation is not None and not can_assign_role_code(current_user, target_role_code):
            raise ValueError(RegistrationActionError.ROLE_TOO_HIGH.value)

    now = _now()

    claim = db.execute(
        text(
            "UPDATE user_registration_requests SET status = :new_status, "
            "decided_at = :decided_at, decided_by_user_id = :decided_by, "
            "granted_role_id = :role_id "
            "WHERE id = :id AND company_id = :company_id AND status = :old_status",
        ),
        {
            "new_status": RegistrationStatus.APPROVED.value,
            "decided_at": now, "decided_by": current_user.id, "role_id": role.id,
            "id": request_id, "company_id": current_user.company_id,
            "old_status": RegistrationStatus.PENDING_APPROVAL.value,
        },
    )

    if claim.rowcount != 1:
        db.rollback()
        raise ValueError(RegistrationActionError.INVALID_TRANSITION.value)

    target_user = db.query(User).filter(User.id == row.user_id).first()
    target_user.role_id = role.id
    target_user.is_active = True
    db.add(target_user)

    write_audit_log(
        db, user_id=current_user.id, action="REGISTRATION_APPROVED",
        entity="user_registration_requests", entity_id=str(request_id),
        description=f"Registration request approved; role granted (role_id={role.id}).",
        company_id=current_user.company_id,
    )
    db.commit()

    db.refresh(row)

    return row


def reject_request(
    db: Session, *, request_id: int, current_user: User, reason_code: str,
) -> UserRegistrationRequest:

    row = _own_company_request_or_404(db, request_id, current_user)

    if row.user_id == current_user.id:
        raise ValueError(RegistrationActionError.SELF_ACTION_FORBIDDEN.value)

    now = _now()

    claim = db.execute(
        text(
            "UPDATE user_registration_requests SET status = :new_status, "
            "decided_at = :decided_at, decided_by_user_id = :decided_by, "
            "rejection_reason_code = :reason_code "
            "WHERE id = :id AND company_id = :company_id AND status = :old_status",
        ),
        {
            "new_status": RegistrationStatus.REJECTED.value,
            "decided_at": now, "decided_by": current_user.id, "reason_code": reason_code,
            "id": request_id, "company_id": current_user.company_id,
            "old_status": RegistrationStatus.PENDING_APPROVAL.value,
        },
    )

    if claim.rowcount != 1:
        db.rollback()
        raise ValueError(RegistrationActionError.INVALID_TRANSITION.value)

    write_audit_log(
        db, user_id=current_user.id, action="REGISTRATION_REJECTED",
        entity="user_registration_requests", entity_id=str(request_id),
        description=f"Registration request rejected (reason_code={reason_code}).",
        company_id=current_user.company_id,
    )
    db.commit()

    db.refresh(row)

    return row


def suspend_user(
    db: Session, *, request_id: int, current_user: User,
) -> UserRegistrationRequest:

    row = _own_company_request_or_404(db, request_id, current_user)

    if row.user_id == current_user.id:
        raise ValueError(RegistrationActionError.SELF_ACTION_FORBIDDEN.value)

    target_user = db.query(User).filter(User.id == row.user_id).first()

    if (
        target_user is not None
        and (target_user.role or "").strip().upper() == "SUPER_ADMIN"
        and target_user.is_active
        and _count_active_super_admins(db) <= 1
    ):
        raise ValueError(RegistrationActionError.LAST_SUPER_ADMIN.value)

    now = _now()

    claim = db.execute(
        text(
            "UPDATE user_registration_requests SET status = :new_status, "
            "decided_at = :decided_at, decided_by_user_id = :decided_by "
            "WHERE id = :id AND company_id = :company_id AND status = :old_status",
        ),
        {
            "new_status": RegistrationStatus.SUSPENDED.value,
            "decided_at": now, "decided_by": current_user.id,
            "id": request_id, "company_id": current_user.company_id,
            "old_status": RegistrationStatus.APPROVED.value,
        },
    )

    if claim.rowcount != 1:
        db.rollback()
        raise ValueError(RegistrationActionError.INVALID_TRANSITION.value)

    target_user.is_active = False
    db.add(target_user)

    revoke_all_sessions_for_user(db, target_user.id, reason="registration_suspended")

    write_audit_log(
        db, user_id=current_user.id, action="USER_ACCESS_SUSPENDED",
        entity="user_registration_requests", entity_id=str(request_id),
        description="User access suspended; all sessions revoked.",
        company_id=current_user.company_id,
    )
    db.commit()

    db.refresh(row)

    return row


def reactivate_user(
    db: Session, *, request_id: int, current_user: User,
) -> UserRegistrationRequest:

    row = _own_company_request_or_404(db, request_id, current_user)

    now = _now()

    claim = db.execute(
        text(
            "UPDATE user_registration_requests SET status = :new_status, "
            "decided_at = :decided_at, decided_by_user_id = :decided_by "
            "WHERE id = :id AND company_id = :company_id AND status = :old_status",
        ),
        {
            "new_status": RegistrationStatus.APPROVED.value,
            "decided_at": now, "decided_by": current_user.id,
            "id": request_id, "company_id": current_user.company_id,
            "old_status": RegistrationStatus.SUSPENDED.value,
        },
    )

    if claim.rowcount != 1:
        db.rollback()
        raise ValueError(RegistrationActionError.INVALID_TRANSITION.value)

    target_user = db.query(User).filter(User.id == row.user_id).first()
    target_user.is_active = True
    db.add(target_user)

    write_audit_log(
        db, user_id=current_user.id, action="USER_ACCESS_REACTIVATED",
        entity="user_registration_requests", entity_id=str(request_id),
        description="User access reactivated; no automatic login granted.",
        company_id=current_user.company_id,
    )
    db.commit()

    db.refresh(row)

    return row


def assign_role(
    db: Session, *, target_user_id: int, current_user: User, role_code: str,
) -> User:

    if target_user_id == current_user.id:
        raise ValueError(RegistrationActionError.SELF_ACTION_FORBIDDEN.value)

    target_user = db.query(User).filter(User.id == target_user_id).first()

    if target_user is None or target_user.company_id != current_user.company_id:
        raise LookupError("NOT_FOUND")

    target_code = role_code.upper()

    if not can_assign_role_code(current_user, target_code):
        raise ValueError(RegistrationActionError.ROLE_TOO_HIGH.value)

    was_last_super_admin_target = (
        (target_user.role or "").strip().upper() == "SUPER_ADMIN"
        and target_user.is_active
        and target_code != "SUPER_ADMIN"
        and _count_active_super_admins(db) <= 1
    )

    if was_last_super_admin_target:
        raise ValueError(RegistrationActionError.LAST_SUPER_ADMIN.value)

    role = db.query(Role).filter(Role.code == target_code).first()

    if role is None:
        raise ValueError(RegistrationActionError.UNKNOWN_ROLE.value)

    target_user.role_id = role.id
    db.add(target_user)

    revoke_all_sessions_for_user(db, target_user.id, reason="role_reassigned")

    write_audit_log(
        db, user_id=current_user.id, action="USER_ROLE_ASSIGNED",
        entity="users", entity_id=str(target_user.id),
        description=f"Role reassigned (role_id={role.id}); all sessions revoked.",
        company_id=target_user.company_id,
    )
    db.commit()

    db.refresh(target_user)

    return target_user


# --------------------------------------------------
# 초대 코드
# --------------------------------------------------

def create_invitation(
    db: Session, *, current_user: User, max_role_code: str,
    ttl_seconds: int | None, max_uses: int | None,
) -> tuple[InvitationCode, str]:

    target_code = max_role_code.upper()

    if not can_assign_role_code(current_user, target_code):
        raise ValueError(RegistrationActionError.ROLE_TOO_HIGH.value)

    now = _now()
    ttl = ttl_seconds or INVITATION_CODE_DEFAULT_TTL_SECONDS
    uses = max_uses or INVITATION_CODE_DEFAULT_MAX_USES

    raw_code = secrets.token_urlsafe(24)

    invitation = InvitationCode(
        company_id=current_user.company_id,
        code_hash=sha256(raw_code),
        created_by_user_id=current_user.id,
        max_role_code=target_code,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        max_uses=uses,
        used_count=0,
        revoked_at=None,
    )
    db.add(invitation)
    db.flush()

    write_audit_log(
        db, user_id=current_user.id, action="INVITATION_CREATED",
        entity="invitation_codes", entity_id=str(invitation.id),
        description=f"Invitation created (max_role_code={target_code}, max_uses={uses}).",
        company_id=current_user.company_id,
    )
    db.commit()
    db.refresh(invitation)

    return invitation, raw_code


def revoke_invitation(db: Session, *, current_user: User, invitation_id: int) -> InvitationCode:

    row = db.query(InvitationCode).filter(InvitationCode.id == invitation_id).first()

    if row is None or row.company_id != current_user.company_id:
        raise LookupError("NOT_FOUND")

    row.revoked_at = _now()
    db.add(row)

    write_audit_log(
        db, user_id=current_user.id, action="INVITATION_REVOKED",
        entity="invitation_codes", entity_id=str(invitation_id),
        description="Invitation revoked.",
        company_id=current_user.company_id,
    )
    db.commit()
    db.refresh(row)

    return row


def list_invitations(db: Session, *, current_user: User) -> list[InvitationCode]:

    return (
        db.query(InvitationCode)
        .filter(InvitationCode.company_id == current_user.company_id)
        .order_by(InvitationCode.id.desc())
        .all()
    )


__all__ = [
    "resolve_sqlite_path",
    "RegisterOutcome",
    "RegisterResult",
    "register",
    "atomic_register_user",
    "issue_status_check_token",
    "verify_status_check_token",
    "RegistrationActionError",
    "list_pending_requests",
    "approve_request",
    "reject_request",
    "suspend_user",
    "reactivate_user",
    "assign_role",
    "create_invitation",
    "revoke_invitation",
    "list_invitations",
    "reset_rate_limit_state_for_tests",
    "_mask_email",
    "ALLOWED_TRANSITIONS",
]
