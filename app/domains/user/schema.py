from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr


class UserBase(
    BaseModel
):

    email: EmailStr

    username: str

    phone: str | None = None

    name: str | None = None

    nickname: str | None = None


class UserCreate(
    UserBase
):

    password: str


class UserUpdate(
    BaseModel
):

    username: str | None = None

    phone: str | None = None

    name: str | None = None

    nickname: str | None = None
class UserPasswordChangeRequest(
    BaseModel
):

    current_password: str

    new_password: str


class UserLoginRequest(
    BaseModel
):

    email: EmailStr

    password: str


class UserProfileResponse(
    BaseModel
):
    """
    실제 homez.db `users` 테이블(id/company_id/role_id/username/email/
    password/name/phone/active)에 존재하는 필드만 선언한다. 이전 정의는
    `nickname`/`is_verified`/`created_at`/`login_count`/`last_login_at`
    등 실제 User 모델(app/domains/user/model.py, 2026-07-30 재작성)에
    없는 필드를 요구해, `/auth/login` 응답을 이 스키마로 직렬화하는
    순간 Pydantic ValidationError(500)로 항상 실패했다 — 로그인이
    서비스 함수 단위로는 성공해도 실제 HTTP 엔드포인트로는 한 번도
    성공한 적이 없었던 원인(2026-07-30, Desktop 로그인 흐름 완성 작업
    중 발견).
    """

    id: int

    email: EmailStr

    username: str

    phone: str | None = None

    name: str | None = None

    role: str | None = None

    is_active: bool

    model_config = ConfigDict(
        from_attributes=True,
    )
class UserResponse(
    UserProfileResponse
):
    pass


class UserListResponse(
    BaseModel
):

    items: list[UserResponse]

    total: int


class UserSearchRequest(
    BaseModel
):

    keyword: str | None = None

    role: str | None = None

    is_active: bool | None = None

    page: int = 1

    size: int = 20
class UserStatusResponse(
    BaseModel
):

    id: int

    is_active: bool

    is_verified: bool

    role: str

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class UserDeleteRequest(
    BaseModel
):

    user_id: int


class UserRoleUpdateRequest(
    BaseModel
):

    role: str


__all__ = [
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserPasswordChangeRequest",
    "UserLoginRequest",
    "UserProfileResponse",
    "UserResponse",
    "UserListResponse",
    "UserSearchRequest",
    "UserStatusResponse",
    "UserDeleteRequest",
    "UserRoleUpdateRequest",
]       