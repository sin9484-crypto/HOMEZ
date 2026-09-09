"""
=========================================================
Homez OS

File : app/domains/auth/schema.py
Version : 2.1.0

Auth Schema
=========================================================
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from app.domains.user.schema import (
    UserResponse,
)


# --------------------------------------------------
# Request
# --------------------------------------------------

class LoginRequest(
    BaseModel
):

    username: str = Field(
        ...,
        description="Username",
        examples=["admin"],
    )

    password: str = Field(
        ...,
        description="Password",
        examples=["password"],
    )


class RefreshRequest(
    BaseModel
):

    refresh_token: str = Field(
        ...,
        description="JWT Refresh Token",
    )


class ChangePasswordRequest(
    BaseModel
):

    current_password: str = Field(..., description="현재 비밀번호(본인 확인용)")
    new_password: str = Field(..., description="새 비밀번호")
    new_password_confirmation: str = Field(..., description="새 비밀번호 확인")


class RecentAuthRequest(
    BaseModel
):
    """
    회사명 변경처럼 민감한 조작 직전에 현재 비밀번호를 다시 확인받아
    5분·1회용 recent-auth 토큰을 발급하기 위한 요청.
    """

    current_password: str = Field(..., description="현재 비밀번호(재확인용)")


# --------------------------------------------------
# Response
# --------------------------------------------------

class LoginResponse(
    BaseModel
):

    access_token: str = Field(
        ...,
        description="JWT Access Token",
    )

    token_type: str = Field(
        default="bearer",
        description="Token Type",
    )

    user: UserResponse

    refresh_token: str | None = Field(
        default=None,
        description="JWT Refresh Token",
    )

    permissions: list[str] = Field(
        default_factory=list,
        description="이 사용자의 역할에 부여된 세부 Permission 코드 목록"
        "(app/core/permission_check.py::get_permission_codes_for_role). "
        "클라이언트 nav 표시 등 UX 편의용이며, 서버측 접근 제어는 "
        "이 값과 무관하게 매 요청마다 다시 검사한다.",
    )

    model_config = ConfigDict(
        from_attributes=True
    )


class AuthMessage(
    BaseModel
):

    message: str


class RecentAuthResponse(
    BaseModel
):

    recent_auth_token: str = Field(..., description="5분 이내 1회만 유효한 재인증 증명 토큰")
    expires_at: str = Field(..., description="ISO8601 만료 시각")
