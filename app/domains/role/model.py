"""
=========================================================
Homez OS

File : app/domains/role/model.py
Version : 1.0.0

Role Model
=========================================================
"""

from __future__ import annotations

from sqlalchemy import Boolean
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class Role(Base):
    """
    실제 homez.db의 `roles` 테이블 컬럼은 id/company_id/name/code/
    description/active뿐이다(2026-07-30 확인). 이전 정의는 실제로 없는
    `created_at`/`updated_at`을 선언하고 있어, 이 Model로 Role 행을
    조회하면(예: User.role 프로퍼티가 role_id로 조회) `OperationalError:
    no such column`으로 실패했다 — 제거한다. `company_id`는 이번 인증
    작업 경로(로그인·권한 판정)에서 사용하지 않아 추가하지 않았다.

    V7 Live Gate 4 재작업(2026-08-16) — 위 docstring이 이미 "실제 컬럼은
    .../active뿐"이라고 명시하고 있었음에도 `active`가 매핑에서
    누락돼 있었다(`migrations/20260816_00_create_v7_gate9_core_
    foundation_schema.sql` 105행: `active BOOLEAN NOT NULL`, DEFAULT
    없음). 이미 DB에 존재하는 컬럼을 ORM에 뒤늦게 매핑하는 것뿐이며
    새 Migration이 아니다 — DB 스키마는 변경하지 않는다. 이 매핑이
    없으면 `Role(active=True)`처럼 키워드로 넘겨도 SQLAlchemy가 이를
    인식하지 못해(TypeError) `seed_roles()`의 `active NOT NULL` 결함
    자체를 고칠 방법이 없었다.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    role_permissions = relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:

        return (
            f"<Role(id={self.id}, "
            f"name={self.name}, "
            f"code={self.code})>"
        )


# role_permissions = relationship("RolePermission", ...) 위는 문자열로
# 클래스를 참조한다 — Role이 어디서 import되든 RolePermission이 항상
# 함께 로드되도록 여기서 명시적으로 import한다. 클래스 정의가 끝난
# "이후"(파일 맨 아래)에 두는 이유: role_permission.model →
# permission.model → permission/__init__.py → permission/policy.py →
# role.repository → role/__init__.py → role.model 로 이어지는 순환
# import 경로가 있어(2026-07-30 발견), 이 import를 클래스 정의보다
# 먼저 두면 "partially initialized module" 오류가 난다. Role 클래스가
# 이미 이 모듈 네임스페이스에 바인딩된 뒤라면 그 순환 경로가 다시
# role.model을 참조해도 Role을 정상적으로 찾을 수 있다.
from app.domains.role_permission.model import RolePermission  # noqa: E402,F401
