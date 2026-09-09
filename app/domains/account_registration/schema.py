"""
=========================================================
Homez OS

File : app/domains/account_registration/schema.py
=========================================================
"""

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator


class GenericMessageResponse(BaseModel):

    message: str


# --------------------------------------------------
# 공개 가입
# --------------------------------------------------

class RegisterRequest(BaseModel):
    """
    company_id/role_id/is_active/승인 상태는 의도적으로 필드 자체가
    없다 — 클라이언트가 지정할 방법이 구조적으로 없다(회사는 초대
    코드 또는 서버측 단일회사 자동 배정 정책으로만 결정된다).

    2026-08-03: invitation_code를 선택 입력으로 변경 — 코드 없이도
    가입 신청은 가능하고(관리자 승인 후 이용), 코드가 주어지면 기존
    검증 정책이 그대로 적용된다. 공백만 있는 값은 "제공되지 않음"으로
    취급한다(silent-invalid 처리 금지 — 실제로 잘못된/만료된/폐기된
    코드가 주어지면 여전히 명시적으로 거부된다).
    """

    email: str = Field(..., min_length=3, max_length=200)
    display_name: str = Field(..., min_length=1, max_length=100)
    password: str
    password_confirmation: str
    invitation_code: str | None = Field(default=None, max_length=200)

    @field_validator("invitation_code")
    @classmethod
    def _normalize_invitation_code(cls, value: str | None) -> str | None:

        if value is None:
            return None

        stripped = value.strip()

        return stripped or None


class RegisterResponse(BaseModel):

    message: str
    status_check_token: str


class RegistrationStatusResponse(BaseModel):
    """
    회사·역할·관리자 정보를 노출하지 않는다 — status만 반환한다.
    """

    status: str


# --------------------------------------------------
# 관리자 — 가입 승인 대기 목록
# --------------------------------------------------

class RegistrationRequestSummary(BaseModel):

    id: int
    display_name: str
    masked_email: str
    status: str
    requested_at: datetime


class RejectRequest(BaseModel):

    reason_code: str


class ApproveRequest(BaseModel):

    role_code: str | None = None  # 생략 시 최소 권한 역할


class AssignRoleRequest(BaseModel):

    role_code: str


# --------------------------------------------------
# 초대 코드
# --------------------------------------------------

class InvitationCreateRequest(BaseModel):

    max_role_code: str
    ttl_seconds: int | None = None
    max_uses: int | None = None


class InvitationCreateResponse(BaseModel):

    invitation_code: str
    expires_at: datetime
    max_uses: int


class InvitationSummary(BaseModel):

    id: int
    max_role_code: str
    expires_at: datetime
    max_uses: int
    used_count: int
    revoked: bool


__all__ = [
    "GenericMessageResponse",
    "RegisterRequest",
    "RegisterResponse",
    "RegistrationStatusResponse",
    "RegistrationRequestSummary",
    "RejectRequest",
    "ApproveRequest",
    "AssignRoleRequest",
    "InvitationCreateRequest",
    "InvitationCreateResponse",
    "InvitationSummary",
]
