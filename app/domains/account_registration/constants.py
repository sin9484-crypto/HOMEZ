"""
=========================================================
Homez OS

File : app/domains/account_registration/constants.py
=========================================================
"""

from enum import Enum


class RegistrationStatus(str, Enum):

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


# 허용된 상태 전이만 정의한다 — 목록에 없는 전이는 전부 차단된다.
ALLOWED_TRANSITIONS = {
    (RegistrationStatus.PENDING_APPROVAL, RegistrationStatus.APPROVED),
    (RegistrationStatus.PENDING_APPROVAL, RegistrationStatus.REJECTED),
    (RegistrationStatus.APPROVED, RegistrationStatus.SUSPENDED),
    (RegistrationStatus.SUSPENDED, RegistrationStatus.APPROVED),
}


class RejectionReasonCode(str, Enum):
    """
    자유 입력 거절 사유 대신 제한된 reason code만 감사 로그에 남긴다.
    """

    INVALID_INVITATION = "INVALID_INVITATION"
    DUPLICATE_ACCOUNT = "DUPLICATE_ACCOUNT"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNVERIFIED_IDENTITY = "UNVERIFIED_IDENTITY"
    OTHER = "OTHER"


# --------------------------------------------------
# 세부 Permission 코드 — 기존 permissions 테이블에 신규 행으로
# 추가한다(실제 DB에는 아직 적용하지 않음). 새 SUPERVISOR 역할을
# 하드코딩하는 대신 이 Permission들을 기존 역할(예: ADMIN)에 부여하는
# 방식을 우선한다.
# --------------------------------------------------

PERM_USER_REGISTRATION_VIEW = "USER_REGISTRATION_VIEW"
PERM_USER_ACCESS_APPROVE = "USER_ACCESS_APPROVE"
PERM_USER_ACCESS_REJECT = "USER_ACCESS_REJECT"
PERM_USER_ACCESS_SUSPEND = "USER_ACCESS_SUSPEND"
PERM_USER_ROLE_ASSIGN = "USER_ROLE_ASSIGN"

NEW_PERMISSION_CODES = [
    PERM_USER_REGISTRATION_VIEW,
    PERM_USER_ACCESS_APPROVE,
    PERM_USER_ACCESS_REJECT,
    PERM_USER_ACCESS_SUSPEND,
    PERM_USER_ROLE_ASSIGN,
]

STATUS_CHECK_TOKEN_TTL_SECONDS = 24 * 60 * 60  # 24시간

INVITATION_CODE_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7일
INVITATION_CODE_DEFAULT_MAX_USES = 1

REGISTRATION_RATE_LIMIT_WINDOW_SECONDS = 900
REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS = 10

INVITATION_VERIFY_MAX_FAILED = 8
INVITATION_VERIFY_LOCKOUT_SECONDS = 900


__all__ = [
    "RegistrationStatus",
    "ALLOWED_TRANSITIONS",
    "RejectionReasonCode",
    "PERM_USER_REGISTRATION_VIEW",
    "PERM_USER_ACCESS_APPROVE",
    "PERM_USER_ACCESS_REJECT",
    "PERM_USER_ACCESS_SUSPEND",
    "PERM_USER_ROLE_ASSIGN",
    "NEW_PERMISSION_CODES",
    "STATUS_CHECK_TOKEN_TTL_SECONDS",
    "INVITATION_CODE_DEFAULT_TTL_SECONDS",
    "INVITATION_CODE_DEFAULT_MAX_USES",
    "REGISTRATION_RATE_LIMIT_WINDOW_SECONDS",
    "REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS",
    "INVITATION_VERIFY_MAX_FAILED",
    "INVITATION_VERIFY_LOCKOUT_SECONDS",
]
