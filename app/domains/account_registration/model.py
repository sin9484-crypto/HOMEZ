"""
=========================================================
Homez OS

File : app/domains/account_registration/model.py

회원가입/승인 워크플로 — 가입 요청(UserRegistrationRequest)과 회사
초대 코드(InvitationCode). FK 없음(다른 Domain과 동일한 설계 원칙).

`users.is_active`/`users.role_id`를 상태의 유일한 표현으로 억지로
쓰지 않는다 — 이 두 테이블이 명시적 상태 기계(PENDING_APPROVAL/
APPROVED/REJECTED/SUSPENDED)와 감사 가능한 이력을 담당하고,
`users.is_active`는 그 상태로부터 "로그인 가능 여부"만 파생시켜
기존에 이미 검증된 로그인 게이트(app/domains/auth/service.py)를
그대로 재사용할 수 있게 하는 캐시 값이다.
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


class UserRegistrationRequest(Base):

    __tablename__ = "user_registration_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    decided_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    rejection_reason_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    granted_role_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    invitation_code_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    def __repr__(self) -> str:

        return (
            f"<UserRegistrationRequest(id={self.id}, user_id={self.user_id}, "
            f"status={self.status})>"
        )


class InvitationCode(Base):

    __tablename__ = "invitation_codes"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    code_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )

    created_by_user_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )

    max_role_code: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True,
    )

    max_uses: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )

    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    def __repr__(self) -> str:

        return (
            f"<InvitationCode(id={self.id}, company_id={self.company_id}, "
            f"used={self.used_count}/{self.max_uses})>"
        )


__all__ = ["UserRegistrationRequest", "InvitationCode"]
